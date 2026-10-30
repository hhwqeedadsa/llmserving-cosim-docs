"""Read six saved networks, checking lineage and normal-only calibration."""
import argparse
from copy import deepcopy
import csv
import gc
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('independent_sequential_analysis',
    ROOT/'experiments/sequential_externality/analyze.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
load, sha = audit.load, audit.sha

def files(path):
    return {str(p.relative_to(path)): sha(p) for p in sorted(path.rglob('*')) if p.is_file()}

def normalized(config):
    config = deepcopy(config)
    config['route_replay']['manifest'] = None
    config['whole_call_replication']['calls'][0]['hedge_after_ns'] = None
    return config

def compare(control, treatment, baseline, changed):
    assert normalized(control['config']) == normalized(treatment['config'])
    for file in ('rdma.json', 'topology.json'):
        assert baseline['sha256'][file] == changed['sha256'][file]
    assert all(treatment['waves'][g] == wave for g, wave in control['waves'].items())
    assert baseline['fault_drops'] == changed['fault_drops']
    assert baseline['standby_memory']['initial_reserved_bytes'] == changed['standby_memory']['initial_reserved_bytes']
    before = {(v['stream'], v['sequence'], v['payload_bytes'], v['attempt'], v['node'], v['port']): v
              for v in control['residence'] if v['group'] == 2}
    changes, missing = [], []
    for value in (v for v in treatment['residence'] if v['group'] == 2):
        key = tuple(value[k] for k in ('stream', 'sequence', 'payload_bytes', 'attempt', 'node', 'port'))
        old = before.pop(key, None)
        if old is None:
            missing.append(key)
        else:
            changes.append(dict(stream=value['stream'], sequence=value['sequence'],
                node=value['node'], port=value['port'],
                baseline_rx_ns=old['rx_ns'], treatment_rx_ns=value['rx_ns'],
                baseline_tx_ns=old['tx_ns'], treatment_tx_ns=value['tx_ns'],
                residence_change_ns=value['residence_ns']-old['residence_ns']))
    assert not missing and not before
    gates = {(g['graph_id'], g['id']): g for g in control['healthy_gates']}
    phase = [dict(graph=g['graph_id'], phase=g['id'],
                  baseline_ns=gates[g['graph_id'], g['id']]['released_ns'],
                  treatment_ns=g['released_ns'],
                  change_ns=g['released_ns']-gates[g['graph_id'], g['id']]['released_ns'])
             for g in treatment['healthy_gates']]
    result = dict(result_change_ns={call: changed['result_ready_ns'][call]-baseline['result_ready_ns'][call]
                                   for call in ('target', 'healthy')},
        target_saved_ns=baseline['result_ready_ns']['target']-changed['result_ready_ns']['target'],
        network_drain_change_ns=changed['network_drained_ns']-baseline['network_drained_ns'],
        attempts_change_ns=changed['attempts_finished_ns']-baseline['attempts_finished_ns'],
        extra_effective_bytes=changed['unique_payload_bytes']-baseline['unique_payload_bytes'],
        extra_source_payload_bytes=changed['source_payload_bytes']-baseline['source_payload_bytes'],
        extra_declared_compute_ns=changed['declared_compute_work_ns']-baseline['declared_compute_work_ns'],
        all_packets_exact=control['result']['packet_capture'] == treatment['result']['packet_capture'],
        all_queue_events_exact=control['result']['queue_events'] == treatment['result']['queue_events'],
        healthy_messages_exact=control['healthy_messages'] == treatment['healthy_messages'],
        healthy_node_times_exact=control['healthy_node_times'] == treatment['healthy_node_times'],
        healthy_phase_gates_exact=control['healthy_gates'] == treatment['healthy_gates'],
        healthy_data_events_exact=control['events'][2] == treatment['events'][2],
        healthy_hops_matched=len(changes), healthy_hops_unmatched=0,
        healthy_max_hop_residence_change_ns=max(v['residence_change_ns'] for v in changes),
        healthy_total_hop_residence_change_ns=sum(v['residence_change_ns'] for v in changes),
        healthy_phase_changes=phase)
    return result, changes

def augment(summary, detail):
    summary['calls'] = detail['frontend']['whole_call_replication']['calls']
    summary['target_deadline_ns'] = detail['config']['whole_call_replication']['calls'][0]['hedge_after_ns']
    summary['source_retransmitted_payload_bytes'] = summary['source_payload_bytes']-summary['unique_payload_bytes']
    return summary

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    frozen = load(args.frozen)
    assert frozen['passed']
    assert sha(Path(__file__).with_name('protocol.md')) == frozen['method_sha256']
    assert sha(ROOT/'experiments/sequential_externality/analyze.py') == frozen['independent_inspector_sha256']
    assert sha(Path(frozen['backend']['path'])) == frozen['backend']['sha256']
    for path, digest in frozen['loaded_ns3_libraries'].items():
        assert sha(Path(path)) == digest
    assert files(Path(frozen['prepared'])) == frozen['prepared_sha256']
    for name, case in frozen['inherited_cases'].items():
        assert files(Path(case['output'])) == case['sha256'], 'old result changed: '+name
    calibration = frozen['calibration']
    source = Path(calibration['source'])
    assert sha(source) == calibration['source_sha256']
    duration = load(source)['calls']['target']['result_ready_ns']
    assert duration == calibration['complete_normal_target_ns']
    assert ((duration+999)//1000)*1000 == calibration['hedge_after_ns']
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    summaries, effects = {}, {}
    for condition in ('normal', 'local'):
        name = 'control-'+condition
        path = Path(frozen['inherited_cases'][name]['output'])
        baseline, control = audit.inspect(path, out, name)
        summaries[name] = augment(baseline, control)
        for policy in ('early', 'calibrated'):
            name = policy+'-'+condition
            path = Path(frozen['inherited_cases']['treatment-'+condition]['output'] if policy == 'early'
                        else frozen['new_cases'][condition]['output'])
            summary, detail = audit.inspect(path, out, name)
            summaries[name] = augment(summary, detail)
            expected = 70000 if policy == 'early' else calibration['hedge_after_ns']
            assert summary['target_deadline_ns'] == expected
            effects[name], hops = compare(control, detail, baseline, summary)
            audit.write_csv(out/(name+'-healthy-matched.csv'), hops)
            audit.write_csv(out/(name+'-healthy-gates.csv'), effects[name]['healthy_phase_changes'])
            del detail
            gc.collect()
        del control
        gc.collect()
    # No pass condition depends on gaining performance or producing interference.
    checks = dict(calibration_rebuilt_from_normal_only=True,
        normal_deadline_margin_ns=calibration['hedge_after_ns']-duration,
        normal_replica_skipped=summaries['calibrated-normal']['calls']['target']['attempts']['replica']['submitted_ns'] is None,
        local_replica_started=summaries['calibrated-local']['calls']['target']['attempts']['replica']['submitted_ns'] is not None,
        inherited_files_unchanged=True, configs_differ_only_by_target_deadline_and_manifest_path=True,
        complete_source_matrix_compute_gates_packets_ack_timer_and_kv_checked=True)
    for name, case in frozen['inherited_cases'].items():
        assert files(Path(case['output'])) == case['sha256']
    result = dict(passed=True, calibration=calibration, cases=summaries, effects=effects, checks=checks,
        new_network_executions=2, inherited_networks_read_only=4, network_reruns=0,
        analyzer_sha256=sha(Path(__file__)), new_algorithm_benefit_proven=False,
        scope='Two-layer two-Decode finite calls; true expert choices, declared compute/layout and preexisting equivalent model/KV; no full671B, measured GPU, arrival distribution, percentile, throughput or economic benefit')
    audit.save(out/'analysis.json', result)
    rows = [dict(case=name, target_ns=value['result_ready_ns']['target'],
                 healthy_ns=value['result_ready_ns']['healthy'],
                 drain_ns=value['network_drained_ns'], unique_payload_bytes=value['unique_payload_bytes'],
                 retransmitted_payload_bytes=value['source_retransmitted_payload_bytes'],
                 declared_compute_work_ns=value['declared_compute_work_ns'],
                 target_winner=value['call_winners']['target']) for name, value in summaries.items()]
    audit.write_csv(out/'summary.csv', rows)
    print(json.dumps(dict(passed=True, cases=rows, checks=checks, effects=effects)))

if __name__ == '__main__':
    main()
