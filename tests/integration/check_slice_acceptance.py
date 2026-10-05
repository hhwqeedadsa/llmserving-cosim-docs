"""Serialize the costly M10 slice acceptance stages through public commands.

The original baseline sampling can already be running. Any failed phase stops
the suite; existing output directories prevent an accidental target rerun.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', type=Path, required=True)
    p.add_argument('--historical-root', type=Path, required=True)
    p.add_argument('--build-dir', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    a = p.parse_args(); root = Path(__file__).resolve().parents[2]
    workspace, history, build = a.workspace.resolve(), a.historical_root.resolve(), a.build_dir.resolve()
    out = a.output_dir.resolve(); out.mkdir(parents=True, exist_ok=False); commands = []
    baseline = workspace/'target-sample-baseline'; bundle = workspace/'target-slice'
    config = workspace/'target-config'; checks = workspace/'checks'
    def save(name, value):
        (out/name).write_text(json.dumps(value, indent=2)+'\n')
    def run(label, argv):
        argv = list(map(str, argv)); started = datetime.now(timezone.utc).isoformat()
        print('START', label, started, flush=True)
        with (out/(label+'.stdout.log')).open('x') as stdout, (out/(label+'.stderr.log')).open('x') as stderr:
            process = subprocess.run(argv, cwd=root, stdout=stdout, stderr=stderr)
        commands.append(dict(label=label, argv=argv, cwd=str(root), started_utc=started,
            ended_utc=datetime.now(timezone.utc).isoformat(), exit_code=process.returncode,
            stdout=str(out/(label+'.stdout.log')), stderr=str(out/(label+'.stderr.log'))))
        save('commands.json', commands)
        assert process.returncode == 0, (label, commands[-1]['stderr'])
        print('PASS', label, flush=True)
    def bounded(label, argv, rss=4, seconds=3600):
        command = [sys.executable, '-m', 'moe_ub_cosim.host_probe', '--output-dir', checks/(label+'-monitor'),
            '--rss-bytes', rss*2**30, '--address-bytes', (8 if rss == 4 else 12)*2**30,
            '--artifact-bytes', 32*2**30, '--min-free-bytes', 20*2**30, '--wall-seconds', seconds]
        for directory in (workspace, history, build, root/'build/m10-resource-efficiency'):
            command += ['--artifact-root', directory]
        run(label, command+['--', *argv])
    start = time.monotonic()
    print('WAIT baseline sampling; no other costly stage runs concurrently', flush=True)
    while True:
        manifest = json.loads((baseline/'manifest.json').read_text())
        if manifest.get('normal_exit'):
            break
        report = baseline/'logs/resources-sample/probe.json'
        if report.exists():
            value = json.loads(report.read_text())
            assert value['status'] == 'completed' and value['child_exit_code'] == 0, ('baseline failed', report)
        assert time.monotonic()-start < 4000, 'Baseline acceptance wait exceeded its expected bounded run'
        time.sleep(5)
    compare = [sys.executable, root/'tests/integration/check_slice_target.py',
        '--historical-export', history/'target-first-round-export/work', '--baseline', baseline]
    bounded('target-baseline-audit', compare+['--output-dir', checks/'target-baseline-audit'])
    bounded('isolated-install-final', [sys.executable, root/'tests/integration/check_user_install.py',
        '--output-dir', checks/'isolated-install-final'], rss=8, seconds=1800)
    run('target-sample-10us', [sys.executable, '-m', 'moe_ub_cosim', 'sample', '--slice', bundle,
        '--config', config/'sample-10us.json', '--output', workspace/'target-sample-10us'])
    # This is the one formal full target backend replay. No loop or automatic
    # retry is permitted; failure preserves the output for diagnosis.
    run('target-replay', [sys.executable, '-m', 'moe_ub_cosim', 'replay', '--slice', bundle,
        '--config', config/'replay.json', '--output', workspace/'target-replay'])
    bounded('target-complete-audit', compare+['--resampled', workspace/'target-sample-10us',
        '--replay', workspace/'target-replay', '--original-raw', workspace/'target-materialized/raw',
        '--output-dir', checks/'target-complete-audit'])
    save('summary.json', dict(passed=True, stages=[c['label'] for c in commands], formal_target_backend_runs=1,
        hardware_calibrated=False, complete_64_step_service_executed=False))
    print('PASS all serialized target/user acceptance stages', flush=True)


if __name__ == '__main__':
    main()
