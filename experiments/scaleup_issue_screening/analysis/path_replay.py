"""Prepare selected STORE path capture and verify replay noninterference.

Uses sealed original runs and public segment freeze/replay/validate commands.
Selection reads researcher facts; its outputs must not become E3 algorithm input.
"""
import argparse
from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.record_storage import iter_result
from moe_ub_cosim.research_storage import stored_rows
from .workflow import CONFIG, cli, digest, host, read, save


def shortest_ports(topology, source, destination):
    neighbors=defaultdict(list)
    kinds={n['node_id']:n['kind'] for n in topology['nodes']}
    for link in topology['links']:
        for a,b in (('src','dst'),('dst','src')):
            neighbors[link[a]].append((link[b],link[a+'_port']))
    def distances(start):
        dist={start:0}; pending=deque([start])
        while pending:
            a=pending.popleft()
            for b,_ in neighbors[a]:
                if b not in dist and (kinds[b]!='DEVICE' or b in (source,destination)):
                    dist[b]=dist[a]+1; pending.append(b)
        return dist
    left,right=distances(source),distances(destination)
    length=left[destination]
    return {(a,port) for a,edges in neighbors.items() if kinds[a]!='DEVICE' and a in left
        for b,port in edges if b in right and left[a]+1+right[b]==length}


