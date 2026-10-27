"""Linux wait4 CPU for one complete command; runs only under a bounded probe."""
import argparse
import os
from pathlib import Path
import subprocess
import time

from .data import save


def measure(command, output):
    assert command and not output.exists()
    before = Path('/proc/loadavg').read_text().strip()
    affinity = sorted(os.sched_getaffinity(0)); start = time.perf_counter_ns()
    process = subprocess.Popen(command)
    pid, status, usage = os.wait4(process.pid, 0)
    end = time.perf_counter_ns(); assert pid == process.pid
    process.returncode = os.waitstatus_to_exitcode(status)
    names = ('ru_utime', 'ru_stime', 'ru_maxrss', 'ru_minflt', 'ru_majflt',
             'ru_inblock', 'ru_oublock', 'ru_nvcsw', 'ru_nivcsw')
    record = dict(schema_version='complete-workflow-wait4/1', command=command, cwd=str(Path.cwd()),
        pid=pid, raw_wait_status=status, exit_code=process.returncode, rusage={n: getattr(usage, n) for n in names},
        start_perf_ns=start, end_perf_ns=end, elapsed_wall_ns=end-start,
        subtree_CPU_ns=round((usage.ru_utime+usage.ru_stime)*10**9),
        max_single_process_rss_kib=usage.ru_maxrss, affinity=affinity,
        loadavg_before=before, loadavg_after=Path('/proc/loadavg').read_text().strip(),
        includes_waited_descendants=True, exact_group_peak_rss=False)
    save(output, record)
    assert process.returncode == 0, 'preserve failed complete command'


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--output', type=Path, required=True)
    p.add_argument('command', nargs=argparse.REMAINDER); a = p.parse_args()
    command = a.command[1:] if a.command[:1] == ['--'] else a.command
    measure(command, a.output.resolve())
