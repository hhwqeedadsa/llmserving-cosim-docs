"""Create/restore the source-only checkpoint and recheck public logical outputs."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def pack(repo, run, archive):
    assert not archive.exists()
    contents = {}
    for path in sorted((repo/'python/moe_ub_cosim').rglob('*.py')):
        assert path.is_file() and not path.is_symlink()
        contents['source/'+str(path.relative_to(repo))] = path.read_bytes()
    for name in ('tests/unit/hybrid_deepep_test.py', 'tests/integration/check_hybrid_logical_workflow.py',
                 'fixtures/hybrid_logical/check_sources.py', 'fixtures/hybrid_logical/checkpoint.py',
                 'examples/hybrid_logical/input.json', 'examples/hybrid_logical/prefix.json'):
        contents['source/'+name] = (repo/name).read_bytes()
    for path in sorted(run.rglob('*')):
        if path.is_file():
            assert not path.is_symlink()
            contents['records/'+str(path.relative_to(run))] = path.read_bytes()
    assert len(contents) < 1999 and sum(map(len, contents.values())) < 64*2**20
    manifest = dict(schema_version='moe-hybrid-checkpoint/1', files={
        n: dict(bytes=len(raw), sha256=digest(raw)) for n, raw in contents.items()})
    contents['manifest.json'] = (json.dumps(manifest, sort_keys=True)+'\n').encode()
    with tarfile.open(archive, 'w:gz') as tar:
        for name, raw in contents.items():
            member = tarfile.TarInfo(name); member.size = len(raw); member.mode = 0o644
            tar.addfile(member, io.BytesIO(raw))
    return digest(archive.read_bytes())


def restore_and_check(archive, sha, output):
    assert not output.exists() and archive.is_file() and digest(archive.read_bytes()) == sha
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        assert len(members) <= 2000 and sum(m.size for m in members) < 64*2**20
        assert len({m.name for m in members}) == len(members)
        for m in members:
            p = PurePosixPath(m.name)
            assert m.isfile() and not p.is_absolute() and '..' not in p.parts and str(p) == m.name
        blobs = {m.name: tar.extractfile(m).read() for m in members}
    manifest = json.loads(blobs.pop('manifest.json'))
    assert manifest['schema_version'] == 'moe-hybrid-checkpoint/1'
    assert set(manifest['files']) == set(blobs)
    for name, raw in blobs.items():
        assert manifest['files'][name] == dict(bytes=len(raw), sha256=digest(raw))
    output.mkdir(parents=True)
    for name, raw in blobs.items():
        path = output/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    cwd = output/'empty-cwd'; cwd.mkdir()
    original = output/'records/workflow'
    env = dict(os.environ, PYTHONPATH=str(output/'source/python'), PYTHONDONTWRITEBYTECODE='1')
    commands = []
    complete_input = original/('complete-corrected.json' if (original/'complete-corrected.json').is_file() else 'complete.json')
    for label, args in (
        ('plan', ['hybrid-deepep-plan', '--config', str(original/'input.json'), '--output', 'plan']),
        ('prefix', ['hybrid-deepep-replay', '--plan', 'plan', '--actions', str(original/'prefix.json'), '--output', 'prefix.json']),
        ('complete', ['hybrid-deepep-replay', '--plan', 'plan', '--actions', str(complete_input), '--output', 'complete.json']),
    ):
        command = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', *args]
        r = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=10)
        (cwd/(label+'.stdout')).write_bytes(r.stdout); (cwd/(label+'.stderr')).write_bytes(r.stderr)
        commands.append(dict(command=command, cwd=str(cwd), exitcode=r.returncode))
        (output/'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
        assert r.returncode == 0, label
    for name in ('input.json', 'plan.json', 'manifest.json'):
        assert (cwd/'plan'/name).read_bytes() == (original/'plan'/name).read_bytes()
    for current, expected in (('prefix.json', 'prefix-result.json'), ('complete.json', 'complete-result.json')):
        assert (cwd/current).read_bytes() == (original/expected).read_bytes()
    return dict(passed=True, archive_sha256=sha, members=len(blobs)+1,
        uncompressed_bytes=sum(map(len, blobs.values())), all_member_hashes_checked=True,
        source_only_empty_cwd=True, public_commands=3, plan_and_two_replays_byte_identical=True,
        new_networks=0, builds=0, downloads=0, gpu_runs=0)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repository', type=Path)
    p.add_argument('--run', type=Path)
    p.add_argument('--archive', type=Path)
    p.add_argument('--sha256')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.repository:
        assert a.run and not a.archive and not a.sha256
        archive = a.run.resolve()/'checkpoint.tar.gz'
        sha = pack(a.repository.resolve(), a.run.resolve(), archive)
    else:
        assert a.archive and a.sha256 and not a.run
        archive, sha = a.archive.resolve(), a.sha256
    result = restore_and_check(archive, sha, a.output.resolve())
    (a.output/'verification.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__': main()
