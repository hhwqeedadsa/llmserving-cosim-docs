"""Prepare hash-checked user-supplied input; no network simulation or download."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

from experiments.static_dag_transfer.prepare import admit

NAMES = ('900', '901', '905', '1003', '1004', '1007', '1008', '1011')
CONDITIONS = ('baseline', 'bandwidth', 'propagation')


def load(p): return json.loads(p.read_bytes())
def save(p, value): p.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    for name in ('repository', 'stage'):
        parser.add_argument('--'+name, type=Path, required=True)
    a = parser.parse_args(); repo, stage = a.repository.resolve(), a.stage.resolve()
    assert not (stage/'observed').exists()
    observed = stage/'observed'; observed.mkdir()
    intake_path = repo/'experiments/route_coverage/results/local_additions_20261027/intake.json'
    intake = load(intake_path); save(observed/'intake.json', intake)
    rows = {row['name']: row for row in intake['files']}
    originals = observed/'routes'; originals.mkdir()
    selected = []
    for name in NAMES:
        row = rows[name+'.json']
        assert row['eligible_offline'] and row['group'] == 'new_user_supplied' and not row['duplicate_of']
        path = repo/'data/moe-trace-small'/(name+'.json')
        assert sha(path) == row['sha256']
        raw = path.read_bytes(); (originals/path.name).write_bytes(raw)
        tokens = json.loads(raw)[1]['3']; assert len(tokens) == 1 and len(set(tokens[0])) == 8
        selected.append(dict(name=path.name, sha256=row['sha256'], entry_index=1, layer=3, choices=tokens[0]))
    assert len({row['sha256'] for row in selected}) == 8
    for batch in (4, 8):
        root = observed/f'batch{batch}'; root.mkdir()
        cfg = load(repo/'examples/modern_deepep_native/input-selective.json')
        cfg['modern']['execution'].update(ranks=4, max_tokens_per_rank=batch//4, num_sms=2,
            dispatch_warps=2, combine_warps=2, call_contexts=5, requested_contexts=5,
            local_devices=[[0, 1] for _ in range(4)])
        cfg['modern']['model'].update(experts=256, top_k=8, hidden_size=7168)
        tokens = [[] for _ in range(4)]
        for i, row in enumerate(selected[:batch]): tokens[i % 4].append(row['choices'])
        cfg['modern']['calls'] = [dict(name='entry1.layer3', tokens=tokens)]
        devices = deepcopy(cfg['deployment'][0]['devices'])
        cfg['deployment'] = [dict(rank=r, node=n, devices=deepcopy(devices)) for r, n in enumerate((0, 1, 4, 5))]
        save(root/'input.json', cfg); save(root/'modern-input.json', cfg['modern'])
    for name in CONDITIONS:
        t = load(repo/'examples/modern_deepep_native/topology.json')
        p = load(repo/'examples/modern_deepep_native/rdma.json'); p['payload_bytes'] = 2000
        if name == 'bandwidth':
            for link in t['links'].values(): link['rate_bps'] = 200000000000
            t['nic']['total_rate_bps'] = 400000000000
            p['ecn_by_rate'] = {'200000000000': next(iter(p['ecn_by_rate'].values()))}
        if name == 'propagation':
            for link in t['links'].values(): link['delay_ns'] = 500
        save(observed/(name+'-topology-config.json'), t)
        save(observed/(name+'-protocol.json'), p)
    runtime_sha = 'd10eec67496fae9df855e7850b79d0add7b25a9dffc6880c90bfb12aef2380e3'
    blobs = admit(repo/'runs/modern_selective_checkpoint_20261026/checkpoint.tar.gz', runtime_sha)
    identity = json.loads(blobs['evidence/runtime-identity.json'])
    runtime = stage/'runtime'; runtime.mkdir()
    for name, digest in identity['files'].items():
        raw = blobs['evidence/runtime/'+name]; assert hashlib.sha256(raw).hexdigest() == digest
        dest = runtime/name; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(raw)
    for name in ('run-transaction-session', 'transaction-session'): (runtime/name).chmod(0o755)
    for name, digest in identity['sources'].items(): assert sha(repo/name) == digest
    save(stage/'runtime-identity.json', identity)
    save(observed/'source.json', dict(repository_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip(),
        intake_sha256=sha(intake_path), selected=selected, runtime_archive_sha256=runtime_sha,
        source=intake['source'], remote_identity_verified=False, hardware_calibrated=False))


if __name__ == '__main__': main()
