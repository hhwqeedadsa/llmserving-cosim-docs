"""Prepare and audit existing passive archives; never execute a backend.

Private records are read only here. Judgment consumes the separately prepared
input and class-wide semantic certificate, never this program's audit/truth.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

from moe_ub_cosim.record_storage import iter_result
from .consumer_review import Archive
from .organization import resolved_members
from . import network_information as old
from .workflow import ROOT, digest, host, read, save

SEMANTIC_FILES = ('python/moe_ub_cosim/a3.py', 'backend/a3_plan.cc',
    'backend/layer_plan.cc', 'backend/ub_write_transfer.cc', 'backend/write_completion.cc',
    'backend/cosim_service.cc')


def restore(archive, output):
    """Restore only named analysis inputs, verifying aliases and every byte."""
    source = Archive(archive)
    names = ['selection.json', 'equivalence.json', 'observation.json', 'run/manifest.json',
        'run/raw/execution/result.json', 'run/inputs/network/backend.json',
        'run/observed/public_config.json', 'run/observed/storage.json',
        'run/observed/records/queue_windows.csv.gz',
        'run/truth/records/message_attribution_windows.csv.gz']
    fields = ('endpoint_ops', 'operation_registry', 'planned_transfers', 'transfers')
    names += [n for n in source.index['entries'] if any(
        n.startswith('run/raw/execution/records/'+f+'-') and n.endswith('.jsonl.gz') for f in fields)]
    output.mkdir(parents=True, exist_ok=False)
    for name, data in resolved_members(source, names):
        path = output/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    save(output/'source.json', dict(archive=str(archive), archive_sha256=digest(archive),
        inventory_sha256=digest(str(archive)+'.json'), members=source.used))


def transfer_bounds(planned, actual, endpoints):
    """All failures are retained; no truth-dependent per-transfer eligibility."""
    src = endpoints[planned['operation_id'], planned['src_rank']]
    dst = endpoints[planned['operation_id'], planned['dst_rank']]
    return dict(source_start=src['start_local_ns'] <= actual['send_start_ns'],
        destination_end=actual['data_ready_ns'] <= dst['end_local_ns'],
        source_end=actual['data_ready_ns'] <= src['end_local_ns'])


def audit(case, bundle, information, output):
    selection = read(case/'selection.json'); manifest = read(case/'run/manifest.json')
    if digest(bundle/'slice.json') != selection['slice_sha256'] or manifest['source_slice_sha256'] != selection['slice_sha256']:
        raise ValueError('Frozen slice identity differs')
    sealed = read(bundle/'slice.json'); sources = {}
    for name in SEMANTIC_FILES:
        identity = digest(ROOT/name)
        if manifest['software']['source_hashes'].get(name) != identity:
            raise ValueError('Reviewed semantics differ from execution source: '+name)
        sources[name] = identity
    ex = case/'run/raw/execution'; report = read(ex/'result.json')
    endpoint_rows = {(r['rank_id'], r['local_record_id']):r for r in iter_result(ex, 'endpoint_ops', report)}
    endpoints = {}; operations = list(iter_result(ex, 'operation_registry', report))
    for op in operations:
        for m in op['endpoint_records']:
            endpoints[op['operation_id'],m['rank_id']] = endpoint_rows.pop((m['rank_id'],m['local_record_id']))
    if endpoint_rows: raise ValueError('Unassigned endpoints')
    planned = {r['transfer_id']:r for r in iter_result(ex, 'planned_transfers', report)}
    structural = Counter(); classes = {}; plan_hashes = {}
    # Audit the saved dependency graph as well as the implementation contract.
    for submission in sealed['submissions']:
        name = 'raw/'+submission['semantic_manifest']; path = bundle/name
        if digest(path) != sealed['artifacts'][name]['sha256']:
            raise ValueError('Frozen graph differs')
        plan_hashes[name] = digest(path); plan = read(path)
        layers = plan.get('layers', [plan])
        graph_planned = {t['transfer_id']:t for t in plan['transfers']}
        if any(planned.get(k) != t for k,t in graph_planned.items()):
            raise ValueError('Executed transfers differ from saved graph')
        for layer in layers:
            profile = layer['input']['cann_profile']
            eligible = (layer['communication_template']=='cann_a3_v850_store_resources_v1'
                        and profile['copy_fence']=='data_ready')
            for op in layer['operations']:
                classes[op['operation_id']] = dict(kind=op['kind'],
                    template=layer['communication_template'],copy_fence=profile['copy_fence'],
                    source_end_bounds_remote_delivery=eligible)
        for rg in plan['rank_graphs']:
            nodes = {n['node_id']:n for n in rg['nodes']}
            events = {n['transfer_id']:n['node_id'] for n in nodes.values() if n.get('event')=='data_ready'}
            phases = rg.get('layer_phases', [rg])
            for layer, phase in zip(layers, phases, strict=True):
                for op in layer['operations']:
                    if not classes[op['operation_id']]['source_end_bounds_remote_delivery']: continue
                    finish = phase['phase_nodes']['dispatch_reorder' if op['kind']=='dispatch' else 'finish']
                    pending = [finish]; ancestors = set()
                    while pending:
                        n = pending.pop()
                        if n in ancestors: continue
                        ancestors.add(n); pending.extend(nodes[n]['deps'])
                    outgoing = [t for t in graph_planned.values() if t['src_rank']==rg['rank_id']
                                and t['operation_id']==op['operation_id']]
                    for t in outgoing:
                        if events.get(t['transfer_id']) not in ancestors:
                            raise ValueError('Operator completion misses an outgoing remote fence')
                        structural[op['kind']+'/'+t['message_role']] += 1
        del plan, graph_planned, nodes
    counts = Counter(); counterexamples = []; seen = set(); slack = {}
    for a in iter_result(ex,'transfers',report):
        tid=a['transfer_id']; t=planned[tid]
        if tid in seen: raise ValueError('Repeated network transfer')
        seen.add(tid)
        if (a['operation']!='STORE' or any(a[k]!=t[k] for k in ('src_rank','dst_rank'))
                or a['bytes']!=t['payload_bytes'] or a['committed_bytes']!=a['bytes']
                or a['data_owner_rank']!=t['src_rank'] or a['consumer_rank']!=t['dst_rank']):
            raise ValueError('Transfer identity/operation/role differs')
        category=t['kind']+'/'+t['message_role']; counts[category]+=1
        checks=transfer_bounds(t,a,endpoints)
        delta=endpoints[t['operation_id'],t['src_rank']]['end_local_ns']-a['data_ready_ns']
        slack[category]=min(slack.get(category,delta),delta)
        if not all(checks.values()):
            counterexamples.append(dict(transfer_id=tid,operation_id=t['operation_id'],category=category,
                src_rank=t['src_rank'],dst_rank=t['dst_rank'],checks=checks,source_end_slack_ns=delta))
    expected={t['transfer_id'] for t in planned.values() if 'ub_task_id' in t}
    if seen!=expected: raise ValueError('Network transfer coverage differs')
    # Class-wide contract, not a list selected from successful truth samples.
    meanings=read(information/'truth.json')['operation_meanings']
    certificate=dict(schema_version='issue-network-end-semantics/1',input_sha256=digest(information/'input.json'),
        clock='aligned_simulation_ns', operations=[dict(operation=meanings[o],
            source_end_bounds_remote_delivery=c['source_end_bounds_remote_delivery']) for o,c in classes.items()])
    save(output/'semantics.json',certificate)
    save(output/'audit.json',dict(schema_version='issue-network-source-end-audit/1',
        network_transfers=len(seen),network_classes=counts,minimum_source_end_slack_ns=slack,
        structural_outgoing_fences=structural,classes=classes,counterexamples=counterexamples,
        local_transfers=len(planned)-len(expected),planned_transfers=len(planned),endpoints=len(endpoints),
        source_files=sources,plan_hashes=plan_hashes,slice_sha256=selection['slice_sha256'],
        archive_source_sha256=digest(case/'source.json'),manifest_sha256=digest(case/'run/manifest.json'),
        semantics_sha256=digest(output/'semantics.json'),program_sha256=digest(__file__),
        distinction='Remote data delivery bounds outgoing application data/status; ACK/credit and protocol retirement are not bounded by operator end. Local read completion alone is ineligible.'))
    if any(not x['checks']['source_start'] or not x['checks']['destination_end'] or
           (classes[x['operation_id']]['source_end_bounds_remote_delivery'] and not x['checks']['source_end'])
           for x in counterexamples):
        raise ValueError('Time-bound contract contradicted; retained all counterexamples')


def prepare(archive, bundle, packets, output, previous=None):
    if output.exists(): raise ValueError('Use a new offline output')
    output.mkdir(parents=True)
    restore(archive,output/'source')
    info=output/'information'
    old.prepare(output/'source',packets,info)
    old.judge(info/'input.json',info/'old-prediction.json')
    old.evaluate(info/'input.json',info/'old-prediction.json',info/'truth.json',info/'old-evaluation.json')
    old.prepare_mapping(output/'source',info/'input.json',info/'truth.json',info/'old-prediction.json',info/'relation.json')
    old.judge(info/'input.json',info/'old-mapping-prediction.json',info/'relation.json')
    old.evaluate(info/'input.json',info/'old-mapping-prediction.json',info/'truth.json',info/'old-mapping-evaluation.json')
    if previous:
        checks={}
        for new,prior,fields in (
                ('input.json','input.json',None),('relation.json','relation.json',None),
                ('truth.json','truth.json',['samples','operation_meanings','bound_checked_network_transfers']),
                ('old-prediction.json','prediction.json',['samples']),
                ('old-evaluation.json','evaluation.json',['primary','related_check','samples']),
                ('old-mapping-prediction.json','mapping-prediction.json',['samples']),
                ('old-mapping-evaluation.json','mapping-evaluation.json',['primary','related_check','samples'])):
            a,b=read(info/new),read(previous/prior)
            checks[new]=a==b if fields is None else all(a[k]==b[k] for k in fields)
        save(output/'reproduction.json',dict(passed=all(checks.values()),checks=checks,
            original_sources={str(p):digest(p) for p in previous.iterdir() if p.is_file()},
            reproduced_sources={str(p):digest(p) for p in info.iterdir() if p.is_file()},
            program_sha256=digest(__file__)))
        if not all(checks.values()): raise ValueError('Old rule reproduction differs')
    audit(output/'source',bundle,info,output)
    print(json.dumps(dict(output=str(output),audit=read(output/'audit.json')['network_classes'])))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('archive','slice','packets','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--previous',type=Path,help='Optional old information directory to reproduce exactly')
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    a=p.parse_args()
    if a.worker:prepare(a.archive,a.slice,a.packets,a.output,a.previous)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m','experiments.scaleup_issue_screening.network_offline',*sys.argv[1:],'--worker'],
            str(a.output)+'-resources',**host())
        if r['status']!='completed':raise SystemExit('Offline preparation failed: '+r['status'])


if __name__=='__main__':main()
