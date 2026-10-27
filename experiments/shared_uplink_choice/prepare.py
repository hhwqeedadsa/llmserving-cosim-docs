"""Prepare real route inputs and hash-admit a previously accepted public build."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

NAMES = ('900', '901', '905', '1003', '1004', '1007', '1008', '1011')
CONDITIONS = ('baseline', 'access', 'core')
ARCHIVE_SHA = '4cff1ee79454faca83ecc1aa732c4d0927f6ff4e6498fbb518f405cf519e2b27'


def load(p): return json.loads(p.read_bytes())
def save(p, value): p.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def admit(path, expected, schema):
    assert path.stat().st_size < 128*2**20 and sha(path) == expected
    blobs, total = {}, 0
    with tarfile.open(path, 'r:gz') as archive:
        for item in archive:
            name = PurePosixPath(item.name)
            assert item.isfile() and not name.is_absolute() and '..' not in name.parts
            assert '\\' not in item.name and str(name) == item.name and item.name not in blobs
            assert len(blobs) < 4096 and 0 <= item.size <= 64*2**20
            total += item.size
            assert total < 256*2**20
            raw = archive.extractfile(item).read()
            assert len(raw) == item.size
            blobs[item.name] = raw
    manifest = json.loads(blobs.pop('manifest.json'))
    assert manifest['schema_version'] == schema and set(blobs) == set(manifest['files'])
    for name, raw in blobs.items():
        assert not any(str(parent) in blobs for parent in PurePosixPath(name).parents)
        row = manifest['files'][name]
        assert row['bytes'] == len(raw) and row['sha256'] == hashlib.sha256(raw).hexdigest()
        assert row['mode'] in (0o644, 0o755)
    return blobs, manifest


def prepare(repo, stage):
    observed = stage/'observed'
    observed.mkdir()
    intake = repo/'experiments/route_coverage/results/local_additions_20261027/intake.json'
    source = load(intake)
    save(observed/'intake.json', source)
    originals = observed/'routes'
    originals.mkdir()
    rows = {row['name']: row for row in source['files']}
    selected = []
    for name in NAMES:
        row = rows[name+'.json']
        assert row['eligible_offline'] and row['origin_directory_user_confirmed'] and not row['duplicate_of']
        path = repo/'data/moe-trace-small'/(name+'.json')
        assert sha(path) == row['sha256']
        raw = path.read_bytes()
        (originals/path.name).write_bytes(raw)
        tokens = json.loads(raw)[1]['3']
        assert len(tokens) == 1 and len(set(tokens[0])) == 8
        selected.append(dict(name=path.name, sha256=row['sha256'], entry_index=1, layer=3, choices=tokens[0]))
    assert len({r['sha256'] for r in selected}) == 8
    for batch in (4, 8):
        root = observed/f'batch{batch}'
        root.mkdir()
        cfg = load(repo/'examples/modern_deepep_native/input-selective.json')
        cfg['modern']['execution'].update(ranks=4, max_tokens_per_rank=batch//4, num_sms=2,
            dispatch_warps=2, combine_warps=2, call_contexts=5, requested_contexts=5,
            local_devices=[[0, 1] for _ in range(4)])
        cfg['modern']['model'].update(experts=256, top_k=8, hidden_size=7168)
        tokens = [[] for _ in range(4)]
        for i, row in enumerate(selected[:batch]): tokens[i % 4].append(row['choices'])
        cfg['modern']['calls'] = [dict(name='entry1.layer3', tokens=tokens)]
        devices = [dict(device=0, nic=1, plane=0), dict(device=1, nic=2, plane=0)]
        cfg['deployment'] = [dict(rank=r, node=n, devices=deepcopy(devices)) for r, n in enumerate((0, 1, 4, 5))]
        save(root/'input.json', cfg)
        save(root/'modern-input.json', cfg['modern'])
    for name in CONDITIONS:
        t = load(repo/'examples/scaleout_multirail_native/topology.json')
        p = load(repo/'examples/modern_deepep_native/rdma.json')
        p['payload_bytes'] = 2000
        edge, core = (200 if name == 'access' else 100), (50 if name == 'core' else 25)
        for tier in ('access', 'tor_agg', 'tor_core'): t['links'][tier]['rate_bps'] = edge*10**9
        t['links']['agg_core']['rate_bps'] = core*10**9
        t['nic']['total_rate_bps'] = 2*edge*10**9
        ecn = next(iter(p['ecn_by_rate'].values()))
        p['ecn_by_rate'] = {str(rate*10**9): deepcopy(ecn) for rate in (edge, core)}
        save(observed/(name+'-topology-config.json'), t)
        save(observed/(name+'-protocol.json'), p)
    blobs, manifest = admit(repo/'runs/physical_nic_binding_20261027/checkpoint.tar.gz', ARCHIVE_SHA,
                            'physical-nic-binding-checkpoint/1')
    provenance = json.loads(blobs['evidence/build/provenance.json'])
    for name, digest in provenance['sources'].items(): assert sha(repo/name) == digest
    runtime = stage/'runtime'
    runtime.mkdir()
    files = {}
    prefix = 'evidence/build/output/'
    for name, raw in blobs.items():
        if not (name.startswith(prefix+'lib/') or name == prefix+'moe-rdma/ns3.42-moe-rdma-transaction-session'):
            continue
        suffix = name.removeprefix(prefix)
        dest = runtime/('transaction-session' if suffix.startswith('moe-rdma/') else suffix)
        dest.parent.mkdir(exist_ok=True, parents=True)
        dest.write_bytes(raw)
        dest.chmod(manifest['files'][name]['mode'])
        files[str(dest.relative_to(runtime))] = sha(dest)
    assert len(files) == 9
    wrapper = runtime/'run-transaction-session'
    wrapper.write_text('#!/usr/bin/env python3\nimport os, sys\nfrom pathlib import Path\n'
        'root = Path(__file__).resolve().parent\nos.environ["LD_LIBRARY_PATH"] = str(root/"lib")\n'
        'binary = str(root/"transaction-session")\nos.execv(binary, [binary, *sys.argv[1:]])\n')
    wrapper.chmod(0o755)
    files[wrapper.name] = sha(wrapper)
    save(stage/'runtime-identity.json', dict(files=files, sources=provenance['sources'],
        native_commit=provenance['native_commit'], archive_sha256=ARCHIVE_SHA))
    save(observed/'source.json', dict(repository_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
        selected=selected, source=source['source'], intake_sha256=sha(intake), runtime_archive_sha256=ARCHIVE_SHA,
        remote_identity_verified=False, hardware_calibrated=False))


def main():
    p = argparse.ArgumentParser()
    for n in ('repository','stage'): p.add_argument('--'+n, type=Path, required=True)
    a = p.parse_args()
    prepare(a.repository.resolve(), a.stage.resolve())


if __name__ == '__main__': main()
