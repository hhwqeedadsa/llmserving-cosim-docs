"""Admit immutable old grace evidence, freeze byte-identical application inputs."""
import argparse
from pathlib import Path
import subprocess
from experiments.reordering_baseline.checkpoint import admit
from experiments.nic_wakeup_effect.stage import load, save, sha


def main():
    p = argparse.ArgumentParser()
    for k in ('repository', 'archive', 'output'): p.add_argument('--'+k, type=Path, required=True)
    a = p.parse_args(); repo = a.repository.resolve(); assert not a.output.exists()
    assert sha(a.archive) == 'b4b1c78b8e2d65e5e6a88fd005f1d8ee7890703f8219ae7c26659756ae238d6d'
    payload, manifest = admit(a.archive)
    out = a.output; out.mkdir()
    def copy(name, dest):
        dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(payload[name])
    obs = out/'observed'
    for name in ('g0.w0.json', 'g0.w1.json', 'topology.json', 'grace-plan.json',
                 'healthy-protocol.json', 'live-delayed-protocol.json', 'live-instant-protocol.json'):
        copy('evidence/inputs/observed/'+name, obs/name)
    copy('evidence/topology/topology.json', out/'baseline/topology.json')
    for case in ('healthy', 'live-delayed', 'live-instant'):
        for name in ('work.json', 'mapping.json'):
            copy('evidence/planned/'+case+'/'+name, out/'baseline'/case/name)
        copy('evidence/network/'+case+'/result.json', out/'baseline'/case/'result.json')
    for name in manifest['files']:
        for prefix in ('evidence/inputs/truth/', 'evidence/inputs/source-inputs/'):
            if name.startswith(prefix): copy(name, out/name[len('evidence/inputs/'):])
    for name in ('source-train.json', 'summary.json'):
        copy('evidence/'+name, out/('old-'+name))
    platform = subprocess.check_output(['git', 'rev-parse', 'eaba713'], cwd=repo, text=True).strip()
    subprocess.run(['git', 'diff', '--exit-code', platform, '--', 'python', 'backend', 'deps.lock.json', '.gitmodules'], cwd=repo, check=True)
    save(obs/'index.json', dict(schema_version='nic-wakeup-effect-input/1', platform_commit=platform,
        waves=['g0.w0.json', 'g0.w1.json'], plan='grace-plan.json', cases=['healthy', 'live-delayed', 'live-instant'],
        scope='four fixed MMLU requests, two layers/two steps legacy contribution-copy and declared compute; no modern calibration or holdout'))
    save(out/'provenance.json', dict(archive_sha256=sha(a.archive), members_verified=len(payload),
        files={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()}))
    print('three frozen application inputs and immutable old results prepared; no networks')


if __name__ == '__main__': main()
