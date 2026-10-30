"""Source-bound consumption audit; public offline commands, no network timing."""
import argparse
from collections import Counter, deque
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
LL = '9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee'
MODERN = 'f6aa38fe06cd4a0489e7730365d4e0114970a2f3'


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    require(path.stat().st_size <= 16 * 2**20, 'oversized JSON input')
    return json.loads(path.read_bytes())


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def file_hashes(path):
    return {str(p.relative_to(path)): sha(p) for p in sorted(path.rglob('*')) if p.is_file()}


def verify_source(raw, metadata, revision):
    require(metadata['commit'] == revision, 'source revision changed')
    require(len(raw) == metadata['bytes'], 'source size changed')
    require(hashlib.sha256(raw).hexdigest() == metadata['sha256'], 'source SHA256 changed')
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    require(blob == metadata['git_blob_sha'], 'source Git blob changed')
    url = f'https://github.com/deepseek-ai/DeepEP/blob/{revision}/{metadata["path"]}'
    require(metadata['public_url'] == url, 'source public URL/revision mismatch')


# Excerpts explain reviewed conditions; string checks are location guards, not
# an automated proof of arbitrary CUDA semantics. Full-file identities are pinned.
SOURCE_SPECS = [
    ('ll', 'csrc/deep_ep.cpp', [
        (1125, 1137, 'low_latency_buffer_idx ^= 1'),
        (1184, 1202, 'launcher(LOW_LATENCY_RECV_PHASE)'),
        (1247, 1258, 'low_latency_buffer_idx ^= 1'),
        (1287, 1305, 'stream_wait(compute_stream, launch_stream)')]),
    ('ll', 'csrc/kernels/internode_ll.cu', [
        (183, 192, 'next_clean[i] = 0'),
        (220, 234, '-num_tokens_sent - 1'),
        (274, 284, 'num_recv_tokens = -num_recv_tokens - 1'),
        (738, 751, 'nvshmemi_ibgda_amo_nonfetch_add'),
        (770, 790, 'cg::this_grid().sync()')]),
    ('ll', 'csrc/kernels/ibgda_device.cuh', [(495, 500, 'ibgda_poll_cq')]),
    ('modern', 'csrc/buffers/ep.hpp', [
        (152, 180, 'record_stream'), (770, 786, 'pybind11::none(), event, epilogue_hook')]),
    ('modern', 'deep_ep/include/deep_ep/comm/barrier.cuh', [
        (146, 167, 'flush(ncclCoopWarp())')]),
    ('modern', 'deep_ep/include/deep_ep/impls/ep/dispatch.cuh', [
        (393, 397, 'kDispatchTag1, true, true, false')]),
    ('modern', 'deep_ep/include/deep_ep/impls/ep/combine.cuh', [
        (77, 82, 'kCombineTag0, false, false, true'),
        (240, 244, 'kCombineTag1, true, true, false')]),
    ('modern', 'deep_ep/include/deep_ep/impls/ep/hybrid_dispatch.cuh', [
        (658, 666, '/* do not scale-out */ false, true')]),
    ('modern', 'deep_ep/include/deep_ep/impls/ep/hybrid_combine.cuh', [
        (588, 605, 'gin.flush<ncclCoopWarp>()'),
        (606, 625, 'No barrier at epilogue')]),
]


def sources(ll_root, modern_root):
    tables = {}
    for name, root, manifests in [
        ('ll', ll_root, ['file-sources.json']),
        ('modern', modern_root, ['file-sources.json', 'file-sources-completion.json'])]:
        table = {}
        for manifest in manifests:
            for row in load(root / manifest)['sources']:
                if row['path'] in table:
                    require(table[row['path']]['sha256'] == row['sha256'], 'conflicting source manifests')
                table[row['path']] = row
        tables[name] = root, table
    result = []
    for name, relative, excerpts in SOURCE_SPECS:
        root, table = tables[name]
        meta = table[relative]
        path = root / 'sources' / relative
        raw = path.read_bytes()
        verify_source(raw, meta, LL if name == 'll' else MODERN)
        lines = raw.decode().splitlines()
        evidence = []
        for first, last, expected in excerpts:
            excerpt = '\n'.join(lines[first-1:last])
            require(expected in excerpt, 'reviewed source boundary moved: ' + relative)
            evidence.append(dict(first_line=first, last_line=last, excerpt=excerpt,
                                 url=f'{meta["public_url"]}#L{first}-L{last}'))
        result.append(dict(**{k: meta[k] for k in ('path', 'commit', 'sha256', 'git_blob_sha', 'bytes', 'public_url')},
                           local_path=str(path.resolve()), excerpts=evidence))
    return result


