"""Review actual completed replay gates and fixed-input invariants internally."""
import argparse
from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path
import sys

from moe_ub_cosim.record_storage import iter_result
from .consumer_review import completion_conditions
from .path_replay import row_digest
from .workflow import digest, host, read, save


def review(case, output, bundle=None):
    case,output=Path(case),Path(output)
    if output.exists():raise ValueError('Review output must be new')
    selection=read(case/'selection.json');bundle=Path(bundle or selection['slice']);ex=case/'run/raw/execution'
    if digest(bundle/'slice.json')!=selection['slice_sha256']:
        raise ValueError('Explicit slice differs from the replay source')
    report=read(ex/'result.json')
    if not report['normal_exit'] or not report['network_drain']['idle']:raise ValueError('Incomplete execution')
    network=read(case/'run/inputs/network/backend.json');kinds={n['node_id']:n['kind'] for n in network['topology']['nodes']}
    node_rank=dict(zip(report['rank_to_ub_node'],report['rank_ids']));targets=[]
    for c in selection['discovery']['candidates']:
        peer=next(l[t] for l in network['topology']['links'] for s,t in (('src','dst'),('dst','src'))
                  if (l[s],l[s+'_port'])==(c['switch_id'],c['port_id']))
        if kinds[peer]!='DEVICE' or peer not in node_rank:raise ValueError('Selected outlet is not an active receiver')
        targets.append(dict(candidate=c,receiver_rank=node_rank[peer],peer_node=peer))
    endpoints={(r['rank_id'],r['local_record_id']):r for r in iter_result(ex,'endpoint_ops',report)}
    operations=list(iter_result(ex,'operation_registry',report));selected=[];ranks=set()
    for op in operations:
        if op['layer_id'] not in (7,8):continue
        members={m['rank_id']:endpoints[m['rank_id'],m['local_record_id']] for m in op['endpoint_records']}
        last=max(r['end_local_ns'] for r in members.values())
        latest={k for k,v in members.items() if v['end_local_ns']==last}
        chosen=latest|{t['receiver_rank'] for t in targets}
        # Keep original last consumers as explicit comparisons when they change.
        source=selection.get('baseline_actual_last',{})
        chosen.update(source.get(op['operation_id'],[]))
        ranks|=chosen
        selected.extend(dict(operation_id=op['operation_id'],kind=op['kind'],layer_id=op['layer_id'],rank_id=rank,
            is_actual_last=rank in latest,all_actual_last=sorted(latest),operation_end_ns=members[rank]['end_local_ns']) for rank in sorted(chosen))
    plans={};files={}
    for path in sorted((bundle/'raw/graphs').glob('*/plan.json')):
        p=read(path);gid=p['graph_id'];files[str(path.relative_to(bundle))]=digest(path)
        for graph in p['rank_graphs']:plans[graph['rank_id']]=graph
        del p
    times=defaultdict(dict);compute=[];kind_counts=Counter();last_model=0
    model_ids={(rank,s['node_id']) for rank,g in plans.items() for s in g['model_stages'] if s['node_id'] is not None}
    for r in iter_result(ex,'node_execution',report):
        times[r['rank_id']][r['node_id']]=r;kind_counts[r['kind']]+=1
        if r['kind']=='COMP':compute.append(dict(rank_id=r['rank_id'],node_id=r['node_id'],duration_ns=r['end_ns']-r['start_ns']))
        if (r['rank_id'],r['node_id']) in model_ids:last_model=max(last_model,r['end_ns'])
    planned={r['transfer_id']:r for r in iter_result(ex,'planned_transfers',report)}
    actual={r['transfer_id']:r for field in ('transfers','local_deliveries') for r in iter_result(ex,field,report)}
    if set(planned)!=set(actual):raise ValueError('Missing actual transfer/local delivery')
    node_maps={rank:{n['node_id']:n for n in g['nodes']} for rank,g in plans.items()}
    for rank,nodes in node_maps.items():
        if set(nodes)!=set(times[rank]):raise ValueError('Full executed graph coverage mismatch')
        for nid,n in nodes.items():
            t=times[rank][nid]
            if n['kind']=='COMP' and t['end_ns']-t['start_ns']!=n['duration_ns']:raise ValueError('Compute cost changed')
    source_gates={}
    for op in selected:
        rank=op['rank_id'];nodes=node_maps[rank];g=plans[rank];ts=times[rank]
        finish=next(n['node_id'] for n in nodes.values() if n.get('layer_id')==op['layer_id'] and n['name']==op['kind']+'-operator-complete')
        if ts[finish]['end_ns']!=op['operation_end_ns']:raise ValueError('Endpoint does not match actual gate')
        op['completion']=completion_conditions(nodes,ts,planned,actual,finish,op['layer_id'],kind=op['kind'])
        op['model_stages']=[dict(stage=s['stage'],layer_id=s['layer_id'],**ts[s['node_id']])
            for s in g['model_stages'] if s['node_id'] is not None and s['layer_id']==op['layer_id']]
        phase=next(p for p in g['layer_phases'] if p['layer_id']==op['layer_id'])
        op['expert_compute']=ts[phase['phase_nodes']['expert']]
        if op['kind']=='dispatch':
            for n in op['completion']['latest_completion_dag']:
                d=n.get('delivery_condition');tid=n.get('transfer_id')
                if d is None or tid is None or tid in source_gates:continue
                t=planned[tid]
                if 'ub_task_id' not in t:continue
                src=t['src_rank'];ranks.add(src)
                send=next(x['node_id'] for x in node_maps[src].values() if x.get('transfer_id')==tid and x['kind']=='SEND')
                source_gates[tid]=dict(identity=t,actual=actual[tid],
                    submission=completion_conditions(node_maps[src],times[src],planned,actual,send,op['layer_id'],kind='dispatch'))
    effective=read(ex/'effective_backend.json');live=effective.pop('live_port_service',None)
    for k in ('origins','catalog'):effective.pop(k,None)
    # The catalog/input schema can mention the override; immutable live objects
    # and fixed attributes are checked independently from that declaration.
    fixed_objects=row_digest(effective['objects']);fixed_attrs=effective['attributes']
    output.mkdir(parents=True)
    with gzip.open(output/'selected-facts.json.gz','wt',compresslevel=6) as stream:
        json.dump(dict(graph_id=gid,rank_graphs={r:plans[r] for r in ranks},node_execution={r:times[r] for r in ranks},
            planned_transfers={tid:t for tid,t in planned.items() if t['src_rank'] in ranks or t['dst_rank'] in ranks},
            actual_transfers={tid:t for tid,t in actual.items() if t['src_rank'] in ranks or t['dst_rank'] in ranks}),stream,separators=(',',':'))
    result=dict(schema_version='issue-network-replay-review/1',selection_sha256=digest(case/'selection.json'),
        manifest_sha256=digest(case/'run/manifest.json'),backend_sha256=read(case/'run/manifest.json')['backend_binary']['sha256'],
        program_sha256=digest(__file__),completion_program_sha256=digest(Path(__file__).with_name('consumer_review.py')),
        targets=targets,operations=selected,source_submission_gates=list(source_gates.values()),selected_ranks=sorted(ranks),
        facts_sha256=digest(output/'selected-facts.json.gz'),plan_files=files,planned_transfers=row_digest(planned.values()),
        computation_costs=row_digest(sorted(compute,key=lambda r:(r['rank_id'],r['node_id']))),executed_node_counts=dict(kind_counts),
        fixed_actual_objects=fixed_objects,fixed_attributes=fixed_attrs,live_service=live,random=report['random'],
        last_model_compute_ns=last_model,service_finished_ns=report['service_finished_ns'],
        simulation_end_ns=report['simulation_end_ns'],network_drain=report['network_drain'],
        interpretation='Actual complete gate review; frozen graph identity is separate from changed execution, physical routes and feedback.')
    save(output/'summary.json',result)
    print(json.dumps(dict(last_model_compute_ns=last_model,ranks=sorted(ranks),operations=len(selected))))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('case','output'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--slice',type=Path,help='Relocated immutable source slice; its manifest must match the recorded digest')
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:review(a.case,a.output,a.slice)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],
            str(a.output.resolve())+'-resources',**host(extra_roots=[a.output]))
        if r['status']!='completed':raise SystemExit('Actual network gate review failed: '+r['status'])


if __name__=='__main__':main()
