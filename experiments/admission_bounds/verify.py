"""Independent small timing cases, raw evidence checks, and corruption probes."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import tempfile

from experiments.admission_bounds.bounds import AdmissionBounds, earliest, propagation
from experiments.admission_bounds.evaluate import condition
from experiments.admission_bounds.predict import load, predict
from experiments.admission_bounds.prepare import save


def small_checks():
    cases = [
        ([('a', 0, []), ('b', 3, []), ('c', 0, [('a', 1), ('b', 2)]),
          ('d', 0, [('c', 2)]), ('d', 0, [('a', 9)])], {'a': 0, 'b': 3, 'c': 5, 'd': 7}),
        ([('x', 0, [('x', 1)])], {'x': float('inf')}),
        ([('a', 0, [('b', 1)]), ('b', 0, [('a', 1)]), ('a', 4, [])], {'a': 4, 'b': 5}),
        ([('a', 1, []), ('b', 0, [('a', 1), ('a', 3)])], {'a': 1, 'b': 4}),
    ]
    # Independent time-indexed closure, not the production priority-queue solver.
    for rules, expected in cases:
        actual = earliest(rules)
        oracle = {}
        for time in range(21):
            changed = True
            while changed:
                changed = False
                for target, base, deps in rules:
                    if target not in oracle and base <= time and all(n in oracle and oracle[n]+lag <= time for n, lag in deps):
                        oracle[target] = time
                        changed = True
        assert actual == expected
        assert all(oracle.get(n, float('inf')) == value for n, value in actual.items())
    topology = dict(nodes=[dict(id=i, kind='gpu') for i in range(3)] +
        [dict(id=3, kind='tor', plane=0), dict(id=4, kind='tor', plane=1)], links=[])
    for index, (a, b, ap, bp, delay) in enumerate(((0, 3, 1, 1, 5), (3, 1, 2, 1, 7),
        (0, 2, 2, 1, 0), (2, 1, 2, 2, 0), (0, 4, 3, 1, 1), (4, 1, 2, 3, 1))):
        topology['links'].append(dict(id=index, src=a, dst=b, src_port=ap, dst_port=bp, delay_ns=delay))
    connection = dict(id=0, src=0, dst=1, source_nic=1, destination_nic=1, plane=0)
    assert propagation(topology, connection) == propagation(topology, connection, True) == 12
    try:
        propagation(topology, connection | {'source_nic': 99})
    except ValueError:
        pass
    else:
        raise AssertionError('unreachable NIC accepted')
    graph = dict(events=[dict(id='started', deps=[], actions=[dict(work=0, wait_before=[])], callback=[0, 'local_started']),
        dict(id='next', deps=['started'], actions=[dict(work=1, wait_before=[[0, 'completed']])], callback=[1, 'completed'])])
    program = dict(connections=[], operations=[dict(id=0, kind='local', rank=0, duration_ns=20, after=[]),
        dict(id=1, kind='local', rank=0, duration_ns=0, after=[dict(operation=0, event='completed')])])
    prefix = SimpleNamespace(now=10, log=[dict(event='started', time_ns=2)],
        submission_log=[dict(operation=0, time_ns=1)], seen={(0, 'local_admitted'): 1, (0, 'local_started'): 2}, submitted={0})
    prediction = AdmissionBounds(graph, program, dict(nodes=[], links=[])).predict(prefix, 'declared')
    assert prediction['work'] == [[1, 22]]  # Retain historical start=2; do not clamp it to now=10.
    assert [0, 'completed', 22] in prediction['callbacks']
    return 7


def independent(stage, name):
    native = [json.loads(l) for l in (stage/'observed'/name/'decisions.jsonl').read_text().splitlines()]
    truth = load(stage/'truth'/name/'result.json')
    callbacks = {(e['operation'], e['event']): e['time_ns'] for row in native for b in row['batches'] for e in b['events']}
    control = [json.loads(l) for l in (stage/'truth'/name/'control.jsonl').read_text().splitlines()]
    registration = {op['id']: row['message']['now_ns'] for row in control if row['direction'] == 'frontend'
                    for op in row['message']['submissions']}
    tested = 0
    for row in [json.loads(l) for l in (stage/'predictions'/(name+'.jsonl')).read_text().splitlines()]:
        for mode in row['modes'].values():
            for identity, bound in mode['work']:
                assert row['now_ns'] <= bound <= registration[identity]
                tested += 1
            for identity, event, bound in mode['callbacks']:
                assert row['now_ns'] <= bound <= callbacks[identity, event]
                tested += 1
    # Exact ACK RX times exist in the wire capture, independently of transport final timestamps.
    witness = next(op for op in truth['transactions']['operations'] if op['id'] == 1)
    reply = next(w for w in truth['transactions']['transport']['work'] if w['stream'] == 3 and w['begin'] == 0)
    con = next(c for c in load(stage/'observed/program.json')['connections'] if c['id'] == 1)
    headers = truth['packet_capture']['wire_headers']
    ack_rows = [event for event, i in zip(truth['packet_capture']['events'], headers['event_header_indices'])
                if event[6] == 252 and headers['headers'][i][4] == con['response_port']]
    assert reply['acked_ns'] > witness['completed_ns']
    assert any(row[0] == reply['acked_ns'] and row[1] == con['dst'] and row[3] == 1 for row in ack_rows)
    assert any(witness['completed_ns'] <= row[0] < reply['acked_ns'] and row[3] == 0 for row in ack_rows)
    return dict(independent_bound_comparisons=tested, reply_tail_ns=reply['acked_ns']-witness['completed_ns'],
                actual_ACK_TX_and_final_RX_checked=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--repository', type=Path, required=True)
    args = parser.parse_args()
    stage, repo = args.stage.resolve(), args.repository.resolve()
    result = dict(passed=False, small_checks=small_checks())
    try:
        for name, digest in load(stage/'source.json')['source_hashes'].items():
            assert hashlib.sha256((repo/name).read_bytes()).hexdigest() == digest
        result['independent'] = {n: independent(stage, n) for n in ('baseline', 'bandwidth')}
        with tempfile.TemporaryDirectory(prefix='verification-', dir=stage) as temporary:
            root = Path(temporary)
            shutil.copytree(stage/'observed', root/'observed')
            (root/'truth').mkdir()
            (root/'truth'/'poison.json').write_text('{invalid future truth, must not be read')
            predict(root/'observed', root/'predictions')
            result['truth_unavailable_prediction_byte_identical'] = {
                n: (root/'predictions'/(n+'.jsonl')).read_bytes() == (stage/'predictions'/(n+'.jsonl')).read_bytes()
                for n in ('baseline', 'bandwidth')}
            assert all(result['truth_unavailable_prediction_byte_identical'].values())
            (root/'truth'/'baseline').mkdir()
            for name in ('control.jsonl', 'result.json'):
                shutil.copyfile(stage/'truth'/'baseline'/name, root/'truth'/'baseline'/name)
            rejected = []
            original_predictions = (root/'predictions/baseline.jsonl').read_bytes()
            original_result = (root/'truth/baseline/result.json').read_bytes()
            original_control = (root/'truth/baseline/control.jsonl').read_bytes()
            for kind in ('missing_callback_bound', 'nonconservative_bound', 'early_reply_ack', 'incomplete_capture', 'changed_work'):
                if kind in ('missing_callback_bound', 'nonconservative_bound'):
                    rows = [json.loads(l) for l in original_predictions.splitlines()]
                    if kind == 'missing_callback_bound':
                        rows[0]['modes']['declared']['callbacks'].pop()
                    else:
                        rows[0]['modes']['declared']['work'][0][1] = 10**9
                    (root/'predictions/baseline.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
                elif kind == 'changed_work':
                    rows = [json.loads(l) for l in original_control.splitlines()]
                    next(r for r in rows if r['direction'] == 'frontend')['message']['submissions'][0]['value'] += 1
                    (root/'truth/baseline/control.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
                else:
                    doc = json.loads(original_result)
                    if kind == 'early_reply_ack':
                        next(w for w in doc['transactions']['transport']['work'] if w['stream'] == 3)['acked_ns'] = 0
                    else:
                        doc['packet_capture']['complete'] = False
                    save(root/'truth/baseline/result.json', doc)
                try:
                    condition(root, 'baseline')
                except ValueError:
                    rejected.append(kind)
                else:
                    raise AssertionError('corruption accepted: '+kind)
                (root/'predictions/baseline.jsonl').write_bytes(original_predictions)
                (root/'truth/baseline/result.json').write_bytes(original_result)
                (root/'truth/baseline/control.jsonl').write_bytes(original_control)
            result['rejected_corruptions'] = rejected
        result['passed'] = True
    finally:
        save(stage/'verification.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
