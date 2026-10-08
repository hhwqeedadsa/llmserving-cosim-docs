"""Freeze the state-observation study and use existing serial public execution."""
from pathlib import Path
import shutil
import sys
import yaml

from .common import HERE, bounded, digest, read, require, save, sources
from .inputs import REFERENCE, make_input
from .send_state_spec import ROLES
from .slots import set_slots, verify_pair_inputs


def prepare_source(output):
    output = Path(output).resolve()
    require(not output.exists(), 'State source batch exists')
    output.mkdir(parents=True)
    cases = []
    for seed in ROLES:
        service, layers, counts = make_input(seed, 8)
        for slots in (1, 2):
            name = f's{seed}-b8-slots{slots}'; folder = output/'inputs'/name
            shutil.copytree(REFERENCE, folder)
            save(folder/'service.json', service)
            save(folder/'layers.json', set_slots(layers, slots))
            scenario = set_slots(yaml.safe_load((folder/'scenario.yaml').read_text()), slots)
            (folder/'scenario.yaml').write_text(yaml.safe_dump(scenario, sort_keys=False))
            observation = read(folder/'observation.json')
            observation.update(schema_version='moe-ub-observation-config/4', send_state=dict(
                schema_version='moe-ub-send-state-config/1', max_records_per_rank=100000, max_total_records=1000000))
            save(folder/'observation.json', observation)
            cases.append(dict(case_id=name, input_set=seed, requests_per_rank=8, request_count=64,
                send_slots_per_rank=slots, expert_inputs=counts, directory=str(folder.relative_to(output)),
                files={p.name: digest(p) for p in sorted(folder.iterdir()) if p.is_file()}))
        verify_pair_inputs(output/f'inputs/s{seed}-b8-slots1', output/f'inputs/s{seed}-b8-slots2')
    save(output/'batch.json', dict(schema_version='scaleup-send-state-source-batch/1',
        synthetic_input=True, simulation_executed=False, implementation=sources(),
        frozen_method_sha256=digest(HERE/'send-state-method.md'), cases=cases))
    save(output/'sources.json', dict(schema_version='scaleup-send-state-sources/1', cases=[
        dict(batch='.', case_id=c['case_id'], role=ROLES[c['input_set']]) for c in cases]))
    return dict(output=str(output), cases=[c['case_id'] for c in cases])


def study(action, inputs, output):
    output = Path(output).resolve()
    require(not output.exists(), f'Output exists: {output}')
    output.mkdir(parents=True)
    module = {'prepare': 'observation_prepare', 'measure': 'send_state_measure', 'evaluate': 'send_state_evaluate'}[action]
    bounded([sys.executable, '-m', 'experiments.scaleup_observability.'+module,
             *[str(Path(p).resolve()) for p in inputs], str(output)], output/'commands', action, output)
    return dict(passed=True, output=str(output))
