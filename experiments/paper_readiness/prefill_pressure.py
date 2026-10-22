"""Fixed real-Prefill data pressure study; analysis never reads route truth."""
import argparse
from collections import Counter, defaultdict, deque
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from analyze_dispatch_validation import hash32

ROOT = Path(__file__).resolve().parents[2]


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def prepare(args):
    out = args.output.resolve()
    assert not out.exists()
    manifest = load(args.manifest)
    assert len(manifest['files']) == 4
    assert manifest['source']['dataset_revision'] == '27febb7b2d24169560a9f6f83d38eb53f94916cf'
    raw = []
    identities = []
    for item in manifest['files']:
        path = ROOT/item['file']['path']
        assert path.stat().st_size < 4*2**20 and sha(path) == item['file']['sha256']
        raw.append(load(path))
        identities.append(dict(path=item['file']['path'],sha256=sha(path)))
    lengths = [len(x[0]['3']) for x in raw]
    assert lengths == [105,28,10,6]
    assert sum((ROOT/item['path']).stat().st_size for item in identities) < 16*2**20
    out.mkdir()
    observed, truth = out/'observed', out/'truth'
    observed.mkdir()
    truth.mkdir()
    shutil.copyfile(args.manifest, truth/'manifest.json')
    topology = load(ROOT/'examples/scaleout_rdma/small-clos-100g.json')
    topology['deployment']['gpu_type'] = 'NONE'
    for tier in ('tor_agg','agg_core','tor_core'):
        topology['links'][tier]['rate_bps'] = 25000000000
    save(observed/'topology.json', topology)
    protocol = load(ROOT/'examples/scaleout_rdma/rdma.json')
    protocol.update(schema_version='moe-scaleout-rdma/2',seed=11,queue_metrics=True,
        packet_capture=dict(nodes=[0,2,4,6],start_ns=0,end_ns=5000000,max_events=100000),
        limits=dict(max_gpus=32,max_switches=16,max_links=256,max_messages=8192,max_queue_events=200000,
            queue_capture='summary',max_sim_ns=5000000,wall_seconds=60,rss_bytes=2**30,
            address_bytes=2**31,artifact_bytes=64*2**20,result_json_bytes=16*2**20))
    protocol['ecn_by_rate']['25000000000'] = deepcopy(protocol['ecn_by_rate']['100000000000'])
    save(observed/'protocol.json', protocol)
    cases = []
    provenance = []
    for layer in (4,32):
        selections = [x[0][str(layer)] for x in raw]
        assert [len(rows) for rows in selections] == lengths
        work, refs = [], []
        for src, tokens in enumerate(selections):
            for token, choices in enumerate(tokens):
                assert len(choices) == len(set(choices)) == 8
                assert all(type(e) is int and 0 <= e < 256 for e in choices)
                for dst in sorted({e//64 for e in choices}-{src}):
                    identity = len(work)
                    work.append(dict(id=identity,stream=4*src+dst,bytes=14464,after=[]))
                    refs.append(dict(work=identity,source_rank=src,token=token,destination_rank=dst))
        assert 0 < len(work) <= 149*3
        for depth in (1,8):
            name = f'layer{layer}-depth{depth}'
            streams = [dict(id=4*s+d,src=2*s,dst=2*d,plane=0,wire_port=21000+4*s+d,
                source_nic=1,destination_nic=1,queue_depth=depth)
                for s in range(4) for d in range(4) if s != d]
            save(observed/(name+'.json'), dict(schema_version='moe-rdma-stream-work/2',
                streams=streams,work=work,limits=dict(max_streams=12,max_work=512)))
            cases.append(dict(name=name,layer=layer,queue_depth=depth,work_count=len(work)))
        provenance.append(dict(layer=layer,work_source_mapping=refs))
    save(observed/'index.json',dict(schema_version='moe-prefill-pressure/1',cases=cases,
        source_token_counts=lengths,token_bytes=14464,
        artifacts={p.name:dict(sha256=sha(p),bytes=p.stat().st_size) for p in sorted(observed.iterdir())},
        synchronized_roots_are_declared=True,modern_control_modeled=False,hardware_calibrated=False))
    save(truth/'provenance.json',dict(manifest_sha256=sha(args.manifest),files=identities,layers=provenance,
        input_expert_placement='expert_id//64',source_assignment='manifest order, one whole request per rank',
        synchronized_launch_observed=False,independent_heldout_requests=False))
    print(json.dumps(dict(prepared=True,cases=cases,source_token_counts=lengths)))


def run(args):
    observed, backend, out = (p.resolve() for p in (args.observed,args.backend,args.output))
    assert not out.exists() and backend.is_file()
    index = load(observed/'index.json')
    for name,identity in index['artifacts'].items():
        assert sha(observed/name) == identity['sha256']
    out.mkdir()
    commands = []
    def cli(label, tail):
        cmd = [sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,tail)]
        record = dict(label=label,command=cmd,cwd=str(ROOT))
        commands.append(record)
        start = time.monotonic()
        try:
            with (out/(label+'.stdout.log')).open('w') as stdout, (out/(label+'.stderr.log')).open('w') as stderr:
                child = subprocess.run(cmd,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=60)
            record['exit_code'] = child.returncode
            assert child.returncode == 0, 'Failed public CLI: preserve and stop'
        finally:
            record['wall_seconds'] = time.monotonic()-start
            save(out/'commands.json',commands)
    cli('topology',['topology','--config',observed/'topology.json','--output',out/'topology'])
    for case in index['cases']:
        name = case['name']
        cli(name,['rdma-stream-probe','--topology',out/'topology','--protocol',observed/'protocol.json',
            '--config',observed/(name+'.json'),'--backend',backend,'--output',out/name])
    save(out/'execution.json',dict(completed=True,commands=len(commands),backend_sha256=sha(backend),
        platform_base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        observed_index_sha256=sha(observed/'index.json'),network_runs=4,hardware_calibrated=False))
    print(json.dumps(dict(completed=True,new_network_runs=4)))


def project(topology, description, seed):
    """Static no-retransmission data+ACK service, never a completion prediction."""
    adjacency = defaultdict(list)
    gpu_count = sum(n['kind'] == 'gpu' for n in topology['nodes'])
    endpoints = {}
    for edge in topology['links']:
        for node,port,peer in ((edge['src'],edge['src_port'],edge['dst']),
                               (edge['dst'],edge['dst_port'],edge['src'])):
            endpoints[node,port] = (peer,edge['rate_bps'])
            adjacency[node].append(peer)
    loads = defaultdict(Counter)
    paths = {}
    streams = {s['id']:s for s in description['streams']}
    for work in description['work']:
        stream = streams[work['stream']]
        packets = (work['bytes']+999)//1000
        for protocol,src,dst,size in ((17,stream['src'],stream['dst'],work['bytes']+36*packets),
                                      (252,stream['dst'],stream['src'],48*packets)):
            distance = {dst:0}
            pending = deque([dst])
            while pending:
                node = pending.popleft()
                for peer in adjacency[node]:
                    if peer not in distance:
                        distance[peer] = distance[node]+1
                        if peer >= gpu_count:
                            pending.append(peer)
            node, visited, path = src, set(), [src]
            while node != dst:
                assert node not in visited
                visited.add(node)
                ports = [key for key,(peer,_) in endpoints.items() if key[0] == node and distance[peer] == distance[node]-1]
                if node < gpu_count:
                    assert len(ports) == 1
                    selected = ports[0]
                else:
                    wire = stream['wire_port']
                    selected = ports[hash32([0x0b000001+src*32,0x0b000001+dst*32,wire|(wire<<16)],seed+node)%len(ports)]
                loads[selected]['data' if protocol == 17 else 'ack'] += size
                if protocol == 17:
                    loads[selected]['data_packets'] += packets
                node = endpoints[selected][0]
                path.append(node)
            paths[stream['id'],protocol] = path
    return loads,endpoints,paths


def analyze(args):
    observed, run_root, out = args.observed,args.run,args.output
    assert not out.exists()
    index = load(observed/'index.json')
    topology = load(run_root/'topology/topology.json')
    rows = []
    for case in index['cases']:
        name = case['name']
        description = load(observed/(name+'.json'))
        path = run_root/name/'result.json'
        result = load(path)
        assert result['passed'] and result['drained'] and result['fault_drops'] == 0
        assert result['packet_capture']['complete'] and result['packet_capture']['omitted_events'] == 0
        transport = result['persistent_transport']
        assert transport['closed'] and transport['physical_drained']
        loads,endpoints,paths = project(topology,description,11)
        incoming, outgoing = [0]*4,[0]*4
        for w in description['work']:
            s = next(s for s in description['streams'] if s['id'] == w['stream'])
            outgoing[s['src']//2] += 1
            incoming[s['dst']//2] += 1
        wait = [w['published_ns']-w['ready_ns'] for w in transport['work']]
        bound = max(Fraction(sum(values[k] for k in ('data','ack'))*8*10**9,endpoints[key][1]) for key,values in loads.items())
        # Do not assert that PFC/feedback control equals the minimum ACK model.
        excess = []
        for p in result['ports']:
            predicted = loads[p['node'],p['port']]
            assert p['data_tx_bytes'] == predicted['data'] and p['data_packets'] == predicted['data_packets']
            assert p['control_tx_bytes'] >= predicted['ack']
            excess.append(p['control_tx_bytes']-predicted['ack'])
        acked = max(w['acked_ns'] for w in transport['work'])
        assert bound <= acked
        rows.append(dict(case=name,layer=case['layer'],queue_depth=case['queue_depth'],result_sha256=sha(path),
            work_count=len(transport['work']),source_tokens=index['source_token_counts'],source_copies=outgoing,
            destination_copies=incoming,source_copy_max_to_mean=4*max(outgoing)/sum(outgoing),
            destination_copy_max_to_mean=4*max(incoming)/sum(incoming),
            accepted_ns=max(w['accepted_ns'] for w in transport['work']),acked_ns=acked,finish_ns=result['finish_ns'],
            minimum_port_service_ns=float(bound),wait_work_count=sum(v>0 for v in wait),
            max_publication_wait_ns=max(wait),sum_publication_wait_ns=sum(wait),
            total_queue_area_byte_ns=sum(p.get('queue_metrics',{}).get('area_byte_ns',0) for p in result['ports']),
            max_queue_bytes=max(p.get('queue_metrics',{}).get('peak_bytes',0) for p in result['ports']),
            ecn_seen_at_hooks=sum(p['ecn_ce_seen'] for p in result['ports']),
            pfc_pause=sum(p['pfc_pause'] for p in result['ports']),pfc_resume=sum(p['pfc_resume'] for p in result['ports']),
            control_bytes_above_minimum_ack=sum(excess),capture_events=len(result['packet_capture']['events']),
            source_data_packets=len(transport['source_packets']),
            source_token_max_to_mean=4*max(index['source_token_counts'])/sum(index['source_token_counts'])))
    out.mkdir()
    save(out/'summary.json',dict(cases=rows,observed_index_sha256=sha(observed/'index.json'),
        analysis_reads_truth=False,scope='Declared simultaneous independent real-Prefill data writes, not full inference or optimization',
        ecn_count_scope='sum of physical hook observations; same CE-marked packet may be counted more than once',
        cumulative_wait_scope='overlapping per-work waits, not removable delay or whole-run time'))
    print(json.dumps(dict(analyzed=True,cases=rows),ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='stage',required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p = sub.add_parser('run')
    for name in ('observed','backend','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p = sub.add_parser('analyze')
    for name in ('observed','run','output'):
        p.add_argument('--'+name,type=Path,required=True)
    args = parser.parse_args()
    dict(prepare=prepare,run=run,analyze=analyze)[args.stage](args)


if __name__ == '__main__':
    main()
