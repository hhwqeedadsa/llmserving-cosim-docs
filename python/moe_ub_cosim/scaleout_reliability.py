"""Conditional bounds for the declared recovery protocol; not an SLO model."""
from .scaleout_topology import require

def silence_budget_ns(reset_rto_ns,max_rto_ns,max_retries):
    """No new cumulative ACK: K retries, then fail on expiry K+1."""
    require(type(reset_rto_ns) is int and type(max_rto_ns) is int and type(max_retries) is int and
            1<=reset_rto_ns<=max_rto_ns<=1000000000 and 1<=max_retries<=16,'invalid silence-bound inputs')
    return sum(min(reset_rto_ns*(2**i),max_rto_ns) for i in range(max_retries+1))

def silence_certificates(result):
    r=result['recovery'];require(r['complete'],'silence certificates need complete source event history')
    epochs={}
    for row in r['events']:
        t,event,_,mid,_,_,count,rto,deadline=row
        if event in (0,1):
            if deadline>=0:
                require(count==0,'timer epoch did not reset consecutive retries')
                epochs[mid]=(t,rto)
            else:epochs.pop(mid,None)
        elif event==3:epochs.pop(mid,None)
    certificates=[]
    for s in r['states']:
        if s['state']=='complete' or s['id'] not in epochs:continue
        origin,rto=epochs[s['id']];budget=silence_budget_ns(rto,r['config']['max_rto_ns'],r['config']['max_retries'])
        deadline=origin+budget;failed=s['state']=='retry_exhausted'
        require(result['finish_ns']==deadline if failed else result['finish_ns']<=deadline,
                'source silence violated finite failure bound')
        certificates.append(dict(id=s['id'],origin_ns=origin,reset_rto_ns=rto,no_progress_budget_ns=budget,
            failure_deadline_ns=deadline,outcome='failed_at_bound' if failed else 'execution_stopped_before_or_at_bound',
            assumptions='source timer armed; no subsequent cumulative ACK progress; simulator processes timer events'))
    return certificates

def credit_obstructions(result):
    """Exact snapshot eligibility inequalities; no claim of permanent blocking."""
    a=result.get('admission')
    if not a:return dict(available=False)
    flows={f['id']:f for f in result['messages']};count_limit=a['max_active_per_source']
    byte_limit=a.get('max_active_bytes_per_source',0);sources=[]
    for s in a['sources']:
        active=[flows[mid] for mid in s['active_ids']];pending=[flows[mid] for mid in s['pending_ids']]
        require(all(f['src']==s['src'] for f in active+pending),'credit certificate source mismatch')
        held=sum(f['bytes'] for f in active)
        require((not count_limit or len(active)<=count_limit) and (not byte_limit or held<=byte_limit),
                'credit certificate ownership exceeds declared capacity')
        count_available=not count_limit or len(active)<count_limit
        remaining=byte_limit-held if byte_limit else None
        fits=lambda f:remaining is None or f['bytes']<=remaining
        if a['policy']=='destination_round_robin':
            heads={}
            for f in pending:heads.setdefault(f['dst'],f)
            candidates=list(heads.values())
        else:candidates=pending[:1]
        byte_eligible=[f['id'] for f in candidates if fits(f)]
        eligible=bool(count_available and byte_eligible)
        if not pending:reason='no_pending_work'
        elif not count_available:reason='source_message_slots_full'
        elif not byte_eligible:reason='fifo_head_bytes_do_not_fit' if a['policy']=='fifo' else 'no_peer_head_bytes_fit'
        elif s['decision_pending']:reason='eligible_waiting_local_decision'
        else:reason='eligible_at_execution_stop'
        sources.append(dict(source=s['src'],active_count=len(active),count_limit=count_limit,
            held_logical_bytes=held,byte_limit=byte_limit,remaining_bytes=remaining,
            active_owner_ids=s['active_ids'],data_ready_but_unconfirmed_owner_ids=[f['id'] for f in active
                if f['receive_ready_ns'] is not None and not f['ack_callbacks']],
            pending_ids=s['pending_ids'],candidate_head_ids=[f['id'] for f in candidates],
            byte_eligible_head_ids=byte_eligible,policy_eligible_now=eligible,reason=reason,
            decision_pending=s['decision_pending']))
    return dict(available=True,sources=sources,
        scope='offline exact end-state eligibility inequalities; no queue-delay attribution or permanent-blocking claim')
