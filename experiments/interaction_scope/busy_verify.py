"""Independent queue-prefix counts, small counterexamples, corruption probes."""
import argparse
import hashlib
from itertools import product
import json
from pathlib import Path
import shutil
import tempfile

from experiments.interaction_scope.busy_prefix import PrefixPlanner, select_prefix, service
from experiments.interaction_scope.busy_study import evaluate, load, optimal_prefix_upper, predict, save, snapshots


def small():
    pid = (0, 17, 0, 2, 0)
    queued = [((1, 17, 0, 3, 0), 3), ((2, 17, 0, 4, 0), 4)]
    assert len(select_prefix((pid, 2), queued, 0, 8*10**9, 10)[0]) == 3
    assert len(select_prefix((pid, 2), queued, 0, 8*10**9, 9)[0]) == 2
    assert len(select_prefix((pid, 2), queued, 0, 8*10**9, 2)[0]) == 1
    assert service(2036, 200*10**9)*2 == 162 and service(4072, 200*10**9) == 163
    nodes = [dict(id=0, kind='gpu'), dict(id=1, kind='tor'), dict(id=2, kind='gpu')]
    links = [dict(src=0, dst=1, src_port=1, dst_port=1, rate_bps=8*10**9, delay_ns=10),
             dict(src=1, dst=2, src_port=2, dst_port=1, rate_bps=8*10**9, delay_ns=10)]
    p = PrefixPlanner(dict(nodes=nodes, links=links), dict(ecn_by_rate={str(8*10**9): dict(kmin_bytes=100)}))
    def row(time, node, port, event, uid, size, protocol=17, occupancy=0):
        return [time, node, port, event, uid, -1, protocol, 0, size, 0, occupancy, 0]
    # ACK already started at a neighbour; same-priority FIFO packets are local inputs.
    records = [row(0, 0, 1, 0, 5, 1, 252), row(1, 1, 2, 3, 0, 2, occupancy=2),
               row(1, 1, 2, 3, 1, 2, occupancy=4), row(10, 1, 2, 4, 0, 2, occupancy=4),
               row(10, 1, 2, 0, 0, 2)]
    plans = []
    for ordinal, r in enumerate(records):
        plans.extend(p.observe(r, ordinal))
    by_mode = {r['mode']: r for r in plans}
    assert by_mode['conservative']['deadline_ns'] == 11
    assert len(by_mode['conservative']['selected']) == 1 and len(by_mode['topology_only']['selected']) == 2
    p.observe(row(11, 1, 1, 1, 5, 1, 252), 5)
    p.observe(row(11, 1, 2, 3, 5, 1, 252, occupancy=3), 6)
    try:
        p.observe(row(12, 1, 2, 4, 1, 2, occupancy=3), 7)
    except ValueError as error:
        assert 'priority' in str(error)
    else:
        raise AssertionError('queued ACK did not prevent low-priority dequeue')
    # A receive tied with the current time leaves no exclusive batching window.
    assert len(select_prefix((pid, 2), queued, 10, 8*10**9, 10)[0]) == 1
    enumerated = 0
    for count in range(1, 7):
        for capacities in product(*(range(1, count-i+1) for i in range(count))):
            def exhaustive(position):
                if position == count:
                    return 0
                return min(1+exhaustive(position+size) for size in range(1, capacities[position]+1))
            assert optimal_prefix_upper(capacities) == count-exhaustive(0)
            enumerated += 1
    return dict(hand_cases=7, independently_enumerated_capacity_cases=enumerated)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--repository', type=Path, required=True)
    args = parser.parse_args()
    stage, repo = args.stage.resolve(), args.repository.resolve()
    out = dict(passed=False, small=small())
    try:
        for name, digest in load(stage/'source.json')['source_hashes'].items():
            assert hashlib.sha256((repo/name).read_bytes()).hexdigest() == digest
        summary = evaluate(stage)
        independent = {}
        for condition in ('baseline', 'bandwidth'):
            result = load(stage/'truth'/condition/'result.json')
            kinds = {n['id']: n['kind'] for n in load(stage/'observed'/condition/'topology.json')['nodes']}
            tx_count = sum(r[3] == 0 for r in result['packet_capture']['events'])
            switch_count = sum(r[3] == 0 and kinds[r[1]] != 'gpu' for r in result['packet_capture']['events'])
            plans = [json.loads(l) for l in (stage/'predictions'/(condition+'.jsonl')).read_text().splitlines()]
            selected = [p for p in plans if p['mode'] == 'conservative']
            assert sum(len(p['selected']) for p in selected) == switch_count == 1936
            assert tx_count == 2464
            saved = switch_count-len(selected)
            assert saved == summary[condition]['modes']['conservative']['candidate_TransmitComplete_callbacks_saved']
            assert summary[condition]['modes']['topology_only']['plans_disagreeing_with_actual_TX'] == 2
            independent[condition] = dict(raw_TX_count=tx_count, raw_switch_TX_count=switch_count,
                conservative_selected_packets=sum(len(p['selected']) for p in selected), callbacks_saved_candidate=saved)
        out['independent'] = independent
        with tempfile.TemporaryDirectory(prefix='busy-verification-', dir=stage) as temporary:
            root = Path(temporary)
            shutil.copytree(stage/'observed', root/'observed')
            (root/'truth').mkdir()
            (root/'truth/poison.json').write_text('not valid future truth')
            predict(root/'observed', root/'predictions')
            snapshots(root)
            out['future_truth_unavailable_byte_identical'] = {
                n: (root/'predictions'/n).read_bytes() == (stage/'predictions'/n).read_bytes()
                for n in ('baseline.jsonl', 'bandwidth.jsonl', 'baseline-snapshots.jsonl', 'bandwidth-snapshots.jsonl')}
            assert all(out['future_truth_unavailable_byte_identical'].values())
            shutil.copytree(stage/'truth/baseline', root/'truth/baseline')
            shutil.copytree(stage/'truth/bandwidth', root/'truth/bandwidth')
            shutil.copyfile(stage/'source.json', root/'source.json')
            target = root/'predictions/baseline.jsonl'
            raw = target.read_bytes()
            snapshot_target = root/'predictions/baseline-snapshots.jsonl'
            snapshot_raw = snapshot_target.read_bytes()
            rejected = []
            for kind in ('forged_deadline', 'changed_TX_time', 'repeated_packet', 'omitted_queued_capacity'):
                rows = [json.loads(l) for l in raw.splitlines()]
                plan = next(r for r in rows if r['mode'] == 'conservative' and len(r['selected']) > 1)
                if kind == 'forged_deadline':
                    plan['deadline_ns'] += 1
                elif kind == 'changed_TX_time':
                    plan['selected'][1][1] += 1
                elif kind == 'repeated_packet':
                    plan['selected'].append(plan['selected'][-1])
                else:
                    ss = [json.loads(l) for l in snapshot_raw.splitlines()]
                    next(s for s in ss if len(s['queued_prefix']) > 1)['queued_prefix'].pop()
                    snapshot_target.write_text(''.join(json.dumps(s)+'\n' for s in ss))
                target.write_text(''.join(json.dumps(r)+'\n' for r in rows))
                try:
                    evaluate(root)
                except ValueError:
                    rejected.append(kind)
                else:
                    raise AssertionError('corrupted plan accepted: '+kind)
                target.write_bytes(raw)
                snapshot_target.write_bytes(snapshot_raw)
            out['rejected_corruptions'] = rejected
        out['passed'] = True
    finally:
        save(stage/'verification.json', out)
    print(json.dumps(out))


if __name__ == '__main__':
    main()
