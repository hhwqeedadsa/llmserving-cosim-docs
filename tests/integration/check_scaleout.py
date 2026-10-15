"""Two bounded public CLI runs: tier rates, shared network and causal feedback."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.record_storage import iter_result
from moe_ub_cosim.research_storage import stored_rows


def read(path):
    return json.loads(path.read_text())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    args = p.parse_args()
    root = Path(__file__).resolve().parents[2]
    backend = args.backend.resolve()
    if not backend.is_file():
        p.error('Build the locked joint backend first: ' + str(backend))
    out = args.output_dir.resolve(); out.mkdir(parents=True, exist_ok=False)
    commands = []

    def save(path, value):
        path.write_text(json.dumps(value, indent=2) + '\n')

    def cli(label, *argv):
        command = [sys.executable, '-m', 'moe_ub_cosim', *map(str, argv)]
        start = datetime.now(timezone.utc).isoformat()
        with (out / (label + '.stdout.log')).open('x') as stdout, (out / (label + '.stderr.log')).open('x') as stderr:
            try:
                result = subprocess.run(command, cwd=root, stdout=stdout, stderr=stderr, timeout=150)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = None
        commands.append(dict(argv=command, cwd=str(root), started_utc=start,
                             ended_utc=datetime.now(timezone.utc).isoformat(), exit_code=code))
        save(out / 'commands.json', commands)
        if code != 0:
            raise RuntimeError(f'{label}: exit {code}; see {out / (label + ".stderr.log")}')

    source = root / 'examples/scaleout'
    original = read(source / 'prepare.json')
    # Two active source ranks occupy different chips on server 0 and share its
    # real uplink. The third rank is on another rack. Same-chip delivery remains
    # absent in both bandwidth cases, so network bytes/dependencies stay fixed.
    original['groups'][0]['rank_to_ub_node'] = [0, 1, 4]
    for key in ('service', 'layer', 'system'):
        original['scenario'][key] = str((source / original['scenario'][key]).resolve())
    original['scenario']['backend']['reference_file'] = str((source / original['scenario']['backend']['reference_file']).resolve())
    cases = {}
    for label, rate in [('baseline', 100000000000), ('server-10g', 10000000000)]:
        c = deepcopy(original); c['link_profiles']['server_uplink']['rate_bps'] = rate
        save(out / (label + '.json'), c)
        prepared, resolved, run = (out / (label + suffix) for suffix in ('-prepared', '-input', '-run'))
        cli(label + '-prepare', 'scaleout', 'prepare', '--config', out / (label + '.json'), '--output', prepared)
        cli(label + '-resolve', 'resolve', '--config', prepared / 'scenario.yaml', '--backend', backend, '--output', resolved)
        run_config = read(source / 'run.json')
        run_config['inputs'] = {k: str(resolved / name) for k, name in
                                [('service', 'service.json'), ('layer', 'layer.json'), ('system', 'system.json'), ('network', 'network')]}
        for key in ('observation', 'record_policy', 'export_policy'):
            run_config[key] = str((source / run_config[key]).resolve())
        run_config['backend'] = str(backend)
        # One cumulative disk budget includes both cases and resolution files.
        run_config['host']['artifact_roots'] = [str(out)]
        save(out / (label + '-run.json'), run_config)
        cli(label + '-run', 'run', '--config', out / (label + '-run.json'), '--output', run)
        cli(label + '-validate', 'validate', '--run', run)
        execution = run / 'raw/execution'; report = read(execution / 'result.json')
        effective = read(execution / 'effective_backend.json')
        assert report['normal_exit'] and report['network_drain']['idle'] and report['network_initializations'] == 1
        assert effective['object_validation'] == 'matched_before_business_activation'
        assert effective['topology'] == read(resolved / 'network/backend.json')['topology']
        assert {l['rate_bps'] for l in effective['topology']['links']} == {rate, 200000000000, 400000000000}
        for transfer in iter_result(execution, 'transfers'):
            assert transfer['submitted_tasks'] == transfer['send_callbacks'] == transfer['receive_callbacks'] == 1
        windows = list(stored_rows(run / 'observed', 'port_windows.csv'))
        assert any(r['switch_id'] in (8, 9, 10) and r['counted_packet_bytes'] > 0 for r in windows)
        assert any(r['switch_id'] in (12, 13) and r['counted_packet_bytes'] > 0 for r in windows)
        cases[label] = dict(run=str(run), simulation_end_ns=report['simulation_end_ns'])
    fast, slow = (Path(cases[k]['run']) / 'raw/execution' for k in ('baseline', 'server-10g'))
    assert list(iter_result(fast, 'planned_transfers')) == list(iter_result(slow, 'planned_transfers'))
    comp = lambda path: {(r['graph_id'], r['rank_id'], r['node_id']): r
                         for r in iter_result(path, 'node_execution') if r['kind'] == 'COMP'}
    a, b = comp(fast), comp(slow)
    assert a.keys() == b.keys()
    assert all(a[k]['end_ns'] - a[k]['start_ns'] == b[k]['end_ns'] - b[k]['start_ns'] for k in a)
    assert any(a[k]['start_ns'] != b[k]['start_ns'] for k in a), 'No compute dependency responded to changed network'
    assert cases['server-10g']['simulation_end_ns'] > cases['baseline']['simulation_end_ns']
    save(out / 'summary.json', dict(passed=True, cases=cases,
        scope='Synthetic Decode; UB tree with server bandwidth control; no hardware performance claim'))
    print(json.dumps(cases, indent=2))


if __name__ == '__main__':
    main()
