"""Hand-computed coverage, aggregation and accidental truth-access checks."""
from collections import defaultdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from experiments.scaleup_observability.common import digest, read, save
from experiments.scaleup_observability.inputs import verified_case
from experiments.scaleup_observability.observation_workflow import prepare_source
from experiments.scaleup_observability.observation_spec import END_NS, GRIDS, COUNT_FIELDS, grid_id, windows
from experiments.scaleup_observability.observation_prepare import aggregate, intersection, overlap, rank_truth
from experiments.scaleup_observability.observation_measure import selected
from experiments.scaleup_observability.observation_evaluate import accumulate, metrics
from experiments.scaleup_observability.slots import verify_pair_inputs


class Observations(unittest.TestCase):
    def test_grid_edges_cover_entire_declared_interval(self):
        for p, o in GRIDS:
            rows = windows(p, o)
            self.assertEqual(sum(b-a for a, b in rows), END_NS)
            self.assertEqual((rows[0][0], rows[-1][1]), (0, END_NS))
            self.assertTrue(all(a % 100 == b % 100 == 0 for a, b in rows))
            self.assertTrue(all(a[1] == b[0] for a, b in zip(rows, rows[1:])))
        self.assertEqual(windows(10000, 5000)[0], (0, 5000))
        self.assertEqual(windows(10000, 5000)[-1], (95000, 100000))
        with self.assertRaises(ValueError): windows(100, 50)

    def test_merge_counts_queue_silence_and_peak(self):
        # Second base window has no packet, but nonzero queue occupancy.
        data = [[[2, 100, 30, 9, 0, 0]], [[0, 0, 40, 7, 20, 3]]]
        self.assertEqual(aggregate(data, 0, 200), [[2, 100, 70, 9, 20, 3]])
        self.assertFalse(selected('counts', data[1]))
        self.assertTrue(selected('counts-queues', data[1]))
        self.assertFalse(selected('counts-queues', [[1, 64, 0, 64, 0, 0]]))
        with self.assertRaises(ValueError): aggregate(data, 50, 200)

    def test_union_rank_wait_and_same_time_release(self):
        def node(i, ready, start, end):
            return dict(rank_id=0, node_id=i, kind='SEND', dependency_ready_ns=ready, start_ns=start, end_ns=end)
        nodes = [node(1, 0, 0, 5), node(2, 1, 5, 7), node(3, 2, 7, 10)]
        pending, blocked = rank_truth(nodes, 1, 10)[0]
        # Node waits total 9, but their union is only 6 rank ns.
        self.assertEqual(overlap(pending, 0, 10), 6)
        self.assertEqual(overlap(blocked, 0, 10), 6)
        self.assertEqual(overlap(blocked, 5, 7), 2)
        self.assertEqual(intersection([[0, 2], [4, 8]], [[2, 5], [7, 9]]), [(4, 5), (7, 8)])

    def test_evaluation_hand_calculation(self):
        total = defaultdict(int)
        truth = dict(start_ns=0, end_ns=10, active_ns=8, ranks=[[4, 1, 5]]+[[0, 0, 10]]*7)
        accumulate(total, truth, dict(selected=True, state='undetermined'))
        truth['ranks'] = [[2, 0, 8]]+[[0, 0, 10]]*7
        accumulate(total, truth, dict(selected=False, state='undetermined'))
        m = metrics(total)
        self.assertEqual(m['declared_rank_ns'], 160)
        self.assertEqual(m['active_rank_ns'], 128)
        self.assertEqual(m['investigated_fraction'], .5)
        self.assertEqual(m['wait_coverage_fraction'], 4/6)
        self.assertEqual(m['wait_share_of_selected_time'], 4/80)
        self.assertEqual(m['false_lead_cells'], 7)
        self.assertEqual(m['missed_target_cells'], 1)
        self.assertEqual(m['missed_wait_rank_ns'], 2)
        self.assertEqual(m['undetermined_fraction'], 1)

    def test_fresh_source_only_slots_change(self):
        with tempfile.TemporaryDirectory() as temp:
            batch = Path(temp)/'source'; prepare_source(batch)
            _, a = verified_case(batch, 's3-b8-slots1')
            _, b = verified_case(batch, 's3-b8-slots2')
            verify_pair_inputs(a, b)
            self.assertEqual(read(a/'service.json')['requests'][0]['request_id'], 192)
            self.assertEqual(len(read(batch/'sources.json')['cases']), 2)

    def test_measure_public_only_and_reject_unknown_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); public = root/'observed'; public.mkdir()
            (root/'truth.json').write_text('do not read')
            with (public/'windows.jsonl').open('w') as stream:
                for p, o in GRIDS:
                    for a, b in windows(p, o):
                        stream.write(json.dumps(dict(sample_id='sample-000', grid=grid_id(p, o),
                            start_ns=a, end_ns=b, ports=[[0, 0]]*32))+'\n')
            config = dict(schema_version='scaleup-observation-public/1', condition='counts',
                rank_ids=list(range(8)), port_ids=[[i, 0] for i in range(32)], fields=list(COUNT_FIELDS),
                topology=dict(nodes=[[i,'SWITCH',1,0,0] for i in range(32)]+
                              [[i,'DEVICE',1,0,0] for i in range(32,40)], links=[],
                              rank_nodes=[[i,i+32] for i in range(8)]),
                interval_ns=[0, END_NS], grids=GRIDS, sample_ids=['sample-000'],
                windows_sha256=digest(public/'windows.jsonl'))
            save(public/'manifest.json', config)
            out = root/'result'; out.mkdir()
            def run(target):
                return subprocess.run([sys.executable, '-m', 'experiments.scaleup_observability.observation_measure',
                    str(public), str(target)], capture_output=True, text=True)
            result = run(out)
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = read(out/'summary.json')
            self.assertEqual(summary['predictions_sha256'], digest(out/'predictions.jsonl'))
            self.assertEqual(summary['windows'], 1222)
            self.assertTrue(all(not json.loads(line)['selected'] for line in (out/'predictions.jsonl').read_text().splitlines()))
            config['true_finish_ns'] = 60000; save(public/'manifest.json', config)
            bad = root/'bad'; bad.mkdir(); result = run(bad)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Public manifest field violation', result.stderr)

    def test_guard_denies_truth_symlink_and_external_process(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); observed = root/'observed'; observed.mkdir(); output = root/'out'; output.mkdir()
            secret = root/'truth'; secret.write_text('internal')
            (observed/'alias').symlink_to(secret)
            for expression in ("Path(sys.argv[3]).read_text()", "(Path(sys.argv[1])/'alias').read_text()",
                               "subprocess.run(['true'])", "Path(sys.argv[3]).write_text('changed')"):
                code = ('from pathlib import Path; import sys, subprocess; '
                        'from experiments.scaleup_observability.observation_measure import access_guard; '
                        'access_guard(sys.argv[1],sys.argv[2]); '+expression)
                result = subprocess.run([sys.executable, '-c', code, str(observed), str(output), str(secret)],
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('PermissionError', result.stderr)
            self.assertEqual(secret.read_text(), 'internal')


if __name__ == '__main__': unittest.main()
