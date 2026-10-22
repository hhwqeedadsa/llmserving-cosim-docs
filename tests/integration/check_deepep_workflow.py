"""Fresh standard-library-only public CLI workflow and qualitative counterexamples."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PYTHONPATH=str(ROOT/'python'), PYTHONDONTWRITEBYTECODE='1')
    shutil.copy2(ROOT/'examples/deepep_partial_order/input.json', out/'input.json')
    commands = []

    def cli(name, argv, expected=0):
        command = [sys.executable, '-m', 'moe_ub_cosim', 'scaleout', *argv]
        start = time.monotonic()
        with (out/(name+'.stdout')).open('w') as stdout, (out/(name+'.stderr')).open('w') as stderr:
            result = subprocess.run(command, cwd=out, env=env, stdout=stdout, stderr=stderr, timeout=30)
        commands.append(dict(argv=command, cwd=str(out), exit_code=result.returncode,
                             expected_exit=expected, wall_seconds=time.monotonic()-start))
        (out/'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
        assert result.returncode == expected, name
        return json.loads((out/(name+'.stdout')).read_text()) if expected == 0 else None

    generated = cli('generate', ['deepep-plan', '--config', 'input.json', '--output', 'plan'])
    assert generated['state'] == 'offline_partial_order_only' and not generated['simulated_packets']
    checks = {}
    for name, event in [('normal', None), ('zero_count', 'm0.d.count.s0.e4.visible'),
                        ('one_flag', 'm0.c.flag.d0.e2.visible'),
                        ('sender_done', 'm0.d.data.s0.t0.e2.sender_done')]:
        args = ['deepep-check', '--plan', 'plan', '--output', name+'.json']
        if event:
            args += ['--withhold', event]
        checks[name] = cli(name, args)
    assert checks['normal']['all_kernel_completions_reachable']
    assert not checks['zero_count']['all_kernel_completions_reachable']
    assert set(f'm0.c.r{r}.complete' for r in range(4)) <= set(checks['zero_count']['blocked_kernel_completions'])
    assert 'm0.c.r0.complete' in checks['one_flag']['blocked_kernel_completions']
    assert all(f'm0.c.r{r}.complete' in checks['one_flag']['reachable_kernel_completions'] for r in (1, 2, 3))
    assert checks['sender_done']['all_kernel_completions_reachable']
    assert not checks['sender_done']['source_buffer_reuse_validated']
    cli('unknown', ['deepep-check', '--plan', 'plan', '--output', 'unknown.json', '--withhold', 'bad-event'], expected=1)
    assert not (out/'unknown.json').exists()
    shutil.copytree(out/'plan', out/'relocated')
    portable = cli('portable', ['deepep-check', '--plan', 'relocated', '--output', 'portable.json'])
    assert portable == checks['normal']
    path = out/'relocated/plan.json'
    path.write_bytes(path.read_bytes()+b' ')
    cli('tamper', ['deepep-check', '--plan', 'relocated', '--output', 'tampered.json'], expected=1)
    assert not (out/'tampered.json').exists()
    report = dict(passed=True, public_commands=len(commands), expected_rejections=2,
                  fresh_cwd=True, frontend_or_native_backend_required=False,
                  portable_check_identical=True, simulated_packets=False, hardware_calibrated=False,
                  event_count=generated['event_count'], operation_count=generated['operation_count'])
    (out/'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
