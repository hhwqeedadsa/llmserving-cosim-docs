"""Enumerate a finite recovery model, with per-QP order and explicit assumptions.

No simulator internals, GPU execution, fault-rate estimates, or real exploit.
"""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def orders(events, edges):
    """Every topological order, once; event names are scheduling choices only."""
    parents={e:set() for e in events}
    for a,b in edges:parents[b].add(a)
    def visit(prefix,remaining):
        if not remaining:
            yield prefix;return
        for event in sorted(remaining):
            if parents[event].isdisjoint(remaining):
                yield from visit(prefix+[event],remaining-{event})
    yield from visit([],set(events))


def replay(trace, *, generation=1, slot=0, tagged=True, revoke=False, fence=False):
    """Target applies writes even when a later consumer rejects their notices.

    Validation and DMA are split ONLY in the revocation thought experiment.
    fence means an explicit guarantee of prior-DMA retirement, not a key change.
    """
    memory={0:None,1:None};ready=False;reused=False;observed=None;accepted=None
    revoked=False;fenced=False;consumed=False;failures=[]
    for event in trace:
        if event=='old_validate':accepted=not revoked
        elif event=='revoke':revoked=True
        elif event=='fence':fenced=True
        elif event=='reuse':memory[slot]=None;ready=False;reused=True
        elif event=='middle_data':memory[1]=1
        elif event=='new_data':memory[slot]=generation
        elif event in ('old_data','old_dma'):
            if event=='old_data' or accepted:
                memory[0]=0
                if consumed and slot==0:failures.append('old_write_after_consume')
        elif event=='old_notice':
            if reused and not tagged:ready=True
        elif event=='new_notice':ready=True
        elif event=='consume':
            assert reused and ready and not consumed
            if fence:assert revoked and fenced
            consumed=True;observed=memory[slot]
            if observed!=generation:failures.append('wrong_generation_at_consume')
    return dict(failures=sorted(set(failures)),observed=observed,consumed=consumed)


def case(name, *, generation=1, slot=0, tagged=True, wait=False, ordered=False, revoke=False, fence=False):
    chain=['timeout']
    if generation==2:
        # One recovery uses slot 1 and is consumed, then the next call wraps to
        # slot 0 while epoch 0 remains outstanding. No second fault is needed.
        chain+=['reuse_one','middle_data','middle_notice','middle_consume']
    if revoke:chain+=['revoke']
    if fence:chain+=['fence']
    chain+=['reuse','new_data','new_notice']
    old=['old_validate','old_dma','old_ack'] if revoke else ['old_data','old_notice','old_ack']
    events=chain+old;edges=list(zip(chain,chain[1:]))+list(zip(old,old[1:]))
    edges.append(('timeout','old_ack')) # timeout observation contains no completion
    if wait:edges.append(('old_ack','reuse'))
    if ordered:edges.append(('old_notice','new_data')) # same-QP old/new ordering control
    if fence:edges.append(('old_dma','fence')) # explicit retirement semantics
    summary=Counter();first=None;seen_traces=set()
    for schedule in orders(events,edges):
        summary['network_orders']+=1
        assert len(schedule)==len(set(schedule))==len(events)
        position={e:i for i,e in enumerate(schedule)}
        assert all(position[a]<position[b] for a,b in edges)
        # The consumer runs at any ready prefix; old notices cannot make a
        # tagged consumer ready. Consume is evaluated both before and after
        # potentially late old writes, rather than assuming a fixed instant.
        for cut in range(position['reuse']+1,len(schedule)+1):
            prefix=schedule[:cut]
            available='new_notice' in prefix or (not tagged and 'old_notice' in prefix and
                position['old_notice']>position['reuse'])
            if not available:continue
            trace=prefix+['consume']+schedule[cut:]
            identity=tuple(trace);assert identity not in seen_traces;seen_traces.add(identity)
            result=replay(trace,generation=generation,slot=slot,tagged=tagged,revoke=revoke,fence=fence)
            assert result['consumed'];summary['consumer_interleavings']+=1
            if result['failures']:
                summary['unsafe_interleavings']+=1
                summary.update(result['failures'])
                if first is None:first=dict(trace=trace,**result)
    assert summary['network_orders'] and summary['consumer_interleavings']
    return dict(name=name,parameters=dict(generation=generation,slot=slot,tagged=tagged,wait=wait,ordered=ordered,revoke=revoke,fence=fence),
        events=events,edges=edges,counts=dict(summary),counterexample=first)


