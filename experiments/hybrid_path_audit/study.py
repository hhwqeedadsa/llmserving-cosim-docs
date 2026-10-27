"""Source-bound hierarchical demand accounting; no network or timing model."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import tarfile

ARCHIVE_SHA = '25ce43a9dc4d72c402df935529d1083dd2801d236b85131ad5646762fc758e40'
COMMIT = 'f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
EPS = (64, 128, 256)
BINDINGS = ('contiguous', 'spread')
HIDDEN_BYTES = 7168*2
CHANNELS = 4
INTERVAL = 6
PATHS = (
    'deep_ep/include/deep_ep/impls/ep/dispatch.cuh',
    'deep_ep/include/deep_ep/impls/ep/hybrid_dispatch.cuh',
    'deep_ep/include/deep_ep/impls/ep/hybrid_combine.cuh',
    'deep_ep/include/deep_ep/layout/ep/token.cuh',
    'csrc/kernels/comm/context.cpp',
    'deep_ep/include/deep_ep/comm/barrier.cuh',
)


def load(path): return json.loads(path.read_bytes())
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def prepare(repo, run):
    from experiments.ecmp_network_choice.checkpoint import admit
    archive = repo/'runs/ecmp_network_choice_20261027/checkpoint.tar.gz'
    manifest = admit(archive, ARCHIVE_SHA)
    observed = run/'observed'
    observed.mkdir()
    with tarfile.open(archive, 'r:gz') as stream:
        intake = json.loads(stream.extractfile('run/stage/observed/intake.json').read())
        selected = sorted((r for r in intake['files'] if r['eligible_offline']), key=lambda r:int(r['name'][:-5]))
        assert len(selected) == 15 and intake['rejected_files'] == ['899.json', '1479.json']
        files = []
        for row in selected:
            member = 'run/stage/truth/routes/'+row['name']
            raw = stream.extractfile(member).read()
            assert hashlib.sha256(raw).hexdigest() == row['sha256'] == manifest['files'][member]['sha256']
            path = observed/'routes'/row['name']
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(raw)
            files.append(dict(name=row['name'], sha256=row['sha256'], bytes=len(raw)))
    save(observed/'routes.json', dict(files=files, source=intake['source'], excluded=['899.json', '1479.json'],
        input_archive_sha256=ARCHIVE_SHA, inferred_eos=False, authentic_request_timestamps=False))
    original = repo/'data/research/deepep_modern_20261022'
    tree = load(original/'tree.json')
    assert tree['sha'] == COMMIT and not tree['truncated']
    blobs = {r['path']:r['sha'] for r in tree['tree'] if r['type'] == 'blob'}
    descriptions = {r['path']:r for n in ('file-sources.json', 'file-sources-completion.json')
                    for r in load(original/n)['sources']}
    sources = {}
    for name in PATHS:
        row = descriptions[name]
        path = original/'sources'/name
        raw = path.read_bytes()
        assert row['commit'] == COMMIT and sha(path) == row['sha256']
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() == row['git_blob_sha'] == blobs[name]
        dest = observed/'library'/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        sources[name] = dict(sha256=sha(dest), git_blob_sha=blobs[name], public_url=row['public_url'])
    save(observed/'library.json', dict(commit=COMMIT, files=sources))
    save(run/'input-identity.json', {str(p.relative_to(run)):sha(p) for p in sorted(observed.rglob('*')) if p.is_file()})
    save(run/'preparation.json', dict(passed=True, files=15, fixed_library_sources=6,
        source_archive_sha256=ARCHIVE_SHA, new_networks=0, new_downloads=0))


def validate_inputs(run):
    assert all(sha(run/n) == h for n,h in load(run/'input-identity.json').items())
    source = load(run/'observed/routes.json')
    assert len(source['files']) == 15 and source['excluded'] == ['899.json', '1479.json']
    originals = []
    for row in source['files']:
        path = run/'observed/routes'/row['name']
        assert sha(path) == row['sha256']
        raw = load(path)
        assert isinstance(raw, list) and len(raw) > 1
        for entry, value in enumerate(raw):
            assert isinstance(value, dict) and all(str(l) in value for l in range(3,61))
            for layer in range(3,61):
                tokens = value[str(layer)]
                assert isinstance(tokens, list) and (entry == 0 or len(tokens) == 1)
                for choices in tokens:
                    assert len(choices) == len(set(choices)) == 8
                    assert all(type(e) is int and 0 <= e < 256 for e in choices)
        originals.append(raw)
    return source, originals


def source_evidence(observed):
    anchors = {
        PATHS[0]: ('ptx::deduplicate',),
        PATHS[1]: ('int kScaleoutUpdateInterval = 6,', 'update_scaleout_tail(true);',
            'stored_dst_scaleout_rank_idx = dst_expert_idx >= 0 ? dst_expert_idx / kNumExpertsPerScaleout : -1;',
            'if (ptx::deduplicate(stored_dst_scaleup_rank_idx, lane_idx) and stored_dst_scaleup_rank_idx >= 0)',
            'if (lane_idx < kNumScaleoutRanks and', 'stored_scaleout_tail >= stored_old_scaleout_tail + kScaleoutUpdateInterval or finish_flag',
            'As scale-out tokens have already been consumed by forwarders, no need to do scale-out barrier again'),
        PATHS[2]: ('if constexpr (not kAllowMultipleReduction)',
            'auto topk_valid_mask = kUseExpandedLayout ?', 'flush_last_tma_and_issue_rdma();',
            '// Replay the dispatch', 'token_metadata_at_forward'),
        PATHS[4]: ('NCCL_GIN_CONNECTION_RAIL : NCCL_GIN_CONNECTION_FULL',),
        PATHS[5]: ('barrier_gin.signal<team_t>',),
    }
    result = {}
    for name, needles in anchors.items():
        lines = (observed/'library'/name).read_text().splitlines()
        locations = {n:[i for i,line in enumerate(lines,1) if n in line] for n in needles}
        assert all(locations.values()), (name, locations)
        result[name] = dict(sha256=sha(observed/'library'/name), anchor_lines=locations)
    return result


def summarize_case(originals, ep, binding):
    domains = ep//8
    result = {p:Counter() for p in ('prefill','decode')}
    calls = {p:set() for p in result}
    streams = Counter()
    for index, raw in enumerate(originals):
        src = index if binding == 'contiguous' else index*ep//len(originals)
        for entry, data in enumerate(raw):
            phase = 'prefill' if entry == 0 else 'decode'
            m = result[phase]
            for layer in range(3,61):
                calls[phase].add((entry,layer))
                for token, experts in enumerate(data[str(layer)]):
                    ranks = {e//(256//ep) for e in experts}
                    groups = {r//8 for r in ranks}
                    remote = {r for r in ranks if r//8 != src//8}
                    far = groups-{src//8}
                    forward = sum(r%8 != src%8 for r in ranks)
                    m.update(tokens=1, expert_contributions=8, destination_gpus=len(ranks),
                        destination_domains=len(groups), direct_cross_server_vectors=len(remote),
                        hybrid_cross_server_vectors=len(far),
                        hybrid_intra_server_remote_gpu_vectors=forward,
                        hybrid_ingress_self_gpu_copies=len(ranks)-forward,
                        combine_multiple_reduction_vectors=len(far),
                        combine_no_multiple_unexpanded_vectors=len(remote),
                        combine_no_multiple_expanded_vectors=sum(e//(256//domains) != src//8 for e in experts))
                    for domain in far:
                        streams[entry,layer,src,token%CHANNELS,domain] += 1
    for phase,m in result.items():
        count = len(calls[phase])
        relevant = {k:v for k,v in streams.items() if (k[0] == 0) == (phase == 'prefill')}
        finish = count*ep*CHANNELS*(domains-1)
        m.update(declared_collectives=count, data_carrying_remote_channels=len(relevant),
            dispatch_remote_finish_calls=finish, dispatch_empty_channel_finish_calls=finish-len(relevant),
            dispatch_remote_periodic_tail_calls=sum(n//INTERVAL for n in relevant.values()),
            dispatch_remote_count_block_put_calls=count*2*ep*(domains-1),
            dispatch_remote_count_operand_bytes=count*ep*(domains-1)*(8+256//domains)*4)
        m['dispatch_hidden_bytes_direct'] = m['direct_cross_server_vectors']*HIDDEN_BYTES
        m['dispatch_hidden_bytes_hybrid'] = m['hybrid_cross_server_vectors']*HIDDEN_BYTES
        m['hybrid_intra_server_remote_gpu_hidden_bytes'] = m['hybrid_intra_server_remote_gpu_vectors']*HIDDEN_BYTES
    candidates = sorted(k for k,n in streams.items() if k[0] == 0 and n >= 7)
    witness = None
    if candidates:
        entry,layer,src,channel,domain = candidates[0]
        index = next(i for i in range(len(originals)) if (i if binding == 'contiguous' else i*ep//len(originals)) == src)
        token_indices = [i for i, es in enumerate(originals[index][entry][str(layer)])
                         if i%CHANNELS == channel and any(e//(256//domains) == domain for e in es)]
        assert len(token_indices) >= 7
        witness = dict(ep=ep,binding=binding,entry=entry,layer=layer,source_rank=src,
            request_index=index,channel=channel,destination_domain=domain,
            stream_tokens=len(token_indices),first_seven_token_indices=token_indices[:7],
            first_seven_choices=[originals[index][entry][str(layer)][i] for i in token_indices[:7]])
        witness['necessary_order_subgraph'] = order_subgraph(len(token_indices))
    return dict(ep=ep,servers=domains,gpus_per_server=8,binding=binding,
                phases={p:dict(m) for p,m in result.items()},prefill_overlap_witness=witness)


def order_subgraph(n):
    """A single-channel necessary-order projection, not a timed/full GPU model."""
    edges = {}
    for i in range(n):
        edges[f'issue.{i}'] = ([] if i == 0 else [f'issue.{i-1}']) + ([f'tail.{i}.release'] if i and i%6 == 0 else [])
        edges[f'visible.{i}'] = [f'issue.{i}']
    for k in range(6,n+1,6):
        edges[f'tail.{k}.release'] = [f'visible.{i}' for i in range(k)] + ([f'tail.{k-6}.release'] if k>6 else [])
        edges[f'tail.{k}.visible'] = [f'tail.{k}.release']
    edges['finish.release'] = [f'visible.{i}' for i in range(n)]+[f'tail.{k}.release' for k in range(6,n+1,6)]
    edges['finish.visible'] = ['finish.release']
    for i in range(n):
        k = ((i//6)+1)*6
        edges[f'forward.{i}'] = ([f'tail.{k}.visible'] if k<=n else ['finish.visible']) + ([] if i==0 else [f'forward.{i-1}'])
    remaining, seen, order = set(edges), set(), []
    priority = {'forward':0,'visible':1,'tail':2,'issue':3,'finish':4}
    while remaining:
        ready = [key for key in remaining if set(edges[key]) <= seen]
        assert ready
        key = min(ready, key=lambda k:(priority[k.split('.')[0]],k))
        remaining.remove(key);seen.add(key);order.append(key)
    assert order.index('forward.0') < order.index('finish.visible')
    return dict(after=edges,linear_extension=order,
        scope='necessary_single_channel_source_projection_not_full_execution_or_timing')


def analyze(run, output):
    assert not output.exists()
    _, originals = validate_inputs(run)
    anchors = source_evidence(run/'observed')
    cases = [summarize_case(originals,ep,binding) for ep in EPS for binding in BINDINGS]
    save(output, dict(schema_version='hybrid-path-audit/1',cases=cases,source_evidence=anchors,
        declared_channels=CHANNELS,dispatch_tail_interval=INTERVAL,hidden_bytes_per_vector=HIDDEN_BYTES,
        library_commit=COMMIT,input_identity_sha256=sha(run/'input-identity.json'),
        only_data_vectors_and_named_dispatch_control_subset=True,
        collective_alignment_is_declared=True,EOS_not_inferred=True,packet_networks=0,
        actual_GPU_or_provider_runs=0,full_hybrid_model_implemented=False,
        new_method_latency_benefit_proven=False,simulator_speedup_proven=False))


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('prepare','analyze'))
    p.add_argument('--repository',type=Path);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path);a=p.parse_args()
    if a.action == 'prepare':prepare(a.repository.resolve(),a.run.resolve())
    else:analyze(a.run.resolve(),a.output.resolve())
