"""Export two explicit fixed decode steps; submission time comes from the backend."""
import argparse
import json
from pathlib import Path
import re

from .generate import export_plan
from .plan import RunIdentities, build_plan, require


def build_sequence(config):
    require(set(config) == {'schema_version', 'steps'} and
            config['schema_version'] == 'moe-ub-sequence-fixture/1' and len(config['steps']) == 2,
            'Require exactly two fixed steps')
    identities = RunIdentities()
    plans = [build_plan(step, identities) for step in config['steps']]
    first, second = [plan['input'] for plan in plans]
    for key in first:
        if key not in ('graph_id', 'wave_id', 'tokens'):
            require(first[key] == second[key], 'Sequence changes shared configuration: ' + key)
    require(first['ready_ns'] == 0 and first['wave_id'] != second['wave_id'], 'Require distinct zero-ready waves')
    for plan in plans:
        require(re.fullmatch(r'[A-Za-z0-9_.-]+', plan['graph_id']), 'Invalid graph_id for execution log')
    for old, new in zip(first['tokens'], second['tokens']):
        require(all(old[key] == new[key] for key in old if key not in ('token_index', 'expert_ids')) and
                new['token_index'] == old['token_index'] + 1, 'Require the same requests at the next decode step')
    return plans


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        plans = build_sequence(json.loads(args.input.read_text()))
    except (ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    args.output_dir.mkdir(exist_ok=False)
    for index, plan in enumerate(plans):
        export_plan(plan, args.output_dir / str(index))
    (args.output_dir / 'sequence.json').write_text(json.dumps(dict(
        schema_version='moe-ub-sequence/1', steps=['0', '1']), indent=2) + '\n')
    print('Generated two fixed steps with disjoint run identities')


if __name__ == '__main__':
    main()
