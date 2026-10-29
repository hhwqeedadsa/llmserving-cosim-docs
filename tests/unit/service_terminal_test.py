"""Actual application completion, terminal contradictions and failed rankings."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.service_terminal import service_terminal
from moe_ub_cosim.run_summary import describe
from moe_ub_cosim.scaleout_sweep import metrics
from moe_ub_cosim.scaleout_topology import generate

ROOT = Path(__file__).resolve().parents[2]


def completed():
    # Concurrent graphs overlap: duration sum must not determine completion.
    waves = [dict(graph_id='g0', start_ns=0, finished_ns=20),
             dict(graph_id='g1', start_ns=0, finished_ns=30)]
    result = dict(passed=True, drained=True, finish_ns=50, native_network_executed=True,
                  messages=[], fault_drops=0, service=dict(service_finished=True, active_graphs=0, waves=waves))
    final = dict(event=dict(type='DECIDE', decision_id=2, now_ns=30,
                           completed=[dict(graph_id='g1', finished_ns=30)]),
                 reply=dict(type='COMMIT', decision_id=2, now_ns=30, service_finished=True, submissions=[]))
    return result, dict(history=deepcopy(waves), transcript=[final])


class TerminalChecks(unittest.TestCase):
    def test_actual_application_and_post_application_drain(self):
        result, frontend = completed()
        original = deepcopy((result, frontend))
        timing = service_terminal(result, frontend, require_complete=True)
        self.assertEqual((timing['application_finished_ns'], timing['network_drained_ns'],
                          timing['network_stopped_ns'], timing['post_application_drain_ns']), (30, 50, 50, 20))
        self.assertEqual(timing['timing_status'], 'complete')
        self.assertEqual((result, frontend), original)
        result['finish_ns'] = 30
        self.assertEqual(service_terminal(result, frontend)['post_application_drain_ns'], 0)

    def test_missing_evidence_never_guesses_application_time(self):
        result, _ = completed()
        for native, reason in ((None, 'missing_native_result'), (result, 'missing_frontend_evidence')):
            self.assertIsNone(service_terminal(native)['application_finished_ns'])
            self.assertEqual(service_terminal(native)['timing_reason'], reason)
            with self.assertRaises(ValueError):
                service_terminal(native, require_complete=True)
        result, frontend = completed()
        frontend.pop('transcript')
        with self.assertRaisesRegex(ValueError, 'transcript'):
            service_terminal(result, frontend)

    def test_failed_partial_graphs_are_not_successful_service(self):
        result, frontend = completed()
        result.update(passed=False, drained=False, failure=dict(reason='recovery_retry_exhausted'))
        frontend['history'].append(dict(graph_id='g2', start_ns=30))
        timing = service_terminal(result, frontend)
        self.assertEqual(timing['timing_status'], 'failed')
        self.assertEqual(timing['network_stopped_ns'], 50)
        self.assertIsNone(timing['application_finished_ns'])
        self.assertIsNone(timing['network_drained_ns'])
        with self.assertRaisesRegex(ValueError, 'failed'):
            service_terminal(result, frontend, require_complete=True)

    def test_summary_cannot_override_control_or_actual_graphs(self):
        for label in ('reply_early', 'event_late', 'native_time', 'missing_graph', 'duplicate_graph',
                      'native_active', 'not_drained', 'extra_submission', 'decision', 'bool_time',
                      'negative_start', 'after_stop', 'wrong_completed', 'missing_terminal', 'duplicate_terminal'):
            r, f = completed()
            final = f['transcript'][-1]
            if label == 'reply_early': final['reply']['now_ns'] = 1
            elif label == 'event_late': final['event']['now_ns'] = 31
            elif label == 'native_time': r['service']['waves'][0]['finished_ns'] = 21
            elif label == 'missing_graph': f['history'].pop()
            elif label == 'duplicate_graph': f['history'].append(deepcopy(f['history'][0]))
            elif label == 'native_active': r['service']['active_graphs'] = 1
            elif label == 'not_drained': r['drained'] = False
            elif label == 'extra_submission': final['reply']['submissions'] = ['g2']
            elif label == 'decision': final['reply']['decision_id'] = 3
            elif label == 'bool_time': final['event']['now_ns'] = True
            elif label == 'negative_start': f['history'][0]['start_ns'] = -1
            elif label == 'after_stop': r['finish_ns'] = 29
            elif label == 'wrong_completed': final['event']['completed'][0]['graph_id'] = 'unknown'
            elif label == 'missing_terminal': final['reply']['service_finished'] = False
            elif label == 'duplicate_terminal': f['transcript'].append(deepcopy(final))
            with self.subTest(label=label), self.assertRaises(ValueError):
                service_terminal(r, f)

    def test_public_summary_and_sweep_share_actual_terminal(self):
        folder = ROOT/'examples/simai_severe_loss'
        c = json.loads((folder/'service.json').read_bytes())
        p = json.loads((folder/'half-burst.json').read_bytes())
        t, _ = generate(json.loads((ROOT/'examples/simai_service/topology.json').read_bytes()))
        r, f = completed()
        case = dict(name='condition', expect='success', comparison_group='same')
        with tempfile.TemporaryDirectory() as temporary:
            d = Path(temporary)
            for name, value in [('input', c), ('rdma', p), ('topology', t), ('result', r), ('frontend', f)]:
                (d/(name+'.json')).write_text(json.dumps(value))
            outcome = describe(d)['outcome']
            sweep = metrics(case, d)
            for key in service_terminal(r, f):
                self.assertEqual(outcome[key], sweep[key])
            self.assertEqual(outcome['finish_ns'], 50)  # Retained legacy meaning.
            self.assertEqual(sweep['frontend_complete_ns'], 30)
            r.update(passed=False, drained=False, failure=dict(reason='simulation_time_limit'))
            (d/'result.json').write_text(json.dumps(r))
            self.assertIsNone(describe(d)['outcome']['application_finished_ns'])
            self.assertIsNone(metrics(case, d)['frontend_complete_ns'])


if __name__ == '__main__':
    unittest.main()
