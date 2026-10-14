"""Private complete consumer-gate review with fixed graph/compute checks."""
import argparse
from collections import defaultdict
from pathlib import Path
import sys
from moe_ub_cosim.record_storage import iter_result
from .consumer_review import completion_conditions
from .path_replay import row_digest
from .workflow import CONFIG,digest,host,read,save


def review(case,output,baseline=None):
    case,output=Path(case),Path(output)
    if output.exists():raise ValueError('Business review output must be new')
    selection=read(case/'selection.json');bundle=Path(selection['slice']);run=case/'run';ex=run/'raw/execution'
    report=read(ex/'result.json')
    if not report['normal_exit'] or not report['network_drain']['idle']:raise ValueError('Incomplete backend')
    if digest(bundle/'slice.json')!=selection['slice_sha256']:raise ValueError('Graph source changed')
    net=read(run/'inputs/network/backend.json');node_rank=dict(zip(report['rank_to_ub_node'],report['rank_ids']))
    details=set(map(tuple,selection['detail_ports']));receivers=set()
    for l in net['topology']['links']:
        for a,b in (('src','dst'),('dst','src')):
            if (l[a],l[a+'_port']) in details and l[b] in node_rank:receivers.add(node_rank[l[b]])
    before=read(baseline) if baseline else None
    endpoints={(r['rank_id'],r['local_record_id']):r for r in iter_result(ex,'endpoint_ops',report)}
    operations=[];selected=[]
    for op in iter_result(ex,'operation_registry',report):
        members={r['rank_id']:endpoints[r['rank_id'],r['local_record_id']] for r in op['endpoint_records']}
        end=max(r['end_local_ns'] for r in members.values());latest=sorted(r for r,v in members.items() if v['end_local_ns']==end)
        operations.append(dict(operation_id=op['operation_id'],kind=op['kind'],layer_id=op['layer_id'],end_ns=end,
            last_ranks=latest,endpoints=list(members.values())))
        ranks=set(latest)|receivers
        if before:
            ranks|=set(next(o['last_ranks'] for o in before['operations'] if o['operation_id']==op['operation_id']))
        for rank in sorted(ranks):selected.append(dict(operation_id=op['operation_id'],layer_id=op['layer_id'],
            kind=op['kind'],rank_id=rank,end_ns=members[rank]['end_local_ns'],is_latest=rank in latest))
    plans={};files={}
    for path in sorted((bundle/'raw/graphs').glob('*/plan.json')):
        doc=read(path);files[str(path.relative_to(bundle))]=digest(path)
        for graph in doc['rank_graphs']:
            if graph['rank_id'] in plans:raise ValueError('Study expects one graph per rank')
            plans[graph['rank_id']]=graph
        del doc
    times=defaultdict(dict);compute=[];last_model=0
    model_ids={(rank,s['node_id']) for rank,g in plans.items() for s in g['model_stages'] if s['node_id'] is not None}
    for r in iter_result(ex,'node_execution',report):
        times[r['rank_id']][r['node_id']]=r
        if r['kind']=='COMP':compute.append(dict(rank_id=r['rank_id'],node_id=r['node_id'],duration_ns=r['end_ns']-r['start_ns']))
        if (r['rank_id'],r['node_id']) in model_ids:last_model=max(last_model,r['end_ns'])
    planned={r['transfer_id']:r for r in iter_result(ex,'planned_transfers',report)}
    actual={r['transfer_id']:r for f in ('transfers','local_deliveries') for r in iter_result(ex,f,report)}
    if set(planned)!=set(actual):raise ValueError('Missing transfer completion')
    node_maps={rank:{n['node_id']:n for n in g['nodes']} for rank,g in plans.items()}
    for rank,nodes in node_maps.items():
        if set(nodes)!=set(times[rank]):raise ValueError('Incomplete executed graph')
        for nid,node in nodes.items():
            t=times[rank][nid]
            if node['kind']=='COMP' and t['end_ns']-t['start_ns']!=node['duration_ns']:raise ValueError('Compute baseline changed')
    for item in selected:
        rank=item['rank_id'];nodes=node_maps[rank];ts=times[rank]
        finish=next(n['node_id'] for n in nodes.values() if n.get('layer_id')==item['layer_id'] and n['name']==item['kind']+'-operator-complete')
        if ts[finish]['end_ns']!=item['end_ns']:raise ValueError('Operator boundary mismatch')
        item['completion']=completion_conditions(nodes,ts,planned,actual,finish,item['layer_id'],item['kind'])
    effective=read(ex/'effective_backend.json');live=effective.pop('live_port_service',None)
    result=dict(schema_version='issue-diagnostic-business/1',manifest_sha256=digest(run/'manifest.json'),
        source_slice_sha256=selection['slice_sha256'],plan_files=files,backend_sha256=selection['backend_sha256'],
        planned_transfers=row_digest(planned.values()),compute_costs=row_digest(sorted(compute,key=lambda r:(r['rank_id'],r['node_id']))),
        effective_attributes=effective['attributes'],effective_objects=row_digest(effective['objects']),random=report['random'],
        service_finished_ns=report['service_finished_ns'],simulation_end_ns=report['simulation_end_ns'],last_model_compute_ns=last_model,
        live_service=live,network_drain=report['network_drain'],operations=operations,reviewed_gates=selected,
        private_reference=selection['case'],program_sha256=digest(__file__),
        scope='Every operation latest consumer and fixed deep receiver references, complete finish/data/status/reduction/core-send ties; prior-layer/callback boundaries explicit. No C1 intervention.')
    if before:
        for name in ('source_slice_sha256','plan_files','planned_transfers','compute_costs','effective_attributes','effective_objects','random'):
            if result[name]!=before[name]:raise ValueError('Fixed business/resource invariant changed: '+name)
        result['baseline_sha256']=digest(baseline)
    output.mkdir(parents=True);save(output/'summary.json',result)
    print('Reviewed',len(selected),'complete consumer gates; end',report['simulation_end_ns'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--baseline',type=Path);p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:review(a.case,a.output,a.baseline)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],str(a.output)+'-resources',**host())
        if r['status']!='completed':raise SystemExit(r['status'])

if __name__=='__main__':main()
