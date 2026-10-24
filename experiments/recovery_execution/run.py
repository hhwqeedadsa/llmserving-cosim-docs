"""Run the fixed matrix once; stop at the first unexpected outcome, no reruns."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

from moe_ub_cosim.host_probe import probe, disk_usage


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', type=Path, required=True)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--continue-unexecuted', action='store_true',
                   help='Only after independently audited first-case terminal classification error; never rerun a case')
    a = p.parse_args()
    stage, repo = a.stage.resolve(), a.repo.resolve()
    manifest = json.loads((stage/'manifest.json').read_text())
    assert len(manifest['conditions']) == 16
    for row in [manifest['backend'], *manifest['libraries']]:
        with Path(row['path']).open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest() == row['sha256']
    for name, expected in manifest['input_hashes'].items():
        assert hashlib.sha256((stage/name).read_bytes()).hexdigest() == expected
    record_path = stage/'execution.json'
    records, conditions = [], manifest['conditions']
    if a.continue_unexecuted:
        prior = json.loads(record_path.read_text())
        audit = json.loads((stage/'first-case-audit.json').read_text())
        assert not prior['completed_matrix'] and len(prior['records']) == 1
        assert prior['records'][0]['name'] == conditions[0]['name']
        assert audit['cases'][0]['name'] == conditions[0]['name']
        assert audit['cases'][0]['controller_exit_classification_error']
        assert len(audit['tampering']) == 3 and all(t['rejected'] for t in audit['tampering'])
        assert not any((stage/'cases'/c['name']).exists() for c in conditions[1:])
        records, conditions = prior['records'], conditions[1:]
    else:
        (stage/'cases').mkdir(exist_ok=False)
        (stage/'monitors').mkdir(exist_ok=False)
    start = time.monotonic()
    for condition in conditions:
        assert time.monotonic()-start < 240, 'leave remaining 300s cumulative budget for audit/checkpoint'
        name = condition['name']; output = stage/'cases'/name
        argv = [sys.executable, '-B', str(repo/'experiments/recovery_execution/controller.py'),
                '--topology', str(stage/'inputs/topology/topology.json'),
                '--protocol', condition['protocol'], '--initial', condition['initial'],
                '--controller', condition['controller'], '--backend', manifest['backend']['path'],
                '--output', str(output)]
        report = probe(argv, stage/'monitors'/name, rss_bytes=2**30, address_bytes=2**31,
                       artifact_bytes=64*2**20, wall_seconds=60,
                       artifact_roots=[output], min_free_bytes=8*2**30)
        row = dict(name=name, status=report['status'], exit_code=report['child_exit_code'],
                   wall_seconds=report['wall_seconds'], remaining_live_group_pids=report['remaining_live_group_pids'])
        records.append(row)
        record_path.write_text(json.dumps(dict(records=records, elapsed_seconds=time.monotonic()-start,
                                              completed_matrix=False), indent=2)+'\n')
        print(json.dumps(row), flush=True)
        assert report['status'] == 'completed' and not report['remaining_live_group_pids'], 'stop matrix, preserve failed case'
        result_path = output/'result.json'
        assert result_path.stat().st_size <= 16*2**20
        result = json.loads(result_path.read_text())
        assert not result['passed'] and not result['session']['finished'] and result['finish_ns'] == 5000000
        assert 'execution_error' not in result
        failures = result['transactions']['transport']['recovery']['failures']
        source = next(r for r in manifest['requests'] if r['request'] == condition['request'])
        assert len(failures) == 1 and failures[0]['stream'] == 2*source['failed_owner']
        assert result['packet_capture']['complete']
        assert max(disk_usage(stage).values()) <= 192*2**20, 'cumulative artifact limit; stop'
    record_path.write_text(json.dumps(dict(records=records, elapsed_seconds=time.monotonic()-start,
        completed_matrix=True, first_case_driver_error_preserved=a.continue_unexecuted,
        first_case_disposition='independent packet/memory/transcript audit; never rerun' if a.continue_unexecuted else None), indent=2)+'\n')


if __name__ == '__main__':
    main()
