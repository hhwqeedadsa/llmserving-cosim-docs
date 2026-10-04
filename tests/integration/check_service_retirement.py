"""Compare bounded record/retirement execution with preserved real UB results."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.plan_io import file_sha256
from moe_ub_cosim.record_storage import iter_file, iter_result, iter_port_records


def read(path):return json.loads(path.read_text())
def save(path, value):path.write_text(json.dumps(value, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-dir', type=Path, required=True)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--verify-only', action='store_true', help='Recheck completed runs and their recorded successful commands')
    args = parser.parse_args(); root = Path.cwd(); baseline = args.baseline_dir.resolve()
    out = args.output_dir.resolve(); out.mkdir(parents=True, exist_ok=args.verify_only)
    binary = args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    policy = dict(schema_version='moe-ub-record-storage/1', max_compressed_bytes=256*2**20,
                  max_buffer_bytes=2**20, max_record_bytes=2**18)
    policy_path = out/'policy.json'
    if args.verify_only:assert read(policy_path) == policy
    else:save(policy_path, policy)
    commands = read(out/'commands.json') if args.verify_only else []
    summary = {}

    def invoke(label, command, expected=0):
        if args.verify_only:
            assert any(c['argv'] == command and c['exit_code'] == expected for c in commands), label
            return
        started = datetime.now(timezone.utc).isoformat()
        with (out/f'{label}.stdout.log').open('w') as stdout, (out/f'{label}.stderr.log').open('w') as stderr:
            process = subprocess.run(command, cwd=root, stdout=stdout, stderr=stderr, timeout=600)
        commands.append(dict(argv=command, cwd=str(root), started_utc=started,
                             ended_utc=datetime.now(timezone.utc).isoformat(), exit_code=process.returncode))
        save(out/'commands.json', commands)
        assert process.returncode == expected, (label, (out/f'{label}.stderr.log').read_text()[-3000:])

    for suite, names in [('closed-service', ['reference', 'provenance-off', 'window', 'empty']),
                         ('closed-pd', ['on'])]:
        fixtures = baseline/suite/'work'; old_commands = read(fixtures/'commands.json')
        for name in names:
            label = suite+'-'+name; run = out/label
            old = fixtures/name; old_report = read(old/'execution/result.json')
            before = {str(p):file_sha256(p) for p in old.rglob('*') if p.is_file()}
            command = list(next(c['argv'] for c in old_commands if c['label'] == name))
            command[command.index('--backend')+1] = str(binary)
            command[command.index('--output-dir')+1] = str(run)
            command += ['--record-storage', str(policy_path)]
            invoke(label, command)
            report = read(run/'execution/result.json'); assert report['schema_version'] == 'moe-ub-service-result/2'
            actual = deepcopy(report)
            for field in ('wqe_starts', 'wqe_completions', 'target_writes', 'transfers', 'local_deliveries', 'node_execution'):
                actual[field] = list(iter_result(run/'execution', field, report))
            actual['schema_version'] = 'moe-ub-service-result/1'
            for key in ('record_storage', 'resource_lifecycle'):actual.pop(key)
            for r in (old_report, actual):
                # zlib was already in the native library set. Preserve the
                # entire set; only the build path and loader order may differ.
                r['loaded_libraries'] = sorted(Path(p).name for p in r['loaded_libraries'])
            assert old_report == actual, (label, [k for k in old_report if old_report[k] != actual.get(k)])
            assert read(old/'service_result.json') == read(run/'service_result.json')
            ports = 0; logical_rows = 0
            directories = list((old/'execution/ports').glob('*'))
            if (old/'execution/observed/public_config.json').exists():directories.append(old/'execution')
            for directory in directories:
                new = run/'execution'/directory.relative_to(old/'execution'); ports += 1
                assert read(directory/'observation-audit.json') == read(new/'observation-audit.json')
                assert read(directory/'observed/public_config.json') == read(new/'observed/public_config.json')
                port_files = [*directory.glob('observed/*.jsonl'), *directory.glob('truth/*.jsonl')]
                port_files += [p for p in (directory/'packet-events.jsonl', directory/'egress-queue-audit.jsonl') if p.exists()]
                for path in port_files:
                    expected = [json.loads(line) for line in path.read_text().splitlines()]
                    rows = list(iter_port_records(run/'execution', new, str(path.relative_to(directory))))
                    assert expected == rows, (label, path)
                    logical_rows += len(rows)
            life = report['resource_lifecycle']; count = len(read(run/'service_result.json')['waves'])
            assert life['retired_graphs'] == count and life['resident_graphs_at_exit'] == life['resident_transfers_at_exit'] == life['adapter_transfers_at_exit'] == 0
            assert life['execution_events_read'] == 2*len(actual['node_execution'])
            if ports:
                assert life['provenance']['resident_packets'] == life['provenance']['resident_transfers'] == 0
                assert life['provenance']['retired_transfers'] == len(actual['transfers'])
            else:assert 'provenance' not in life
            assert report['record_storage']['peak_buffered_payload_bytes'] <= policy['max_buffer_bytes']
            assert report['network_initializations'] == 1 and report['network_drain']['idle']
            for path, digest in before.items():assert file_sha256(Path(path)) == digest
            summary[label] = dict(graphs=count, writes=len(actual['transfers']), logical_port_rows=logical_rows,
                                 ports=ports, lifecycle=life, storage_bytes=report['record_storage']['compressed_bytes'])
            if name == 'reference':
                # A damaged archived event must fail the ordinary public reader.
                ref = report['record_storage']['fields']['transfers']['files'][0]
                path = run/'execution/records'/ref['name']; saved = path.read_bytes()
                path.write_bytes(saved[:-3])
                try:
                    rejected = False
                    try:list(iter_file(run/'execution', ref))
                    except (ValueError, OSError, EOFError):rejected = True
                    assert rejected
                finally:path.write_bytes(saved)
    save(out/'summary.json', dict(passed=True, cases=summary, target_scale_executed=False,
                                 research_export_verified=False, frontend_graph_archival_verified=False))
    print('PASS preserved real UB records, out-of-order graph retirement, sparse windows and PD handoff')


if __name__ == '__main__':main()
