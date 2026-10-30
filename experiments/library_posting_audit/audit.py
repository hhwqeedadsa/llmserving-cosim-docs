"""Read pinned sources and existing waves; count conditional library operations."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
LL = '9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee'
MODERN = 'f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
NCCL = '12df1a11afad322be5a204a2db890161cbf8131d'
CATALOG = [
    ('deepep_semantics', LL, 'csrc/kernels/ibgda_device.cuh', [
        'return &state->globalmem.rcs[', 'constexpr int kNumRequestInBatch = 4;',
        'template <bool kAlwaysDoPostSend = false>', 'return min(lchunk_size, rchunk_size);',
        'ctrl_seg.fm_ce_se = MLX5_WQE_CTRL_CQ_UPDATE;', 'auto remaining_bytes = bytes;',
        'ibgda_submit_requests<true>(qp, my_wqe_idx, 1);']),
    ('deepep_semantics', LL, 'csrc/kernels/internode_ll.cu', [
        'nvshmemi_ibgda_put_nbi_warp(dst_ptr, src_ptr, num_bytes_per_msg',
        'nvshmemi_ibgda_put_nbi_warp(dst_ptr, buf_ptr, num_send_bytes',
        'nvshmemi_ibgda_amo_nonfetch_add']),
    ('deepep_modern', MODERN, 'deep_ep/include/deep_ep/impls/ep/dispatch.cuh', [
        'gin.put<team_t>(recv_buffer.get_token_buffer']),
    ('nccl_gin', NCCL, 'src/transport/net_ib/gdaki/gin_host_gdaki.cc', [
        'doca_verbs_qp_attr_set_ack_timeout(verbs_qp_attr, ncclParamIbTimeout())',
        'doca_verbs_qp_attr_set_retry_cnt(verbs_qp_attr, ncclParamIbRetryCnt())']),
    ('nccl_gin', NCCL, 'src/transport/net_ib/gdaki/doca-gpunetio/include/common/doca_gpunetio_verbs_def.h', [
        'DOCA_GPUNETIO_IB_MLX5_WQE_CTRL_CQ_UPDATE =']),
    ('nccl_gin', NCCL, 'src/transport/net_ib/gdaki/doca-gpunetio/include/device/doca_gpunetio_dev_verbs_qp.cuh', [
        'Traditional GPU-CQ mode preserves historical behavior by always requesting a CQE.']),
]


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(2**20), b''):
            h.update(block)
    return h.hexdigest()


def load(path):
    return json.loads(path.read_bytes())


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def sources():
    result = []
    for family, revision, relative, markers in CATALOG:
        directory = ROOT/'data/research'/(family+'_20261022')
        manifests = sorted(directory.glob('file-sources*.json')) if family != 'nccl_gin' else sorted(directory.glob('files-*.json'))
        table = {}
        for manifest in manifests:
            for row in load(manifest)['sources']:
                if row['path'] in table:
                    require(row['sha256'] == table[row['path']]['sha256'], 'conflicting source identity')
                table[row['path']] = row
        meta = table[relative]
        path = directory/'sources'/relative
        raw = path.read_bytes()
        require(meta['commit'] == revision and sha(path) == meta['sha256'], 'source revision/hash changed')
        blob = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        require(blob == meta['git_blob_sha'] and len(raw) == meta['bytes'], 'source blob/size changed')
        tree = {r['path']: r for r in load(directory/'tree.json')['tree']}
        require(tree[relative]['sha'] == blob, 'cached source tree mismatch')
        lines = raw.decode().splitlines()
        excerpts = []
        for marker in markers:
            matches = [i for i, line in enumerate(lines) if marker in line]
            require(bool(matches), 'source marker missing: '+marker)
            index = matches[0]
            first, last = max(0, index-2), min(len(lines), index+4)
            excerpts.append(dict(marker=marker, first_line=first+1, last_line=last,
                                 text='\n'.join(lines[first:last])))
        result.append(dict(family=family, revision=revision, path=str(path.relative_to(ROOT)),
            public_url=meta['public_url'], sha256=meta['sha256'], git_blob_sha1=blob,
            bytes=len(raw), excerpts=excerpts))
    return result


def prepare(args):
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    run = args.run.resolve()
    prior = ROOT/'experiments/explicit_prefix_moe/results/20261030'
    receipt, previous = load(prior/'receipt.json'), load(prior/'analysis.json')
    require(sha(prior/'analysis.json') == receipt['delivery_sha256']['analysis.json'], 'prior analysis changed')
    baseline = previous['cases']['normal']
    require(str(run) == baseline['directory'] and sha(run/'result.json') == baseline['result_sha256'], 'normal identity changed')
    raw_hashes = {str(p): sha(p) for p in (run/'result.json', run/'input.json')}
    for name, h in baseline['graphs_sha256'].items():
        path = run/'graphs'/name
        require(sha(path) == h, 'original graph changed')
        raw_hashes[str(path)] = h
    config = load(run/'input.json')
    require(config['model']['experts'] == 256 and config['model']['hidden_size'] == 7168, 'unexpected model')
    result = load(run/'result.json')
    require(result['gpu_count'] == 128 and result['switch_count'] == 20 and result['passed'] and result['drained'], 'unexpected original network')
    evidence = sources()
    save(out/'sources.json', evidence)
    projections = []
    for path in sorted((run/'graphs').glob('*/wave.json')):
        wave = load(path)
        projections.append({k: wave[k] for k in ('graph_id', 'rank_ids', 'group_index', 'decode_step', 'routes', 'messages', 'token_transport')})
    require(len(projections) == 8, 'expected four groups and two decode steps')
    save(out/'projections.json', projections)
    code = [*sorted(Path(__file__).parent.glob('*.py')), Path(__file__).with_name('README.md')]
    save(out/'frozen.json', dict(method_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        source_code_sha256={str(p.relative_to(ROOT)): sha(p) for p in code}, raw_sha256=raw_hashes,
        sources_sha256=sha(out/'sources.json'), projections_sha256=sha(out/'projections.json'),
        new_networks=0, original_network_endpoints=128, model=config['model']))
    print(json.dumps(dict(prepared=True, sources=len(evidence), waves=8, original_network_endpoints=128, new_networks=0)))


def checked(directory):
    frozen = load(directory/'frozen.json')
    for name, h in frozen['raw_sha256'].items():
        require(sha(Path(name)) == h, 'original artifact changed')
    for name, h in frozen['source_code_sha256'].items():
        require(sha(ROOT/name) == h, 'method source changed')
    require(sha(directory/'projections.json') == frozen['projections_sha256'], 'projection changed')
    require(sha(directory/'sources.json') == frozen['sources_sha256'], 'source evidence changed')
    for source in load(directory/'sources.json'):
        require(sha(ROOT/source['path']) == source['sha256'], 'public source changed')
    return frozen


def analyze(args):
    directory = args.input.resolve()
    frozen = checked(directory)
    rows, origins = [], {}
    for wave in load(directory/'projections.json'):
        ranks = wave['rank_ids']
        require(len(ranks) == 32 and len(set(ranks)) == 32, 'expected EP32')
        for route in wave['routes']:
            require(route['layer'] in (0, 1), 'unexpected layer')
            count = Counter()
            for token in route['tokens']:
                require(token['rank'] in ranks and token['slot'] == 0, 'unexpected token source')
                require(len(token['experts']) == len(set(token['experts'])) == 8, 'unexpected choices')
                origin = token['source']
                origins[origin['request_id']] = origin['sha256']
                for expert in token['experts']:
                    require(0 <= expert < 256, 'invalid expert')
                    count[token['rank'], expert] += 1
            phases = {}
            for phase in ('dispatch', 'combine'):
                remote = [(src, e, n) for (src, e), n in count.items() if src != ranks[e//8]]
                direct = sum(n//4 for _, _, n in remote)
                pending = sum(n % 4 for _, _, n in remote)
                markers = 32*256-256
                qps = {(src, ranks[e//8], e % 8) if phase == 'dispatch' else
                       (ranks[e//8], src, e % 8) for src, e, _ in remote}
                phases[phase] = dict(data_puts=sum(n for _, _, n in remote), nonempty_marker_contexts=len(remote),
                    marker_operations=markers, zero_token_markers=markers-len(remote), data_active_logical_QPs=len(qps),
                    synchronous_data_postsend_calls=direct, puts_in_final_unrung_batch=pending,
                    marker_forced_postsend_calls=markers, cq_update_requests_per_WQE='one request in reviewed constructors; not a wire ACK count')
            current = [m for m in wave['messages'] if m['layer'] == route['layer']]
            rows.append(dict(graph_id=wave['graph_id'], group=wave['group_index'], step=wave['decode_step'],
                layer=route['layer'], ranks=ranks, tokens=len(route['tokens']), selections=sum(count.values()),
                low_latency_projection=phases, current_direct=dict(mode=wave['token_transport'],
                    messages=len(current), logical_payload_bytes=sum(m['bytes'] for m in current))))
    totals = Counter()
    for row in rows:
        totals['tokens'] += row['tokens']
        totals['selections'] += row['selections']
        totals['current_direct_messages'] += row['current_direct']['messages']
        totals['current_direct_payload_bytes'] += row['current_direct']['logical_payload_bytes']
        for phase in ('dispatch', 'combine'):
            for name, value in row['low_latency_projection'][phase].items():
                if type(value) is int:
                    totals[phase+'_'+name] += value
    # Explicit illustrative key spans, not addresses recovered from route JSON.
    examples = []
    for local, remote in ((0, 0), (30000, 30000), (25000, 30000)):
        remaining, a, b, chunks = 14352, local, remote, []
        while remaining:
            length = min(remaining, 32768-a % 32768, 32768-b % 32768)
            chunks.append(length)
            a += length; b += length; remaining -= length
        examples.append(dict(put_bytes=14352, assumed_key_span_bytes=32768, local_offset=local,
            remote_offset=remote, WQE_payload_bytes=chunks, network_packets=None, ACKs=None))
    save(args.output, dict(passed=True, frozen_sha256=sha(directory/'frozen.json'), rows=rows,
        totals=dict(totals), distinct_real_requests=len(origins), request_sha256=origins, key_examples=examples,
        conditions=dict(library_projection='DeepEP v1.2.1 low-latency BF16, RDMA-only',
            conditional_sync_postsend=True, actual_async_mode_known=False, runtime_reservation_order_known=False,
            source_key_layout_known=False, current_direct_is_same_library_mode=False,
            NCCL_provider_sources_jointly_built=False, hardware_ACK_cadence_verified=False),
        network_executed=False, latency_predicted=False, hardware_calibrated=False))
    print(json.dumps(dict(passed=True, windows=len(rows), requests=len(origins), totals=dict(totals))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    prepare_parser = sub.add_parser('prepare')
    prepare_parser.add_argument('--run', type=Path, required=True)
    prepare_parser.add_argument('--output', type=Path, required=True)
    analyze_parser = sub.add_parser('analyze')
    analyze_parser.add_argument('--input', type=Path, required=True)
    analyze_parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    (prepare if args.mode == 'prepare' else analyze)(args)


if __name__ == '__main__':
    main()
