"""Bounded public-API research execution, frozen inputs and failure-stop ledger."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from experiments.compact_network_choice.evaluate import modern_metrics
from experiments.shared_uplink_choice.evaluate import compare
from experiments.readiness_network_choice.prepare import PROFILES, CONDITIONS, load, save, sha
from experiments.readiness_network_choice.verify import input_checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo, output = args.repository.resolve(), args.output.resolve()
    output.mkdir()
    stage, monitor = output/'stage', output/'monitor'
    stage.mkdir(); monitor.mkdir(); (stage/'empty-cwd').mkdir()
    env = os.environ | {'PYTHONPATH': str(repo/'python')+':'+str(repo), 'PYTHONDONTWRITEBYTECODE': '1'}
    ledger = dict(state='started', closed=False, records=[], network_attempt_limit=24, reruns=0)

    def runtime_check():
        identity = load(stage/'runtime-identity.json')
        assert all(sha(stage/'runtime'/n) == d for n, d in identity['files'].items())
        assert all(sha(repo/n) == d for n, d in identity['sources'].items())

    def run(label, argv, network=False):
        assert not ledger['closed'] and sum(r['network'] for r in ledger['records'])+int(network) <= 24
        seconds = sum(load(path).get('wall_seconds', 0) for path in output.rglob('probe.json'))
        size = sum(path.stat().st_size for path in output.rglob('*') if path.is_file())
        assert seconds+65 < 1800 and size+64*2**20 < 2*2**30
        assert shutil.disk_usage(output).free > 128*2**20
        if network:
            runtime_check()
            assert all(sha(stage/n) == d for n, d in load(stage/'input-identity.json').items())
        row = dict(label=label, command=list(map(str, argv)), cwd=str(stage/'empty-cwd'), network=network, state='started')
        ledger['records'].append(row)
        save(stage/'execution.json', ledger)
        command = [sys.executable, '-B', '-m', 'moe_ub_cosim.host_probe',
            '--output-dir', str(monitor/(label+'-monitor')), '--rss-bytes', str(2**30),
            '--address-bytes', str(2**31), '--artifact-bytes', str(2*2**30), '--artifact-root', str(output),
            '--wall-seconds', '60', '--min-free-bytes', str(128*2**20), '--', *map(str, argv)]
        result = subprocess.run(command, cwd=stage/'empty-cwd', env=env, timeout=65)
        row.update(state='terminal', exit_code=result.returncode)
        save(stage/'execution.json', ledger)
        assert result.returncode == 0, 'preserve failure and close remaining networks'
        print(label, 'passed', flush=True)

    def script(name, *argv):
        return [sys.executable, '-B', '-m', 'experiments.readiness_network_choice.'+name, *argv]

    cli = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout']
    try:
        run('prepare', script('prepare', '--repository', repo, '--stage', stage))
        observed = stage/'observed'
        run('plan', [*cli, 'modern-deepep-plan', '--config', observed/'modern-input.json', '--output', observed/'plan'])
        for name in CONDITIONS:
            run(name+'-topology', [*cli, 'topology', '--config', observed/(name+'-topology-config.json'),
                                  '--output', observed/(name+'-topology')])
        run('predict', script('predict', '--observed', observed, '--output', stage/'predictions'))
        save(stage/'input-preflight.json', input_checks(observed))
        save(stage/'input-identity.json', {str(path.relative_to(stage)): sha(path)
            for root in (observed, stage/'predictions') for path in root.rglob('*') if path.is_file()})
        (stage/'truth').mkdir()
        for profile in PROFILES:
            root = stage/'truth'/profile
            root.mkdir()
            for name in CONDITIONS:
                condition = root/name
                condition.mkdir()
                run(profile+'-'+name+'-modern', [*cli, 'modern-deepep-native',
                    '--topology', observed/(name+'-topology'), '--protocol', observed/(name+'-protocol.json'),
                    '--config', observed/(profile+'.json'), '--backend', stage/'runtime/run-transaction-session',
                    '--output', condition/'modern', '--callback-batches'], True)
                modern_metrics(condition/'modern')
                if name == 'baseline':
                    run(profile+'-compile', script('generic', 'compile', '--stage', stage, '--profile', profile))
                run(profile+'-'+name+'-generic', script('generic', 'run', '--stage', stage,
                    '--profile', profile, '--name', name), True)
                comparison = compare(condition, root/'baseline/modern')
                save(condition/'comparison.json', comparison)
                assert comparison['equivalent'], comparison['checks']
                print(profile, name, 'full-equivalence passed', flush=True)
        runtime_check()
        ledger.update(closed=True, state='completed')
    except BaseException as error:
        ledger.update(closed=True, state='failure_preserved_no_remaining_networks', error=str(error))
        raise
    finally:
        save(stage/'execution.json', ledger)


if __name__ == '__main__':
    main()
