"""Offline RTT-spec replay from source-local packet/feedback observations."""
from .scaleout_topology import require

def replay(result):
    r=result['recovery'];c=r['config'];a=c['rtt_estimator'];flows={f['id']:f for f in result['messages']}
    require(r['rtt_columns']==['time_ns','event','message_id','start_or_ack','end_bytes'],'unknown RTT event columns')
    states={};progress_rtos={};clock=-1
    def ambiguous(s):
        s['barrier']=max(s['barrier'],s['high'])
        if s['probe_time']>=0:s['discarded']+=1;s['probe_time']=-1
    for row in r['rtt_events']:
        require(len(row)==5 and all(type(v) is int for v in row),'invalid RTT event row')
        now,kind,mid,start,end=row
        require(clock<=now<=result['finish_ns'] and mid in flows,'invalid RTT event clock/identity')
        clock=now
        if mid not in states:
            require(kind==0,'RTT state did not start with an actual source packet')
            states[mid]=dict(ack=0,high=0,barrier=0,probe_end=0,probe_time=-1,samples=0,discarded=0,
                srtt=0,var=0,base=c['initial_rto_ns'],payload=0,timeouts=0)
        s=states[mid]
        if kind==0:
            require(0<=start<end<=flows[mid]['bytes'] and start<=s['high'],'invalid source RTT sequence')
            if start<s['high']:ambiguous(s)
            elif s['probe_time']<0 and s['ack']>=s['barrier']:s.update(probe_end=end,probe_time=now)
            s['high']=max(s['high'],end);s['payload']+=end-start
        elif kind in (1,2):
            require(end==0 and 0<=start<=s['high'],'RTT feedback beyond actually emitted bytes')
            if kind==2:ambiguous(s)
            if start>s['ack']:
                if s['probe_time']>=0 and start>=s['probe_end']:
                    sample=now-s['probe_time'];require(sample>0,'invalid RTT sample')
                    if not s['samples']:s['srtt']=sample;s['var']=(sample+1)//2
                    else:
                        variance=(3*s['var']+abs(s['srtt']-sample)+3)//4
                        s['srtt']=(7*s['srtt']+sample+7)//8;s['var']=variance
                    s['base']=min(c['max_rto_ns'],max(a['min_rto_ns'],s['srtt']+max(a['granularity_ns'],4*s['var'])))
                    s['samples']+=1;s['probe_time']=-1
                s['ack']=start;progress_rtos[mid,now,start]=s['base']
        else:
            require(kind==3 and start==end==0,'unknown RTT protocol event')
            s['timeouts']+=1;ambiguous(s)
    require(set(states)=={s['id'] for s in r['states']},'RTT source history coverage differs from recovery states')
    for final in r['states']:
        mid=final['id'];s=states[mid];f=flows[mid]
        expected=dict(samples=s['samples'],discarded_probes=s['discarded'],srtt_ns=s['srtt'],rttvar_ns=s['var'],
            base_rto_ns=s['base'],ambiguity_until_bytes=s['barrier'],probe_end_bytes=s['probe_end'],probe_sent_ns=s['probe_time'])
        require(final['rtt']==expected,'RTT estimator final state does not match source-local replay')
        require(s['high']==final['highest_sent_bytes'] and s['ack']==final['acknowledged_bytes'] and
                s['payload']==f['source_data_payload_bytes'] and s['timeouts']==final['timeout_expiries'],
                'RTT history omitted source transmissions, feedback progress or timeout decisions')
    return progress_rtos,dict(reconstructed=True,samples=sum(s['samples'] for s in states.values()),
        discarded_probes=sum(s['discarded'] for s in states.values()),scope='source-local conservative Karn replay; not hardware RTT validation')
