"""Reconstruct only recorded frontend progress after a host-killed native run."""
import argparse
from collections import Counter
import json
from pathlib import Path
from fixtures.native_wakeup.stage import load, save, sha
from moe_ub_cosim.modern_native import ModernNativeController


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stage',type=Path,required=True)
    args=parser.parse_args();stage=args.stage.resolve();directory=stage/'four-routes'
    monitor=load(stage/'four-routes-monitor/probe.json')
    assert monitor['status']=='wall_limit' and not monitor['remaining_live_group_pids']
    assert not (directory/'result.json').exists()
    provenance=load(directory/'provenance.json')
    controller_path=Path(__file__).resolve().parents[2]/'python/moe_ub_cosim/modern_native.py'
    assert provenance['hashes'][str(controller_path)]==sha(controller_path)
    controller=ModernNativeController(*(load(directory/name) for name in ('topology.json','protocol.json','input.json')))
    transcript=[json.loads(line) for line in (directory/'control.jsonl').read_text().splitlines()]
    assert len(transcript)%2==0
    for index in range(0,len(transcript),2):
        incoming,outgoing=transcript[index:index+2]
        assert incoming['direction']=='native' and outgoing['direction']=='frontend'
        event,command=incoming['message'],outgoing['message']
        assert event['decision_id']==command['decision_id']==index//2
        assert event['now_ns']==command['now_ns']
        assert controller.advance(event)==command['submissions']
        assert not command['finish']
    snapshot=controller.snapshot();old=load(stage/'original/four-routes/modern-execution.json')
    assert snapshot['events'][:len(old['events'])]==old['events']
    assert snapshot['native_operations'][:len(old['native_operations'])]==old['native_operations']
    save(stage/'partial-controller-reconstructed.json',dict(
        reconstructed_offline=True,network_reexecuted=False,source='recorded actual callbacks and commits only',
        native_memory_or_packet_state_reconstructed=False,controller=snapshot))
    pending=[]
    for reference in transcript[-1]['message']['wake_on']:
        identity=reference['operation'];node=controller.native_nodes[identity]
        pending.append(dict(reference=reference,node=node,task=controller.tasks[identity],
            received_callbacks={event:time for (i,event),time in controller.callbacks.items() if i==identity}))
    started=__import__('datetime').datetime.fromisoformat(monitor['started_utc']).timestamp()
    last_write=(directory/'control.jsonl').stat().st_mtime-started
    events={event['event'] for event in snapshot['events']}
    plan={event['id']:event for event in controller.layout.plan['events']}
    save(stage/'failure-analysis.json',dict(
        failed=True,reason='60-second host wall limit; native did not write a terminal result',
        last_recorded_decision=transcript[-1]['message']['decision_id'],
        actual_control_exchanges=len(transcript)//2,last_reported_ns=controller.now,
        native_clock_at_termination_known=False,network_deadlock_proven=False,
        four_request_full_call_passed=False,decision_limit=256,limits_raised=False,network_reexecuted=False,
        fired_modern_events=len(events),total_modern_events=len(plan),
        remaining_event_kinds=dict(Counter(event['kind'] for name,event in plan.items() if name not in events)),
        pending_application_completions=snapshot['runtime']['pending_application_completions'],
        preserved_original_failure_prefix=True,last_control_file_write_seconds_after_start=last_write,
        pending_actual_callback_subscriptions=pending,
        missing_native_evidence=['final simulator clock','packet and queue state after last DECIDE',
                                 'executing native event or stack','terminal memory/lease state'],
        next_requirement='bounded native progress diagnostics before another full-call execution; do not extend wall budget',
        scope='actual recorded frontend prefix only; not native packet replay, network deadlock diagnosis or completed inference'))
    print(json.dumps(dict(decisions=len(transcript)//2,last_reported_ns=controller.now,
        fired_events=len(events),total_events=len(plan),last_control_write_seconds=last_write),indent=2))


if __name__=='__main__':main()
