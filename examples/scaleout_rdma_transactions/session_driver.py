"""Build the second request from the actual first atomic response, on CPU.

Use session-program.json and wrap this whole driver in the public host_probe.
This is a functional control example, not a GPU inference implementation.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from moe_ub_cosim.scaleout_rdma import checked_json
from moe_ub_cosim.transaction_session import NativeTransactionSession


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('topology', 'protocol', 'config', 'backend', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    program = checked_json(args.config.resolve(), 4*2**20)
    assert len(program['operations']) == 2
    atom, put = deepcopy(program['operations'])
    assert atom['verb'] == 'fetch_add' and put['verb'] == 'put'
    output = args.output.resolve()
    output.mkdir(exist_ok=False)
    returned, second = None, False
    with NativeTransactionSession(args.topology.resolve()/'topology.json', args.protocol,
                                  dict(program, operations=[]), args.backend, output) as session:
        first = True
        while (event := session.receive()) is not None:
            if first:
                session.commit([atom])
                first = False
                continue
            done = False
            for row in event['events']:
                if row['operation'] == atom['id'] and 'returned_value' in row:
                    returned = row['returned_value']
                done |= row['operation'] == atom['id'] and row['event'] == 'completed'
            if done and not second:
                assert returned is not None
                put['hex'] = ((returned+100) % 2**64).to_bytes(8, 'little').hex()
                session.commit([put])
                second = True
            else:
                session.commit([], finish=second and event['drained'])
    (output/'effective-program.json').write_text(json.dumps(dict(program, operations=[atom, put]), indent=2)+'\n')
    print(json.dumps(dict(actual_returned_value=returned, derived_value=(returned+100) % 2**64,
                          modern_deepep_connected=False)))


if __name__ == '__main__':
    main()
