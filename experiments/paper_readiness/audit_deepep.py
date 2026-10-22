"""Offline fixed-source audit and four-request operation accounting, not simulation.

No platform implementation imports, policy inputs, library execution or downloads.
Counts are logical calls/operand bytes, never packets, WRs, wire bytes or latency.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMIT = '9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee'


def checked(path, limit=2**21):
    assert path.is_file() and path.stat().st_size <= limit, str(path)
    raw = path.read_bytes()
    assert len(raw) <= limit
    return raw


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def unique(pairs):
    out = {}
    for k, v in pairs:
        assert k not in out, 'Duplicate JSON key'
        out[k] = v
    return out


def load(raw):
    return json.loads(raw, object_pairs_hook=unique)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def sources(directory):
    tree_raw = checked(directory / 'tree.json')
    record = load(checked(directory / 'tree-source.json'))
    tree = load(tree_raw)
    assert record['status'] == 'read' and sha(tree_raw) == record['sha256']
    assert tree['sha'] == COMMIT and not tree['truncated']
    entries = {r['path']: r for r in tree['tree'] if r['type'] == 'blob'}
    manifest = load(checked(directory / 'file-sources.json'))
    assert manifest['commit'] == COMMIT and len(manifest['sources']) == 7
    items = [(dict(s), directory / 'sources' / s['path']) for s in manifest['sources']]
    cached = load(checked(ROOT / 'docs/review_artifacts/hardware_systems_20261020/sources.json'))
    for sid, name in [('deepep_v121_buffer', 'deep_ep/buffer.py'), ('deepep_v121_readme', 'README.md')]:
        s = next(dict(s) for s in cached['sources'] if s['id'] == sid)
        s['path'] = name
        items.append((s, ROOT / s['local_snapshot']))
    verified, texts = [], {}
    for s, path in items:
        raw = checked(path)
        e = entries[s['path']]
        assert s['commit'] == COMMIT and s['status'] in ('read', 200)
        assert len(raw) == s['bytes'] == e['size']
        assert sha(raw) == s['sha256'] and blob(raw) == s['git_blob_sha'] == e['sha']
        verified.append(dict(path=s['path'], commit=COMMIT, git_blob_sha=blob(raw),
                             sha256=sha(raw), bytes=len(raw), local_snapshot=str(path.relative_to(ROOT)),
                             public_url=s['public_url']))
        texts[s['path']] = raw.decode()
    return verified, texts


def span(texts, path, start, end=None, occurrence=0, expected=1):
    """Store a short exact excerpt and fixed-source line coordinates."""
    lines = texts[path].splitlines()
    hits = [i for i, line in enumerate(lines) if start in line]
    assert len(hits) == expected, (path, start, hits)
    begin = hits[occurrence]
    finish = begin if end is None else next(i for i in range(begin, len(lines)) if end in lines[i])
    assert finish - begin <= 35, 'Keep source excerpts small'
    return dict(path=path, first_line=begin+1, last_line=finish+1,
                excerpt='\n'.join(lines[begin:finish+1]),
                url=f'https://github.com/deepseek-ai/DeepEP/blob/{COMMIT}/{path}#L{begin+1}-L{finish+1}')


def evidence(texts):
    py, cpp, kernel, device = 'deep_ep/buffer.py', 'csrc/deep_ep.cpp', 'csrc/kernels/internode_ll.cu', 'csrc/kernels/ibgda_device.cuh'
    specs = [
        ('E01', 'Buffer创建时配置RC池；不是每次dispatch创建新QP', [
            (py, "os.environ['NVSHMEM_IBGDA_NUM_RC_PER_PE']", "os.environ['NVSHMEM_QP_DEPTH']"),
            (cpp, 'auto nvshmem_rank =', 'internode::alloc('),
            (device, 'nvshmemi_ibgda_device_qp_t* ibgda_get_rc(', 'return &state->globalmem.rcs')]),
        ('E02', 'WQE预留推进已选QP的resv_head；不能据此认定RTT估计器或硬件时延', [
            (device, 'ibgda_reserve_wqe_slots(nvshmemi', 'return atomicAdd')]),
        ('E03', 'Dispatch逐token、逐expert发数据；同rank的不同expert没有rank去重', [
            (kernel, 'auto dst_expert_idx = warp_id', None),
            (kernel, 'int slot_idx = lane_id', 'nvshmemi_ibgda_put_nbi_warp(dst_ptr, src_ptr')]),
        ('E04', 'Dispatch对每个source/expert发count，零token也发布-1并由接收方等待', [
            (kernel, '// Issue count sends', 'st_release_sys_global(reinterpret_cast<int*>(dst_p2p_ptr), -num_tokens_sent - 1)'),
            (kernel, 'while ((num_recv_tokens = ld_acquire_sys_global', 'num_recv_tokens = -num_recv_tokens - 1;')]),
        ('E05', 'Combine每本地expert/原source发flag，循环为空仍发布flag', [
            (kernel, '// Put the finishing flag', 'st_release_sys_global(reinterpret_cast<int*>(dst_p2p_ptr), 1)'),
            (kernel, 'while (ld_acquire_sys_global(rdma_recv_flag', None)]),
        ('E06', 'BF16 Dispatch带16B token元数据；FP8另带scale；Combine BF16只发向量', [
            (kernel, '// Message package: index at source', 'const size_t num_bytes_per_msg'),
            (kernel, 'int num_send_bytes = hidden', None)]),
        ('E07', '数据写与通知选择同一peer/expert QP；一个put可能按key边界拆多WQE', [
            (device, 'nvshmemi_ibgda_put_nbi_warp(uint64_t', 'auto qp = ibgda_get_rc'),
            (device, 'void nvshmemi_ibgda_amo_nonfetch_add(', 'nvshmemi_ibgda_device_qp_t *qp'),
            (device, '// Decide how many messages', 'EP_DEVICE_ASSERT(num_wqes <= 32)')]),
        ('E08', '每次Dispatch与Combine均翻转双buffer，不能任意提前复用', [
            (py, 'Warning: as there are only two buffers', 'low-latency kernels\' result tensors'),
            (cpp, 'auto buffer = layout.buffers[low_latency_buffer_idx];', None)]),
        ('E09', '异步返回CUDA事件；hook只issue、后续显式receive，二者不同时启用', [
            (py, 'async_finish: the current stream will not wait', 'If you do not set this flag, the kernel will ensure'),
            ('csrc/event.hpp', 'explicit EventHandle(const', 'event->record(stream)'),
            (cpp, 'EP_HOST_ASSERT(not (async and return_recv_hook));', None)]),
        ('E10', 'destroy路径才释放NVSHMEM；其内部与NIC恢复/拥塞状态仍需单独核验', [
            (cpp, 'internode::free(rdma_buffer_ptr);', 'internode::finalize();')]),
    ]
    # Dispatch and combine contain identical control statements: scope before locating.
    findings = []
    for sid, statement, anchors in specs:
        proof = []
        for path, first, last in anchors:
            if path == cpp and first in ('auto buffer = layout.buffers[low_latency_buffer_idx];', 'EP_HOST_ASSERT(not (async and return_recv_hook));'):
                lines = texts[path].splitlines()
                matches = [i for i, line in enumerate(lines) if first in line]
                assert len(matches) == (3 if first.startswith('auto buffer') else 2)
                for i in matches[:2]:
                    n = 2 if first.startswith('auto buffer') else 1
                    proof.append(dict(path=path, first_line=i+1, last_line=i+n,
                                      excerpt='\n'.join(lines[i:i+n]), url=f'https://github.com/deepseek-ai/DeepEP/blob/{COMMIT}/{path}#L{i+1}-L{i+n}'))
            elif path == py and first.startswith('async_finish:'):
                # First three are throughput APIs; last two are low-latency.
                proof.extend(span(texts, path, first, last, occurrence=i, expected=5) for i in (3, 4))
            elif path == py and first.startswith('Warning: as there are only two buffers'):
                proof.extend(span(texts, path, first, last, occurrence=i, expected=2) for i in range(2))
            else:
                proof.append(span(texts, path, first, last))
        findings.append(dict(id=sid, statement=statement, status='fixed_source_static', evidence=proof))
    return findings


def account(selected, experts, hidden):
    """Four distinct requests, one token per rank; uniform contiguous placement.

    All off-rank calls would use IBGDA only if cross-rank P2P is disabled.
    The supplied choices are facts; this deployment and precision are hypotheses.
    """
    ranks = len(selected)
    assert ranks == 4 and experts % ranks == 0 and hidden % 128 == 0
    matrix = Counter()
    for src, choices in enumerate(selected):
        for expert in choices:
            dst = expert // (experts//ranks)
            if src != dst:
                matrix[src, dst] += 1
    n = sum(matrix.values())
    directed = ranks*(ranks-1)
    notify_per_leg = (ranks-1)*experts
    # Choices are unique within each token; one token/source, so each touched
    # source/expert entry contributes exactly one put and one nonzero count.
    return dict(offrank_contributions=n, rank_pair_messages_two_legs=2*len(matrix),
                offrank_payload_put_calls_two_legs=2*n,
                count_atomic_calls=notify_per_leg, combine_flag_atomic_calls=notify_per_leg,
                zero_token_count_atomic_calls=notify_per_leg-n,
                zero_token_combine_flag_atomic_calls=notify_per_leg-n,
                zero_payload_rank_pairs_per_leg=directed-len(matrix),
                abstract_two_leg_bf16_operand_bytes=2*n*hidden*2,
                deepep_two_leg_bf16_data_operand_bytes=n*(4*hidden+16),
                deepep_dispatch_fp8_data_operand_bytes=n*(hidden+(hidden//128)*4+16),
                dispatch_metadata_bytes=n*16)


def routes(manifest_path):
    manifest_raw = checked(manifest_path)
    manifest = load(manifest_raw)
    assert manifest['schema_version'] == 'moe-ub-route-manifest/1'
    assert manifest['source']['kind'] == 'published_model_trace'
    files, layers = manifest['files'], manifest['layers']
    assert len(files) == 4 and len(layers) == manifest['model']['num_layers'] == 61
    assert [l['layer_id'] for l in layers] == list(range(61))
    assert all(l['kind'] == ('dense' if l['layer_id'] < 3 else 'moe') for l in layers)
    assert all(l['num_experts'] == 256 and l['top_k'] == 8 for l in layers[3:])
    assert manifest['model']['hidden_dim'] == 7168
    records, identities = [], []
    total = 0
    for row in files:
        path = (ROOT / row['file']['path']).resolve()
        raw = checked(path, 16*2**20)
        total += len(raw)
        assert total <= 16*2**20 and sha(raw) == row['file']['sha256']
        source = load(raw)
        assert len(source) == 129
        prompt = None
        for entry_no, entry in enumerate(source):
            assert set(entry) == {str(i) for i in range(61)}
            for layer in layers:
                value = entry[str(layer['layer_id'])]
                if layer['kind'] == 'dense':
                    assert value is None
                    continue
                assert isinstance(value, list) and value
                if entry_no:
                    assert len(value) == 1
                else:
                    if prompt is None:
                        prompt = len(value)
                    assert len(value) == prompt
                for choices in value:
                    assert len(choices) == len(set(choices)) == 8
                    assert all(type(e) is int and 0 <= e < 256 for e in choices)
        records.append(source)
        identities.append(dict(file_id=row['file_id'], original_path=row['original_path'],
                               sha256=sha(raw), bytes=len(raw), prompt_tokens=prompt, decode_entries=128))
    assert len({r['file_id'] for r in identities}) == len({r['sha256'] for r in identities}) == 4
    totals = Counter()
    by_layer = []
    for lid in range(3, 61):
        counts = Counter()
        for entry in range(1, 129):
            counts.update(account([r[entry][str(lid)][0] for r in records], 256, 7168))
        totals.update(counts)
        by_layer.append(dict(source_layer_id=lid, synchronized_windows=128, **dict(counts)))
    return dict(manifest=dict(sha256=sha(manifest_raw), path=str(manifest_path.relative_to(ROOT))),
                source=manifest['source'], files=identities, input_bytes=total,
                synchronized_layer_windows=128*58, request_layer_records=4*128*58,
                hypothetical_deployment=dict(ep_ranks=4, batch_per_rank=1, experts=256, top_k=8,
                    placement='contiguous_equal', rank_assignment=[r['file_id'] for r in identities],
                    cross_rank_p2p=False, timing_inferred=False,
                    synchronization='equal_decode_index; no observed arrival times',
                    operand_precision='BF16 and alternative FP8 accounting; original activation dtype unknown'),
                totals=dict(totals), by_layer=by_layer), records, identities


def compare_graphs(directory, records, identities):
    """Read public saved-wave output; do not call private graph generators."""
    items = []
    input_config = load(checked(directory / 'input.json'))
    assert input_config['parallel']['groups'] == [[0, 1, 2, 3]]
    assert input_config['workload']['batch_per_rank'] == 1
    mapping = input_config['route_replay']['requests']
    lookup = {r['file_id']: (source, r['sha256']) for r, source in zip(identities, records)}
    rank_map = {m['rank_id']: m for m in mapping}
    assert set(rank_map) == set(range(4)) and len(mapping) == 4
    assert [rank_map[r]['file_id'] for r in range(4)] == [r['file_id'] for r in identities]
    layers = input_config['route_replay']['layer_ids']
    assert input_config['model']['hidden_size'] == 7168 and input_config['model']['activation_bytes'] == 2
    total = Counter()
    for step in range(input_config['workload']['decode_steps']):
        path = directory / 'graphs' / f'g0.w{step}' / 'wave.json'
        raw = checked(path)
        wave = load(raw)
        assert wave['rank_ids'] == list(range(4)) and wave['decode_step'] == step
        assert [r['source_layer_id'] for r in wave['routes']] == layers
        expected = Counter()
        for route in wave['routes']:
            lid = route['source_layer_id']
            assert lid == layers[route['layer']]
            assert len(route['tokens']) == 4 and sorted(t['rank'] for t in route['tokens']) == list(range(4))
            selected = []
            for src in range(4):
                token = next(t for t in route['tokens'] if t['rank'] == src)
                assignment = rank_map[src]
                source, source_sha = lookup[assignment['file_id']]
                entry = assignment['first_decode_entry'] + step
                assert token['experts'] == source[entry][str(lid)][0]
                assert token['source']['sha256'] == source_sha and token['source']['source_entry'] == entry
                selected.append(token['experts'])
                for e in token['experts']:
                    dst = e//64
                    if src != dst:
                        expected[route['layer'], 'dispatch', src, dst] += 1
                        expected[route['layer'], 'combine', dst, src] += 1
            total.update(account(selected, 256, 7168))
        actual = {}
        for m in wave['messages']:
            key = m['layer'], m['phase'], m['src'], m['dst']
            assert key not in actual
            actual[key] = m['contributions']
            assert m['bytes'] == m['contributions']*7168*2
        assert actual == dict(expected)
        items.append(dict(path=str(path.relative_to(directory)), sha256=sha(raw), rank_pair_messages=len(actual)))
    return dict(scope='existing public output reanalysis; no new network execution',
                input_sha256=sha(checked(directory / 'input.json')), waves=items, totals=dict(total),
                all_saved_messages_match_abstract_payload_accounting=True,
                deepep_notifications_present_in_saved_messages=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-dir', required=True, type=Path)
    p.add_argument('--manifest', required=True, type=Path)
    p.add_argument('--graphs', type=Path, help='Optional existing public rdma-run output; read only')
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    verified, texts = sources(a.source_dir.resolve())
    findings = evidence(texts)
    accounting, records, identities = routes(a.manifest.resolve())
    summary = dict(schema_version='moe-deepep-static-audit/1', sources=verified, findings=findings,
                   accounting=accounting, hardware_measured=False, simulated_new_packets=False,
                   latency_or_speedup_claim=False, overall_paper_ready=False)
    if a.graphs:
        summary['saved_graph_comparison'] = compare_graphs(a.graphs.resolve(), records, identities)
    platform = ['python/moe_ub_cosim/scaleout_service.py', 'backend/rdma/fabric.cc',
                'backend/rdma/astra_adapter.cc', 'backend/rdma/service.cc',
                'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3/src/point-to-point/model/rdma-hw.cc']
    summary['platform_sources'] = [dict(path=n, sha256=sha(checked(ROOT / n))) for n in platform]
    save(a.output / 'audit.json', summary)
    print(json.dumps(dict(verified_sources=len(verified), findings=len(findings),
                         windows=accounting['synchronized_layer_windows'], totals=accounting['totals'])))


if __name__ == '__main__':
    main()
