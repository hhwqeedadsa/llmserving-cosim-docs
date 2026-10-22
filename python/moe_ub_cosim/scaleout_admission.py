"""Closed admission config and independent evaluation of resource transitions."""
from collections import defaultdict

from .scaleout_topology import fields, integer, require


def validate_options(protocol):
    if 'admission' in protocol:
        a=protocol['admission']
        fields(a,'policy max_active_per_source decision_ns'+''.join(' '+k for k in
               ('max_active_bytes_per_source','per_destination') if k in a),'software admission')
        require(a['policy'] in ('passthrough','fifo','destination_round_robin'),'unknown admission policy')
        integer(a['max_active_per_source'],'software active limit',0,1024)
        integer(a['decision_ns'],'source-local decision cost',0,1000000)
        integer(a.get('max_active_bytes_per_source',0),'software active logical byte limit',0,1073741824)
        require(a['policy']!='passthrough' or not a.get('max_active_bytes_per_source',0),'passthrough cannot enforce a byte cap')
        require((a['max_active_per_source']==0 and a['decision_ns']==0) if a['policy']=='passthrough'
                else a['max_active_per_source']>0, 'passthrough must be uncapped/zero-cost; controlled policies need positive limit')
        if 'per_destination' in a:
            p=a['per_destination'];require(isinstance(p,dict),'per_destination must be an object')
            fields(p,'max_active max_bytes','per-destination share')
            require(a['policy']=='destination_round_robin','per-destination limits require destination round-robin')
            integer(p['max_active'],'per-destination active share',1,a['max_active_per_source']-1)
            integer(p['max_bytes'],'per-destination byte share',1,a.get('max_active_bytes_per_source',0)-1)
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
    byte_limit=final.get('max_active_bytes_per_source',0)
    peer_limit=final.get('per_destination')
    def peer_fits(owners,mid):
        if not peer_limit:return True
        flow=flows[mid];same=[flows[i] for i in owners if flows[i]['dst']==flow['dst']]
        return len(same)<peer_limit['max_active'] and sum(f['bytes'] for f in same)+flow['bytes']<=peer_limit['max_bytes']
    def check_peers(owners):
        if peer_limit:
            by_peer=defaultdict(list)
            for mid in owners:by_peer[flows[mid]['dst']].append(mid)
            for ids in by_peer.values():
                require(len(ids)<=peer_limit['max_active'] and sum(flows[i]['bytes'] for i in ids)<=peer_limit['max_bytes'],
                        'per-destination ownership exceeds declared share')
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
            if peer_limit:require(flows[identity]['bytes']<=peer_limit['max_bytes'],'message exceeds per-destination byte share')
        check_peers(s['active_ids'])
        if 'max_active_bytes_per_source' in final:
            for name in ('active','pending'):
                require(all(type(flows[i].get('bytes')) is int and flows[i]['bytes']>0 for i in s[name+'_ids']),
                        'byte ownership requires positive logical payload sizes')
                require(s[name+'_bytes']==sum(flows[i]['bytes'] for i in s[name+'_ids']),'final logical byte ownership mismatch')
            require(not byte_limit or s['active_bytes']<=byte_limit,'software byte capacity exceeded')
        if result['passed']:
            require(not s['active_ids'] and not s['pending_ids'] and not s['decision_pending'] and
                    s['native_send_qps']==0,'successful run retained sender resources')
    summary=dict(available=True,reconstructed=False,policy=final['policy'],software_limit=limit,
        software_byte_limit=byte_limit,
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
    peak=defaultdict(int);peak_bytes=defaultdict(int);omitted_snapshots=0
    order=defaultdict(list);last_peer={}
    def held(src):return sum(flows[i].get('bytes',0) for i in active[src])
    def candidate(src):
        if not order[src] or (limit and len(active[src])>=limit):return None
        def fits(mid):return (not byte_limit or flows[mid]['bytes']<=byte_limit-held(src)) and peer_fits(active[src],mid)
        if final['policy']!='destination_round_robin':return order[src][0] if fits(order[src][0]) else None
        heads={}
        for mid in order[src]:heads.setdefault(flows[mid]['dst'],mid)
        peers=sorted(heads);previous_peer=last_peer.get(src,-1)
        for peer in [p for p in peers if p>previous_peer]+[p for p in peers if p<=previous_peer]:
            if fits(heads[peer]):return heads[peer]
        return None
    for row in capture['events']:
        require(len(row)==8 and all(type(x) is int for x in row),'invalid lifecycle event types')
        t,event,mid,src,count,queued,capacity,native=row
        require(t>=previous and src in source_ids and capacity==limit,'lifecycle clock/source/capacity mismatch')
        previous=t
        if event==6:
            require(mid==-1 and candidate(src) is not None,
                    'decision event without eligible ready work')
        else:
            require(mid in flows and flows[mid]['src']==src,'lifecycle message/source mismatch')
            flow=flows[mid]
            if event==0:
                require(mid not in requested and t==flow['ready_ns'],'duplicate or mistimed admission request')
                requested.add(mid);pending[src].add(mid);order[src].append(mid)
                # Deltas from t=0 retain the full occupying set for capacity>1.
                if active[src]:
                    if len(waits)<256:
                        waits.append(dict(message_id=mid,source=src,time_ns=t,active_owner_ids=sorted(active[src]),
                            capacity_full=bool(limit and len(active[src])>=limit),
                            active_logical_bytes=held(src),
                            insufficient_logical_bytes=bool(byte_limit and flow['bytes']>byte_limit-held(src))))
                    else:omitted_snapshots+=1
            elif event==1:
                require(mid in pending[src] and mid not in granted and t==flow['admission_granted_ns'],
                        'grant without one prior ready request')
                require(mid==candidate(src),'grant violates queue policy or logical byte eligibility')
                order[src].remove(mid)
                if final['policy']=='destination_round_robin':last_peer[src]=flow['dst']
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
        require(not byte_limit or held(src)<=byte_limit,'software active byte capacity exceeded')
        check_peers(active[src])
        peak[src]=max(peak[src],count)
        peak_bytes[src]=max(peak_bytes[src],held(src))
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
        peak_active_logical_bytes={str(src):n for src,n in peak_bytes.items()},
        omitted_owner_snapshots=omitted_snapshots,
        owner_snapshot_scope='First 256 nonempty request snapshots; full grant/release ledger remains available.')
    return summary
