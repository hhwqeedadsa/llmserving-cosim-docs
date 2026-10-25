"""Preserve observation-budget failure and compare its actual control prefix."""
import argparse
import json
from pathlib import Path
from fixtures.session_host_timing.stage import load,save,sha
from fixtures.session_host_timing.verify import summarize
from moe_ub_cosim.host_timing import read_trace


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);a=p.parse_args()
    stage=a.stage.resolve();directory=stage/'four-routes'
    monitor=load(stage/'four-routes-monitor/probe.json')
    assert monitor['status']=='child_failed' and not monitor['remaining_live_group_pids']
    assert 'host timing record budget exceeded' in (directory/'native.stderr.log').read_text()
    assert not (directory/'result.json').exists()
    original=stage/'original/four-routes'
    for name in ('initial.json','input.json','protocol.json','topology.json'):
        assert load(directory/name)==load(original/name)
    actual=(directory/'control.jsonl').read_bytes();prior=(original/'control.jsonl').read_bytes()
    assert actual.endswith(b'\n') and prior.startswith(actual)
    rows=[json.loads(line) for line in actual.splitlines()]
    assert len(rows)==196 and all(row['direction']==('native' if index%2==0 else 'frontend') for index,row in enumerate(rows))
    modern=load(directory/'modern-execution.json');assert not modern['runtime']['all_events_fired']
    assert modern['decisions']==98 and len(modern['events'])==246 and len(modern['native_operations'])==75
    summary=load(stage/'four-host-summary.json');assert summary==summarize(directory)
    native=summary['native'];assert native['records']==65536 and not native['trace_complete']
    assert [row['name'] for row in native['open_spans']]==['native_total','simulator_run','session_progress','session_drained']
    sample=native['last_snapshot']['values']
    assert sample['now_ns']==17546 and sample['processed_events']==40931
    assert sample['collected_callbacks']==341 and sample['registered_nodes']==75
    phases=native['phases']
    pending=None;closed_scans=without_callbacks=unchanged_counters=0
    for row in read_trace(directory/'native_host.jsonl')['snapshots']:
        values=row['values']
        if row['name']=='session_progress_enter':
            assert pending is None;pending=values
        else:
            assert row['name']=='session_progress_exit' and pending is not None
            closed_scans+=1
            without_callbacks+=values['collected_callbacks']==pending['collected_callbacks']
            keys=('collected_callbacks','decisions','registered_nodes','submitted_nodes','local_busy_ranks','pending_callbacks','finished')
            unchanged_counters+=all(values[key]==pending[key] for key in keys)
            pending=None
    packetization=load(directory/'protocol.json')
    save(stage/'failure-analysis.json',dict(preserved_failure=True,reason='native observation record budget exhausted',
        host_limit_exhausted=False,actual_monitor_seconds=monitor['wall_seconds'],observation_record_limit=65536,
        native_trace_complete=False,service_complete=False,network_deadlock_proven=False,
        original_control_prefix_byte_identical=True,control_prefix_sha256=sha(directory/'control.jsonl'),
        decisions=98,modern_events=246,modern_total_events=566,native_registered_nodes=75,
        last_native_snapshot=sample,last_native_snapshot_is_terminal_state=False,
        closed_operation_progress_snapshots=phases['operation_progress_snapshot']['count'],
        completed_progress_scans=closed_scans,scans_without_new_operation_callbacks=without_callbacks,
        scans_without_changes_to_recorded_work_counters=unchanged_counters,
        closed_json_progress_wall_ns=phases['operation_progress_snapshot']['exclusive_wall_ns'],
        closed_event_collection_exclusive_wall_ns=phases['session_events']['exclusive_wall_ns'],
        closed_json_progress_cpu_ns=phases['operation_progress_snapshot']['exclusive_cpu_ns'],
        closed_event_collection_exclusive_cpu_ns=phases['session_events']['exclusive_cpu_ns'],
        open_spans=[row['name'] for row in native['open_spans']],
        original_packetization=dict(payload_bytes=packetization['payload_bytes'],
            chunk_bytes=packetization['chunk_bytes'],ack_interval_bytes=packetization['ack_interval_bytes']),
        packetization_hardware_calibrated=False,model_or_protocol_changed=False,
        scope='actual recorded prefix; observer exhaustion is a new failure, not the previous 60-second host timeout',
        next_requirement='incremental operation-transition collection plus bounded aggregate observation; preserve exact callback ordering and original four-rank workload'))


if __name__=='__main__':main()
