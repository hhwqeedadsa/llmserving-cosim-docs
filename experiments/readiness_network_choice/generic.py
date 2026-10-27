"""Untimed graph frozen from each profile's baseline, using actual callbacks."""
import argparse
from copy import deepcopy
from pathlib import Path
from moe_ub_cosim.transaction_session import NativeTransactionSession
from experiments.static_dag_transfer.compile_event_graph import compile_graph, replay
from experiments.static_dag_transfer.event_graph import EventGraph
from experiments.readiness_network_choice.prepare import PROFILES, CONDITIONS, load, save, sha


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('compile', 'run'))
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--profile', choices=PROFILES, required=True)
    parser.add_argument('--name', choices=CONDITIONS, default='baseline')
    args = parser.parse_args()
    stage = args.stage.resolve()
    root = stage/'truth'/args.profile
    frozen = root/'frozen'
    if args.action == 'compile':
        assert args.name == 'baseline'
        frozen.mkdir()
        reference = root/'baseline/modern'
        graph = compile_graph(reference)
        program = load(reference/'initial.json')
        program['operations'] = load(reference/'modern-execution.json')['native_operations']
        save(frozen/'graph.json', graph)
        save(frozen/'program.json', program)
        save(frozen/'offline-equivalence.json', replay(reference, graph, program))
        save(frozen/'identity.json', {n: sha(frozen/n) for n in ('graph.json', 'program.json')})
        return
    assert load(frozen/'offline-equivalence.json')['passed']
    assert all(sha(frozen/n) == digest for n, digest in load(frozen/'identity.json').items())
    controller = EventGraph(load(frozen/'graph.json'), load(frozen/'program.json'))
    initial = deepcopy(load(frozen/'program.json'))
    initial['operations'] = []
    out = root/args.name/'generic'
    out.mkdir()
    reference = root/args.name/'modern'
    try:
        with NativeTransactionSession(reference/'topology.json', reference/'protocol.json', initial,
                stage/'runtime/run-transaction-session', out, callback_batches=True) as session:
            while (event := session.receive()) is not None:
                nodes = controller.advance(event)
                finished = not nodes and event['drained'] and controller.snapshot()['all_events_fired']
                assert nodes or finished or not event['drained'], 'generic graph blocked at drain'
                session.commit(nodes, finish=finished, wake_groups=controller.wake_groups())
        result = load(out/'result.json')
        assert result['passed'] and result['session']['finished'] and controller.snapshot()['all_events_fired']
        assert result['transactions']['transport']['physical_drained'] and result['packet_capture']['omitted_events'] == 0
    finally:
        save(out/'event-execution.json', controller.snapshot())


if __name__ == '__main__':
    main()
