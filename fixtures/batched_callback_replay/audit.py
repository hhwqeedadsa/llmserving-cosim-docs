"""Saved batches only: validate suppression then unfold each original boundary."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from moe_ub_cosim.modern_native import ModernNativeController
from moe_ub_cosim.modern_native_wakeups import conditions,satisfied
from fixtures.batched_callback_replay.stage import load,save,sha
from fixtures.batched_callback_replay.prepare import CASES,FILES


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def pairs(path):
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows)%2==0
    output=[]
    for i in range(0,len(rows),2):
        a,b=rows[i:i+2]
        assert (a['direction'],b['direction'])==('native','frontend')
        event,command=a['message'],b['message']
        assert event['decision_id']==command['decision_id']==i//2 and event['now_ns']==command['now_ns']
        output.append((event,command))
    return output


def candidate(controller,transcript,frontier=None):
    """No future callback informs selection; original commands are audit oracles.

    End-of-input is not a production wakeup. Its pending suffix is unfolded only
    for reconstruction, with every such step explicitly marked artificial.
    """
    frontier=frontier or (lambda c:conditions(c,goal='submissions'))
    groups=[];seen=set();pending=[];flushes=[];steps=[];previous_drained=False
    divergence=None;maximum_groups=maximum_terms=maximum_batches=maximum_bytes=0

    def unfold(batch,physical):
        for index,(event,command) in enumerate(batch):
            before=len(controller.log);nodes=controller.advance(deepcopy(event))
            finished=not nodes and event['drained'] and controller.runtime.snapshot()['all_events_fired']
            if nodes!=command['submissions'] or finished!=command['finish']:
                return dict(kind='changed_submission_or_finish',original_decision=event['decision_id'])
            if index<len(batch)-1 and (nodes or finished):
                return dict(kind='intermediate_step_needed_immediate_action',original_decision=event['decision_id'])
            steps.append(dict(original_decision=event['decision_id'],now_ns=event['now_ns'],
                callback_count=len(event['events']),callback_sha256=digest(event['events']),
                native_submissions=[node['id'] for node in nodes],submission_sha256=digest(nodes),finish=finished,
                new_modern_events=deepcopy(controller.log[before:]),
                physical_flush=physical,artificial_tail_unfold=physical is None))
        return None

    for event,command in transcript:
        pending.append((event,command))
        seen.update((row['operation'],row['event']) for row in event['events'] if 'operation' in row)
        reason='first' if not flushes else 'alarm' if any('alarm' in row for row in event['events']) else (
            'new_drain' if event['drained'] and not previous_drained else 'submission_frontier' if satisfied(groups,seen) else None)
        previous_drained=event['drained']
        maximum_batches=max(maximum_batches,len(pending))
        # Payload excludes output/audit truth and original COMMIT; only actual
        # callback batch metadata would be transmitted by a future interface.
        payload=[dict(now_ns=e['now_ns'],events=e['events'],drained=e['drained']) for e,_ in pending]
        maximum_bytes=max(maximum_bytes,len(json.dumps(payload,separators=(',',':')).encode()))
        if reason is None:
            if command['submissions'] or command['finish']:
                divergence=dict(kind='suppressed_required_submission_or_finish',original_decision=event['decision_id'])
                break
            continue
        divergence=unfold(pending,len(flushes))
        if divergence:break
        groups=frontier(controller)
        maximum_groups=max(maximum_groups,len(groups));maximum_terms=max(maximum_terms,sum(map(len,groups)))
        flushes.append(dict(original_decision=event['decision_id'],now_ns=event['now_ns'],reason=reason,
            original_batch_ids=[e['decision_id'] for e,_ in pending],next_conditions=deepcopy(groups)))
        pending=[]
    delivered=controller.snapshot()
    pending_ids=[e['decision_id'] for e,_ in pending]
    # Do not manufacture a condition, a drain transition or a network result.
    if not divergence and pending:divergence=unfold(pending,None)
    reconstructed=controller.snapshot()
    return dict(offline_only=True,network_executed=False,divergence=divergence,
        original_logical_steps=len(transcript),physical_flushes=flushes,physical_flush_count=len(flushes),
        logical_steps=steps,maximum_condition_groups=maximum_groups,maximum_condition_terms=maximum_terms,
        maximum_buffered_batches=maximum_batches,maximum_compact_batch_payload_bytes=maximum_bytes,
        pending_tail_original_decisions=pending_ids,actual_delivered_snapshot=delivered,
        reconstructed_snapshot_after_explicit_tail_unfold=reconstructed,
        original_native_limit_unchanged=256,physical_interface_implemented=False,
        same_ns_native_scheduler_order_verified=False,representative_four_rank_call_complete=False)


def audit(source):
    report=dict(schema_version='moe-batched-callback-offline/1',network_executed=False,cases={})
    for case in CASES:
        directory=source/case;transcript=pairs(directory/'control.jsonl')
        def make():return ModernNativeController(*(load(directory/name) for name in ('topology.json','protocol.json','input.json')))
        reference=make()
        for event,command in transcript:
            nodes=reference.advance(deepcopy(event));assert nodes==command['submissions']
            finished=not nodes and event['drained'] and reference.runtime.snapshot()['all_events_fired']
            assert finished==command['finish']
        expected=load(directory/'modern-execution.json');assert reference.snapshot()==expected
        value=candidate(make(),transcript)
        value['source_sha256']={name:sha(directory/name) for name in FILES}
        value['baseline_full_snapshot_equal']=True
        value['reconstructed_full_snapshot_equal']=value['reconstructed_snapshot_after_explicit_tail_unfold']==expected
        report['cases'][case]=value
    return report


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();save(a.output,audit(a.source))


if __name__=='__main__':main()