def blocks(request, pending, hostile, retired=False, revoked_and_retired=False):
    """Conservative admission uses possible writes, never their future order.

    A retired finite WQE set is not authority revocation against a live peer.
    Physical overlap is checked independent of key labels.
    """
    if revoked_and_retired or (retired and not hostile):return []
    result=[]
    for p in pending:
        if request['owner']!=p['owner']:continue
        low,high=p['authority'] if hostile else p['range']
        if request['range'][0]<high and low<request['range'][1]:result.append('possible_data_write')
        domains=p['authorized_signals'] if hostile else p['signals']
        if request['signal'] in domains:result.append('possible_signal_update')
    return sorted(set(result))


def certificate():
    pending=[dict(owner=4,range=[0,128],authority=[0,4096],signals=[0],authorized_signals=[0,1])]
    requests=[dict(id='same_bytes',owner=4,range=[0,128],signal=0),
        dict(id='disjoint_expert_bytes',owner=4,range=[128,256],signal=1),
        dict(id='disjoint_bytes_shared_signal',owner=4,range=[256,384],signal=0),
        dict(id='other_gpu',owner=5,range=[0,128],signal=0)]
    rows=[]
    for request in requests:
        row=dict(request=request)
        for name,hostile,retired,revoked in [('cooperative',False,False,False),('live_key_holder',True,False,False),
                ('retired_wqes_cooperative',False,True,False),('retired_wqes_live_key_holder',True,True,False),
                ('authority_revoked_and_dma_retired',True,True,True)]:
            row[name]=blocks(request,pending,hostile,retired,revoked)
        rows.append(row)
    assert rows[0]['cooperative'] and rows[0]['live_key_holder']
    assert rows[1]['cooperative']==[] and rows[1]['live_key_holder']==['possible_data_write','possible_signal_update']
    assert rows[2]['cooperative']==['possible_signal_update']
    assert all(not value for key,value in rows[3].items() if key!='request')
    assert all(not r['retired_wqes_cooperative'] and not r['authority_revoked_and_dma_retired'] for r in rows)
    assert rows[1]['retired_wqes_live_key_holder']==rows[1]['live_key_holder']
    # Inclusion: adversarial authority contains declared WQEs. Revocation with
    # retirement permits at least as much as retirement of a cooperative set.
    for r in rows:assert set(r['cooperative'])<=set(r['live_key_holder'])
    return dict(pending=pending,rows=rows,scope='declared interval/domain illustration; no production placement or timing measurement')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    out=a.output.resolve();out.mkdir(exist_ok=False,parents=True)
    cases=[case('clear_and_reuse',tagged=False),case('notice_generation_only'),
        case('two_buffers_one_recovery',slot=1),case('two_buffers_wrap',generation=2),
        case('global_old_queue_retirement',wait=True),case('selective_same_range_wait',wait=True),
        case('selective_disjoint_range',slot=1),case('same_qp_order_and_generation',ordered=True),
        case('key_change_without_dma_retirement',revoke=True),case('key_change_with_dma_retirement',revoke=True,fence=True)]
    unsafe={'clear_and_reuse','notice_generation_only','two_buffers_wrap','key_change_without_dma_retirement'}
    for c in cases:
        assert bool(c['counterexample'])==(c['name'] in unsafe),c['name']
        if c['counterexample']:
            # Verify exported witness independently against declared graph.
            t=c['counterexample']['trace'];pos={e:t.index(e) for e in c['events']}
            assert len(t)==len(c['events'])+1 and all(pos[x]<pos[y] for x,y in c['edges'])
            assert replay(t,**{k:v for k,v in c['parameters'].items() if k in ('generation','slot','tagged','revoke','fence')})['failures']
    # Hand-constructed sanity witness: old payload overwrites NEW payload, then
    # the correct NEW-generation notice arrives. Rejecting old notices alone
    # cannot protect the payload. Strong old/new per-QP order forbids this trace.
    witness=['timeout','reuse','new_data','old_data','new_notice','consume','old_notice','old_ack']
    assert replay(witness)['failures']==['wrong_generation_at_consume']
    assert not replay([e for e in witness if not e.startswith('old_')])['failures']
    result=dict(schema_version='moe-recovery-safety-screen/1',passed=True,cases=cases,
        isolation=certificate(),scope='finite conditional recovery model; not DeepEP execution, an exploit, performance or a novel algorithm proof',
        same_qp_and_global_retirement_controls_safe=True,production_vulnerability_demonstrated=False,
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(passed=True,cases=[dict(name=c['name'],**c['counts']) for c in cases])))
if __name__=='__main__':main()
