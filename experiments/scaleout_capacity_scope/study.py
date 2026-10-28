"""Offline payload-service floors for declared, source-derived Hybrid routes."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil

INTAKE_SHA = '6fb24dd4229bbd42d73900062f769806aecda2b20a78c30c62c564ffe6cda558'
REVISION = 'f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
EPS = (64, 128, 256)
BINDINGS = ('packed', 'spread')
OPERANDS = {'dispatch': 14464, 'combine': 14400}
SOURCE_PATHS = ('deep_ep/include/deep_ep/impls/ep/hybrid_dispatch.cuh',
                'deep_ep/include/deep_ep/impls/ep/hybrid_combine.cuh',
                'deep_ep/include/deep_ep/layout/ep/token.cuh')


def load(path):
    return json.loads(path.read_bytes())


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))+'\n').encode()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(repo, stage):
    assert not (stage/'observed').exists(), 'source preparation must not be repeated'
    intake_path = repo/'experiments/route_coverage/results/local_additions_20261027/intake.json'
    assert sha(intake_path) == INTAKE_SHA
    intake = load(intake_path)
    selected = sorted((r for r in intake['files'] if r['eligible_offline']), key=lambda r: int(r['name'][:-5]))
    assert len(selected) == 15 and intake['rejected_files'] == ['899.json', '1479.json']
    observed = stage/'observed'
    (observed/'routes').mkdir(parents=True)
    shutil.copyfile(intake_path, observed/'intake.json')
    for row in selected:
        original = repo/'data/moe-trace-small'/row['name']
        assert sha(original) == row['sha256']
        shutil.copyfile(original, observed/'routes'/row['name'])
    base = repo/'data/research/deepep_modern_20261022'
    tree = load(base/'tree.json')
    assert tree['sha'] == REVISION and not tree['truncated']
    blobs = {r['path']: r['sha'] for r in tree['tree'] if r['type'] == 'blob'}
    descriptions = {r['path']: r for n in ('file-sources.json', 'file-sources-completion.json')
                    for r in load(base/n)['sources']}
    sources = {}
    for name in SOURCE_PATHS:
        original, row = base/'sources'/name, descriptions[name]
        raw = original.read_bytes()
        blob = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        assert row['commit'] == REVISION and blob == blobs[name] == row['git_blob_sha']
        assert hashlib.sha256(raw).hexdigest() == row['sha256']
        dest = observed/'library'/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        sources[name] = {'sha256': row['sha256'], 'git_blob_sha': blob, 'public_url': row['public_url']}
    save(observed/'sources.json', {'revision': REVISION, 'files': sources,
         'root_identity': 'existing_fixed_cache_not_new_remote_verification', 'operands': OPERANDS})
    save(observed/'identity.json', {str(p.relative_to(observed)): sha(p)
                                  for p in sorted(observed.rglob('*')) if p.is_file()})


def read_inputs(observed):
    for name, digest in load(observed/'identity.json').items():
        assert sha(observed/name) == digest, name
    assert sha(observed/'intake.json') == INTAKE_SHA
    intake = load(observed/'intake.json')
    rows = sorted((r for r in intake['files'] if r['eligible_offline']), key=lambda r: int(r['name'][:-5]))
    documents = []
    for row in rows:
        path = observed/'routes'/row['name']
        assert sha(path) == row['sha256']
        raw = load(path)
        for entry, record in enumerate(raw):
            assert set(record) == {str(layer) for layer in range(3, 61)}
            for tokens in record.values():
                assert isinstance(tokens, list) and (entry == 0 or len(tokens) == 1)
                for choices in tokens:
                    assert len(choices) == len(set(choices)) == 8
                    assert all(type(e) is int and 0 <= e < 256 for e in choices)
        documents.append(raw)
    assert len(documents) == 15
    return rows, documents


def matrix(documents, ep, binding, entry, layer):
    result = Counter()
    sources, tokens = set(), 0
    for index, document in enumerate(documents):
        if entry >= len(document):
            continue
        src = index if binding == 'packed' else index*ep//len(documents)
        choices_by_token = document[entry][str(layer)]
        if choices_by_token:
            sources.add(src)
        tokens += len(choices_by_token)
        for choices in choices_by_token:
            domains = {expert//(256//(ep//8)) for expert in choices}
            for domain in domains-{src//8}:
                result[src, domain*8+src%8] += 1
    return result, len(sources), tokens


def measures(demand, ep):
    tx, rx, server_tx, server_rx, cuts = (Counter() for _ in range(5))
    for (src, dst), count in demand.items():
        assert src//8 != dst//8 and src%8 == dst%8
        tx[src] += count
        rx[dst] += count
        server_tx[src//8] += count
        server_rx[dst//8] += count
        left, right = src//(ep//2), dst//(ep//2)
        if left != right:
            cuts[src%8, left] += count
    maximum = lambda c: max(c.values(), default=0)
    canonical = [[src, dst, count] for (src, dst), count in sorted(demand.items())]
    return dict(vectors=sum(demand.values()), matrix_sha256=hashlib.sha256(encoded(canonical)).hexdigest(),
                active_tx_nics=len(tx), active_rx_nics=len(rx),
                nic_tx_max=maximum(tx), nic_rx_max=maximum(rx),
                nic_max=max(maximum(tx), maximum(rx)),
                server_tx_max=maximum(server_tx), server_rx_max=maximum(server_rx),
                server_max=max(maximum(server_tx), maximum(server_rx)),
                cut_forward_max=max((cuts[r, 0] for r in range(8)), default=0),
                cut_reverse_max=max((cuts[r, 1] for r in range(8)), default=0), cut_max=maximum(cuts))


def percentile(values, percent):
    ordered = sorted(values)
    return ordered[max(0, (len(ordered)*percent+99)//100-1)]


def summarize(rows):
    output = []
    for ep in EPS:
        for binding in BINDINGS:
            for phase in ('prefill', 'decode'):
                group = [r for r in rows if (r['ep'], r['binding'], r['phase']) == (ep, binding, phase)]
                n = len(group)
                assert n
                cut_gate = sum(10*r['cut_max'] > 11*r['nic_max'] for r in group)
                out = dict(ep=ep, binding=binding, phase=phase, calls=n,
                           input_source_ranks_max=max(r['input_sources'] for r in group),
                           data_source_nics_max=max(r['active_tx_nics'] for r in group),
                           data_ingress_nics_max=max(r['active_rx_nics'] for r in group),
                           calls_cut_over_nic_at_400=cut_gate,
                           data_cut_screen_passed=5*cut_gate >= n,
                           vectors_total=sum(r['vectors'] for r in group))
                for q in (50, 95, 100):
                    out['cut_crossover_gbps_p'+str(q)] = percentile(
                        [400*r['cut_max']/r['nic_max'] if r['nic_max'] else 0 for r in group], q)
                    out['nic_vs_allrank_average_p'+str(q)] = percentile(
                        [r['nic_max']*ep/r['vectors'] if r['vectors'] else 0 for r in group], q)
                    out['nic_dispatch_floor_ns_p'+str(q)] = percentile(
                        [r['nic_max']*14464/50 for r in group], q)
                for factor in (1, 2, 4):
                    out['cut_over_nic_calls_at_'+str(400*factor)] = sum(
                        r['cut_max'] > factor*r['nic_max'] for r in group)
                output.append(out)
    return dict(schema_version='moe-scaleout-capacity-scope/1', cases=output, call_rows=len(rows),
                operands=OPERANDS, nic_gbps=400, server_gbps=3200, cut_gbps=[400, 800, 1600],
                network_executions=0, hardware_calibrated=False, innovations_established=0,
                scope='payload-service lower bounds, not queue/latency/optimization benefits')


def selfcheck():
    # Independent hand count: rank0 -> 8,9 is invalid rail traffic; use two rails.
    row = measures(Counter({(0, 8): 2, (1, 9): 1, (8, 0): 1}), 16)
    assert row['vectors'] == 4 and row['nic_max'] == 2 and row['server_max'] == 3
    assert row['cut_forward_max'] == 2 and row['cut_reverse_max'] == 1
    assert percentile([1, 4, 2, 3], 50) == 2 and percentile([1, 2, 3], 95) == 3
    assert 14464/50 == 289.28  # 400Gb/s = 50 payload bytes/ns.


def repair_preparation(repo, stage):
    """Preserve failed metadata and restore exact admitted bytes, before any case."""
    assert not (stage/'analysis').exists()
    assert (stage/'failed-preparation/study.py').is_file()
    observed = stage/'observed'
    original = repo/'experiments/route_coverage/results/local_additions_20261027/intake.json'
    assert sha(original) == INTAKE_SHA
    assert load(original) == load(observed/'intake.json')
    assert sha(observed/'intake.json') == sha(stage/'failed-preparation/intake.json')
    identity = load(observed/'identity.json')
    assert all(sha(observed/n) == h for n, h in identity.items())
    before = sha(observed/'intake.json')
    shutil.copyfile(original, observed/'intake.json')
    identity['intake.json'] = INTAKE_SHA
    save(observed/'identity.json', identity)
    save(stage/'preparation-repair.json', dict(research_conditions_before_repair=0,
        preserved_invalid_serialization_sha256=before, restored_exact_source_sha256=INTAKE_SHA,
        all_route_bytes_unchanged=True, budgets_inputs_and_screen_unchanged=True,
        workflow_exception='post-failure preparation repair; unstarted offline conditions only'))


def analyze(stage, output):
    assert not output.exists(), 'analysis output exists'
    _, documents = read_inputs(stage/'observed')
    rows = []
    for ep in EPS:
        for binding in BINDINGS:
            for entry in range(max(map(len, documents))):
                for layer in range(3, 61):
                    demand, sources, tokens = matrix(documents, ep, binding, entry, layer)
                    row = dict(ep=ep, binding=binding, entry=entry, layer=layer,
                               phase='prefill' if entry == 0 else 'decode',
                               input_sources=sources, input_tokens=tokens, **measures(demand, ep))
                    assert row['server_max'] <= 8*row['nic_max']
                    rows.append(row)
    output.mkdir(parents=True)
    save(output/'calls.json', rows)
    save(output/'summary.json', summarize(rows))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('run', 'analyze', 'selfcheck', 'repair-preparation'))
    p.add_argument('--repository', type=Path)
    p.add_argument('--stage', type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    selfcheck()
    if a.action == 'run':
        assert a.repository and a.stage
        prepare(a.repository.resolve(), a.stage.resolve())
        analyze(a.stage.resolve(), a.stage.resolve()/'analysis')
    elif a.action == 'analyze':
        assert a.stage and a.output
        analyze(a.stage.resolve(), a.output.resolve())
    elif a.action == 'repair-preparation':
        assert a.repository and a.stage
        repair_preparation(a.repository.resolve(), a.stage.resolve())
