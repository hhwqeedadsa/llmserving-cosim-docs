"""Private bounded dependency continuation across actual transfer callbacks.

This reads an existing execution only. Native submission-to-progress remains
an explicit boundary; membership alone is never attributed to a switch queue.
"""
import argparse
from collections import defaultdict
from pathlib import Path
import sys
from moe_ub_cosim.record_storage import iter_result
from .diagnostic_judge import rows
from .workflow import digest,host,read,save


def latest_dependencies(node,times):
    ready=max((times[d]['end_ns'] for d in node['deps']),default=0)
    return ready,[d for d in node['deps'] if times[d]['end_ns']==ready]


def review(case,business,deep,output):
    case,business,deep,output=map(Path,(case,business,deep,output))
    if output.exists():raise ValueError('Feedback review output must be new')
    run=case/'run';ex=run/'raw/execution';seal=read(business/'summary.json');dseal=read(deep/'summary.json')
    if seal['manifest_sha256']!=digest(run/'manifest.json') or dseal['business_sha256']!=digest(business/'summary.json'):
        raise ValueError('Feedback source mismatch')
    if dseal['packet_evidence_sha256']!=digest(deep/'packet-evidence.jsonl.gz'):raise ValueError('Packet evidence changed')
    selection=read(case/'selection.json');bundle=Path(selection['slice']);plans={};sends={};resources=defaultdict(list)
    for path in sorted((bundle/'raw/graphs').glob('*/plan.json')):
        if seal['plan_files'][str(path.relative_to(bundle))]!=digest(path):raise ValueError('Plan changed')
        doc=read(path)
        for graph in doc['rank_graphs']:
            rank=graph['rank_id'];plans[rank]={n['node_id']:n for n in graph['nodes']}
            for n in graph['nodes']:
                if n['kind']=='SEND':sends[n['transfer_id']]=(rank,n['node_id'])
                if n['kind']=='COMP' and n.get('resource_id'):resources[rank,n['resource_id']].append(n['node_id'])
        del doc
    times=defaultdict(dict)
    for t in iter_result(ex,'node_execution'):times[t['rank_id']][t['node_id']]=t
    actual={t['transfer_id']:t for field in ('transfers','local_deliveries') for t in iter_result(ex,field)}
    terminal=defaultdict(list)
    for p in rows(deep/'packet-evidence.jsonl.gz'):
        if p.get('is_final_wire_arrival_for_transfer'):
            terminal[p['transfer_id']].append({k:p[k] for k in ('node_id','port_id','packet_uid','network_arrival_ns',
                'tx_start_ns','completion_delay_ns','rate_bps','downstream_wire_arrival_ns','arrival_to_tx_ns','busy_during_wait_ns') if k in p})
    roots=[];pending=[]
    for g in seal['reviewed_gates']:
        if g['is_latest']:
            key=(g['rank_id'],g['completion']['finish']['node_id']);pending.append(key)
            roots.append(dict(operation_id=g['operation_id'],rank_id=key[0],node_id=key[1],end_ns=g['end_ns']))
    found={}
    while pending:
        key=pending.pop()
        if key in found:continue
        if len(found)>=10000:raise ValueError('Feedback witness exceeds fixed 10000-node bound')
        rank,nid=key;n=plans[rank][nid];t=times[rank][nid];ready,ties=latest_dependencies(n,times[rank])
        if not ready<=t['start_ns']<=t['end_ns']:raise ValueError('Dependency timing violated')
        record=dict(rank_id=rank,node_id=nid,name=n['name'],kind=n['kind'],start_ns=t['start_ns'],end_ns=t['end_ns'],
            latest_start_dependencies=ties,start_ready_ns=ready,edges=[],resource_owners=[])
        found[key]=record
        for dep in ties:
            record['edges'].append(dict(kind='latest_start_dependency',rank_id=rank,node_id=dep));pending.append((rank,dep))
        rid=n.get('resource_id');covered=0
        for other in resources.get((rank,rid),[]) if rid and ready<t['start_ns'] else []:
            o=times[rank][other];lo,hi=max(ready,o['start_ns']),min(t['start_ns'],o['end_ns'])
            if other!=nid and lo<hi:
                covered+=hi-lo;record['resource_owners'].append(dict(node_id=other,start_ns=lo,end_ns=hi));pending.append((rank,other))
        record['unexplained_ready_to_start_ns']=t['start_ns']-ready-covered
        tid=n.get('transfer_id');field=('data_ready_ns' if n['kind']=='RECV' or n.get('event')=='data_ready' else
            'local_read_complete_ns' if n.get('event')=='local_read_complete' else None)
        if tid is not None and field:
            a=actual[tid];local='ready_ns' in a
            at=a['ready_ns'] if local else (a[field] if field in a else a['local_facts'][field])
            if t['end_ns']!=max(t['start_ns'],at):raise ValueError('Callback time mismatch')
            record['callback']=dict(transfer_id=tid,field=field,time_ns=at,pending_after_start=at>t['start_ns'])
            if not local:
                record['callback'].update(src_rank=a['src_rank'],dst_rank=a['dst_rank'],submitted_ns=a['send_start_ns'],
                    data_ready_ns=a['data_ready_ns'],local_read_complete_ns=a['local_facts']['local_read_complete_ns'],
                    transaction_done_ns=a['transaction_done_ns'],terminal_packet_witnesses=terminal[tid],
                    limit='Submission-to-progress includes native waiting and network feedback; not assigned wholesale to an outlet')
            if at>t['start_ns']:
                if tid not in sends:raise ValueError('Missing source SEND for pending callback')
                src=sends[tid];record['edges'].append(dict(kind='actual_callback_source_submission',rank_id=src[0],node_id=src[1]));pending.append(src)
    save(output,dict(schema_version='issue-diagnostic-feedback/1',manifest_sha256=seal['manifest_sha256'],
        business_sha256=digest(business/'summary.json'),deep_sha256=digest(deep/'summary.json'),roots=roots,nodes=list(found.values()),
        program_sha256=digest(__file__),scope='All latest-consumer start dependencies, actual callback source submissions and occupied COMP resources. Edges are necessary conditions, not additive or uniquely critical loss.'))
    print('Reviewed',len(found),'bounded cross-callback dependency nodes')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('case','business','deep','output'):p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:review(a.case,a.business,a.deep,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],str(a.output)+'-resources',**host())
        if r['status']!='completed':raise SystemExit(r['status'])


if __name__=='__main__':main()
