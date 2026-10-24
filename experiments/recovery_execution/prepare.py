"""Verify fixed raw routing inputs and prepare the preregistered 16 conditions."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from moe_ub_cosim.scaleout_topology import execute

REVISION = '27febb7b2d24169560a9f6f83d38eb53f94916cf'
REQUESTS = (1472, 1808, 2675, 6447)
SIZE, STRIDE, RECV, CANONICAL, SOURCE = 14336, 16384, 4096, 262144, 1048576


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)+'\n')


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def owner_bytes(request, owner, experts):
    # Functional content identity only; no activation/numerical inference claim.
    seed = json.dumps([request, owner, sorted(experts)], separators=(',', ':')).encode()
    return hashlib.shake_256(b'MoE-functional-owner-output/1:'+seed).digest(SIZE)


def window(owner, key, address, length, content=None):
    return dict(owner=owner, key=key, address=address, bytes=length,
                allow_atomic=False, initialization='zero_fill/1',
                segments=[] if content is None else [dict(offset=0, hex=content.hex())])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', required=True, type=Path)
    p.add_argument('--stage', required=True, type=Path)
    p.add_argument('--backend', required=True, type=Path)
    p.add_argument('--library-dir', required=True, type=Path,
                   help='Directory containing the eight fixed native ns-3 shared libraries')
    args = p.parse_args()
    repo, stage = args.repo.resolve(), args.stage.resolve()
    stage.mkdir(exist_ok=False)
    inputs = stage/'inputs'; inputs.mkdir()
    manifest_path = repo/'data/moe-trace-small/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    assert manifest['source']['dataset_revision'] == REVISION
    assert manifest['model']['hidden_dim'] == 7168
    raw_files = {Path(row['file']['path']).stem: row['file'] for row in manifest['files']}
    topology = repo/'examples/scaleout_rdma/small-clos-100g.json'
    execute(topology, inputs/'topology')
    normal = json.loads((repo/'examples/scaleout_rdma/rdma.json').read_text())
    normal['packet_capture'] = dict(nodes=[], start_ns=0, end_ns=5000000,
                                    max_events=100000, wire_headers=True)
    conditions, requests = [], []
    for request in REQUESTS:
        item = raw_files[str(request)]; raw = repo/item['path']
        assert raw.stat().st_size <= 16*2**20 and digest(raw) == item['sha256']
        data = json.loads(raw.read_text())
        assert len(data) == 129 and len(data[1]['4']) == 1
        experts = data[1]['4'][0]
        assert len(experts) == len(set(experts)) == 8
        assert all(type(e) is int and 0 <= e < 256 for e in experts)
        owner_experts = {owner: sorted(e for e in experts if e//64 == owner)
                         for owner in range(4) if any(e//64 == owner for e in experts)}
        owners = sorted(owner_experts); failed_owner = min(owners)
        windows = [window(4, 7, RECV, STRIDE*8), window(4, 9, CANONICAL, STRIDE*4)]
        windows += [window(o, 8, SOURCE, SIZE, owner_bytes(request, o, es))
                    for o, es in owner_experts.items()]
        connections = [dict(id=c, src=c%4, dst=4, plane=0,
                            request_port=24000+2*c, response_port=24001+2*c,
                            source_nic=1, destination_nic=1, queue_depth=1)
                       for c in range(8)]
        initial = dict(schema_version='moe-rdma-transactions/5', connections=connections,
                       windows=windows, operations=[],
                       local_model=dict(mode='serial_per_gpu/1', duration_source='declared_ns'),
                       source_model=dict(capture='registered_window_at_publish/1', release='transaction_completed/1'),
                       local_memory_model=dict(mode='guarded_copy/1', acquire='admission/1',
                                               capture='start/1', commit='completion/1'),
                       transport_recovery=dict(mode='go_back_n/1', initial_rto_ns=20000,
                                               max_rto_ns=20000, max_retries=2,
                                               max_events=10000, on_exhausted='isolate_stream'))
        save(inputs/f'{request}-initial.json', initial)
        requests.append(dict(request=request, file=str(raw), sha256=item['sha256'],
                             experts=experts, owner_experts=owner_experts, owners=owners,
                             failed_owner=failed_owner))
        for fault in ('data_tail', 'ack_tail'):
            protocol = json.loads(json.dumps(normal))
            protocol['faults'] = [dict(node=4 if fault == 'data_tail' else failed_owner,
                                       port=0, message_id=2*failed_owner,
                                       kind='data' if fault == 'data_tail' else 'ack',
                                       sequence=14000 if fault == 'data_tail' else SIZE+32,
                                       count=1000, start_ns=0, end_ns=5000000)]
            protocol_path = inputs/f'{request}-{fault}-protocol.json'
            save(protocol_path, protocol)
            for strategy in ('whole_token', 'owner_retention'):
                name = f'{request}-{fault}-{strategy}'
                control = dict(strategy=strategy, owners=owners)
                save(inputs/f'{name}-controller.json', control)
                conditions.append(dict(name=name, request=request, fault=fault, strategy=strategy,
                                       initial=str(inputs/f'{request}-initial.json'),
                                       protocol=str(protocol_path),
                                       controller=str(inputs/f'{name}-controller.json')))
    libs = sorted(args.library_dir.resolve().glob('libns3*.so'))
    assert len(libs) == 8
    save(stage/'manifest.json', dict(schema_version='receiver-recovery-execution/1',
         source_revision=REVISION, platform_commit='18025e3',
         worktree_commit=subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip(),
         requests=requests, conditions=conditions,
         backend=dict(path=str(args.backend.resolve()), sha256=digest(args.backend)),
         libraries=[dict(path=str(f), sha256=digest(f)) for f in libs],
         source_hashes={str(f.relative_to(repo)): digest(f) for f in
                        sorted((repo/'experiments/recovery_execution').glob('*.py'))},
         input_hashes={str(f.relative_to(stage)): digest(f) for f in sorted(inputs.rglob('*')) if f.is_file()},
         timer_scope='rank4 serial local lane, also delays early canonical copies until 100us',
         hardware_calibrated=False, full_inference=False))
    print(json.dumps(dict(conditions=len(conditions), requests=requests), ensure_ascii=False))


if __name__ == '__main__':
    main()
