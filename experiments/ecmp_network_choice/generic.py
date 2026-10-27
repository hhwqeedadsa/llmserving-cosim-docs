"""Full captured event-DAG reference, actual public native callbacks only."""
import argparse
from copy import deepcopy
from pathlib import Path

from moe_ub_cosim.transaction_session import NativeTransactionSession
from experiments.static_dag_transfer.compile_event_graph import compile_graph, replay
from experiments.static_dag_transfer.event_graph import EventGraph
from .data import CONDITIONS, load, save, sha


def execute(stage,batch,name,action):
    root = stage/f'truth/batch{batch}'
    frozen = root/'frozen'
    if action == 'compile':
        assert name == 'single'
        frozen.mkdir()
        reference = root/'single/modern'
        graph = compile_graph(reference)
        program = load(reference/'initial.json')
        program['operations'] = load(reference/'modern-execution.json')['native_operations']
        save(frozen/'graph.json',graph);save(frozen/'program.json',program)
        save(frozen/'offline-equivalence.json',replay(reference,graph,program))
        save(frozen/'identity.json',{n:sha(frozen/n) for n in ('graph.json','program.json')})
        return
    assert load(frozen/'offline-equivalence.json')['passed']
    assert all(sha(frozen/n) == h for n,h in load(frozen/'identity.json').items())
    graph,program = load(frozen/'graph.json'),load(frozen/'program.json')
    controller = EventGraph(graph,program)
    initial = deepcopy(program);initial['operations'] = []
    out,reference = root/name/'generic',root/name/'modern'
    out.mkdir()
    try:
        with NativeTransactionSession(reference/'topology.json',reference/'protocol.json',initial,
                stage/'runtime/run-transaction-session',out,callback_batches=True) as session:
            while (event := session.receive()) is not None:
                nodes = controller.advance(event)
                finished = not nodes and event['drained'] and controller.snapshot()['all_events_fired']
                assert nodes or finished or not event['drained'],'generic graph blocked at drain'
                session.commit(nodes,finish=finished,wake_groups=controller.wake_groups())
        result = load(out/'result.json')
        assert result['passed'] and result['session']['finished'] and controller.snapshot()['all_events_fired']
        assert result['transactions']['transport']['physical_drained'] and result['packet_capture']['omitted_events'] == 0
    finally: save(out/'event-execution.json',controller.snapshot())


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=('compile','run'))
    p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--batch',type=int,choices=(4,8),required=True)
    p.add_argument('--name',choices=CONDITIONS,required=True)
    a=p.parse_args();execute(a.stage.resolve(),a.batch,a.name,a.action)
