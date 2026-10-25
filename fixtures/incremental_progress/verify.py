"""Whole-result equivalence and original workload/prefix checks, no controller."""
import argparse
import json
from pathlib import Path
from fixtures.incremental_progress.stage import load,save,sha
from fixtures.session_host_timing.verify import normalized
from fixtures.native_wakeup.verify import delivery
from fixtures.modern_native.audit import audit
from moe_ub_cosim.host_timing import read_trace


def compare(stage,case,*,captured_root=None):
    directory=stage/case;old=stage/'original'/case
    libraries={Path(path).name:digest for path,digest in load(stage/'identity.json')['libraries'].items()}
    for name in ('initial.json','input.json','protocol.json','topology.json','modern-execution.json'):
        assert load(directory/name)==load(old/name),name+' changed'
    assert normalized(load(directory/'result.json'),libraries,captured_root)==normalized(load(old/'result.json'),libraries,captured_root)
    assert (directory/'control.jsonl').read_bytes()==(old/'control.jsonl').read_bytes()
    value=dict(passed=True,case=case,whole_native_modern_control_equal=True,
        normalization='only verified fixed eight library paths',delivery=delivery(directory),acceptance=audit(directory),
        native_full_snapshot_shadow_enabled=case in ('default-healthy','wake-fault'))
    if case=='timed-healthy':
        traces={name:read_trace(directory/(name+'_host.jsonl')) for name in ('worker','native')}
        assert all(trace['trace_complete'] for trace in traces.values())
        native=traces['native'];phase=native['phases']['operation_progress_snapshot']
        value['host_observation']=dict(native_records=native['records'],progress_calls=phase['count'],
            closed_progress_wall_ns=phase['exclusive_wall_ns'],closed_progress_cpu_ns=phase['exclusive_cpu_ns'],
            scope='includes observer overhead; no same-host wall-time acceleration claim')
    return value


def four(stage):
    directory=stage/'four-routes'
    for name,target in (('input.json','four-routes.json'),('protocol.json','four-routes-protocol.json')):
        assert load(directory/name)==load(stage/'inputs'/target)
    assert load(directory/'topology.json')==load(stage/'original/default-healthy/topology.json')
    actual=(directory/'control.jsonl').read_bytes();old=(stage/'original-four-control.jsonl').read_bytes()
    assert actual.endswith(b'\n') and old.endswith(b'\n')
    assert actual.startswith(old) or old.startswith(actual),'original control time/order/content changed'
    rows=[json.loads(line) for line in actual.splitlines()]
    for index,row in enumerate(rows):
        assert row['direction']==('native' if index%2==0 else 'frontend')
        assert row['message']['decision_id']==index//2
    result=load(directory/'result.json') if (directory/'result.json').exists() else None
    modern=load(directory/'modern-execution.json') if (directory/'modern-execution.json').exists() else None
    monitor=load(stage/'four-routes-monitor/probe.json')
    completed=bool(result and result['passed'] and result['session']['finished'] and modern and modern['runtime']['all_events_fired'])
    value=dict(original_input_protocol_topology_preserved=True,original_control_prefix_byte_identical=True,
        original_control_sha256=sha(stage/'original-four-control.jsonl'),actual_control_sha256=sha(directory/'control.jsonl'),
        decisions=len(rows)//2,representative_four_rank_call_complete=completed,
        monitor_status=monitor['status'],monitored_wall_seconds=monitor['wall_seconds'],
        native_result_present=result is not None,modern_snapshot_present=modern is not None,
        decision_limit_unchanged=256,payload_bytes=64,chunk_bytes=256,ack_interval_bytes=1,
        network_deadlock_proven=False)
    if modern:value.update(modern_events=len(modern['events']),native_registered_nodes=len(modern['native_operations']))
    if result:
        value.update(finish_ns=result['finish_ns'],native_execution_error=result.get('execution_error'),
            capture_complete=result['packet_capture']['complete'],omitted_capture_events=result['packet_capture']['omitted_events'])
    if completed:
        value['delivery']=delivery(directory)
        if result['packet_capture']['complete']:value['acceptance']=audit(directory)
        else:value['strict_full_packet_application_audit_complete']=False
    else:
        value['preserved_failure']=True
        value['stderr_tail']=(directory/'native.stderr.log').read_text()[-2000:]
    return value


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--case',required=True);a=p.parse_args();stage=a.stage.resolve()
    value=four(stage) if a.case=='four-routes' else compare(stage,a.case)
    save(stage/(a.case+'-verification.json'),value)


if __name__=='__main__':main()
