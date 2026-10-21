"""Closed admission config and independent evaluation of resource transitions."""
from collections import defaultdict

from .scaleout_topology import fields, integer, require


def validate_options(protocol):
    if 'admission' in protocol:
        a=protocol['admission']
        fields(a,'policy max_active_per_source decision_ns','software admission')
        require(a['policy'] in ('passthrough','fifo','destination_round_robin'),'unknown admission policy')
        integer(a['max_active_per_source'],'software active limit',0,1024)
        integer(a['decision_ns'],'source-local decision cost',0,1000000)
        require((a['max_active_per_source']==0 and a['decision_ns']==0) if a['policy']=='passthrough'
                else a['max_active_per_source']>0, 'passthrough must be uncapped/zero-cost; controlled policies need positive limit')
    if 'lifecycle_capture' in protocol:
        fields(protocol['lifecycle_capture'],'max_events','lifecycle capture')
        integer(protocol['lifecycle_capture']['max_events'],'lifecycle max_events',1,100000)


def audit(result):
    """Reconstruct all concurrent owners, never select a single ideal blocker.

    With truncated capture, return the explicit final snapshot only. This reader
    is offline truth evaluation and cannot be imported by an online policy.
    """
    if 'admission' not in result:return dict(available=False,reconstructed=False)
    final=result['admission'];capture=result.get('lifecycle_capture')
    sources=final['sources'];limit=final['max_active_per_source']
    require(len({s['src'] for s in sources})==len(sources),'duplicate admission source')
    flows={f['id']:f for f in result['messages']}
    require(len(flows)==len(result['messages']),'duplicate admission message identity')
    for s in sources:
        require(len(s['active_ids'])==len(set(s['active_ids'])) and len(s['pending_ids'])==len(set(s['pending_ids'])) and
                not set(s['active_ids']) & set(s['pending_ids']), 'duplicate/overlapping admission owners')
        require(not limit or len(s['active_ids'])<=limit,'software credit capacity exceeded')
        for identity in s['active_ids']+s['pending_ids']:
            require(identity in flows and flows[identity]['src']==s['src'],'owner belongs to wrong source')
            require(flows[identity].get('transport')!='intra_host_chunked_crossbar','local transfer consumes RDMA quota')
        if result['passed']:
            require(not s['active_ids'] and not s['pending_ids'] and not s['decision_pending'] and
                    s['native_send_qps']==0,'successful run retained sender resources')
    summary=dict(available=True,reconstructed=False,policy=final['policy'],software_limit=limit,
        final_sources=sources,scope='offline_evaluation_only',
        notes='Software quota only; does not infer GPU buffers, hardware QP capacity or a unique causal blocker.')
    if not capture or not capture['complete']:
        summary['reason']='capture_absent' if not capture else 'capture_truncated'
        return summary
    expected=['time_ns','event','message_id','source','active','pending','software_limit','native_send_qps']
    require(capture['columns']==expected and capture['observations']==len(capture['events']) and capture['omitted_events']==0,
            'invalid complete lifecycle capture')
    require(capture['event_codes']==dict(request=0,grant=1,qp_registered=2,data_ready=3,ack_complete=4,
            qp_map_removed_credit_release=5,decision_start=6),'unknown lifecycle event encoding')
    active,pending,qps=defaultdict(set),defaultdict(set),defaultdict(set)
    requested,granted,registered,received,acked,released=set(),set(),set(),set(),set(),set()
    source_ids={s['src'] for s in sources};waits=[];previous=-1
    peak=defaultdict(int);omitted_snapshots=0
    for row in capture['events']:
        require(len(row)==8 and all(type(x) is int for x in row),'invalid lifecycle event types')
        t,event,mid,src,count,queued,capacity,native=row
        require(t>=previous and src in source_ids and capacity==limit,'lifecycle clock/source/capacity mismatch')
        previous=t
        if event==6:
            require(mid==-1 and pending[src] and (not limit or len(active[src])<limit),
                    'decision event without eligible ready work')
        else:
            require(mid in flows and flows[mid]['src']==src,'lifecycle message/source mismatch')
            flow=flows[mid]
            if event==0:
                require(mid not in requested and t==flow['ready_ns'],'duplicate or mistimed admission request')
                requested.add(mid);pending[src].add(mid)
                # Deltas from t=0 retain the full occupying set for capacity>1.
                if active[src]:
                    if len(waits)<256:
                        waits.append(dict(message_id=mid,source=src,time_ns=t,active_owner_ids=sorted(active[src]),
                            capacity_full=bool(limit and len(active[src])>=limit)))
                    else:omitted_snapshots+=1
            elif event==1:
                require(mid in pending[src] and mid not in granted and t==flow['admission_granted_ns'],
                        'grant without one prior ready request')
                pending[src].remove(mid);active[src].add(mid);granted.add(mid)
            elif event==2:
                require(mid in active[src] and mid not in registered and t==flow['qp_registered_ns'],'invalid QP registration')
                registered.add(mid);qps[src].add(mid)
            elif event==3:
                require(mid in qps[src] and mid not in received and t==flow['receive_ready_ns'],'invalid data-ready event')
                received.add(mid)
            elif event==4:
                require(mid in qps[src] and mid in received and mid not in acked and t==flow['ack_ns'],'invalid ACK completion')
                acked.add(mid)
            elif event==5:
                require(mid in active[src] and mid in registered and mid not in qps[src] and mid in acked and mid not in released and
                        t==flow['sender_resource_released_ns'],'premature, duplicate or mismatched sender release')
                active[src].remove(mid);released.add(mid)
            else:require(False,'unknown lifecycle event')
        require(count==len(active[src]) and queued==len(pending[src]) and native==len(qps[src]),
                'resource counts do not match all reconstructed owners')
        require(not limit or count<=limit,'software active capacity exceeded')
        peak[src]=max(peak[src],count)
        if event==4:
            # Pinned RdmaHw::QpComplete invokes the trace, then synchronously
            # erases m_qpMap after Noop application notification. Its stack
            # returns before the next Fabric lifecycle event. The trace's
            # sampled map includes this QP; subsequent traces do not, even if
            # a same-timestamp second-plane ACK precedes ReleaseSender.
            qps[src].remove(mid)
    for s in sources:
        src=s['src']
        require(active[src]==set(s['active_ids']) and pending[src]==set(s['pending_ids']) and len(qps[src])==s['native_send_qps'],
                'final ownership differs from complete lifecycle')
    network_ids={f['id'] for f in flows.values() if f.get('transport')!='intra_host_chunked_crossbar'}
    require(requested==network_ids,'missing lifecycle requests')
    if result['passed']:
        require(requested==released and not any(s['decision_pending'] for s in sources),'successful run did not release admission resources')
    summary.update(reconstructed=True,requests=len(requested),grants=len(granted),releases=len(released),
        peak_active={str(src):n for src,n in peak.items()},request_owner_snapshots=waits,
        omitted_owner_snapshots=omitted_snapshots,
        owner_snapshot_scope='First 256 nonempty request snapshots; full grant/release ledger remains available.')
    return summary
