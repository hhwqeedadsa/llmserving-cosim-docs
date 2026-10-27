"""Hash-admit existing archives; freeze a complete DAG from baseline only."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile


def sha(raw): return hashlib.sha256(raw).hexdigest()
def save(p, v): p.write_text(json.dumps(v, ensure_ascii=False, indent=2)+'\n')
def load(p): return json.loads(p.read_bytes())


def admit(path, expected):
    assert path.stat().st_size <= 32*2**20 and sha(path.read_bytes()) == expected
    blobs = {}; total = 0
    with tarfile.open(path, 'r:gz') as archive:
        for item in archive:
            p = PurePosixPath(item.name)
            assert item.isfile() and not p.is_absolute() and '..' not in p.parts and '\\' not in item.name
            assert str(p) == item.name and item.name not in blobs and len(blobs) < 2048
            assert 0 <= item.size <= 16*2**20
            total += item.size; assert total < 128*2**20
            blobs[item.name] = archive.extractfile(item).read()
            assert len(blobs[item.name]) == item.size
    manifest = json.loads(blobs.pop('manifest.json'))
    assert set(blobs) == set(manifest['files'])
    assert not any(str(parent) in blobs for name in blobs for parent in PurePosixPath(name).parents if str(parent) != '.')
    for name, body in blobs.items():
        assert manifest['files'][name] == dict(bytes=len(body), sha256=sha(body))
    return blobs


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repository', type=Path, required=True)
    p.add_argument('--stage', type=Path, required=True)
    a = p.parse_args(); repo = a.repository.resolve(); stage = a.stage.resolve()
    assert not stage.exists(); stage.mkdir()
    reference_path = repo/'runs/modern_network_levers_checkpoint_20261026/checkpoint.tar.gz'
    runtime_path = repo/'runs/modern_selective_checkpoint_20261026/checkpoint.tar.gz'
    reference_sha = '00a98a5739d7af0520978d0759ce0e58f5241ca3717871c32c928d3f152e5497'
    runtime_sha = 'd10eec67496fae9df855e7850b79d0add7b25a9dffc6880c90bfb12aef2380e3'
    blobs = admit(reference_path, reference_sha)
    for name, body in blobs.items():
        if not name.startswith('evidence/'): continue
        path = stage/'reference'/name.removeprefix('evidence/')
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body)
    del blobs
    blobs = admit(runtime_path, runtime_sha)
    identity = json.loads(blobs['evidence/runtime-identity.json'])
    runtime = stage/'runtime'; runtime.mkdir()
    assert len(identity['files']) == 10
    for name, digest in identity['files'].items():
        raw = blobs['evidence/runtime/'+name]; assert sha(raw) == digest
        dest = runtime/name; dest.parent.mkdir(exist_ok=True, parents=True); dest.write_bytes(raw)
    for name in ('run-transaction-session', 'transaction-session'): (runtime/name).chmod(0o755)
    for name, digest in identity['sources'].items(): assert sha((repo/name).read_bytes()) == digest
    save(stage/'runtime-identity.json', identity); del blobs
    original = stage/'reference/baseline'
    native = load(original/'modern-execution.json')
    initial = load(original/'initial.json')
    assert not initial['operations'] and native['runtime']['all_events_fired']
    program = deepcopy(initial); program['operations'] = deepcopy(native['native_operations'])
    assert len({n['id'] for n in program['operations']}) == len(program['operations'])
    save(stage/'program.json', program)
    save(stage/'mapping.json', dict(native_tasks=native['native_tasks'],
        model_plan=load(original/'modern-plan.json'), baseline_events=native['events'],
        scope='baseline-only recorded native graph; dynamic slot/address/context frozen'))
    assert all(load(stage/'reference'/name/'initial.json') == initial for name in ('bandwidth', 'propagation'))
    assert len({sha((stage/'reference'/name/'input.json').read_bytes())
                for name in ('baseline', 'bandwidth', 'propagation')}) == 1
    for row in load(stage/'reference/source.json')['selected_rows']:
        assert sha((repo/row['file']['path']).read_bytes()) == row['file']['sha256']
    save(stage/'source.json', dict(reference_archive_sha256=reference_sha, runtime_archive_sha256=runtime_sha,
        program_sha256=sha((stage/'program.json').read_bytes()),
        extraction_reference='baseline', evaluation_networks=['baseline', 'bandwidth', 'propagation'],
        copied_nodes=len(program['operations']), copied_connections=len(program['connections']),
        copied_remote=sum(n['kind']=='remote' for n in program['operations']),
        copied_local=sum(n['kind']=='local' for n in program['operations']),
        reference_networks_reexecuted=0, runtime_rebuilt=False))
    save(stage/'execution.json', dict(state='prepared', records=[], network_executions=0, closed=False))
    print('Hash-admitted reference and runtime; frozen complete native graph:', len(program['operations']), 'nodes')


if __name__ == '__main__': main()
