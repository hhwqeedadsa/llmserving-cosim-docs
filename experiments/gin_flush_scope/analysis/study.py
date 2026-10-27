"""Pinned-source completion-scope screening; no network or GPU timing model."""
import argparse
from collections import Counter
import hashlib
from itertools import permutations
import json
from pathlib import Path


REFERENCES = {
    'deepep': ('deepep_modern_20261022', 'f6aa38fe06cd4a0489e7730365d4e0114970a2f3',
               ('file-sources.json', 'file-sources-completion.json'), (
                   'deep_ep/include/deep_ep/comm/barrier.cuh',
                   'deep_ep/include/deep_ep/comm/handle.cuh',
                   'deep_ep/include/deep_ep/impls/ep/hybrid_combine.cuh',
                   'csrc/kernels/comm/context.cpp')),
    'nccl': ('nccl_gin_20261022', '12df1a11afad322be5a204a2db890161cbf8131d',
             ('files-first.json', 'files-contract.json', 'files-queue.json'), (
                 'src/include/nccl_device/impl/gin__funcs.h',
                 'src/include/nccl_device/gin/gdaki/gin_gdaki.h',
                 'docs/userguide/source/api/device_gin.rst')),
}


def load(path):
    return json.loads(path.read_bytes())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(repository, stage):
    assert not stage.exists()
    stage.mkdir()
    records = []
    for project, (cache, commit, catalogs, names) in REFERENCES.items():
        root = repository / 'data/research' / cache
        tree = load(root / 'tree.json')
        assert not tree['truncated']
        entries = {r['path']: r['sha'] for r in tree['tree'] if r['type'] == 'blob'}
        sources = {r['path']: r for cat in catalogs for r in load(root / cat)['sources']}
        for name in names:
            row = sources[name]
            source = root / 'sources' / name
            body = source.read_bytes()
            blob = hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest()
            assert row['commit'] == commit and row['git_blob_sha'] == entries[name] == blob
            assert sha(source) == row['sha256']
            relative = str(Path('observed') / project / name)
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            records.append(dict(project=project, path=name, saved_path=relative,
                                commit=commit, sha256=sha(target), git_blob_sha=blob,
                                public_url=row['public_url']))
    save(stage / 'observed/sources.json', dict(files=records,
         scope='cached pinned-ref/tree-entry/blob; no new remote commit-root verification'))
    save(stage / 'state.json', dict(status='prepared', planned_configurations=6,
         planned_partial_order_extensions=6, network_runs=0, GPU=0))


def source_claims(stage):
    needles = {
        'barrier.cuh': ('if constexpr (kNumQPs == 1)',
                        'global_channel_idx % kNumAvailableQPs'),
        'handle.cuh': ('gin(ncclGin(nccl_dev_comm, qp_idx, resource_sharing_mode))',
                       'gin.flush(coop_t());'),
        'hybrid_combine.cuh': ('EP_STATIC_ASSERT(kNumScaleoutRanks <= 32, "Invalid ranks")',
                               'gin.flush<ncclCoopWarp>();', '// No barrier at epilogue'),
        'context.cpp': ('reqs.ginCustomStride = gin_min_stride;',
                        'reqs.ginConnectionType = NCCL_GIN_CONNECTION_CUSTOM_STRIDE;',
                        'allow_hybrid_mode ? NCCL_GIN_CONNECTION_RAIL : NCCL_GIN_CONNECTION_FULL;'),
        'gin__funcs.h': ('ans.nRanks = idivFast32(comm.nRanks, comm.ginConnectionStride,',
                         'ncclGin_BackendMask<beMask>::flush(Coop coop,'),
        'gin_gdaki.h': ('for (int peer = coop.thread_rank(); peer < ctx.nRanks; peer += coop.size())',
                        '&qps[peer].sq_rsvd_index);',
                        'const uint64_t n = doca_gpu_dev_verbs_atomic_read<uint64_t, doca_sharing_mode>(&qp->sq_rsvd_index);'),
        'device_gin.rst': ('Ensures that all the pending transfer operations scheduled by any threads of *coop* are locally consumed.',
                            'status on the remote peer(s). For get operations, this means that the data is visible to the local rank.',
                            'Initiates a non-blocking flush operation for one peer'),
    }
    result = []
    for row in load(stage / 'observed/sources.json')['files']:
        lines = (stage / row['saved_path']).read_text().splitlines()
        for needle in needles[Path(row['path']).name]:
            hits = [i + 1 for i, line in enumerate(lines) if needle in line]
            if Path(row['path']).name == 'gin_gdaki.h' and needle != needles['gin_gdaki.h'][-1]:
                start = next(i for i,line in enumerate(lines) if 'static ncclResult_t flushImplModeCore(' in line)
                end = next(i for i,line in enumerate(lines) if 'static void flushImplMode(' in line)
                hits = [i for i in hits if start <= i-1 < end]
            assert len(hits) == 1, (row['path'], needle, hits)
            result.append(dict(project=row['project'], path=row['path'], line=hits[0],
                               anchor=needle, url=row['public_url'] + '#L' + str(hits[0])))
    return result


