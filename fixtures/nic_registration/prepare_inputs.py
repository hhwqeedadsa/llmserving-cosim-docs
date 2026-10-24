"""Generate three bounded functional variants, without starting a backend."""
import argparse
from copy import deepcopy
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    original = json.loads(args.work.read_text())
    changed = deepcopy(original)
    changed['windows'][0]['hex'] = bytes((v + 32) % 256 for v in bytes.fromhex(changed['windows'][0]['hex'])).hex()
    sparse = deepcopy(original)
    sparse['schema_version'] = 'moe-rdma-transactions/5'
    for window in sparse['windows']:
        initial = window.pop('hex')
        window.update(bytes=65536, initialization='zero_fill/1', segments=[dict(offset=0, hex=initial)])
    bounded = deepcopy(original)
    bounded['operations'].pop(2)
    bounded['operations'][2]['after'] = [dict(operation=1, event='completed')]
    bounded['buffer_model'] = dict(mode='bounded_copied_put/1', transport_bytes_per_source=48,
                                 registered_bytes_per_owner=64, target_cache_bytes_per_owner=80,
                                 local_copy_bytes_per_owner=16)
    for name, value in [('changed', changed), ('sparse', sparse), ('bounded', bounded)]:
        (args.output / (name + '.json')).write_text(json.dumps(value, indent=2) + '\n')


if __name__ == '__main__':
    main()
