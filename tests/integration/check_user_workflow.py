"""Exercise public commands and independently check first-round replay/sampling."""
import argparse
from collections import Counter
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

from moe_ub_cosim.record_storage import iter_result, iter_port_records
from moe_ub_cosim.research_storage import stored_rows, logical_windows


def read(p):
    return json.loads(p.read_text())


def save(p, v):
    p.write_text(json.dumps(v, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(); root = Path(__file__).resolve().parents[2]
    out = args.output_dir.resolve(); out.mkdir(parents=True, exist_ok=False)
    commands = []; binary = args.backend.resolve(); example = root/'examples/first_round'

    def cli(label, *words, fail=False, offline=False):
        argv = [sys.executable, *(['-S'] if offline else []), '-m', 'moe_ub_cosim', *map(str, words)]
        started = datetime.now(timezone.utc).isoformat()
        # The relocated config below resolves sibling files from its own path.
        result = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=360)
        for stream in ('stdout', 'stderr'):
            (out/(label+'.'+stream+'.log')).write_text(getattr(result, stream))
        commands.append(dict(argv=argv, cwd=str(root), started_utc=started,
                             exit_code=result.returncode, log_prefix=str(out/label)))
        save(out/'commands.json', commands)
        assert (result.returncode != 0) if fail else result.returncode == 0, (label, result.stderr)
        return result

    for words in [('--help',), ('run', '--help'), ('sample', '--help'), ('slice', 'inspect', '--help')]:
        cli('help-'+str(len(commands)), *words, offline=True)
    cli('run', 'run', '--config', example/'run.json', '--backend', binary, '--output', out/'run')
    cli('existing-output', 'run', '--config', example/'run.json', '--backend', binary, '--output', out/'run', fail=True)
    cli('freeze', 'slice', 'create', '--from-run', out/'run', '--output', out/'slice')
    # Remove every original input location of the bundle, then operate on the
    # moved copy. Historic compiler/log absolute paths cannot be live inputs.
    (out/'slice').rename(out/'moved-slice'); bundle = out/'moved-slice'
    (out/'run').rename(out/'original-run-hidden'); original = out/'original-run-hidden'
    cli('inspect', 'slice', 'inspect', '--slice', bundle, '--verify', offline=True)
    cli('sample', 'sample', '--slice', bundle, '--config', example/'sample.json', '--output', out/'sample', offline=True)
    cli('replay', 'replay', '--slice', bundle, '--config', example/'replay.json', '--backend', binary, '--output', out/'replay', offline=True)
    for name in ('sample', 'replay'):
        cli('validate-'+name, 'validate', '--run', out/name, offline=True)
    public = out/'observed-only'; shutil.copytree(out/'sample/observed', public)
    cli('validate-public', 'validate', '--observed', public, offline=True)
    original_report = read(original/'raw/execution/result.json')
    replay_report = read(out/'replay/raw/execution/result.json')
    compared = {}
    for field in original_report['record_storage']['fields']:
        expected = list(iter_result(original/'raw/execution', field, original_report))
        actual = list(iter_result(out/'replay/raw/execution', field, replay_report))
        assert actual == expected, ('replayed field', field)
        compared[field] = len(expected)
    for scope in ('observed', 'truth', 'metrics'):
        for name in read(original/scope/'storage.json')['fields']:
            assert list(stored_rows(original/scope, name)) == list(stored_rows(out/'replay'/scope, name)), (scope, name)
    assert original_report['simulation_end_ns'] == replay_report['simulation_end_ns']

    observation = read(example/'observation.json')
    switch = observation['switches'][0]
    raw_port = original/'raw/execution/ports'/f'{switch["switch_id"]}-{switch["port_ids"][1]}'
    events = list(iter_port_records(original/'raw/execution', raw_port, 'packet-events.jsonl'))
    assert events
    t = events[0]['sim_time_ns']
    for clock in observation['clocks']:
        clock.update(offset_ns=-17, drift_ppb=100003)
    at = int(Fraction((10**9+100003)*t, 10**9)//1)-17
    switch.update(start_local_ns=at-2, end_local_ns=at+1, period_ns=2)
    save(out/'edge-observation.json', observation)
    config = read(example/'sample.json'); config['observation'] = 'edge-observation.json'
    config['export_policy'] = str(example/'export-policy.json'); save(out/'edge.json', config)
    cli('edge', 'sample', '--slice', bundle, '--config', out/'edge.json', '--output', out/'edge', offline=True)
    expected_attribution = Counter()
    for row in logical_windows(out/'edge/observed'):
        raw_port = original/'raw/execution/ports'/f'{row["switch_id"]}-{row["port_id"]}'
        clock = next(c for c in observation['clocks'] if c['device_type'] == 'switch' and c['device_id'] == row['switch_id'])
        def selected(event):
            local = ((10**9+clock['drift_ppb'])*event['sim_time_ns'])//10**9+clock['offset_ns']
            return row['local_window_start_ns'] <= local < row['local_window_end_ns']
        selected_events = [e for e in iter_port_records(original/'raw/execution', raw_port, 'packet-events.jsonl') if selected(e)]
        assert row['counted_packets'] == len(selected_events)
        assert row['counted_packet_bytes'] == sum(e['counted_packet_bytes'] for e in selected_events)
        for part in iter_port_records(original/'raw/execution', raw_port, 'truth/packet_ranges.jsonl'):
            if selected(part):
                expected_attribution[part['operation_id'], row['port_id'], row['local_window_start_ns']] += part['task_byte_range'][1]-part['task_byte_range'][0]
    actual_attribution = Counter({(r['operation_id'], r['port_id'], r['local_window_start_ns']): r['business_payload_bytes']
        for r in stored_rows(out/'edge/truth', 'attribution_windows.csv')})
    assert actual_attribution == expected_attribution
    assert any(r['start_local_ns'] < switch['start_local_ns'] for r in stored_rows(out/'edge/observed', 'endpoint_ops.jsonl'))
    assert len(list(stored_rows(out/'edge/observed', 'endpoint_ops.jsonl'))) == compared['endpoint_ops']

    network = out/'slow-network'; shutil.copytree(example/'network', network)
    path = network/'topology.csv'
    original_text = path.read_text(); text = original_text.replace(',20ns', ',320ns')
    assert text != original_text; path.write_text(text)
    config = read(example/'replay.json'); config.update(network=str(network), backend=str(binary), export_policy=str(example/'export-policy.json'))
    save(out/'slow.json', config)
    cli('slow', 'replay', '--slice', bundle, '--config', out/'slow.json', '--output', out/'slow', offline=True)
    slow_report = read(out/'slow/raw/execution/result.json')
    assert slow_report['simulation_end_ns'] > replay_report['simulation_end_ns']
    fast = list(iter_result(out/'replay/raw/execution', 'transfers'))
    slow = list(iter_result(out/'slow/raw/execution', 'transfers'))
    assert [r['transfer_id'] for r in fast] == [r['transfer_id'] for r in slow]
    assert any(a['data_ready_ns'] != b['data_ready_ns'] for a, b in zip(fast, slow))
    compute = lambda path: {(r['rank_id'], r['node_id']): r for r in iter_result(path/'raw/execution', 'node_execution') if r['kind'] == 'COMP'}
    fast_compute, slow_compute = compute(out/'replay'), compute(out/'slow')
    assert set(fast_compute) == set(slow_compute)
    assert any(fast_compute[k]['start_ns'] != slow_compute[k]['start_ns'] for k in fast_compute)
    assert all(fast_compute[k]['end_ns']-fast_compute[k]['start_ns'] == slow_compute[k]['end_ns']-slow_compute[k]['start_ns'] for k in fast_compute)
    assert list(iter_result(out/'replay/raw/execution', 'planned_transfers')) == list(iter_result(out/'slow/raw/execution', 'planned_transfers'))

    bad = deepcopy(config); bad['unknown'] = 1; save(out/'bad.json', bad)
    cli('bad-fields', 'replay', '--slice', bundle, '--config', out/'bad.json', '--output', out/'bad', fail=True)
    assert not (out/'bad').exists()
    missing = read(example/'sample.json'); missing['observation'] = 'absent.json'
    missing['export_policy'] = str(example/'export-policy.json'); save(out/'missing.json', missing)
    cli('missing-input', 'sample', '--slice', bundle, '--config', out/'missing.json', '--output', out/'missing', fail=True, offline=True)
    assert not (out/'missing').exists()
    declaration = read(bundle/'slice.json'); reduced = deepcopy(declaration)
    reduced['artifacts'].pop('inputs/system.json'); save(bundle/'slice.json', reduced)
    cli('incomplete-inventory', 'slice', 'inspect', '--slice', bundle, fail=True, offline=True)
    save(bundle/'slice.json', declaration)
    graph = next((bundle/'raw/graphs').glob('*/plan.json')); graph.write_text(graph.read_text()+' ')
    cli('tampered', 'slice', 'inspect', '--slice', bundle, '--verify', fail=True, offline=True)
    save(out/'summary.json', dict(passed=True, exact_record_counts=compared,
        baseline_simulation_ns=replay_report['simulation_end_ns'], perturbed_simulation_ns=slow_report['simulation_end_ns'],
        relocated_bundle=True, no_site_packages_for_sampling_and_replay=True,
        clock_boundary_attribution_and_crossing_endpoints=True, missing_and_corrupt_inputs_rejected=True))
    print('PASS: public workflow, relocation, offline sampling, exact replay and real network delay feedback')


if __name__ == '__main__':
    main()
