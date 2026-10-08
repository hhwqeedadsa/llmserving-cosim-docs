"""Freeze nine normal inputs before seeing any execution result."""
from collections import Counter
from copy import deepcopy
import hashlib
from pathlib import Path
import shutil

from .common import HERE, digest, read, require, save, sources

SEEDS = (0, 1, 2)
BATCHES = (2, 4, 8)
REFERENCE = HERE/'configs/reference'


def requests(seed, per_rank):
    require(type(seed) is int and seed in (*SEEDS, 3, 4) and type(per_rank) is int and per_rank in BATCHES,
            'Supported input sets: 0..4; 2/4/8 requests per rank; study manifests fix input roles')
    rows = []
    for rank in range(8):
        for slot in range(per_rank):
            # Stable identity across nested sizes; separate identities across sets.
            identity = seed*64 + rank*8 + slot
            routes = {}
            for layer in (1, 2, 3):
                def score(expert):
                    key = f'scaleup-observability/v1/set={seed}/rank={rank}/slot={slot}/layer={layer}/expert={expert}'
                    return hashlib.sha256(key.encode()).digest(), expert
                routes[str(layer)] = sorted(range(16), key=score)[:2]
            rows.append(dict(request_id=identity, rank_id=rank, arrival_ns=0,
                             prompt_tokens=64, generated_tokens=1, decode_routes=[routes]))
    return rows


def make_input(seed, per_rank):
    service = read(REFERENCE/'service.json')
    layers = read(REFERENCE/'layers.json')
    service['run_id'] = f'scaleup-s{seed}-b{per_rank}'
    service['requests'] = requests(seed, per_rank)
    limits = dict(max_num_seqs=per_rank, max_num_batched_tokens=per_rank)
    service['groups'][0]['scheduling'] = deepcopy(limits)
    for layer in layers['groups'][0]['layers']:
        layer['batch_limits'] = deepcopy(limits)
    counts = {}
    for lid in ('1', '2', '3'):
        c = Counter(e for row in service['requests'] for e in row['decode_routes'][0][lid])
        counts[lid] = [c[e] for e in range(16)]
        require(sum(counts[lid]) == 8*per_rank*2, 'Expert work mismatch')
    return service, layers, counts


def prepare(output):
    output = Path(output).resolve()
    require(not output.exists(), f'Output already exists: {output}')
    output.mkdir(parents=True)
    cases = []
    for seed in SEEDS:
        for batch in BATCHES:
            name = f's{seed}-b{batch}'
            folder = output/'inputs'/name
            shutil.copytree(REFERENCE, folder)
            service, layers, counts = make_input(seed, batch)
            save(folder/'service.json', service)
            save(folder/'layers.json', layers)
            cases.append(dict(case_id=name, input_set=seed, requests_per_rank=batch,
                request_count=8*batch, directory=str(folder.relative_to(output)),
                expert_inputs=counts, files={p.name:digest(p) for p in sorted(folder.iterdir()) if p.is_file()}))
    save(output/'batch.json', dict(schema_version='scaleup-normal-batch/1',
        input_rule='sha256-top2-v1, nested rank-local slots, seeds 0/1/2',
        synthetic_input=True, simulation_executed=False, implementation=sources(), cases=cases))
    return dict(output=str(output), cases=[c['case_id'] for c in cases])


def verified_case(batch, name):
    batch = Path(batch).resolve()
    metadata = read(batch/'batch.json')
    require(metadata['schema_version'] in ('scaleup-normal-batch/1','scaleup-slot-batch/1',
            'scaleup-observation-source-batch/1','scaleup-send-state-source-batch/1',
            'scaleup-exploration-batch/1'), 'Unsupported batch')
    matches = [row for row in metadata['cases'] if row['case_id'] == name]
    require(len(matches) == 1, f'Unknown or repeated case {name}')
    case = matches[0]
    folder = batch/case['directory']
    require(folder.resolve().is_relative_to(batch), 'Input directory escapes batch')
    actual = {p.name:digest(p) for p in folder.iterdir() if p.is_file()}
    require(actual == case['files'], 'Frozen input differs; no backend launched')
    return case, folder