def closure(plan, withheld=()):
    """Independent Kahn traversal of saved public events; no compiler imports."""
    rows = plan['events']
    nodes = {r['id']: r for r in rows}
    require(len(nodes) == len(rows), 'duplicate event')
    held = set(withheld)
    require(held <= nodes.keys(), 'unknown withheld event')
    children = {k: [] for k in nodes}
    counts = {}
    for identity, row in nodes.items():
        require(len(row['deps']) == len(set(row['deps'])), 'duplicate dependency')
        require(set(row['deps']) <= nodes.keys(), 'unknown dependency')
        counts[identity] = len(row['deps'])
        for parent in row['deps']:
            children[parent].append(identity)
    ready = deque(k for k, n in counts.items() if n == 0 and k not in held)
    done = set()
    while ready:
        identity = ready.popleft()
        done.add(identity)
        for child in children[identity]:
            counts[child] -= 1
            if counts[child] == 0 and child not in held:
                ready.append(child)
    return done


def summarize(plan, done):
    result = {}
    for phase in ('dispatch', 'combine'):
        expected = {f'm0.{phase}.r{rank}.complete': rank for rank in range(32)}
        require(set(expected) <= {r['id'] for r in plan['events']}, 'missing expected phase completion')
        result[phase + '_blocked_ranks'] = [rank for event, rank in expected.items() if event not in done]
    result['reachable_events'] = len(done)
    result['events'] = len(plan['events'])
    return result


def notification_probes():
    # Two *application* attempts, not duplicate packets within reliable transport.
    notifications = []
    for n in (0, 1, 2, 8):
        encoded = -n-1
        twice = 2*encoded
        decoded = -twice-1
        require(decoded == 2*n+1 and decoded != n, 'duplicate count arithmetic changed')
        notifications.append(dict(actual_tokens=n, once=encoded, twice=twice,
                                  receiver_decodes_after_two_publications=decoded))
    # Conditional on an INVALID early reset while the old attempt still owns
    # writes: the flag is an integer, without an epoch in this operand.
    flag_after_early_reset = 0
    flag_after_old_late_publication = flag_after_early_reset + 1
    require(flag_after_old_late_publication != 0, 'late flag not observable')
    shared = bytearray(b'----')
    shared[:] = b'NEW!'
    shared[:] = b'OLD!'
    separate = [bytearray(b'OLD!'), bytearray(b'NEW!')]
    separate[0][:] = b'LATE'
    require(bytes(shared) == b'OLD!' and bytes(separate[1]) == b'NEW!', 'address isolation probe failed')
    return dict(scope='CPU algebra and ordinary memory alias examples, no DeepEP execution or vulnerability claim',
                duplicate_application_count_publications=notifications,
                invalid_early_reuse=dict(flag_before_old_arrival=0, flag_after_old_arrival=flag_after_old_late_publication,
                                        old_flag_satisfies_nonzero_predicate=True),
                address_alias=dict(logical_epoch_alone_prevents_overwrite=False,
                                   shared_output_after_late_write=bytes(shared).decode(),
                                   separate_output_after_late_write=bytes(separate[1]).decode()),
                transport_sr_deduplication_is_not_application_attempt_deduplication=True)


def command(out, label, args, expected=0, diagnostic=None):
    directory = out / 'commands' / label
    directory.mkdir(parents=True, exist_ok=False)
    argv = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', *map(str, args)]
    with (directory/'stdout.log').open('w') as stdout, (directory/'stderr.log').open('w') as stderr:
        proc = subprocess.run(argv, cwd=ROOT, stdout=stdout, stderr=stderr, timeout=90)
    save(directory/'command.json', dict(argv=argv, cwd=str(ROOT), exit_code=proc.returncode,
                                        expected_exit_code=expected))
    require(proc.returncode == expected, 'public command failed: ' + label)
    if diagnostic:
        require(diagnostic in (directory/'stderr.log').read_text(), 'expected diagnostic absent: ' + label)


