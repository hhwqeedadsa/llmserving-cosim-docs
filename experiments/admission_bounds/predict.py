"""Read observed inputs only; consume callback decisions strictly in order."""
import argparse
import json
from pathlib import Path

from experiments.static_dag_transfer.event_graph import EventGraph
from experiments.admission_bounds.bounds import AdmissionBounds


def load(path):
    return json.loads(path.read_bytes())


def predict(observed, output):
    graph, program = [load(observed/name) for name in ('event-graph.json', 'program.json')]
    output.mkdir(parents=True, exist_ok=False)
    for condition in ('baseline', 'bandwidth'):
        controller = EventGraph(graph, program)
        algorithm = AdmissionBounds(graph, program, load(observed/condition/'topology.json'))
        with (observed/condition/'decisions.jsonl').open() as stream, (output/(condition+'.jsonl')).open('x') as sink:
            for line in stream:
                decision = json.loads(line)
                nodes = controller.advance(decision)
                completed = {identity for identity, event in controller.seen if event == 'completed'}
                pending = {str(plane): sorted(op['id'] for op in program['operations']
                    if op['kind'] == 'remote' and op['id'] in controller.submitted - completed and
                    algorithm.connections[op['connection']]['src'] != algorithm.connections[op['connection']]['dst'] and
                    algorithm.connections[op['connection']]['plane'] == plane)
                    for plane in sorted({c['plane'] for c in program['connections']})}
                row = dict(decision_id=decision['decision_id'], now_ns=decision['now_ns'],
                    native_drained_before_commit=decision['drained'],
                    submitted_now=[n['id'] for n in nodes],
                    completed_wire_pending_by_plane=pending,
                    modes={mode: algorithm.predict(controller, mode) for mode in ('causal', 'propagation', 'declared')})
                sink.write(json.dumps(row, separators=(',', ':'))+'\n')
    print('Predicted three generic timing bounds from observed prefixes only')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--observed', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    predict(args.observed.resolve(), args.output.resolve())


if __name__ == '__main__':
    main()
