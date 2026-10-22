"""Independent offline checks for the declared native-RDMA recovery extension."""
from .scaleout_topology import require


def audit(result):
    if 'recovery' not in result:return dict(available=False)
    r=result['recovery'];c=r['config'];states=r['states'];flows={f['id']:f for f in result['messages']}
    require(len({s['id'] for s in states})==len(states),'duplicate recovery state')
    state_by_id={s['id']:s for s in states}
    totals=0;failed=[];adaptive=c.get('rtt_estimator');floor=adaptive['min_rto_ns'] if adaptive else c['initial_rto_ns']
    for s in states:
        require(s['id'] in flows,'recovery state lacks an actual message')
        f=flows[s['id']];ack,high=s['acknowledged_bytes'],s['highest_sent_bytes']
        if 'history' in c:
            require(c['initial_rto_ns']<=s['seed_rto_ns']<=c['max_rto_ns'] and type(s['history_hit']) is bool,
                    'invalid history-seeded initial RTO')
        require(s['source']==f['src'] and 0<=ack<=high<=f['bytes'] and high==f['source_unique_data_payload_bytes'],
                'recovery source sequence/identity mismatch')
        require(s['timeout_expiries']==s['timeout_retransmissions']+(s['state']=='retry_exhausted'),
                'timeout attempts do not match actual retry/exhaustion decisions')
        require(0<=s['retries_since_progress']<=c['max_retries'] and floor<=s['current_rto_ns']<=c['max_rto_ns'],
                'recovery retry/RTO bounds violated')
        if adaptive:
            e=s.get('rtt',{});require(e and floor<=e['base_rto_ns']<=c['max_rto_ns'] and
                0<=e['ambiguity_until_bytes']<=high and 0<=e['probe_end_bytes']<=high and e['samples']>=0 and
                e['discarded_probes']>=0 and e['srtt_ns']>=0 and e['rttvar_ns']>=0,'invalid adaptive RTT state')
        totals+=s['timeout_retransmissions']
        if s['state']=='complete':
            require(ack==f['bytes'] and f['ack_callbacks']==1 and not f['sender_qp_present'] and s['deadline_ns']==-1,
                    'recovery completion without native completion/cleanup')
        elif s['state']=='retry_exhausted':
            failed.append(s['id'])
            require(ack<f['bytes'] and not f['ack_callbacks'] and f['sender_qp_present'] and
                    f['sender_resource_released_ns'] is None and s['deadline_ns']==-1,
                    'exhaustion synthesized successful completion or released resources')
        else:
            require(s['state']=='waiting_ack' and ack<f['bytes'] and f['sender_qp_present'] and
                    (s['deadline_ns']>=0 if high>ack else s['deadline_ns']==-1),
                    'invalid unfinished source recovery state')
    require(bool(failed)==r['failed'],'recovery failure flag disagrees with source states')
    if failed:
        require(len(failed)==1 and not result['passed'] and result.get('failure',{}).get('reason')=='recovery_retry_exhausted' and
                result['failure']['message_id']==failed[0] and result['failure']['time_ns']==result['finish_ns'],
                'retry exhaustion lacks truthful terminal run failure')
    if result['passed']:
        require(not r['failed'] and r['inflight_packets']==0 and not r['finish_waiting_for_drain'] and
                all(s['state']=='complete' for s in states),'successful recovery run retained work/packets')
        network_ids={f['id'] for f in flows.values() if f.get('transport')!='intra_host_chunked_crossbar'}
        require({s['id'] for s in states}==network_ids,'successful recovery omitted source transmission state')
    saved=len(r['events'])+len(r['receiver_events'])+len(r.get('rtt_events',[]))
    if 'history' in c:
        h=r['history'];counts={}
        for entry in h['entries']:counts[entry['source']]=counts.get(entry['source'],0)+1
        require(all(n<=c['history']['max_entries_per_source'] for n in counts.values()),'history exceeds per-source capacity')
        require(len({(e['source'],e['destination_ipv4']) for e in h['entries']})==len(h['entries']),
                'duplicate recovery history key')
        require(all(1<=e['rto_ns']<=c['max_rto_ns'] and 0<=e['evidence_ns']<=result['finish_ns'] for e in h['entries']),
                'history snapshot has invalid RTO/time')
    require(saved<=c['max_events'] and r['observations']==saved+r['omitted_events'] and
            r['complete']==(r['omitted_events']==0),'recovery capture completeness is misleading')
    summary=dict(available=True,failed=r['failed'],failed_ids=failed,source_states=len(states),
        timeout_retransmissions=totals,duplicate_ack_responses=r['duplicate_ack_responses'],
        reconstructed=False,scope='offline protocol audit; no source policy access to receiver/global truth')
    if not r['complete']:
        if adaptive:summary['rtt']=dict(reconstructed=False,reason='capture_truncated')
        return dict(summary,reason='capture_truncated')
    progress_rtos={}
    if adaptive:
        from .scaleout_rtt_audit import replay
        progress_rtos,summary['rtt']=replay(result)
    require(r['columns']==['time_ns','event','source','message_id','acknowledged_bytes','highest_sent_bytes',
            'retries_since_progress','rto_ns','deadline_ns'],'unknown recovery event columns')
    previous={};retries={};completions=set();exhausted=set();clock=-1
    for row in r['events']:
        require(len(row)==9 and all(type(v) is int for v in row),'invalid recovery event types')
        t,event,src,mid,ack,high,count,rto,deadline=row
        require(clock<=t<=result['finish_ns'] and mid in flows and src==flows[mid]['src'] and
                mid not in completions and mid not in exhausted and 0<=ack<=high<=flows[mid]['bytes'],
                'invalid recovery event clock/source/terminal state')
        clock=t;old=previous.get(mid)
        if old:require(ack>=old[0] and high>=old[1],'source progress went backwards')
        if event==0:
            require((old is None or old[-1]==-1) and high>ack and deadline==t+rto,'timer start without new outstanding source bytes')
            if old is None and 'history' in c:
                state=state_by_id[mid]
                require(rto==state['seed_rto_ns'],'first timer does not use independently replayed history seed')
        elif event in (1,3):
            expected_rto=progress_rtos.get((mid,t,ack)) if adaptive else c['initial_rto_ns']
            require(old is not None and t<=old[-1] and ack>old[0] and count==0 and rto==expected_rto and
                    deadline==(t+rto if high>ack else -1),'ACK progress failed to reset bounded timer')
            if event==3:
                require(ack==flows[mid]['bytes'] and flows[mid]['ack_ns']==t,'protocol complete lacks matching native ACK')
                completions.add(mid)
        elif event in (2,4):
            require(old is not None and t==old[-1] and ack==old[0] and high>=old[1],'timeout not driven by source ACK deadline')
            if event==2:
                require(count==old[2]+1 and count<=c['max_retries'] and rto==min(c['max_rto_ns'],2*old[3]) and deadline==t+rto,
                        'timeout retry/backoff violated')
                retries[mid]=retries.get(mid,0)+1
            else:
                require(count==old[2]==c['max_retries'] and deadline==-1,'failure before declared retry limit')
                exhausted.add(mid)
        elif event==6:
            require(old is not None and (old[-1]<0 or t<=old[-1]) and ack==old[0] and (count,rto,deadline)==old[2:],
                    'duplicate feedback postponed or reset recovery timer')
        else:require(False,'unknown recovery event')
        previous[mid]=(ack,high,count,rto,deadline)
    for s in states:
        mid=s['id'];require(mid in previous and retries.get(mid,0)==s['timeout_retransmissions'],'missing recovery event history')
        last=previous[mid]
        require(last[1]<=s['highest_sent_bytes'] and (last[0],*last[2:])==
                (s['acknowledged_bytes'],s['retries_since_progress'],s['current_rto_ns'],s['deadline_ns']),
                'recovery final snapshot differs from event history')
    require(len(r['receiver_events'])==r['duplicate_ack_responses'],'duplicate ACK response count differs from complete capture')
    for row in r['receiver_events']:
        require(len(row)==8 and all(type(v) is int for v in row) and 0<=row[0]<=result['finish_ns'] and
                row[1]>=0 and 0<=row[7]<row[6], 'invalid receiver-local duplicate ACK event')
    summary.update(reconstructed=True,completed=len(completions),exhausted=len(exhausted))
    from .scaleout_reliability import silence_certificates
    summary['silence_bounds']=silence_certificates(result)
    return summary
