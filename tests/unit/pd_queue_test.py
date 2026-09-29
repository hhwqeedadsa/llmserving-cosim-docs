"""Exercise real scheduler pools at queued handoff boundaries."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.pd_queue_service import QueuedPdService

ROOT=Path(__file__).resolve().parents[2]


class QueuedPdTest(unittest.TestCase):
    def setUp(self):
        self.config=json.loads((ROOT/'fixtures/m4_pd_queue/service.json').read_text())
        self.layer=json.loads((ROOT/'fixtures/m4_pd_queue/layer.json').read_text())
        self.config['independent_requests']=[]

    def test_capacity_wait_and_fifo_lifecycle(self):
        s=QueuedPdService(self.config,self.layer)
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);(out/'graphs').mkdir()
            def decide(now,graph=None):
                completed=[]
                if graph:
                    w=s.waves[graph];completed=[dict(kind=w['kind'],graph_id=graph,wave_id=w['wave_id'],finished_ns=now)]
                busy={r for g,w in s.waves.items() if g!=graph for r in w['rank_ids']}
                return s.decide(dict(schema_version='cosim/1',type='DECIDE',run_id=self.config['run_id'],
                    decision_id=s.decision_id,now_ns=now,completed=completed,
                    available_rank_ids=[r for r in range(12) if r not in busy]),out)
            r=decide(0);self.assertEqual(len(r['submissions']),1)
            self.assertEqual(s.history[0]['request_ids'],[501,502])
            r=decide(121000,'g0');self.assertEqual(s.history[-1]['role'],'KV')
            self.assertEqual([q.id for q in s.handoff_queue],[502])
            self.assertEqual(s.requests[0].ttft,121000)
            self.assertEqual(s.requests[0].itl,[])
            self.assertEqual(s.schedulers[0].kv.npu_used_bytes(),16384)
            self.assertEqual(s.schedulers[2].kv.npu_used_bytes(),16384)
            self.assertEqual(s.schedulers[5].kv.npu_used_bytes(),16384)
            r=decide(200000,'g1');self.assertEqual(s.history[-1]['role'],'D')
            self.assertEqual(s.schedulers[0].kv.npu_used_bytes(),0)
            self.assertEqual(s.schedulers[2].kv.npu_used_bytes(),16384)
            self.assertEqual(s.history[-1]['request_ids'],[501])
            self.assertTrue(any(e['event']=='capacity_wait' and e['request_id']==502 for e in s.memory_events))
            decide(230000,'g2');decide(260000,'g3')
            self.assertEqual(s.history[-1]['role'],'KV');self.assertEqual(s.history[-1]['request_ids'],[502])
            decide(300000,'g4');decide(330000,'g5');r=decide(360000,'g6')
            self.assertTrue(r['service_finished']);self.assertTrue(all(x.kv.is_free() for x in s.schedulers))
            self.assertEqual(s.requests[0].itl,[109000,30000])
            self.assertEqual(s.requests[1].itl,[209000,30000])
            self.assertEqual([w['request_ids'] for w in s.history if w['role']=='KV'],[[501],[502]])

    def test_available_capacity_prioritizes_queued_handoff(self):
        self.config['npu_memory_bytes'][5]+=16384
        s=QueuedPdService(self.config,self.layer)
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);(out/'graphs').mkdir()
            def call(now,graph=None):
                w=s.waves.get(graph)
                return s.decide(dict(schema_version='cosim/1',type='DECIDE',run_id=self.config['run_id'],
                    decision_id=s.decision_id,now_ns=now,available_rank_ids=list(range(12)),
                    completed=[] if w is None else [dict(kind=w['kind'],graph_id=graph,wave_id=w['wave_id'],finished_ns=now)]),out)
            call(0);call(121000,'g0');call(200000,'g1')
            self.assertEqual(s.history[-1]['role'],'KV');self.assertEqual(s.history[-1]['request_ids'],[502])
            self.assertFalse(any(w['role']=='D' for w in s.history))
            self.assertEqual(s.schedulers[5].kv.npu_used_bytes(),32768)
            self.assertEqual([r.id for r in s.schedulers[5].running],[501])

    def test_busy_arrival_does_not_release_or_schedule_source(self):
        c=deepcopy(self.config);late=deepcopy(c['requests'][0]);late.update(request_id=503,arrival_ns=130000)
        c['requests'].append(late);s=QueuedPdService(c,self.layer)
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);(out/'graphs').mkdir()
            first=dict(schema_version='cosim/1',type='DECIDE',run_id=c['run_id'],decision_id=0,now_ns=0,
                available_rank_ids=list(range(12)),completed=[])
            s.decide(first,out)
            s.decide(dict(first,decision_id=1,now_ns=121000,completed=[dict(kind='WAVE_DONE',graph_id='g0',wave_id='P.w0',finished_ns=121000)]),out)
            response=s.decide(dict(first,decision_id=2,now_ns=130000,available_rank_ids=[8,9,10,11]),out)
            self.assertEqual(response['submissions'],[]);self.assertEqual(len(s.history),2)
            self.assertEqual([q.id for q in s.pending[0]],[503]);self.assertEqual(s.states[501],'KV_INFLIGHT')

    def test_reject_mapping_capacity_and_duplicates(self):
        for modify in (lambda c:c['groups'][2]['rank_ids'].__setitem__(0,7),
                       lambda c:c['requests'][1].update(request_id=501),
                       lambda c:c['requests'][0].update(prompt_tokens=16,prefill_routes=[[0,1]]*16)):
            c=deepcopy(self.config);modify(c)
            with self.assertRaises((ValueError,RuntimeError)):QueuedPdService(c,self.layer)


if __name__=='__main__':unittest.main()