def prepare_inputs(run, out):
    config = load(run/'input.json')
    require(config['model']['experts'] == 256 and config['model']['top_k'] == 8 and
            config['model']['hidden_size'] == 7168, 'unexpected source model')
    require(config['parallel']['groups'] == [list(range(i, 128, 4)) for i in range(4)], '128-endpoint EP32 scope changed')
    rows = []
    (out/'inputs').mkdir()
    for step in range(2):
        wave = load(run/f'graphs/g0.w{step}/wave.json')
        ranks = wave['rank_ids']
        require(ranks == list(range(0, 128, 4)) and len(wave['routes']) == 2, 'wave group/layer mismatch')
        for layer, route in enumerate(wave['routes']):
            tokens = [[] for _ in ranks]
            matrix = Counter()
            origins = []
            for token in sorted(route['tokens'], key=lambda t: (t['rank'], t['slot'])):
                rank = ranks.index(token['rank'])
                require(token['slot'] == len(tokens[rank]), 'nonsequential token slots')
                choices = token['experts']
                require(len(choices) == len(set(choices)) == 8 and all(type(e) is int and 0 <= e < 256 for e in choices), 'invalid real choices')
                tokens[rank].append(choices)
                for expert in choices:
                    matrix[token['rank'], ranks[expert//8]] += 1
                origins.append(dict(logical_rank=rank, physical_endpoint=token['rank'], slot=token['slot'], **token['source']))
            require(dict(matrix) == {(r['src'], r['dst']): r['count'] for r in route['contributions']}, 'placement projection differs')
            name = f's{step}-l{layer}'
            modern = dict(schema_version='moe-modern-deepep-input/1',
                model=dict(experts=256, top_k=8, hidden_size=7168, precision='bf16'),
                execution=dict(ranks=32, max_tokens_per_rank=1, mode='direct_rdma_only_fresh',
                    stream='compute_wait_no_deferred_epilogue', expanded_layout=True,
                    allow_multiple_reduction=True, expert_alignment=1, num_sms=2,
                    dispatch_warps=2, combine_warps=2, call_contexts=5, requested_contexts=5,
                    connections=1, local_devices=[[0] for _ in ranks]),
                calls=[dict(name=name, tokens=tokens)],
                limits=dict(max_events=65536, max_operations=16384, max_input_bytes=2**20, max_output_bytes=16*2**20))
            save(out/'inputs'/f'{name}.json', modern)
            # This budget rejection is preregistered, not a reason to alter input.
            if step == layer == 0:
                ll = dict(schema_version='moe-deepep-ll-plan-input/1',
                    model=dict(experts=256, top_k=8, hidden_size=7168, dispatch_format='bf16'),
                    execution=dict(ranks=32, api='compute_stream_wait_no_hook', cross_rank_path='rdma_only',
                        nics_per_rank=1, max_tokens_per_rank=1), calls=modern['calls'], limits=modern['limits'])
                save(out/'inputs'/'low-latency-s0-l0.json', ll)
            rows.append(dict(name=name, step=step, layer=layer, source_layer=route['source_layer_id'],
                             tokens=sum(map(len, tokens)), contributions=sum(len(c) for ts in tokens for c in ts),
                             rank_map=ranks, origins=origins))
    return rows


def run_audit(args):
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    run = args.run.resolve()
    before = file_hashes(run)
    evidence = sources(args.ll_source.resolve(), args.modern_source.resolve())
    save(out/'sources.json', evidence)
    inputs = prepare_inputs(run, out)
    save(out/'frozen.json', dict(platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        run=str(run), run_sha256=before, inputs=inputs, input_sha256=file_hashes(out/'inputs'),
        source_sha256={r['path']: r['sha256'] for r in evidence},
        method_sha256=sha(Path(__file__).with_name('protocol.md')),
        analyzer_sha256=sha(Path(__file__)), network_executed=False))
    (out/'plans').mkdir()
    (out/'checks').mkdir()
    comparisons = []
    for row in inputs:
        name = row['name']
        directory = out/'plans'/name
        command(out, 'plan-'+name, ['modern-deepep-plan','--config',out/'inputs'/f'{name}.json','--output',directory])
        plan = load(directory/'plan.json')
        require(plan['source']['deepep'] == MODERN, 'compiler revision mismatch')
        probes = {'all': []}
        if name == 's0-l0':
            # Logical rank 7 is physical endpoint 28 in this EP32 group.
            for src in (1, 2, 3):
                op = next(o for o in plan['operations'] if o['id'] == f'm0.dispatch.data.s{src}.t0.d7')
                require(op['topk_positions'] == [load(out/'inputs'/f'{name}.json')['calls'][0]['tokens'][src][0].index(56)],
                        'missing input no longer matches expert 56')
            zero = next(o for o in plan['operations'] if o['kind']=='expert_count_block' and
                        o['src'] != o['dst'] and all(v == -1 for v in o['values']))
            probes.update(
                missing_target_visibility=[f'm0.dispatch.data.s{s}.t0.d7.visible' for s in (1,2,3)],
                missing_source_completion=['m0.dispatch.data.s1.t0.d7.local_complete'],
                zero_count_visibility=[zero['events']['visible']],
                final_signal_completion=['m0.combine.exit.s0.d1.local_complete'])
        for probe, held in probes.items():
            dest = out/'checks'/f'{name}-{probe}.json'
            argv = ['modern-deepep-check','--plan',directory,'--output',dest]
            for event in held:
                argv += ['--withhold',event]
            command(out, 'check-'+name+'-'+probe, argv)
            actual = load(dest)
            done = closure(plan, held)
            expected_blocked = [e['id'] for e in plan['events'] if e['kind']=='application_stage_complete' and e['id'] not in done]
            require(actual['blocked_completions'] == expected_blocked and actual['reachable_events']==len(done), 'independent closure disagrees')
            result = dict(input=name, probe=probe, withheld=held, **summarize(plan,done))
            if probe == 'all':
                require(len(done)==len(plan['events']), 'baseline DAG has unreachable nodes')
            elif probe == 'missing_target_visibility':
                require(result['dispatch_blocked_ranks']==[7] and result['combine_blocked_ranks']==list(range(32)), 'unexpected target visibility boundary')
            elif probe in ('missing_source_completion','zero_count_visibility'):
                require(result['dispatch_blocked_ranks']==result['combine_blocked_ranks']==list(range(32)), 'expected flush/count group constraint absent')
            else:
                require(not result['dispatch_blocked_ranks'] and not result['combine_blocked_ranks'] and len(done)<len(plan['events']), 'final signal completion conflated with consumption')
            comparisons.append(result)
    command(out, 'low-latency-shape-rejection', ['deepep-plan','--config',out/'inputs'/'low-latency-s0-l0.json',
        '--output',out/'plans'/'low-latency-s0-l0'], expected=1, diagnostic='budget exceeded')
    require(not (out/'plans'/'low-latency-s0-l0').exists(), 'failed plan published output')
    require(file_hashes(run)==before, 'historical network files changed')
    analysis = dict(passed=True, network_executed=False, cuda_executed=False, latency_predicted=False,
        sources_verified=len(evidence), input_projection=inputs, comparisons=comparisons,
        notification_probes=notification_probes(),
        low_latency_shape=dict(ranks=32, experts=256, real_contributions=32,
                              operations_required=16448, declared_operation_limit=16384,
                              public_command_rejected=True, limits_not_expanded=True),
        historical_network_unchanged=True, historical_file_count=len(before),
        scope='fixed-source audit and untimed logical probes; not hardware or recovery benefit')
    save(out/'analysis.json', analysis)
    print(json.dumps(dict(passed=True, sources=len(evidence), projections=len(inputs), probes=len(comparisons), new_networks=0)))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('run','ll-source','modern-source','output'):
        p.add_argument('--'+name, type=Path, required=True)
    run_audit(p.parse_args())


if __name__ == '__main__':
    main()