def qp_index(sm, channel, qps):
    """get_qp_mode for declared 2SM/2 channels per SM, Combine (no notify)."""
    if qps == 1:
        return 0, 'GPU'
    if 2 <= qps:
        available_in_sm = qps // 2 + int(sm < qps % 2)
        return sm + (channel % available_in_sm) * 2, 'CTA'
    raise AssertionError('outside fixed conditions')


def execute(stage, continue_unstarted=False):
    state = load(stage / 'state.json')
    if continue_unstarted:
        assert state['status']=='stopped_after_failure' and not (stage/'analysis.json').exists()
        assert 'gin_gdaki.h' in state['error'] and '[407, 666]' in state['error']
        assert load(stage/'failed-source-locator/state.json')==state
    else:
        assert state['status'] == 'prepared'
    try:
        claims = source_claims(stage)
        configurations = []
        for ep in (64, 128, 256):
            for stride in (1, 8):
                n_peers = ep // stride
                scans = [list(range(lane, n_peers, 32)) for lane in range(32)]
                # Source is world rank 0; only its rail's remote data peers are active here.
                active = [world // stride for world in range(8, ep, 8)]
                lane_counts = Counter(peer % 32 for peer in active)
                configurations.append(dict(ep=ep,domain_gpus=8,stride=stride,provider_peers=n_peers,
                    max_lane_scan_count=max(map(len, scans)),max_remote_rail_peers_per_lane=max(lane_counts.values()),
                    active_remote_provider_peers=active,lane_scan_lists=scans,
                    same_lane_late_active_peer_exists=any(n > 1 for n in lane_counts.values()),
                    binding_scope=('declared compact rail stride; physical binding unverified' if stride == 8 else
                                   'conditional expanded provider; host requires CUSTOM_STRIDE with gin_min_stride=1')))
        sharing = []
        for qps in (1, 2, 4):
            rows = [dict(sm=sm,channel=channel,context=qp_index(sm,channel,qps)[0],
                         mode=qp_index(sm,channel,qps)[1]) for sm in range(2) for channel in range(2)]
            counts = Counter(row['context'] for row in rows)
            sharing.append(dict(allocated_context_indices=qps,channels=rows,
                                max_channels_per_context=max(counts.values())))
        extensions = []
        events = ('A_complete_first','A_sample_later','B_reserve_later','B_complete_later')
        for order in permutations(events):
            if order.index('A_complete_first') > order.index('A_sample_later'):
                continue
            if order.index('B_reserve_later') > order.index('B_complete_later'):
                continue
            reserved = completed = False
            captured = pending = None
            for event in order:
                if event == 'B_reserve_later':
                    reserved = True
                elif event == 'B_complete_later':
                    assert reserved
                    completed = True
                elif event == 'A_sample_later':
                    captured = reserved
                    pending = reserved and not completed
            extensions.append(dict(events=['A_sample_first',*order],captured_B=captured,
                B_pending_at_sample=pending,entry_snapshot_captures_B=False,
                required_completion_nodes=['A_complete_first']+(['B_complete_later'] if captured else [])))
        assert len(extensions) == 6
        output = dict(configurations=configurations,context_sharing=sharing,extensions=extensions,
            same_peer_preexisting_other_producer=dict(shared_context=dict(sampled_prefix=2,waits_for_B=True),
                                                      separate_context=dict(sampled_prefix=1,waits_for_B=False)),
            rejected_large_ep=[dict(ep=ep,domain_gpus=8,scaleout_domains=ep//8,
                                   accepted_by_fixed_hybrid_kernel=False) for ep in (512,1024)],
            captured_extensions=sum(r['captured_B'] for r in extensions),
            pending_extensions=sum(r['B_pending_at_sample'] for r in extensions),
            dynamic_resource_DAG_can_bind_sampled_prefix=True,
            source_claims=claims,network_runs=0,GPU=0,measured_latency_benefit=None,
            measured_simulator_speedup=None,new_innovations_established=0,
            scope='finite source transcription; not provider execution, GPU scheduling, API minimum contract or network timing')
        save(stage / 'analysis.json', output)
        final={k:v for k,v in state.items() if k!='error'}
        save(stage / 'state.json', dict(final,status='completed',initial_source_locator_failure_preserved=True))
    except BaseException as error:
        save(stage / 'state.json', dict(state,status='stopped_after_failure',error=repr(error)))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare','execute','continue-unstarted'))
    parser.add_argument('--repository', type=Path)
    parser.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.repository.resolve(), args.stage.resolve())
    else:
        execute(args.stage.resolve(), continue_unstarted=args.action=='continue-unstarted')
