"""Ready-KV handoff, request progress and protocol validation using real schedulers."""
from copy import deepcopy
import json
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import unittest

from moe_ub_cosim.service import Service, read_line, validate_service

ROOT = Path(__file__).resolve().parents[2]


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'fixtures/m3_service/service.json').read_text())
        self.layer = json.loads((ROOT / 'fixtures/m2_single_layer/layer.json').read_text())

    def test_scheduler_progress_only_on_matching_wave_done(self):
        service = Service(self.config, self.layer)
        initial = [r.num_tokens_reached for r in service.requests]
        self.assertEqual([s['decode_step'] for s in service.initial], [1, 2, 3, 4])
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / 'graphs').mkdir()
            times = [0, 20100, 50400, 80900]
            for step, now in enumerate(times):
                message = dict(schema_version='cosim/1', type='DECIDE', run_id=self.config['run_id'],
                    decision_id=step, now_ns=now, completed=[] if not step else [dict(kind='WAVE_DONE',
                    graph_id=f'g{step-1}', wave_id=f'D.w{step-1}', finished_ns=now)], available_rank_ids=[0, 1, 2, 3])
                before = service.snapshot()
                bad = deepcopy(message); bad['decision_id'] = step + 1
                with self.assertRaisesRegex(ValueError, 'decision_id'):
                    service.decide(bad, output)
                self.assertEqual(before, service.snapshot())
                response = service.decide(message, output)
                self.assertEqual([r.num_tokens_reached for r in service.requests], [n + step for n in initial])
                self.assertEqual(response['service_finished'], step == 3)
                self.assertEqual(response['now_ns'], now)
                if step < 3:
                    tokens = json.loads((output / f'graphs/g{step}/plan.json').read_text())['input']['tokens']
                    self.assertEqual([t['token_index'] for t in tokens], [n - 1 + step for n in initial])
                    self.assertTrue(all(b.batch_time == now for s in service.schedulers for b in s.inflight))
            self.assertTrue(all(r.status.name == 'FINISHED' for r in service.requests))
            self.assertTrue(all(s.is_request_empty() and len(s.done) == 1 for s in service.schedulers))
            self.assertTrue(all(r.itl == [20100, 30300, 30500] for r in service.requests))
            self.assertEqual(service.current_ns, 80900)

    def test_reject_out_of_scope_or_malformed_service(self):
        cases = []
        def change(callback):
            value = deepcopy(self.config); callback(value); cases.append(value)
        change(lambda c: c['requests'][0].update(arrival_ns=1))
        change(lambda c: c['requests'][0].update(generated_tokens=0))
        change(lambda c: c['requests'][0].update(rank_id=1))
        change(lambda c: c['requests'][0].update(request_id=102))
        change(lambda c: c['requests'][0]['decode_routes'].pop())
        change(lambda c: c['requests'][0]['decode_routes'][0].__setitem__(1, 2))
        change(lambda c: c.update(mode='research'))
        change(lambda c: c.update(execution={'entry_mode': 'other'}))
        for config in cases:
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_service(config, self.layer)

    def test_partial_protocol_line_has_bounded_wait(self):
        child = subprocess.Popen([sys.executable, '-c',
            "import os,time; os.write(1,b'{'); time.sleep(10)"], stdout=subprocess.PIPE, bufsize=0)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                with self.assertRaisesRegex(ValueError, 'Timed out'):
                    read_line(child, selector, bytearray(), 0.1)
        finally:
            child.kill(); child.wait(); child.stdout.close()


if __name__ == '__main__':
    unittest.main()
