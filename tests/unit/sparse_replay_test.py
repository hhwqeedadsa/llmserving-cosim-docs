"""Sparse requests preserve source identities, expert work and real KV lifecycle."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from moe_ub_cosim.scaleout_rdma import ROOT
from moe_ub_cosim.scaleout_llm import EXTRA_COSTS,capacity
from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_service import Frontend,make_wave
from moe_ub_cosim.scaleout_verify import verify_replay_requests


class SparseChecks(unittest.TestCase):
    def setUp(self):
        self.c=json.loads((ROOT/'examples/scaleout_route_replay/service.json').read_bytes())
        self.c['workload']['batch_per_rank']=2
        spec=self.c['route_replay'];spec['request_layout']='sparse_explicit/1'
        spec['requests']=[spec['requests'][0],spec['requests'][2]]
        spec['requests'][0]['slot']=1  # Preserve gaps; do not renumber to slot zero.

    def test_ids_routes_and_relocatable_snapshot(self):
        replay=Replay(self.c,ROOT)
        self.assertEqual(replay.request_ids(0),[1]);self.assertEqual(replay.request_ids(2),[4])
        self.assertEqual(replay.contexts(1),[]);self.assertEqual(replay.contexts(0),[17])
        selected=replay.routes([0,1,2,3],1,1)
        self.assertEqual([(r['rank'],r['slot'],r['source']['request_id']) for r in selected],[(0,1,1),(2,0,4)])
        with tempfile.TemporaryDirectory() as folder:
            c,_=replay.snapshot(folder);saved=Replay(c,folder)
            self.assertEqual(saved.routes([0,1,2,3],1,1),selected)

    def test_empty_group_and_unknown_mode_rejected(self):
        for kind in ('empty','empty-group','unknown','dense'):
            c=deepcopy(self.c)
            if kind=='empty':c['route_replay']['requests']=[]
            if kind=='empty-group':c['parallel']['groups']=[[0,2],[1,3]]
            if kind=='unknown':c['route_replay']['request_layout']='infer_or_clone'
            if kind=='dense':del c['route_replay']['request_layout']
            with self.subTest(kind=kind),self.assertRaises(ValueError):Replay(c,ROOT)

    def test_expert_only_rank_retains_remote_dependencies_and_work(self):
        replay=Replay(self.c,ROOT);wave=make_wave(self.c,[0,1,2,3],0,0,1,replay)
        expected={r:0 for r in range(4)}
        for row in self.c['route_replay']['requests']:
            original=json.loads((ROOT/'examples/scaleout_route_replay/routes'/f"request-{row['rank_id']}.json").read_bytes())
            for expert in original[row['first_decode_entry']]['1'][0]:expected[expert]+=1
        for rank in (1,3):
            tasks={n['name']:n for n in wave['rank_nodes'][rank]}
            self.assertEqual(tasks['L0.attention']['duration_ns'],0)
            self.assertEqual(tasks['L0.pack']['duration_ns'],0)
            self.assertEqual(tasks['L0.experts']['duration_ns'],100*expected[rank])
            incoming=[n['id'] for n in tasks.values() if n['kind']=='RECV' and n['name'].startswith('L0.dispatch.')]
            self.assertTrue(set(incoming)<=set(tasks['L0.experts']['deps']))
        self.assertTrue(any(expected[r] for r in (1,3)))
        expected_bytes=2*512*sum(e!=row['rank'] for row in wave['routes'][0]['tokens'] for e in row['experts'])
        self.assertEqual(sum(m['bytes'] for m in wave['messages'] if m['layer']==0),expected_bytes)

    def test_real_scheduler_drains_sparse_slots_without_fake_requests(self):
        replay=Replay(self.c,ROOT)
        class Preflight:
            def accept_wave(self,wave,now):pass  # Scheduler-only test; no network model.
        with tempfile.TemporaryDirectory() as folder,patch('moe_ub_cosim.scaleout_service.export_wave'):
            frontend=Frontend(self.c,4,1,replay,message_preflight=Preflight())
            self.assertEqual(set(frontend.schedulers),{0,2})
            event=dict(now_ns=0,completed=[],available_rank_ids=list(range(4)),decision_id=0)
            self.assertFalse(frontend.decide(event,Path(folder),None)['service_finished'])
            for step in range(2):
                event.update(now_ns=(step+1)*100000,completed=[dict(graph_id=f'g0.w{step}',finished_ns=(step+1)*100000)],decision_id=step+1)
                reply=frontend.decide(event,Path(folder),None)
            self.assertTrue(reply['service_finished'])
            self.assertEqual(frontend.calls,4)
            self.assertEqual({q.id for q in frontend.requests},{1,4})
            self.assertTrue(all(s.kv.npu_pool.used_bytes()==0 for s in frontend.schedulers.values()))
            report=dict(history=frontend.history,scheduler_calls=frontend.calls,active_source_ranks=[0,2],
                requests=[dict(id=q.id,state='OBSERVATION_ENDED',scheduler_state=q.status.name,
                    initial_context_tokens=replay.records[q.id]['context_tokens'],intertoken_ns=q.itl) for q in frontend.requests])
            verify_replay_requests(self.c,replay,report)
            for kind in ('missing','duplicate','wrong-rank','fake-request','call-count'):
                changed=deepcopy(report)
                if kind=='missing':changed['history'][0]['batch_request_ids'].pop('0')
                if kind=='duplicate':changed['history'][0]['batch_request_ids']['0']*=2
                if kind=='wrong-rank':changed['history'][0]['batch_request_ids']['1']=changed['history'][0]['batch_request_ids'].pop('0')
                if kind=='fake-request':changed['requests'].append(dict(changed['requests'][0],id=2))
                if kind=='call-count':changed['scheduler_calls']+=1
                with self.subTest(kind=kind),self.assertRaises(ValueError):verify_replay_requests(self.c,replay,changed)

    def test_sparse_full_gqa_has_no_idle_token_cost_or_kv(self):
        with tempfile.TemporaryDirectory() as folder:
            c,_=Replay(self.c,ROOT).snapshot(folder)
            manifest_path=Path(folder)/'routes/manifest.json';manifest=json.loads(manifest_path.read_bytes())
            manifest['layers'][0].update(kind='moe',num_experts=4,top_k=2)
            for item in manifest['files']:
                path=Path(folder)/item['file']['path'];raw=json.loads(path.read_bytes())
                for entry in raw:entry['0']=deepcopy(entry['1'])
                path.write_text(json.dumps(raw));item['file']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest))
            c['route_replay']['manifest']['sha256']=hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            c['route_replay']['layer_ids']=[0,1,2,3];c['model']['layers']=4
            c['schema_version']='moe-scaleout-service/2'
            c['model']['llm']=dict(attention_heads=4,kv_heads=2,head_dim=64,vocab_size=100,
                                 tied_embeddings=False,weight_bytes=2,device_bytes=2**30)
            c['compute'].update({key+'_ns_per_token':3 for key in EXTRA_COSTS})
            replay=Replay(c,folder);wave=make_wave(c,[0,1,2,3],0,0,1,replay)
            for rank in (1,3):
                self.assertEqual(capacity(c,4,replay.contexts(rank))['kv_reserved_bytes_per_rank'],0)
                self.assertGreater(capacity(c,4,[])['weight_bytes_per_rank'],0)
                for task in wave['rank_nodes'][rank]:
                    if task['kind']=='COMP' and not task['name'].endswith('.experts'):
                        self.assertEqual(task['duration_ns'],0,task['name'])
            active=next(n for n in wave['rank_nodes'][0] if n['name']=='L0.attention')
            self.assertEqual(active['duration_ns'],1000+17*3)

    def test_same_content_under_different_file_identity_is_not_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            c,_=Replay(self.c,ROOT).snapshot(folder)
            path=Path(folder)/'routes/manifest.json';manifest=json.loads(path.read_bytes())
            first,second=manifest['files']
            (Path(folder)/second['file']['path']).write_bytes((Path(folder)/first['file']['path']).read_bytes())
            second['file']['sha256']=first['file']['sha256'];path.write_text(json.dumps(manifest))
            c['route_replay']['manifest']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError,'cloning'):Replay(c,folder)


if __name__=='__main__':unittest.main()
