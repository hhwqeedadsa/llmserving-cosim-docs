"""Build and check the untimed GIN resource core; never build/run ns-3 or CUDA."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (2*2**30, 2*2**30))
    resource.setrlimit(resource.RLIMIT_CPU, (50, 50))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path, help='New bounded check directory')
    p.add_argument('--cxx', default='c++', help='Existing C++17 compiler; never installs one')
    a = p.parse_args()
    compiler = shutil.which(a.cxx)
    if compiler is None:
        raise ValueError('Existing C++ compiler not found')
    out = a.output.resolve()
    if out.exists() or shutil.disk_usage(out.parent).free < 8*2**30+8*2**20:
        raise ValueError('Check requires a new directory and 8GiB reserve')
    out.mkdir()
    started = time.monotonic()
    result = dict(state='started', cwd=str(ROOT), commands=[], network_execution=False,
                  timed=False, hardware_calibrated=False,
                  budgets=dict(wall_seconds=60, rss_bytes=2**30, address_bytes=2*2**30,
                               output_bytes=8*2**20), sources={})
    for name in ('backend/rdma/gin_work_queue.hh', 'tests/unit/gin_work_queue_test.cc'):
        result['sources'][name] = hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    def save():
        (out/'verification.json').write_text(json.dumps(result, indent=2)+'\n')
    def run(name, argv):
        remaining = 59 - (time.monotonic()-started)
        if remaining <= 0:
            raise TimeoutError('Aggregate check deadline exhausted')
        before = time.monotonic()
        with (out/(name+'.stdout')).open('w') as stdout, (out/(name+'.stderr')).open('w') as stderr:
            process = subprocess.run(argv, cwd=ROOT, stdout=stdout, stderr=stderr,
                                     timeout=remaining, preexec_fn=limits)
        result['commands'].append(dict(argv=list(map(str, argv)), exitcode=process.returncode,
                                       wall_seconds=time.monotonic()-before))
        save()
        if process.returncode:
            raise RuntimeError(name+' failed; preserve logs, no automatic retry')
    try:
        run('compiler', [compiler, '--version'])
        run('build', [compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', '-pedantic',
                      '-I'+str(ROOT/'backend'), str(ROOT/'tests/unit/gin_work_queue_test.cc'),
                      '-o', str(out/'gin-work-queue-test')])
        run('check', [str(out/'gin-work-queue-test')])
        checks = json.loads((out/'check.stdout').read_text())
        if checks['passed'] is not True or checks['network_execution'] is not False:
            raise ValueError('Unexpected resource-core result')
        if resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss > 2**20:
            raise ValueError('Compiler/check RSS exceeded 1GiB')
        if sum(p.stat().st_size for p in out.iterdir() if p.is_file()) > 8*2**20:
            raise ValueError('Check output exceeded 8MiB')
        result['checks'] = checks
        result['state'] = 'passed'
    except Exception as error:
        result.update(state='failed', reason=str(error))
        raise
    finally:
        result['wall_seconds'] = time.monotonic()-started
        result['peak_child_rss_kib'] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        result['output_bytes_before_manifest'] = sum(p.stat().st_size for p in out.iterdir() if p.is_file())
        save()
    print(json.dumps(result))


if __name__ == '__main__':
    main()
