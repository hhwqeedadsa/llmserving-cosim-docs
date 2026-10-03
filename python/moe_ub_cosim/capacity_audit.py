"""Audit an explicit MLA storage inventory without creating an execution graph."""
import argparse
import json
from pathlib import Path

from .mla_capacity import audit_inventory
from .model_capacity import fields
from .plan import require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.input.read_text())
    fields(config, 'schema_version model_spec rank_ids memory', 'capacity-only input')
    require(config['schema_version'] == 'moe-ub-capacity-input/1', 'Unsupported capacity-only schema')
    audit = audit_inventory(config['model_spec'], config['memory'], config['rank_ids'])
    # Exclusive creation also prevents an accidental overwrite of the input.
    with args.output.open('x') as output:
        json.dump(audit, output, indent=2, allow_nan=False)
        output.write('\n')
    print(f"Audited {len(audit['ranks'])} ranks: capacity only; no model kernels or graph executed")


if __name__ == '__main__':
    main()
