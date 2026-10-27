"""Independent source, compute causality, full equivalence and physical audit."""
import argparse
from collections import Counter
from copy import deepcopy
from itertools import permutations
from pathlib import Path
import shutil

from experiments.shared_uplink_choice.audit import inspect, need
from experiments.shared_uplink_choice.evaluate import compare
from experiments.shared_uplink_choice.predict import combine_context
from experiments.readiness_network_choice.prepare import PROFILES, CONDITIONS, NAMES, IDENTITY, load, save, sha
from experiments.readiness_network_choice.predict import execute


def input_checks(observed):
    source = load(observed/'source.json')
    need(tuple(r['name'].removesuffix('.json') for r in source['selected']) == NAMES, 'selection changed')
    tokens = [[] for _ in range(4)]
    counts = [0]*4
    for index, row in enumerate(source['selected']):
        path = observed/'routes'/row['name']
        need(sha(path) == row['sha256'], 'source hash mismatch')
        raw = load(path)
        need(set(raw[1]) == {str(i) for i in range(61)} and all(raw[1][str(i)] is None for i in range(3)),
             'incomplete source or unexpected dense-layer selection')
        need(raw[1]['3'] == [row['choices']], 'source choices changed')
        tokens[index % 4].append(row['choices'])
        for expert in row['choices']:
            owners = [rank for rank in range(4) if rank*64 <= expert < (rank+1)*64]
            need(len(owners) == 1, 'expert outside placement')
            counts[owners[0]] += 1
    need(counts == [28, 10, 10, 16] == source['expert_contribution_counts'], 'contribution count mismatch')
    reference = None
    vectors = {}
    for profile in PROFILES:
        cfg = load(observed/(profile+'.json'))
        need(cfg['modern']['calls'][0]['tokens'] == tokens, 'source rank mapping changed')
        vectors[profile], = cfg['expert_stage']['durations_ns']
        coefficient = 10000 if profile.endswith('10') else 1000
        expected = [sum(counts)*coefficient//4]*4 if profile.startswith('balanced') else [x*coefficient for x in counts]
        need(vectors[profile] == expected, 'declared duration law changed')
        without = deepcopy(cfg)
        del without['expert_stage']
        if reference is None:
            reference = without
        need(reference == without and 'expert_ns' not in cfg['costs'], 'other scenario fields changed')
    for suffix in ('1', '10'):
        need(sum(vectors['balanced'+suffix]) == sum(vectors['routed'+suffix]), 'compute sum not conserved')
    modern = load(observed/'modern-input.json')
    need(modern == reference['modern'], 'symbolic plan input mismatch')
    plan = load(observed/'plan/plan.json')
    groups = {}
    for op in plan['operations']:
        if op['phase'] == 'combine' and op['kind'] == 'token_data' and op['path'] == 'rdma':
            groups.setdefault((op['src'], op['dst']), []).append(op)
    checked = 0
    for group in groups.values():
        first = None
        for perm in permutations(range(len(group))):
            aggregate = Counter()
            for op, slot in zip(group, perm):
                binding = op['binding']
                width = (binding['owner_received_tokens']+3)//4
                channel = (binding['source_rank_prefix']+slot)//width
                context = (0, 1, 2, 3)[channel]
                need(combine_context(binding, slot, modern['execution']) == context, 'context arithmetic mismatch')
                aggregate[context % 2] += op['operand_bytes']+32
            if first is None:
                first = aggregate
            need(first == aggregate, 'slot permutation changes demand')
            checked += 1
    return dict(raw_requests=8, tokens=8, contribution_counts=counts, vectors_ns=vectors,
                slot_permutations_checked=checked, compute_sum_conserved=True)


def expert_checks(directory, result=None, execution=None):
    cfg = load(directory/'input.json')
    plan = load(directory/'modern-plan.json')
    result = load(directory/'result.json') if result is None else result
    execution = load(directory/'modern-execution.json') if execution is None else execution
    vector, = cfg['expert_stage']['durations_ns']
    need(execution['expert_stage'] == cfg['expert_stage'], 'snapshot duration declarations differ')
    events = {e['event']: e for e in execution['events']}
    need(len(events) == len(plan['events']), 'missing/duplicate application events')
    for event in plan['events']:
        need(all(events[parent]['time_ns'] <= events[event['id']]['time_ns'] for parent in event['deps']),
             'application dependency precedes parent')
    callbacks = {(e.get('operation'), e['event']): e['time_ns'] for e in result['session']['events']}
    work = {o['id']: o for o in execution['native_operations']}
    actual = {o['id']: o for o in result['local_work']}
    nodes = {r['rank']: r['node'] for r in cfg['deployment']}
    rows = []
    for event in plan['events']:
        if event['kind'] != 'expert_stage_done':
            continue
        rank, name = event['rank'], event['id']
        identity = events[name]['native_operation']
        task = execution['native_tasks'][str(identity)]
        need(task == dict(role='event', event=name), 'wrong expert task identity')
        local, node = actual[identity], work[identity]
        need(node['kind'] == 'local' and node['rank'] == nodes[rank], 'expert on wrong node')
        need(node['duration_ns'] == local['duration_ns'] == vector[rank], 'expert duration differs from declaration')
        need(local['completed_ns']-local['start_ns'] == vector[rank], 'expert elapsed duration mismatch')
        dispatch = events[f'm0.dispatch.r{rank}.complete']['time_ns']
        need(dispatch <= local['ready_ns'] <= local['start_ns'], 'expert starts before Dispatch')
        for kind, key in (('local_admitted', 'ready_ns'), ('local_started', 'start_ns'), ('completed', 'completed_ns')):
            need(callbacks[identity, kind] == local[key], 'expert actual callback mismatch')
        need(events[name]['time_ns'] == events[name]['native_time_ns'] == local['completed_ns'], 'expert completion changed')
        entries = [e for e in plan['events'] if name in e['deps']]
        need(entries and all(events[e['id']]['time_ns'] >= local['completed_ns'] for e in entries),
             'Combine dependency not gated by expert completion')
        rows.append(dict(rank=rank, dispatch_ns=dispatch, ready_ns=local['ready_ns'], start_ns=local['start_ns'],
            completed_ns=local['completed_ns'], duration_ns=vector[rank], queued_ns=local['start_ns']-local['ready_ns']))
    need(len(rows) == 4, 'expert rank count changed')
    return dict(passed=True, all_plan_edges_checked=True, ranks=sorted(rows, key=lambda r: r['rank']))


def verify(stage, check):
    ledger = load(stage/'execution.json')
    need(ledger['closed'] and ledger['state'] == 'completed', 'incomplete execution')
    need(sum(r['network'] for r in ledger['records']) == 24, 'network count changed')
    need(all(sha(stage/n) == digest for n, digest in load(stage/'input-identity.json').items()), 'frozen input changed')
    need(sha(stage/'runtime-identity.json') == IDENTITY, 'runtime identity changed')
    identity = load(stage/'runtime-identity.json')
    repo = Path(__file__).resolve().parents[2]
    need(all(sha(repo/n) == digest for n, digest in identity['sources'].items()), 'build sources changed')
    need(all(sha(stage/'runtime'/n) == digest for n, digest in identity['files'].items()), 'runtime files changed')
    provenance = input_checks(stage/'observed')
    audits = {}
    for profile in PROFILES:
        predictions = load(stage/'predictions'/(profile+'.json'))['predictions']
        frozen = stage/'truth'/profile/'frozen'
        need(all(sha(frozen/n) == digest for n, digest in load(frozen/'identity.json').items()), 'frozen graph changed')
        for name in CONDITIONS:
            root = stage/'truth'/profile/name
            comparison = compare(root, stage/'truth'/profile/'baseline/modern')
            need(comparison['equivalent'] and comparison == load(root/'comparison.json'), 'full graph behavior mismatch')
            result = load(root/'modern/result.json')
            # Replay's result carries the actual dynamic-loader inventory.
            for mode in ('modern', 'generic'):
                loaded = [Path(n).name for n in load(root/mode/'result.json')['loaded_libraries'] if 'libns3.' in n]
                expected = [Path(n).name for n in identity['files'] if n.startswith('lib/')]
                need(sorted(loaded) == sorted(expected), 'actual native library inventory differs')
            audits[profile+'-'+name] = dict(physical=inspect(root/'modern', predictions[name], result),
                expert=expert_checks(root/'modern', result), full_graph_equivalent=True)
    check.mkdir()
    shutil.copytree(stage/'observed', check/'observed')
    need(not (check/'truth').exists(), 'truth accidentally copied')
    execute(check/'observed', check/'predictions')
    need(all((stage/'predictions'/(p+'.json')).read_bytes() == (check/'predictions'/(p+'.json')).read_bytes()
             for p in PROFILES), 'prediction changed without truth')
    directory = stage/'truth/routed1/baseline/modern'
    prediction = load(stage/'predictions/routed1.json')['predictions']['baseline']
    original = load(directory/'result.json')
    corruptions = []

    def reject(label, function):
        try:
            function()
        except (AssertionError, ValueError, KeyError, IndexError):
            corruptions.append(label)
        else:
            raise ValueError('corruption accepted: '+label)

    bad = deepcopy(original)
    bad['packet_capture']['omitted_events'] = 1
    reject('capture omission', lambda: inspect(directory, prediction, bad))
    bad = deepcopy(original)
    next(e for e in bad['packet_capture']['events'] if e[3] == 1)[0] += 1
    reject('physical receive time', lambda: inspect(directory, prediction, bad))
    bad_prediction = deepcopy(prediction)
    bad_prediction['loads'][0]['framed_bytes'] += 1
    reject('shared-link demand', lambda: inspect(directory, bad_prediction, original))
    bad = deepcopy(original)
    expert_ids = {int(i) for i, t in load(directory/'modern-execution.json')['native_tasks'].items()
                  if t['role'] == 'event' and t['event'].endswith('experts_done')}
    next(o for o in bad['local_work'] if o['id'] in expert_ids)['duration_ns'] += 1
    reject('expert duration', lambda: expert_checks(directory, bad))
    bad = load(directory/'modern-execution.json')
    next(e for e in bad['events'] if e['kind'] == 'expert_stage_done')['time_ns'] += 1
    reject('expert completion', lambda: expert_checks(directory, original, bad))
    source = load(check/'observed/source.json')
    source['selected'][0]['sha256'] = '0'*64
    save(check/'observed/source.json', source)
    reject('source hash', lambda: input_checks(check/'observed'))
    return dict(passed=True, input=provenance, audits=audits, rejected_corruptions=corruptions,
        frozen_inputs_unchanged=True, predictions_identical_without_truth=True, actual_library_inventory_checked=True,
        new_networks=0, hardware_calibrated=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    args = parser.parse_args()
    save(args.output, verify(args.stage.resolve(), args.scratch.resolve()))
