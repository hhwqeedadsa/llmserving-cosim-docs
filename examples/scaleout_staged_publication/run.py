"""Small event-driven provider example using only the public session API.

This controller declares a functional producer order, not GPU instruction costs.
Run it under moe_ub_cosim.host_probe with explicit resource limits.
"""
import argparse
import json
from pathlib import Path

from moe_ub_cosim.transaction_session import NativeTransactionSession


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('topology', 'protocol', 'config', 'backend', 'output'):
        p.add_argument('--'+key, type=Path, required=True)
    a = p.parse_args()
    config = json.loads(a.config.read_bytes())
    assert config['submission_model'] == dict(mode='staged_prefix/1')
    assert [op['id'] for op in config['operations']] == [0, 1, 2, 3, 4]
    a.output.mkdir(exist_ok=False)
    seen, actions = set(), set()
    with NativeTransactionSession(a.topology/'topology.json', a.protocol,
            dict(config, operations=[]), a.backend, a.output) as session:
        while (event := session.receive()) is not None:
            seen.update((row['operation'], row['event']) for row in event['events'])
            if 'registered' not in actions:
                session.commit(config['operations']); actions.add('registered'); continue
            prepare, publish = [], []
            if (4, 'submitted') in seen and 'later_producers' not in actions:
                prepare = [1, 2, 3, 4]; actions.add('later_producers')
            if (4, 'prepared') in seen and 'other_qp' not in actions:
                publish = [4]; actions.add('other_qp')
            if (4, 'completed') in seen and 'earlier_producer' not in actions:
                prepare += [0]; actions.add('earlier_producer')
            if (3, 'prepared') in seen and 'prefix' not in actions:
                publish += [3]; actions.add('prefix')
            if (0, 'applied') in seen and 'late_rings' not in actions:
                publish += [1, 0]; actions.add('late_rings')
            finish = event['drained'] and 'late_rings' in actions
            session.commit([], prepare_operations=prepare or None,
                           publish_operations=publish or None, finish=finish)
    result = json.loads((a.output/'result.json').read_bytes())
    assert result['passed'] and result['session']['finished']
    print(json.dumps(dict(passed=True, finish_ns=result['finish_ns'],
                         completed_operations=result['completed_operations'])))


if __name__ == '__main__': main()
