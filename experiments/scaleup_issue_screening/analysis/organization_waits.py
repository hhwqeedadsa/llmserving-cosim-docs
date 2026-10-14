"""Partition recorded data-fence waits at actual request-formation callbacks.

This is event accounting, not isolation of switch delay or removable losses.
"""
import argparse
from collections import Counter, defaultdict
from pathlib import Path
import sys
from .consumer_review import Archive
from .workflow import digest,host,read,save
from .organization import zipped_rows
from .path_replay import row_digest


def request_order(rows):
    groups=defaultdict(list)
    for row in rows:
        i=row['identity']
        groups[i['src_rank'],i['producer']].append((i['graph_id'],i['transfer_id'],i['send_node_id'],row['offset'],row['bytes']))
    return row_digest([dict(rank=k[0],producer=k[1],requests=groups[k]) for k in sorted(groups)])


def partition(step,actual):
    start,end=step['start_ns'],step['end_ns']
    formed=actual['local_facts']['local_read_complete_ns']
    if not actual['send_start_ns']<=start<=end==actual['data_ready_ns'] or formed>end:
        raise ValueError('Fence interval disagrees with actual transfer callbacks')
    forming=max(0,min(end,formed)-start)
    return dict(start_ns=start,end_ns=end,submit_ns=actual['send_start_ns'],all_requests_formed_ns=formed,
        before_all_requests_formed_ns=forming,after_all_requests_formed_ns=end-start-forming,
        transaction_done_ns=actual['transaction_done_ns'])


def formation_states(rows,steps,actual,capture):
    """Integrate only the recorded interval before all requests were formed."""
    selected=set(capture['policy']['rank_ids']);last={};count=0
    totals={tid:Counter() for tid in steps};uncovered=[]
    for tid,step in steps.items():
        if actual[tid]['src_rank'] not in selected:uncovered.append(tid)
    for row in rows:
        count+=1
        if row['sequence']!=count:raise ValueError('Native queue sequence mismatch')
        rank=row['rank_id'];old=last.get(rank)
        if rank not in selected or row['store_capacity']!=32 or row['request_pool_capacity']!=256:
            raise ValueError('Native queue selection or capacities differ')
        if old:
            if old['time_ns']>row['time_ns']:raise ValueError('Native queue time moved backwards')
            for tid,step in steps.items():
                a=actual[tid]
                if a['src_rank']!=rank:continue
                formed=a['local_facts']['local_read_complete_ns']
                dt=min(formed,row['time_ns'])-max(step['start_ns'],old['time_ns'])
                if dt<=0:continue
                if not any(q['identity']['transfer_id']==tid for q in old['queue']):
                    raise ValueError('Unformed data request missing from actual queue')
                reason=('worker_full' if old['store_inflight']==32 else
                        'pool_full' if len(old['pending'])==256 else 'capacity_available')
                totals[tid][reason]+=dt
        last[rank]=row
    if count!=capture['records'] or set(last)!=selected or any(r['event']!='final' for r in last.values()):
        raise ValueError('Incomplete archived queue stream')
    for tid,step in steps.items():
        if tid not in uncovered and sum(totals[tid].values())!=partition(step,actual[tid])['before_all_requests_formed_ns']:
            raise ValueError('Queue states do not cover the entire formation wait')
    return totals,uncovered


def describe(review_path,archive_path):
    review=read(review_path);archive=Archive(archive_path)
    if archive.index['entries']['run/manifest.json']['sha256']!=review['manifest_sha256']:
        raise ValueError('Review/archive execution binding differs')
    steps={n['transfer_id']:n for c in review['contributions'] for n in c['chain']['all_latest_dependency_dag']
           if n.get('kind')=='data_ready' and n['duration_ns']>0}
    actual={t['transfer_id']:t for t in archive.rows('transfers') if t['transfer_id'] in steps}
    if set(actual)!=set(steps):raise ValueError('Missing actual data-fence transfers')
    name='run/raw/execution/records/native_queue.jsonl.gz'
    _,data=next(archive.members([name]))
    states,uncovered=formation_states(zipped_rows(data),steps,actual,review['native_capture'])
    del data
    _,data=next(archive.members(['run/raw/execution/records/native_requests.jsonl.gz']))
    order=request_order(zipped_rows(data));del data
    rows=[]
    for c in review['contributions']:
        details=[];total=Counter();state_total=Counter();path=set(c['chain']['representative_path'])
        for n in c['chain']['all_latest_dependency_dag']:
            if n.get('kind')!='data_ready' or n['duration_ns']==0:continue
            value=partition(n,actual[n['transfer_id']]);on_path=n['node_id'] in path
            details.append(dict(node_id=n['node_id'],transfer_id=n['transfer_id'],on_representative_path=on_path,
                formation_queue_state_ns=dict(states[n['transfer_id']]),**value))
            if on_path:
                for field in ('before_all_requests_formed_ns','after_all_requests_formed_ns'):total[field]+=value[field]
                state_total.update(states[n['transfer_id']])
        if sum(total.values())!=c['chain']['representative_components_ns'].get('data_ready',0):
            raise ValueError('Fence partitions do not match the original elapsed path')
        rows.append(dict(transfer_id=c['transfer_id'],layer_id=c['layer_id'],src_rank=c['src_rank'],dst_rank=c['dst_rank'],
            reasons=c['reasons'],totals_ns=dict(total),formation_queue_state_ns=dict(state_total),all_tied_data_fences=details))
    return dict(review_sha256=digest(review_path),archive_sha256=archive.index['archive_sha256'],contributions=rows,
        missing_queue_sources=uncovered,per_producer_request_order=order)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('baseline-review','control-review','baseline-archive','control-archive','output'):
        p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if not a.worker:
        from moe_ub_cosim.host_probe import probe
        result=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],str(a.output)+'-resources',**host())
        if result['status']!='completed':raise SystemExit(result['status'])
    else:
        if a.output.exists():raise ValueError('Wait review output must be new')
        before=describe(a.baseline_review,a.baseline_archive);after=describe(a.control_review,a.control_archive)
        if before['per_producer_request_order']!=after['per_producer_request_order']:
            raise ValueError('Per-producer actual request order changed')
        save(a.output,dict(schema_version='issue-screening-organization-wait-partition/1',passed=True,
            baseline=before,control=after,per_producer_request_order_identical=True,
            scope='Recorded time partition at local_read_complete; formation waits can depend on network responses, and post-formation time includes sender/network/receiver work. No independent switch-delay or removable-loss attribution.',
            program_sha256=digest(__file__),simulation_executed=False))


if __name__=='__main__':main()
