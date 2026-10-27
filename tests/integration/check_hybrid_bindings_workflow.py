"""Fresh-cwd public binding workflow, evidence rejection and source relocation."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from moe_ub_cosim.hybrid_deepep_bindings import load_bindings, manifest
from moe_ub_cosim.deepep_plan import encoded

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    out = parser.parse_args().output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    for name in ('input.json', 'provider.json'):
        shutil.copyfile(ROOT/'examples/hybrid_bindings'/name, out/name)
    env = os.environ | {'PYTHONPATH': str(ROOT/'python'), 'PYTHONDONTWRITEBYTECODE': '1'}
    commands = []

    def save(name, value):
        (out/name).write_bytes(encoded(value))

    def cli(label, arguments, code=0, source=ROOT):
        command = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', *arguments]
        result = subprocess.run(command, cwd=out, env=env | {'PYTHONPATH': str(source/'python')},
                                capture_output=True, timeout=10)
        (out/(label+'.stdout')).write_bytes(result.stdout)
        (out/(label+'.stderr')).write_bytes(result.stderr)
        commands.append(dict(label=label, command=command, cwd=str(out), source=str(source),
                             exit_code=result.returncode, expected=code))
        save('commands.json', commands)
        assert result.returncode == code, (label, result.stderr.decode())

    cli('logical', ['hybrid-deepep-plan', '--config', 'input.json', '--output', 'logical'])
    bind = ['hybrid-deepep-bind', '--plan', 'logical', '--provider', 'provider.json']
    cli('bind', bind+['--output', 'bound'])
    bound, config, provider = load_bindings(out/'bound')
    # Hand-written input has 15 domain data copies, 32 count blocks and 64 finish updates.
    assert len(bound['bindings']) == 111 and len(bound['resources']) == 16
    assert len(bound['data_streams']) == 64 and len(bound['count_sequences']) == 16
    assert len({row['id'] for row in bound['bindings']}) == 111
    assert sum(row['kind'] == 'token_data' for row in bound['bindings']) == 15
    assert sum(row['kind'] == 'finish' for row in bound['bindings']) == 64
    assert all(not value for value in bound['scope'].values())
    assert bound['allocated_contexts'] == 4
    original = {p.name: p.read_bytes() for p in (out/'bound').iterdir()}
    cli('repeat', bind+['--output', 'repeat'])
    assert original == {p.name: p.read_bytes() for p in (out/'repeat').iterdir()}
    cli('existing-output', bind+['--output', 'bound'], 1)
    assert original == {p.name: p.read_bytes() for p in (out/'bound').iterdir()}

    rejected = []
    for label, key, value in (('handler', 'nic_handler', 'auto'),
                              ('stride', 'gin_connection_stride', 3),
                              ('bool', 'connections', True),
                              ('devices', 'local_devices', [[10, 10], [20], [30], [40]]),
                              ('budget', 'limits', provider['limits'] | {'max_resources': 1})):
        bad = deepcopy(provider); bad[key] = value
        save(label+'.json', bad)
        cli(label, ['hybrid-deepep-bind', '--plan', 'logical', '--provider', label+'.json',
                    '--output', 'rejected-'+label], 1)
        assert not (out/('rejected-'+label)).exists()
        rejected.append(label)
    # A previously accepted logical SM1 example stays valid there, rejected only at host binding.
    bad = deepcopy(config); bad['execution']['num_sms'] = 1
    save('abstract.json', bad)
    cli('abstract-plan', ['hybrid-deepep-plan', '--config', 'abstract.json', '--output', 'abstract'])
    cli('host-shape', ['hybrid-deepep-bind', '--plan', 'abstract', '--provider', 'provider.json',
                       '--output', 'rejected-shape'], 1)
    assert not (out/'rejected-shape').exists(); rejected.append('host-shape')
    shutil.copytree(out/'logical', out/'corrupt-logical')
    (out/'corrupt-logical/plan.json').write_bytes(b'{}\n')
    cli('logical-integrity', ['hybrid-deepep-bind', '--plan', 'corrupt-logical', '--provider', 'provider.json',
                              '--output', 'rejected-logical'], 1)
    assert not (out/'rejected-logical').exists(); rejected.append('logical-integrity')

    for label in ('hash', 'binding', 'provider-rehash', 'scope'):
        destination = out/('corrupt-'+label)
        shutil.copytree(out/'bound', destination)
        files = {name: raw for name, raw in original.items() if name != 'manifest.json'}
        if label == 'binding':
            value = deepcopy(bound); value['bindings'][0]['resource'] = None
            files['bindings.json'] = encoded(value)
        elif label == 'scope':
            value = deepcopy(bound); value['scope']['network_executed'] = True
            files['bindings.json'] = encoded(value)
        elif label == 'provider-rehash':
            value = deepcopy(provider); value.update(call_contexts=1, requested_contexts=1)
            files['provider.json'] = encoded(value)
        else:
            files['input.json'] += b' '
        for name, raw in files.items():
            (destination/name).write_bytes(raw)
        if label != 'hash':
            (destination/'manifest.json').write_bytes(encoded(manifest(files)))
        try:
            load_bindings(destination)
        except ValueError:
            rejected.append(label)
        else:
            raise AssertionError('accepted evidence corruption '+label)

    copied = out/'copied-source'
    shutil.copytree(ROOT/'python', copied/'python', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    cli('relocated-logical', ['hybrid-deepep-plan', '--config', 'input.json', '--output', 'relocated-logical'], source=copied)
    cli('relocated-bind', ['hybrid-deepep-bind', '--plan', 'relocated-logical', '--provider', 'provider.json',
                          '--output', 'relocated'], source=copied)
    assert original == {p.name: p.read_bytes() for p in (out/'relocated').iterdir()}
    # Move the bundle away from the original plan; loading needs only its four files.
    shutil.copytree(out/'bound', out/'moved')
    assert load_bindings(out/'moved')[0] == bound
    summary = dict(passed=True, bindings=111, active_resources=16, calls=2,
                   count_sequences=16, data_streams=64, allocated_contexts=4,
                   rejected=rejected, new_networks=0, repeat_byte_identical=True,
                   copied_source_byte_identical=True, standalone_bundle_load=True,
                   sources={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in (ROOT/'python/moe_ub_cosim/hybrid_deepep_bindings.py', ROOT/'python/moe_ub_cosim/cli.py')})
    save('verification.json', summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