def prepare(case, summary_path, backend, output, fixed_pairs=()):
    case,output=Path(case).resolve(),Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    platform=read(CONFIG/'scenarios/batch.json')['platform_commit']
    if subprocess.run(['git','diff','--quiet',platform,'--','python/moe_ub_cosim','backend',
            'deps.lock.json','third_party/LLMServingSim','third_party/ns-3-ub']).returncode:
        raise ValueError('Platform differs from the accepted experimental baseline')
    cli(output,'original-validate','validate','--run',case/'run')
    summary=read(summary_path); layers=summary['analyzed_layers']
    if summary['public_manifest_sha256']!=digest(case/'run/manifest.json'):
        raise ValueError('Analysis summary is not bound to this original run')
    ex=case/'run/raw/execution'; actual={t['transfer_id']:t for t in iter_result(ex,'transfers')}
    ops={r['operation_id']:r for r in iter_result(ex,'operation_registry')
         if r['kind']=='combine' and r['layer_id'] in layers}
    planned={t['transfer_id']:t for t in iter_result(ex,'planned_transfers')
             if t['operation_id'] in ops and t['transfer_id'] in actual}
    pairs=set(tuple(p) for p in fixed_pairs); reasons=[]
    for op in summary['operations']:
        if op['kind']!='combine' or op['layer_id'] not in layers:continue
        for consumer in op['last_finishing_ranks']:
            eligible=[t for tid,t in planned.items() if t['operation_id']==op['operation_id'] and
                t['message_role']=='data' and t['dst_rank']==consumer and
                actual[tid]['data_ready_ns'] <= op['last_finish_ns']]
            if not eligible:raise ValueError('Last consumer has no incoming native data within completion')
            latest=max(actual[t['transfer_id']]['data_ready_ns'] for t in eligible)
            for t in eligible:
                if actual[t['transfer_id']]['data_ready_ns']==latest:
                    pair=(op['layer_id'],t['src_rank'],consumer);pairs.add(pair)
                    reasons.append(dict(layer=pair[0],source=pair[1],consumer=pair[2],
                        transfer_id=t['transfer_id'],data_ready_ns=latest,combine_finish_ns=op['last_finish_ns']))
    selected={tid:dict(plan=t,actual=actual[tid]) for tid,t in planned.items()
              if (ops[t['operation_id']]['layer_id'],t['src_rank'],t['dst_rank']) in pairs}
    if not selected:raise ValueError('Empty candidate path selection')
    net=read(case/'run/inputs/network/backend.json')
    result=read(ex/'result.json'); ports=set()
    for t in selected.values():
        a=t['actual'];ports |= shortest_ports(net['topology'],a['src_ub_node'],a['dst_ub_node'])
    observation=read(case/'run/inputs/observation.json')
    known={(s['switch_id'],p) for s in observation['switches'] for p in s['port_ids']}
    if not ports <= known:raise ValueError('Original observation misses statically possible candidate paths')
    end=((result['simulation_end_ns']//1000)+2)*1000
    original=read(case/'run/inputs/effective.json')
    if end>original['max_simulation_ns']:raise ValueError('Selected detail exceeds original simulation limit')
    observation['recording']['detail_ranges']=[dict(switch_id=n,port_id=p,start_ns=0,end_ns=end)
        for n,p in sorted(ports)]
    save(output/'observation.json',observation)
    save(output/'selection.json',dict(schema_version='issue-screening-path-selection/1',
        original_manifest_sha256=digest(case/'run/manifest.json'),summary_sha256=digest(summary_path),
        rule='latest incoming native data source for every last Combine consumer, all pair data/status chunks; plus declared fixed reference pairs',
        fixed_pairs=list(fixed_pairs),pairs=sorted(pairs),critical_data=reasons,transfers=selected,
        ports=sorted(ports),detail_start_ns=0,detail_end_ns=end,
        port_selection='all statically shortest paths, selected before detailed execution',
        original_backend_sha256=read(case/'run/manifest.json')['backend_binary']['sha256'],
        program_sha256=digest(__file__)))
    if digest(backend)!=read(case/'run/manifest.json')['backend_binary']['sha256']:
        raise ValueError('Backend binary differs from original execution')
    if not (case/'slice').exists():
        cli(output,'freeze','segment','freeze','--from-run',case/'run','--output',case/'slice','--storage','hardlink')
    config=dict(schema_version='moe-ub-replay/2',host=host(True,[case,output]),export_host=host(extra_roots=[case,output]),
        backend=str(Path(backend).resolve()),backend_timeout_seconds=original['backend_timeout_seconds'],
        max_simulation_ns=original['max_simulation_ns'],observation=str(output/'observation.json'),
        export_policy=str(CONFIG/'observation/export-policy.json'))
    save(output/'replay.json',config)
    print(json.dumps(dict(transfers=len(selected),ports=len(ports),pairs=sorted(pairs),detail_end_ns=end)))


def row_digest(rows):
    sha=hashlib.sha256();count=0
    for r in rows:
        sha.update(json.dumps(r,sort_keys=True,separators=(',',':')).encode()+b'\n');count+=1
    return dict(records=count,sha256=sha.hexdigest())


def equivalent(case, output):
    case,output=Path(case),Path(output)
    cli(output,'replay-validate','validate','--run',output/'run')
    a,b=case/'run/raw/execution',output/'run/raw/execution'
    ar,br=read(a/'result.json'),read(b/'result.json')
    fields=('simulation_end_ns','service_finished_ns','random','network_drain',
        'graph_completions','rank_resources','boundary_resources','resource_lifecycle')
    if any(ar[k]!=br[k] for k in fields):raise ValueError('Detailed replay changed execution metadata')
    records={}
    for f in ar['record_storage']['fields']:
        x,y=row_digest(iter_result(a,f)),row_digest(iter_result(b,f))
        if x!=y:raise ValueError('Detailed replay changed execution records: '+f)
        records[f]=x
    observed={}
    for f in read(case/'run/observed/storage.json')['fields']:
        x,y=row_digest(stored_rows(case/'run/observed',f)),row_digest(stored_rows(output/'run/observed',f))
        if x!=y:raise ValueError('Detailed replay changed observed records: '+f)
        observed[f]=x
    ports={}
    for folder in sorted((a/'ports').iterdir()):
        before=read(folder/'observation-audit.json')
        after=read(b/'ports'/folder.name/'observation-audit.json')
        if before!=after:raise ValueError('Detailed capture changed packet wire/time fingerprint: '+folder.name)
        ports[folder.name]=before
    save(output/'port-equivalence.json',ports)
    report=dict(schema_version='issue-screening-capture-equivalence/1',passed=True,
        original_manifest_sha256=digest(case/'run/manifest.json'),replay_manifest_sha256=digest(output/'run/manifest.json'),
        selection_sha256=digest(output/'selection.json'),execution_fields=records,observed_fields=observed,
        metadata_fields=list(fields),simulation_end_ns=ar['simulation_end_ns'],random=ar['random'],
        packet_wire_and_start_fingerprints_match=True,ports_compared=len(ports),port_equivalence_sha256=digest(output/'port-equivalence.json'),
        backend_executed=True,frontend_executed=False,independent_statistical_repeat=False)
    save(output/'equivalence.json',report)
    print(json.dumps(dict(passed=True,simulation_end_ns=ar['simulation_end_ns'])))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('prepare','run','compare'))
    p.add_argument('--case',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--summary',type=Path);p.add_argument('--backend',type=Path)
    p.add_argument('--fixed-pairs',type=Path,help='JSON list of [layer, source rank, consumer rank]')
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.action=='run':
        cli(a.output,'replay','replay','--slice',a.case/'slice','--config',a.output/'replay.json','--output',a.output/'run')
    elif a.worker:
        if a.action=='prepare':prepare(a.case,a.summary,a.backend,a.output,read(a.fixed_pairs) if a.fixed_pairs else ())
        else:equivalent(a.case,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        report=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],
            str(a.output.resolve())+'-'+a.action,**host(extra_roots=[a.case,a.output]))
        if report['status']!='completed':raise SystemExit('Path '+a.action+' failed: '+report['status'])


if __name__=='__main__':main()
