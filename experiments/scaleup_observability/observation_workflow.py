"""Public preparation and bounded observation-study commands."""
from pathlib import Path
import shutil
import sys
import yaml

from .common import HERE, bounded, digest, read, require, save, sources
from .inputs import REFERENCE, make_input
from .slots import set_slots, verify_pair_inputs


def prepare_source(output):
    output = Path(output).resolve()
    require(not output.exists(), 'Source batch exists')
    output.mkdir(parents=True)
    service, layers, counts = make_input(3, 8)
    cases = []
    for slots in (1, 2):
        name = f's3-b8-slots{slots}'
        folder = output/'inputs'/name
        shutil.copytree(REFERENCE, folder)
        save(folder/'service.json', service)
        save(folder/'layers.json', set_slots(layers, slots))
        scenario = set_slots(yaml.safe_load((folder/'scenario.yaml').read_text()), slots)
        (folder/'scenario.yaml').write_text(yaml.safe_dump(scenario, sort_keys=False))
        cases.append(dict(case_id=name, input_set=3, requests_per_rank=8, request_count=64,
            send_slots_per_rank=slots, expert_inputs=counts, directory=str(folder.relative_to(output)),
            files={p.name: digest(p) for p in sorted(folder.iterdir()) if p.is_file()}))
    verify_pair_inputs(output/cases[0]['directory'], output/cases[1]['directory'])
    save(output/'batch.json', dict(schema_version='scaleup-observation-source-batch/1',
        synthetic_input=True, simulation_executed=False, implementation=sources(),
        frozen_method_sha256=digest(HERE/'observation-method.md'), cases=cases))
    save(output/'sources.json', dict(schema_version='scaleup-observation-sources/1', cases=[
        dict(batch='.', case_id=c['case_id'], role='holdout') for c in cases]))
    return dict(output=str(output), cases=[c['case_id'] for c in cases])


def study(action, inputs, output):
    output = Path(output).resolve()
    require(not output.exists(), f'Output exists: {output}')
    output.mkdir(parents=True)
    module = {'prepare': 'observation_prepare', 'measure': 'observation_measure',
              'evaluate': 'observation_evaluate'}[action]
    bounded([sys.executable, '-m', f'experiments.scaleup_observability.{module}',
             *[str(Path(p).resolve()) for p in inputs], str(output)],
            output/'commands', action, output)
    return dict(passed=True, output=str(output))
