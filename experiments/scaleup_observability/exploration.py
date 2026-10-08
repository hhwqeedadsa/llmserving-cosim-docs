"""Frozen small exploration batches using the existing public execution workflow."""
from copy import deepcopy
from pathlib import Path
import shutil
import sys

import yaml

from .common import HERE, bounded, digest, read, require, save, sources
from .inputs import REFERENCE, make_input
from .slots import set_slots

TEMPO_FACTORS = (('half', 1, 2), ('base', 1, 1), ('double', 2, 1))


def scaled(value, numerator, denominator):
    require(type(value) is int and value >= 0 and numerator > 0 and denominator > 0 and
            value*numerator % denominator == 0, 'Cost must scale to an exact integer')
    return value*numerator//denominator


def tempo_inputs(service, layers, numerator, denominator):
    service, layers = deepcopy(service), deepcopy(layers)
    for group in service['groups']:
        for layer in group['full_model_profile']['layers']:
            if 'routed_expert_per_input_ns' in layer:
                layer['routed_expert_per_input_ns'] = scaled(layer['routed_expert_per_input_ns'], numerator, denominator)
                per = layer['per_token_ns']
                per['shared_expert'] = scaled(per['shared_expert'], numerator, denominator)
    for group in layers['groups']:
        for layer in group['layers']:
            cost = layer['compute_profile_ns']
            cost['expert_per_input'] = scaled(cost['expert_per_input'], numerator, denominator)
    return service, layers


def prepare(study, output):
    require(study == 'compute-tempo', 'Unsupported exploration')
    output = Path(output).resolve()
    require(not output.exists(), 'Exploration output exists')
    output.mkdir(parents=True)
    service, layers, counts = make_input(4, 8)
    cases = []
    for name, numerator, denominator in TEMPO_FACTORS:
        for slots in (1, 2):
            identity = f's4-b8-{name}-slots{slots}'
            folder = output/'inputs'/identity
            shutil.copytree(REFERENCE, folder)
            changed_service, changed_layers = tempo_inputs(service, layers, numerator, denominator)
            save(folder/'service.json', changed_service)
            save(folder/'layers.json', set_slots(changed_layers, slots))
            scenario = set_slots(yaml.safe_load((folder/'scenario.yaml').read_text()), slots)
            (folder/'scenario.yaml').write_text(yaml.safe_dump(scenario, sort_keys=False))
            observation = read(folder/'observation.json')
            observation.update(schema_version='moe-ub-observation-config/4', send_state=dict(
                schema_version='moe-ub-send-state-config/1', max_records_per_rank=100000, max_total_records=1000000))
            save(folder/'observation.json', observation)
            cases.append(dict(case_id=identity, input_set=4, requests_per_rank=8, request_count=64,
                send_slots_per_rank=slots, factor_name=name, factor=[numerator, denominator],
                expert_inputs=counts, directory=str(folder.relative_to(output)),
                files={p.name:digest(p) for p in sorted(folder.iterdir()) if p.is_file()}))
    save(output/'batch.json', dict(schema_version='scaleup-exploration-batch/1', study=study,
        synthetic_input=True, simulation_executed=False, implementation=sources(), cases=cases,
        method='experiments/scaleup_observability/tempo-method.md', method_sha256=digest(HERE/'tempo-method.md')))
    return dict(output=str(output), study=study, cases=[c['case_id'] for c in cases])


def compare(batch, output):
    batch, output = Path(batch).resolve(), Path(output).resolve()
    require(not output.exists(), 'Exploration comparison output exists')
    output.mkdir(parents=True)
    bounded([sys.executable, '-m', 'experiments.scaleup_observability.exploration_analysis', batch, output],
            output/'commands', 'compare', output)
    return dict(passed=True, output=str(output))
