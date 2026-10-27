"""One bounded generic-DAG execution through the public session API."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from moe_ub_cosim.transaction_session import NativeTransactionSession
from experiments.static_dag_transfer.event_graph import EventGraph, require


def load(path):
    return json.loads(path.read_bytes())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--name', choices=('baseline', 'bandwidth', 'propagation'), required=True)
    args = parser.parse_args()
    stage, repo = args.stage.resolve(), args.repository.resolve()
    ledger = load(stage/'execution.json')
    require(not ledger['closed'] and ledger['network_executions'] < 3, 'execution phase closed')
    require(args.name not in {r['name'] for r in ledger['records']}, 'network already attempted')
    require(load(stage/'offline-equivalence.json')['passed'], 'offline equivalence required')
    require(hashlib.sha256((stage/'event-graph.json').read_bytes()).hexdigest() == ledger['event_graph_sha256'], 'graph changed')
    require(hashlib.sha256((stage/'program.json').read_bytes()).hexdigest() == load(stage/'source.json')['program_sha256'], 'program changed')
    identity = load(stage/'runtime-identity.json')
    for name, digest in identity['files'].items():
        require(hashlib.sha256((stage/'runtime'/name).read_bytes()).hexdigest() == digest, 'runtime identity changed')
    for name, digest in identity['sources'].items():
        require(hashlib.sha256((repo/name).read_bytes()).hexdigest() == digest, 'native source identity changed')
    if args.name != 'baseline':
        require(load(stage/'baseline-equivalence.json')['equivalent'], 'new baseline not equivalent')
    seconds = sum(load(p).get('wall_seconds', 0) for p in (repo/'runs/generic_event_dag_20261027').glob('*-monitor/probe.json'))
    require(seconds+65 < 900, 'stage time budget')
    require(sum(p.stat().st_size for p in stage.rglob('*') if p.is_file())+192*2**20 < 512*2**20, 'stage storage budget')
    program, graph = load(stage/'program.json'), load(stage/'event-graph.json')
    controller = EventGraph(graph, program)
    initial = deepcopy(program)
    initial['operations'] = []
    output = stage/args.name
    output.mkdir()
    save(output/'input-identity.json', dict(event_graph_sha256=ledger['event_graph_sha256'],
         program_sha256=load(stage/'source.json')['program_sha256'], frontend='generic_event_dag/1',
         callback_batches=True, hardware_calibrated=False, extraction_network='baseline_only'))
    row = dict(name=args.name, state='started')
    ledger['records'].append(row)
    ledger['network_executions'] += 1
    save(stage/'execution.json', ledger)
    complete = False
    try:
        reference = stage/'reference'/args.name
        with NativeTransactionSession(reference/'topology.json', reference/'protocol.json', initial,
                                      stage/'runtime/run-transaction-session', output, callback_batches=True) as session:
            while (event := session.receive()) is not None:
                nodes = controller.advance(event)
                finished = not nodes and event['drained'] and controller.snapshot()['all_events_fired']
                require(nodes or finished or not event['drained'], 'generic graph blocked at native drain')
                session.commit(nodes, finish=finished, wake_groups=controller.wake_groups())
        result = load(output/'result.json')
        require(result['passed'] and result['session']['finished'] and
                result['transactions']['transport']['physical_drained'] and
                controller.snapshot()['all_events_fired'], 'incomplete execution')
        require(result['packet_capture']['omitted_events'] == 0, 'packet capture incomplete')
        complete = True
    finally:
        save(output/'event-execution.json', controller.snapshot())
        row['state'] = 'completed' if complete else 'failed'
        if not complete:
            ledger.update(closed=True, state='failure_preserved_no_remaining_networks')
        save(stage/'execution.json', ledger)
    print(args.name, 'complete:', result['finish_ns'], 'ns;', controller.decisions, 'physical decisions')


if __name__ == '__main__':
    main()
