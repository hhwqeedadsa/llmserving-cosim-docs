"""Observed packet/CNP/work ancestry; no truth, platform decoder or new network."""
import argparse
from collections import Counter,defaultdict,deque
from pathlib import Path
from experiments.feedback_ancestry.stage import load,save
from experiments.local_fault_baselines.graph_summary import case_summary


def groups(marks,period):
    """Periodic NP batches; a mark on a pending boundary belongs to next batch."""
    answer=[];due=None;pending=[]
    for mark in marks:
        now=mark['time_ns']
        while due is not None and due<=now:
            if pending:
                answer.append(dict(time_ns=due,members=pending,kind='periodic'));pending=[];due+=period
            else:due=None
        if due is None:
            answer.append(dict(time_ns=now,members=[mark],kind='immediate'));due=now+period
        else:pending.append(mark)
    if pending:answer.append(dict(time_ns=due,members=pending,kind='periodic'))
    return answer


def analyze(observed,planned,r):
    summary=case_summary(observed,planned,r);t=r['persistent_transport'];reference=t['dcqcn_reference'];cfg=reference['config']
    cap=r['packet_capture'];events=[dict(zip(cap['columns'],row)) for row in cap['events']]
    by_uid=defaultdict(list);snapshots={};dataqueues=defaultdict(deque);ports={(p['node'],p['port']):p for p in r['ports']}
    for e in events:
        by_uid[e['packet_uid']].append(e)
        if e['ip_protocol']!=17 or e['node']<r['gpu_count']:continue
        key=e['node'],e['port']
        if e['event']==3:dataqueues[key].append(e)
        elif e['event']==4:
            p=dataqueues[key].popleft();assert p['packet_uid']==e['packet_uid'] and p['sequence']==e['sequence']
            remaining=Counter()
            for p in dataqueues[key]:remaining[str(p['message_id'])]+=p['model_bytes']
            snapshots[e['packet_uid'],*key]=dict(remaining_by_stream_model_bytes=dict(remaining),
                remaining_data_model_bytes=sum(remaining.values()),captured_total_before_dequeue_bytes=e['egress_queue_bytes'])
    assert not any(dataqueues.values())
    streams={s['id']:s for s in t['streams']};remote={w['id']:w for w in t['work']};labels={w['work']:w for w in summary['writes']}
    sourcecols=t['source_packet_columns'];source={x['packet_uid']:x for x in (dict(zip(sourcecols,row)) for row in t['source_packets'])}
    marks=defaultdict(list);marked={}
    for uid,packet in source.items():
        s=streams[packet['stream']];lineage=by_uid[uid]
        destination=[e for e in lineage if e['event']==1 and e['node']==s['dst']]
        if not destination or destination[0]['ecn']!=3:continue
        assert len(destination)==1
        sites=[];arrivals={}
        for e in lineage:
            if e['event']==1:arrivals[e['node']]=e
            if e['event']==0 and e['node']>=r['gpu_count'] and e['ecn']==3 and arrivals[e['node']]['ecn']!=3:
                snap=snapshots[uid,e['node'],e['port']];p=ports[e['node'],e['port']]
                sites.append(dict(node=e['node'],port=e['port'],time_ns=e['time_ns'],previous_ecn=arrivals[e['node']]['ecn'],
                    kmin_bytes=p['ecn_kmin_bytes'],kmax_bytes=p['ecn_kmax_bytes'],**snap))
        assert len(sites)==1
        pieces=[];seq=packet['sequence'];end=seq+packet['payload_bytes']
        for wid,w in remote.items():
            if w['stream']!=s['id']:continue
            n=max(0,min(end,w['end'])-max(seq,w['begin']))
            if n:pieces.append(dict(work=wid,bytes=n,begin=max(seq,w['begin']),end=min(end,w['end']),
                name=labels[wid]['name'],graph_id=labels[wid]['graph_id'],ready_ns=w['ready_ns'],published_ns=w['published_ns'],
                accepted_ns=w['accepted_ns'],acked_ns=w['acked_ns']))
        assert sum(x['bytes'] for x in pieces)==packet['payload_bytes']
        item=dict(packet_uid=uid,stream=s['id'],sequence=seq,end=end,payload_bytes=packet['payload_bytes'],
            source_tx_ns=packet['time_ns'],np_rx_ns=destination[0]['time_ns'],first_ce=sites[0],work_intersections=pieces)
        marked[uid]=item;marks[s['id']].append(dict(time_ns=destination[0]['time_ns'],packet_uid=uid))
    generated=[e for e in reference['events'] if e['kind']=='cnp_generated'];received={e['packet_uid']:e for e in reference['events'] if e['kind']=='cnp_received'}
    txlog={e['packet_uid']:e for e in reference['events'] if e['kind']=='cnp_tx'};batches={}
    for sid in streams:
        batches[sid]=groups(sorted(marks[sid],key=lambda x:(x['time_ns'],x['packet_uid'])),cfg['np_interval_ns'])
        assert [g['time_ns'] for g in batches[sid]]==[e['time_ns'] for e in generated if e['stream']==sid]
    rates=t['source_rate_capture'];before={x['packet_uid']:x for x in (dict(zip(rates['columns'],row)) for row in rates['samples']) if x['event']==3}
    cursor=Counter();notices=[]
    for e in generated:
        uid=e['packet_uid'];sid=e['stream'];s=streams[sid];batch=batches[sid][cursor[sid]];cursor[sid]+=1
        rx=received[uid];lineage=by_uid[uid];hops=[]
        for tx in [p for p in lineage if p['event']==0]:
            p=ports[tx['node'],tx['port']];peer=[v for v in lineage if v['event']==1 and v['node']==p['peer']]
            assert len(peer)==1
            enter=e['time_ns'] if tx['node']==s['dst'] else next(v['time_ns'] for v in lineage if v['event']==1 and v['node']==tx['node'])
            hops.append(dict(node=tx['node'],port=tx['port'],peer=p['peer'],enter_ns=enter,tx_ns=tx['time_ns'],
                rx_ns=peer[0]['time_ns'],service_ns=round(tx['model_bytes']*8*10**9/p['rate_bps']),wait_ns=tx['time_ns']-enter))
        assert hops[0]['node']==s['dst'] and hops[-1]['peer']==s['src']
        members=[marked[x['packet_uid']] for x in batch['members']]
        current=[w['id'] for w in remote.values() if w['stream']==sid and w['published_ns']<=rx['time_ns']<w['acked_ns']]
        notices.append(dict(packet_uid=uid,stream=sid,np_batch=batch['kind'],generated_ns=e['time_ns'],source_tx_ns=txlog[uid]['time_ns'],
            received_ns=rx['time_ns'],np_members=[x['packet_uid'] for x in batch['members']],hops=hops,
            rate_before_bps=before[uid]['rate_bps'],rate_after_bps=rx['rate_bps'],alpha_before=before[uid]['alpha'],alpha_after=rx['alpha'],
            source_ack_prefix=rx['snd_una'],source_published_bytes=rx['published_bytes'],active_published_works=current,
            all_marked_bytes_acked_at_receive=all(m['end']<=rx['snd_una'] for m in members),
            all_marked_works_acked_at_receive=all(x['acked_ns']<=rx['time_ns'] for m in members for x in m['work_intersections']),
            all_marked_works_accepted_at_receive=all(x['accepted_ns']<=rx['time_ns'] for m in members for x in m['work_intersections']),
            members_with_other_streams_queued=sum(any(k!=str(sid) and b for k,b in m['first_ce']['remaining_by_stream_model_bytes'].items()) for m in members)))
    critical={}
    for wid in (99,108,132):
        w=remote[wid];prior=[n for n in notices if n['stream']==w['stream'] and n['received_ns']<=w['ready_ns']]
        samples=[dict(zip(rates['columns'],row)) for row in rates['samples'] if row[rates['columns'].index('event')]==0]
        first=next(x for x in samples if x['stream']==w['stream'] and w['begin']<=x['sequence']<w['end'])
        critical[str(wid)]=dict(work=wid,name=labels[wid]['name'],stream=w['stream'],ready_ns=w['ready_ns'],first_tx_ns=first['time_ns'],
            first_rate_bps=first['rate_bps'],prior_cnp_uids=[n['packet_uid'] for n in prior],last_prior_cnp=prior[-1] if prior else None,
            in_final_declared_frontier=any(x['work']==wid for x in summary['windows'][-1]['frontier']['writes']))
    stats=dict(marked_packets=len(marked),notices=len(notices),immediate=sum(n['np_batch']=='immediate' for n in notices),
        periodic=sum(n['np_batch']=='periodic' for n in notices),all_marked_bytes_acked=sum(n['all_marked_bytes_acked_at_receive'] for n in notices),
        all_marked_works_acked=sum(n['all_marked_works_acked_at_receive'] for n in notices),
        source_no_outstanding_bytes=sum(n['source_ack_prefix']==n['source_published_bytes'] for n in notices),
        marked_packets_with_other_streams_queued=sum(any(k!=str(m['stream']) and b for k,b in m['first_ce']['remaining_by_stream_model_bytes'].items()) for m in marked.values()))
    return dict(application_completion_ns=summary['group_completion_ns'],statistics=stats,marked_packets=list(marked.values()),notices=notices,critical_works=critical,
        final_frontier=summary['windows'][-1]['frontier']['writes'])


def main():
    p=argparse.ArgumentParser()
    for k in ('inputs','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();cases={}
    for name in ('healthy','live-delayed','live-instant'):
        cases[name]=analyze(a.inputs/'inputs/observed',a.inputs/'planned'/name,load(a.inputs/'network'/name/'result.json'))
    save(a.output,dict(schema_version='feedback-ancestry/1',cases=cases,
        scope='three immutable legacy application windows; observable packet ancestry, not independent recoverable gain or calibrated hardware',
        warnings=['all marks in a periodic batch retained','first CE only: later CE hops do not prove re-marking','data queue remaining after dequeue is ECN context, not causal removal benefit','old-work feedback remains valid QP feedback; not classified as stale or safely discardable']))


if __name__=='__main__':main()
