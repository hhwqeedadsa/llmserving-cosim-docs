"""Build the posting policy and run independent CPU checks with an existing compiler."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]


def child_limits():
    resource.setrlimit(resource.RLIMIT_AS, (2*2**30, 2*2**30))
    resource.setrlimit(resource.RLIMIT_CPU, (80, 80))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cxx', default='c++')
    args = parser.parse_args()
    compiler = shutil.which(args.cxx)
    if compiler is None:
        raise ValueError('Existing C++17 compiler required; no install attempted')
    out = args.output.resolve()
    if out.exists() or shutil.disk_usage(out.parent).free < 8*2**30+16*2**20:
        raise ValueError('A new directory and 8GiB free reserve are required')
    out.mkdir()
    started = time.monotonic()
    result = dict(state='started', commands=[], cwd=str(ROOT), network_execution=False,
                  hardware_calibrated=False, GPU_execution=False, sources={})
    for name in ('backend/rdma/gin_work_queue.hh', 'backend/rdma/low_latency_posting.hh',
                 'tests/unit/low_latency_posting_test.cc', 'examples/low_latency_posting/demo.cc',
                 'tests/integration/check_low_latency_posting.py'):
        result['sources'][name] = hashlib.sha256((ROOT/name).read_bytes()).hexdigest()

    def save():
        (out/'verification.json').write_text(json.dumps(result, indent=2)+'\n')

    def run(label, command, expected=0):
        remaining = 115-(time.monotonic()-started)
        if remaining <= 0:
            raise TimeoutError('Posting check total deadline exhausted')
        before = time.monotonic()
        with (out/(label+'.stdout')).open('x') as stdout, (out/(label+'.stderr')).open('x') as stderr:
            process = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr,
                                     timeout=remaining, preexec_fn=child_limits)
        result['commands'].append(dict(label=label, argv=list(map(str, command)),
            exit_code=process.returncode, expected_exit_code=expected, seconds=time.monotonic()-before))
        save()
        if process.returncode != expected:
            raise RuntimeError(label+' failed; preserve logs and do not retry automatically')
        if sum(p.stat().st_size for p in out.iterdir() if p.is_file()) > 16*2**20:
            raise ValueError('Posting check output limit exceeded')

    save()
    try:
        run('compiler', [compiler, '--version'])
        common = [compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', '-pedantic', '-I'+str(ROOT/'backend')]
        run('build-unit', common+[str(ROOT/'tests/unit/low_latency_posting_test.cc'), '-o', str(out/'unit')])
        run('unit', [str(out/'unit')])
        result['unit'] = json.loads((out/'unit.stdout').read_text())
        assert result['unit']['passed'] and result['unit']['network_execution'] is False
        run('build-demo', common+[str(ROOT/'examples/low_latency_posting/demo.cc'), '-o', str(out/'demo')])
        workflows = []
        for count in (0, 1, 3, 4, 5, 8, 9):
            for layout in (1, 2, 3):
                label = f'demo-{count}-{layout}'
                run(label, [str(out/'demo'), str(count), str(layout)])
                value = json.loads((out/(label+'.stdout')).read_text())
                assert value['passed'] and value['WQEs_per_put'] == layout
                assert value['before_notification']['published'] == count//4*4*layout
                assert value['before_notification']['reserved'] == count*layout
                assert value['after_notification']['published'] == count*layout+1
                assert value['consumed_before_completion'] == count*layout+1
                assert value['completed_before_supplied_callback'] == 0
                assert value['closed'] and value['completion_supplied_by_demo']
                assert not value['network_execution'] and not value['hardware_calibrated']
                workflows.append(value)
        for label, count, layout in [('negative', '-1', '1'), ('large', '64', '1'), ('layout', '1', '4')]:
            run('reject-'+label, [str(out/'demo'), count, layout], expected=1)
        result['workflow_cases'] = workflows
        result['invalid_workflows_rejected'] = 3
        result['peak_child_rss_kib'] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        if result['peak_child_rss_kib'] > 2**20:
            raise ValueError('Compiler/check RSS exceeded 1GiB')
        result['state'] = 'passed'
    except Exception as error:
        result.update(state='failed', reason=str(error))
        raise
    finally:
        result['wall_seconds'] = time.monotonic()-started
        save()
    print(json.dumps(dict(passed=True, checks=result['unit']['checks'],
        oracle_states=result['unit']['oracle_states'], workflow_cases=len(result['workflow_cases']),
        invalid_workflows_rejected=3, network_execution=False)))


if __name__ == '__main__':
    main()
