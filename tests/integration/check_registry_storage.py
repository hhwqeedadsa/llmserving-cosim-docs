"""Compare shared registry files against preserved real UB execution outputs."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.plan_io import file_sha256


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-dir', type=Path, required=True)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    out = args.output_dir.resolve(); out.mkdir(parents=True, exist_ok=False)
    baseline = args.baseline_dir.resolve()
    assert read(baseline / 'summary.json')['passed']
    protected = {str(path): file_sha256(path) for path in baseline.rglob('*') if path.is_file()}
    command = [sys.executable, str(root / 'tests/integration/check_host_preflight.py'),
               '--build-dir', str(args.build_dir.resolve()), '--output-dir', str(out / 'after')]
    started = datetime.now(timezone.utc).isoformat()
    with (out / 'stdout.log').open('w') as stdout, (out / 'stderr.log').open('w') as stderr:
        process = subprocess.run(command, cwd=root, stdout=stdout, stderr=stderr, timeout=1300)
    save(out / 'command.json', dict(argv=command, cwd=str(root), started_utc=started,
        ended_utc=datetime.now(timezone.utc).isoformat(), exit_code=process.returncode))
    assert process.returncode == 0
    cases = {}
    for name in ('one', 'two', 'ten', 'ten-source-off'):
        old = baseline / name / 'run'; new = out / 'after' / name / 'run'
        assert read(old / 'service_result.json') == read(new / 'service_result.json')
        reports = [read(p / 'execution/result.json') for p in (old, new)]
        for report in reports:
            report['loaded_libraries'] = [Path(p).name for p in report['loaded_libraries']]
        assert reports[0] == reports[1]
        compared = {}
        for directory in ('graphs', 'execution/ports', 'execution/network/runlog'):
            def hashes(run):
                return {str(p.relative_to(run)): file_sha256(p) for p in (run / directory).rglob('*')
                        if p.is_file() and p.name not in ('resource-audit.json', 'compiler.json') and p.suffix != '.pyc'}
            before, after = hashes(old), hashes(new)
            assert before == after, (name, [k for k in before if before[k] != after.get(k)][:5])
            compared.update(after)
        ports = sorted((new / 'execution/ports').iterdir())
        names = ['observed/endpoint_ops.jsonl']
        if name != 'ten-source-off':
            names += ['truth/operation_registry.jsonl', 'truth/transfer_registry.jsonl']
        old_bytes = new_bytes = 0
        for relative in names:
            common = new / 'execution/registry' / Path(relative).name
            old_bytes += sum((old / p.relative_to(new) / relative).stat().st_size for p in ports)
            new_bytes += common.stat().st_size
            assert all(common.samefile(port / relative) for port in ports)
            assert common.stat().st_nlink == len(ports) + 1
        if name == 'ten-source-off':
            assert not (new / 'execution/registry/transfer_registry.jsonl').exists()
        cases[name] = dict(ports=len(ports), registry_bytes_before=old_bytes,
            registry_physical_bytes_after=new_bytes, registry_reduction_factor=old_bytes / new_bytes,
            identical_files=len(compared), file_sha256=compared,
            service_finished_ns=reports[1]['service_finished_ns'])
    assert all(file_sha256(Path(p)) == digest for p, digest in protected.items())
    save(out / 'summary.json', dict(passed=True, cases=cases, protected_files=len(protected),
        shared_files='hard_links_same_filesystem_no_content_or_schema_change', target_scale_executed=False))
    print('PASS: same UB execution and per-port bytes; one physical registry, including provenance disabled')


if __name__ == '__main__':
    main()
