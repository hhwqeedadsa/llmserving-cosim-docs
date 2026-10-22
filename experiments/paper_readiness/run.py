"""Screen whether a claimed bottleneck depends on uncalibrated software credits."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
MODES = {
    'small': dict(policy='destination_round_robin', max_active_per_source=3, max_active_bytes_per_source=16384, decision_ns=0),
    'slots_only': dict(policy='destination_round_robin', max_active_per_source=3, max_active_bytes_per_source=0, decision_ns=0),
    'nonbinding': dict(policy='destination_round_robin', max_active_per_source=64, max_active_bytes_per_source=0, decision_ns=0),
    'passthrough': dict(policy='passthrough', max_active_per_source=0, max_active_bytes_per_source=0, decision_ns=0),
}


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def case(batch, mode, fault, targets):
    protocol = dict(admission=deepcopy(MODES[mode]))
    if fault == 'single':
        t = targets[str(batch)]
        protocol['faults'] = [dict(node=t['src'], port=0, message_id=t['id'], kind='ack', sequence=t['bytes'],
                                   count=1, start_ns=0, end_ns=5000000)]
    return dict(name=f'b{batch}-{mode}-{fault}', expect='success', comparison_group=f'batch{batch}',
                patches=dict(service=dict(workload=dict(batch_per_rank=batch)), protocol=protocol))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend', required=True, type=Path)
    p.add_argument('--protoc', required=True)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    out = a.output.resolve()
    assert shutil.disk_usage(out.parent).free >= 8 * 1024**3
    out.mkdir(exist_ok=False)
    for name in ('service', 'topology', 'protocol'):
        shutil.copyfile(HERE / (name + '.json'), out / (name + '.json'))
    commands = []

    def sweep(name, cases):
        assert sum(q.stat().st_size for q in out.rglob('*') if q.is_file()) < 128 * 1024**2
        save(out / (name + '.json'), dict(schema_version='moe-scaleout-sweep/1',
             base={k: str(out / (k + '.json')) for k in ('service', 'topology', 'protocol')},
             budget=dict(wall_seconds=300, rss_bytes=1073741824, address_bytes=2147483648, artifact_bytes=64*1024**2), cases=cases))
        argv = [sys.executable, '-m', 'moe_ub_cosim', 'scaleout', 'sweep', '--config', str(out / (name + '.json')),
                '--backend', str(a.backend), '--protoc', a.protoc, '--output', str(out / name)]
        with (out / (name + '.stdout')).open('w') as f, (out / (name + '.stderr')).open('w') as e:
            done = subprocess.run(argv, cwd=ROOT, stdout=f, stderr=e, timeout=330)
        commands.append(dict(argv=argv, cwd=str(ROOT), exit_code=done.returncode))
        save(out / 'commands.json', commands)
        if done.returncode:
            raise RuntimeError('Preserve failed sweep; no automatic retry or budget expansion')
        return read(out / name / 'summary.json')

    pilot = sweep('pilot', [case(b, 'small', 'none', {}) for b in (1, 4)])
    targets = {}
    for b in (1, 4):
        graph = read(out / 'pilot' / f'b{b}-small-none/run/graphs/g0.w0/wave.json')
        matches = [m for m in graph['messages'] if m['src'] == 0 and m['dst'] == 4 and m['phase'] == 'dispatch']
        assert len(matches) == 1
        targets[str(b)] = matches[0]
    cases = [case(b, mode, fault, targets) for b in (1, 4) for mode in MODES for fault in ('none', 'single')
             if not (mode == 'small' and fault == 'none')]
    matrix = sweep('matrix', cases)
    records = [*pilot['cases'], *matrix['cases']]
    assert len(records) == 16 and sum(q.stat().st_size for q in out.rglob('*') if q.is_file()) <= 128 * 1024**2
    save(out / 'experiment.json', dict(platform_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
         targets=targets, modes=MODES, cases={r['name']: dict(metrics=r, run=str(Path('pilot' if r['name'].endswith('small-none') else 'matrix') / r['name'] / 'run')) for r in records},
         source_hashes={str(q.relative_to(ROOT)): hashlib.sha256(q.read_bytes()).hexdigest() for q in HERE.iterdir() if q.suffix in ('.py', '.json')},
         budget=dict(study_bytes=128*1024**2, sweep_bytes=64*1024**2, sweep_wall_seconds=300),
         hypotheses=['removing byte cap reduces admission-mediated fault propagation', 'three slots alone may retain blocking',
                     'nonbinding RR and passthrough should expose the native network rather than software credit bottlenecks'],
         scope='mechanism sensitivity screen; no hardware calibration, no new algorithm, no publication-readiness claim'))
    print('16 credit-sensitivity conditions completed via public CLI')


if __name__ == '__main__':
    main()
