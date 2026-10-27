"""Public source-only Hybrid workflow in an empty cwd; never starts a backend."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--resume-prepared', action='store_true', help='Continue only the preserved three-command fixture failure; does not repeat successful preparation')
    a = p.parse_args()
    out = a.output.resolve()
    if not a.resume_prepared:
        out.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PYTHONPATH=str(ROOT/'python'), PYTHONDONTWRITEBYTECODE='1')
    commands = []
    if a.resume_prepared:
        commands = json.loads((out/'commands.json').read_text())
        assert [(r['label'], r['exitcode']) for r in commands] == [('plan', 0), ('prefix', 0), ('complete', 1)]
        assert (out/'failed-checker.py').is_file() and not (out/'complete-result.json').exists()
        for name in ('input.json', 'prefix.json'):
            assert (out/name).read_bytes() == (ROOT/'examples/hybrid_logical'/name).read_bytes()
    else:
        for name in ('input.json', 'prefix.json'):
            shutil.copy2(ROOT/'examples/hybrid_logical'/name, out/name)

    def save(name, value):
        (out/name).write_text(json.dumps(value, separators=(',', ':'))+'\n')

    def load(name):
        return json.loads((out/name).read_text())

    def cli(label, args, code=0, source=ROOT):
        command = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', *args]
        run_env = dict(env, PYTHONPATH=str(source/'python'))
        r = subprocess.run(command, cwd=out, env=run_env, capture_output=True, timeout=10)
        (out/(label+'.stdout')).write_bytes(r.stdout)
        (out/(label+'.stderr')).write_bytes(r.stderr)
        commands.append(dict(label=label, command=command, cwd=str(out), pythonpath=run_env['PYTHONPATH'],
                             exitcode=r.returncode, expected_exitcode=code))
        save('commands.json', commands)
        assert r.returncode == code, (label, r.stderr.decode())

    if not a.resume_prepared:
        cli('plan', ['hybrid-deepep-plan', '--config', 'input.json', '--output', 'plan'])
        cli('prefix', ['hybrid-deepep-replay', '--plan', 'plan', '--actions', 'prefix.json', '--output', 'prefix-result.json'])
    prefix = load('prefix-result.json')
    steps = [r for r in prefix['log'] if r['kind'] == 'forward_step']
    assert [r['status'] for r in steps] == ['waiting_for_selected_peer', 'waiting_for_selected_peer',
                                          'finished_empty_peer', 'chunk_admitted']
    assert steps[-1]['admitted'] == [f'h0.s0.t{i}.d1' for i in range(6)]
    assert not prefix['all_forwarder_admissions_complete']
    plan = load('plan/plan.json')
    # Independent expansion of the hand-written input: 7+2+1 domain sends,
    # 14+4+2 GPU forwards, 10 domain-reduced returns and 16 count blocks.
    assert [len(plan[k]) for k in ('dispatch', 'forwards', 'combine', 'count_blocks', 'channels')] == [10, 20, 10, 16, 8]
    assert sum(c['path'] == 'gin_self' for c in plan['count_blocks']) == 8
    assert plan['scope']['complete_collective_dag'] is False
    actions = [dict(kind='data_visible', transfer=d['id']) for d in plan['dispatch']]
    actions += [dict(kind='tail_visible', channel=c['id'], publication=i)
                for c in plan['channels'] for i in range(len(c['publications']))]
    # All tails visible first: the initially selected peer is domain 1.
    # After that first load, other finished-empty peers are already excluded.
    for ingress in range(4):
        counts = [len(c['transfers']) for c in plan['channels'] if c['target'] == ingress]
        for _ in range(sum((n+5)//6 for n in counts)+int(counts[1] == 0)):
            actions.append(dict(kind='forward_step', call=0, ingress=ingress, channel=0))
    complete_name = 'complete-corrected' if a.resume_prepared else 'complete'
    save(complete_name+'.json', dict(schema_version='moe-hybrid-progress-actions/1', actions=actions))
    cli(complete_name, ['hybrid-deepep-replay', '--plan', 'plan', '--actions', complete_name+'.json', '--output', 'complete-result.json'])
    complete = load('complete-result.json')
    assert complete['all_forwarder_admissions_complete'] and not complete['unpublished_channels']
    assert sum(len(f['admissions']) for f in complete['forwarders']) == 10
    assert all(len(r['admitted']) <= 6 for r in complete['log'] if r['kind'] == 'forward_step')

    cli('repeat-plan', ['hybrid-deepep-plan', '--config', 'input.json', '--output', 'repeat'])
    cli('repeat-prefix', ['hybrid-deepep-replay', '--plan', 'repeat', '--actions', 'prefix.json', '--output', 'repeat-prefix.json'])
    for name in ('input.json', 'plan.json', 'manifest.json'):
        assert (out/'plan'/name).read_bytes() == (out/'repeat'/name).read_bytes()
    assert (out/'prefix-result.json').read_bytes() == (out/'repeat-prefix.json').read_bytes()

    bad = deepcopy(load('input.json')); bad['deployment']['rails'][0]['ranks'] = [0, 3]; save('wrong-rail.json', bad)
    cli('wrong-rail', ['hybrid-deepep-plan', '--config', 'wrong-rail.json', '--output', 'wrong-rail'], 1)
    assert not (out/'wrong-rail').exists()
    bad = deepcopy(load('input.json')); bad['limits']['max_rows'] = 1; save('row-limit.json', bad)
    cli('row-limit', ['hybrid-deepep-plan', '--config', 'row-limit.json', '--output', 'row-limit'], 1)
    assert not (out/'row-limit').exists()
    bad = deepcopy(load('input.json')); bad['limits']['max_input_bytes'] = 1; save('input-limit.json', bad)
    cli('input-limit', ['hybrid-deepep-plan', '--config', 'input-limit.json', '--output', 'input-limit'], 1)
    assert not (out/'input-limit').exists()
    for name, action in (
        ('early-tail', dict(kind='tail_visible', channel='h0.s0.d1.c0', publication=0)),
        ('skipped-tail', dict(kind='tail_visible', channel='h0.s0.d1.c0', publication=1)),
        ('unknown', dict(kind='data_visible', transfer='no-such-token')),
        ('bad-type', dict(kind='forward_step', call=True, ingress=2, channel=0)),
    ):
        save(name+'.json', dict(schema_version='moe-hybrid-progress-actions/1', actions=[action]))
        cli(name, ['hybrid-deepep-replay', '--plan', 'plan', '--actions', name+'.json', '--output', name+'-result.json'], 1)
        assert not (out/(name+'-result.json')).exists()
    # Hash failure and a maliciously rehashed semantic change are both rejected.
    shutil.copytree(out/'plan', out/'tampered')
    path = out/'tampered/plan.json'; value = load('tampered/plan.json'); value['dispatch'][0]['ingress'] = 3
    path.write_text(json.dumps(value, separators=(',', ':'))+'\n')
    cli('hash-tamper', ['hybrid-deepep-replay', '--plan', 'tampered', '--actions', 'prefix.json', '--output', 'hash-tamper.json'], 1)
    record = load('tampered/manifest.json'); record['sha256']['plan.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
    save('tampered/manifest.json', record)
    cli('semantic-tamper', ['hybrid-deepep-replay', '--plan', 'tampered', '--actions', 'prefix.json', '--output', 'semantic-tamper.json'], 1)
    shutil.copytree(out/'plan', out/'scope-tamper')
    record = load('scope-tamper/manifest.json'); record['scope']['network_executed'] = 0
    save('scope-tamper/manifest.json', record)
    cli('scope-tamper', ['hybrid-deepep-replay', '--plan', 'scope-tamper', '--actions', 'prefix.json', '--output', 'scope-tamper.json'], 1)
    assert not any((out/n).exists() for n in ('hash-tamper.json', 'semantic-tamper.json', 'scope-tamper.json'))

    # Source-only relocation: exact files, hashes checked, no old build/runtime.
    copied = out/'copied-source'
    shutil.copytree(ROOT/'python/moe_ub_cosim', copied/'python/moe_ub_cosim', ignore=shutil.ignore_patterns('__pycache__'))
    for path in (copied/'python/moe_ub_cosim').rglob('*'):
        if path.is_file(): assert path.read_bytes() == (ROOT/'python/moe_ub_cosim'/path.relative_to(copied/'python/moe_ub_cosim')).read_bytes()
    cli('relocated-plan', ['hybrid-deepep-plan', '--config', 'input.json', '--output', 'relocated'], source=copied)
    cli('relocated-prefix', ['hybrid-deepep-replay', '--plan', 'relocated', '--actions', 'prefix.json', '--output', 'relocated-prefix.json'], source=copied)
    for name in ('input.json', 'plan.json', 'manifest.json'):
        assert (out/'plan'/name).read_bytes() == (out/'relocated'/name).read_bytes()
    assert (out/'prefix-result.json').read_bytes() == (out/'relocated-prefix.json').read_bytes()
    result = dict(passed=True, public_commands=len(commands), expected_rejections=10,
        preserved_fixture_failure=a.resume_prepared,
        independent_expected_counts=dict(dispatch=10, forwards=20, combine=10, count_blocks=16, channels=8),
        gin_self_count_blocks=8, repeat_byte_identical=True, source_relocation_byte_identical=True,
        prefix_admissions=6, complete_admissions=10, network_executed=False, complete_collective_executed=False,
        source_sha256={str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in (ROOT/'python/moe_ub_cosim/hybrid_deepep_plan.py',
                                    ROOT/'python/moe_ub_cosim/hybrid_deepep_progress.py',
                                    ROOT/'python/moe_ub_cosim/cli.py')})
    save('verification.json', result)
    print(json.dumps(result))


if __name__ == '__main__': main()
