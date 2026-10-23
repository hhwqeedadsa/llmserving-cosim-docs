"""Record bounded experiment commands, without shell evaluation or dependency changes."""
import argparse
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--name', required=True)
    p.add_argument('command', nargs=argparse.REMAINDER)
    a = p.parse_args()
    command = a.command[1:] if a.command[:1] == ['--'] else a.command
    assert command and a.name.replace('-', '').isalnum()
    assert a.run.is_dir() and shutil.disk_usage(a.run).free >= 8 * 2**30 + 64 * 2**20
    log = a.run / (a.name + '.execution.json')
    assert not log.exists(), 'do not overwrite prior attempts'
    earlier = [json.loads(f.read_text()) for f in a.run.glob('*.execution.json')]
    elapsed_before = sum(r['wall_seconds'] for r in earlier)
    # Source acquisition was bounded separately; conservatively reserve its full 60s.
    remaining = 180 - 60 - elapsed_before
    assert remaining > 0
    started = time.monotonic()
    peak = 0; reason = None
    with (a.run / (a.name + '.stdout.txt')).open('w') as stdout, (a.run / (a.name + '.stderr.txt')).open('w') as stderr:
        child = subprocess.Popen(command, stdout=stdout, stderr=stderr, start_new_session=True)
        while child.poll() is None:
            try:
                status = Path('/proc') / str(child.pid) / 'status'
                data = status.read_text()
                rss = next(int(line.split()[1])*1024 for line in data.splitlines() if line.startswith('VmRSS:'))
                peak = max(peak, rss)
            except (FileNotFoundError, StopIteration): pass
            size = sum(f.stat().st_size for f in a.run.rglob('*') if f.is_file())
            if time.monotonic()-started > min(60, remaining): reason = 'wall_budget'
            elif peak > 2**30: reason = 'rss_budget'
            elif size > 128*2**20: reason = 'disk_budget'
            if reason:
                os.killpg(child.pid, signal.SIGKILL); break
            time.sleep(0.05)
        code = child.wait()
    row = dict(command=command, cwd=str(Path.cwd()), exit_code=code, reason=reason,
               wall_seconds=time.monotonic()-started, peak_sampled_rss_bytes=peak,
               child_ru_maxrss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
               source_acquisition_reserved_seconds=60, preceding_stage_seconds=elapsed_before,
               limits=dict(wall_seconds=min(60, remaining), rss_bytes=2**30, cumulative_bytes=128*2**20))
    log.write_text(json.dumps(row, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(row))
    raise SystemExit(code if code else (1 if reason else 0))


if __name__ == '__main__': main()
