"""Snapshot exact inputs/code and rerun CPU accounting from an empty cwd."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tarfile


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (2*2**30, 2*2**30))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    assert not a.output.exists()
    a.output.mkdir()
    snapshot = a.output / 'snapshot'
    assert shutil.disk_usage(a.run).free >= 8*2**30 + 64*2**20
    snapshot.mkdir()
    manifest_rel = Path('experiments/moe_trace_hotspots/results/four_requests_20261018/manifest.json')
    manifest = json.loads((a.repo / manifest_rel).read_text())
    paths = [manifest_rel, Path('docs/reviews/MoE在途恢复价值与状态存活判别_20261023.md')]
    paths += [Path(r['file']['path']) for r in manifest['files']]
    paths += [p.relative_to(a.repo) for p in (a.repo/'experiments/recovery_value').iterdir() if p.suffix in ('.py', '.md')]
    for path in paths:
        target = snapshot / 'source' / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(a.repo/path, target)
        assert digest(a.repo/path) == digest(target)
    for folder in ('account', 'sources'):
        shutil.copytree(a.run/folder, snapshot/'reference'/folder)
    shutil.copyfile(a.run/'verification.json', snapshot/'reference'/'verification.json')
    shutil.copyfile(a.run/'contribution-identity.json', snapshot/'reference'/'contribution-identity.json')
    members = {str(p.relative_to(snapshot)): digest(p) for p in snapshot.rglob('*') if p.is_file()}
    (snapshot/'members.json').write_text(json.dumps(members, sort_keys=True, indent=2)+'\n')
    assert sum(p.stat().st_size for p in snapshot.rglob('*') if p.is_file()) < 32*2**20
    archive = a.output/'checkpoint.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for path in sorted(snapshot.rglob('*')):
            if path.is_file(): tar.add(path, arcname=str(path.relative_to(snapshot)), recursive=False)
    assert archive.stat().st_size < 16*2**20
    restored = a.output/'restored'
    restored.mkdir()
    with tarfile.open(archive) as tar:
        for item in tar.getmembers():
            # This archive was generated above; still reject traversal and links.
            assert item.isfile() and not Path(item.name).is_absolute() and '..' not in Path(item.name).parts
        tar.extractall(restored, filter='data')
    for name, value in members.items(): assert digest(restored/name) == value
    empty = a.output/'empty-cwd'; empty.mkdir()
    repo = restored/'source'
    scripts = repo/'experiments/recovery_value'
    commands = [
        [sys.executable, '-B', str(scripts/'account.py'), '--repo', str(repo),
         '--manifest', str(repo/manifest_rel), '--output', str(a.output/'recomputed')],
        [sys.executable, '-B', str(scripts/'verify.py'), '--repo', str(repo),
         '--manifest', str(repo/manifest_rel), '--account', str(a.output/'recomputed'),
         '--output', str(a.output/'reverification.json')],
        [sys.executable, '-B', str(scripts/'contribution_identity.py'),
         '--output', str(a.output/'contribution-identity.json')],
    ]
    executions = []
    for index, command in enumerate(commands):
        with (a.output/f'reproduce-{index}.stdout.txt').open('w') as stdout, (a.output/f'reproduce-{index}.stderr.txt').open('w') as stderr:
            r = subprocess.run(command, cwd=empty, stdout=stdout, stderr=stderr, timeout=25, check=False)
        executions.append(dict(command=command, cwd=str(empty), exit_code=r.returncode))
        assert r.returncode == 0
    equal = {name: digest(a.run/'account'/name) == digest(a.output/'recomputed'/name)
             for name in ('summary.json', 'inputs.json', 'cases.csv')}
    equal['verification.json'] = digest(a.run/'verification.json') == digest(a.output/'reverification.json')
    equal['contribution-identity.json'] = digest(a.run/'contribution-identity.json') == digest(a.output/'contribution-identity.json')
    assert all(equal.values())
    result = dict(passed=True, members_checked=len(members), archive_bytes=archive.stat().st_size,
                  archive_sha256=digest(archive), deterministic_outputs_equal=equal, executions=executions,
                  storage='tmpfs; volatile, no persistent backup', offline=True, gpu_executed=False,
                  scope='CPU work/representation bounds; no network or official documentation refetch')
    (a.output/'reproduction.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__': main()
