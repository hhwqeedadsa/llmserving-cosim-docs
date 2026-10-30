"""Source identity, independent byte accounting and replay failure boundaries."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.scaleout_rdma import ROOT
from moe_ub_cosim.scaleout_llm import EXTRA_COSTS, capacity
from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_service import make_wave, validate
from moe_ub_cosim.scaleout_topology import generate

EXAMPLE=ROOT/'examples/scaleout_route_replay'


class ReplayChecks(unittest.TestCase):
    def setUp(self):
        self.c=json.loads((EXAMPLE/'service.json').read_text())

    def test_exact_origin_context_and_bytes(self):
        replay=Replay(self.c,ROOT)
        self.assertEqual([replay.contexts(r) for r in range(4)],[[17],[34],[20],[46]])
        wave=make_wave(self.c,[0,1,2,3],0,0,1,replay)
        expected={}
        for local,original in enumerate([1,2]):
            for rank in range(4):
                source=json.loads((EXAMPLE/'routes'/f'request-{rank}.json').read_text())
                entry=1+rank%2
                token=wave['routes'][local]['tokens'][rank]
                self.assertEqual(token['experts'],source[entry][str(original)][0])
                self.assertEqual(token['source']['source_entry'],entry)
                self.assertEqual(token['source']['source_layer_id'],original)
                for expert in source[entry][str(original)][0]:
                    if rank!=expert:
                        for phase,src,dst in [('dispatch',rank,expert),('combine',expert,rank)]:
                            key=local,phase,src,dst
                            expected[key]=expected.get(key,0)+512
        self.assertEqual({(m['layer'],m['phase'],m['src'],m['dst']):m['bytes'] for m in wave['messages']},expected)

    def test_snapshot_is_relocatable(self):
        replay=Replay(self.c,ROOT)
        with tempfile.TemporaryDirectory() as directory:
            c,evidence=replay.snapshot(directory)
            saved=Replay(c,directory)
            self.assertEqual(saved.routes([0,1,2,3],1,1),replay.routes([0,1,2,3],1,1))
            self.assertFalse(evidence['eos_inferred'])
            self.assertEqual(evidence['source']['kind'],'synthetic_test')

    def test_user_provided_unknown_revision_survives_replay_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);manifest=json.loads((EXAMPLE/'manifest.json').read_text())
            for row in manifest['files']:row['file']['path']=str(ROOT/row['file']['path'])
            manifest['source'].update(kind='user_provided_model_routes',dataset_revision=None)
            manifest['source']['unknown_fields'].append('dataset_revision')
            path=root/'manifest.json';path.write_text(json.dumps(manifest))
            c=deepcopy(self.c);c['route_replay']['manifest']=dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            replay=Replay(c,ROOT);out=root/'snapshot';out.mkdir()
            saved,evidence=replay.snapshot(out);restored=Replay(saved,out)
            self.assertEqual(restored.routes([0,1,2,3],1,1),replay.routes([0,1,2,3],1,1))
            self.assertIsNone(evidence['source']['dataset_revision'])
            self.assertEqual(evidence['source']['kind'],'user_provided_model_routes')

    def test_missing_duplicate_and_unknown_request(self):
        for change in ('missing','slot','file','unknown'):
            c=deepcopy(self.c);rows=c['route_replay']['requests']
            if change=='missing':rows.pop()
            if change=='slot':rows[-1]['rank_id']=0
            if change=='file':rows[-1]['file_id']=rows[0]['file_id']
            if change=='unknown':rows[-1]['file_id']='missing'
            with self.subTest(change=change),self.assertRaises(ValueError):Replay(c,ROOT)

    def test_model_shape_range_and_budget(self):
        for change in ('name','hidden','experts','top_k','layers','eof','context','budget','full'):
            c=deepcopy(self.c)
            if change=='name':c['model']['name']='other/model'
            if change=='hidden':c['model']['hidden_size']=512
            if change=='experts':c['model']['experts']=8
            if change=='top_k':c['model']['top_k']=1
            if change=='layers':c['route_replay']['layer_ids']=[1,3]
            if change=='eof':c['workload']['decode_steps']=4
            if change=='context':c['workload']['context_tokens']=32
            if change=='budget':c['route_replay']['budget']['max_bytes']=1
            if change=='full':c['schema_version']='moe-scaleout-service/2'
            with self.subTest(change=change),self.assertRaises(ValueError):Replay(c,ROOT)
        replay=Replay(self.c,ROOT)
        for step,layer in [(-1,0),(2,0),(0,2)]:
            with self.assertRaises(ValueError):replay.routes([0,1,2,3],step,layer)

    def test_hash_check_covers_raw_and_manifest(self):
        c=deepcopy(self.c);c['route_replay']['manifest']['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'SHA-256'):Replay(c,ROOT)
        with tempfile.TemporaryDirectory() as directory:
            saved,_=Replay(self.c,ROOT).snapshot(directory)
            path=Path(directory)/'routes/source-0000.json'
            path.write_bytes(path.read_bytes()+b' ')
            with self.assertRaises(ValueError):Replay(saved,directory)

    def test_permutation_preserves_choices_and_rejects_extra_capacity(self):
        original=Replay(self.c,ROOT)
        c=json.loads((EXAMPLE/'service-permuted.json').read_text())
        changed=Replay(c,ROOT)
        self.assertEqual(changed.routes([0,1,2,3],0,0),original.routes([0,1,2,3],0,0))
        self.assertEqual(changed.owner([0,1,2,3],0,0),2)
        c['route_replay']['expert_to_group_rank'][0]=[0,0,2,3]
        with self.assertRaisesRegex(ValueError,'equal number'):Replay(c,ROOT)

    def test_replay_forbids_synthetic_hotspot(self):
        c=deepcopy(self.c);c['workload']['hotspot_fraction']=0.5
        t,_=generate(json.loads((EXAMPLE/'topology.json').read_text()))
        rdma=json.loads((EXAMPLE/'rdma.json').read_text())
        with self.assertRaisesRegex(ValueError,'synthetic hotspot'):validate(c,t,rdma)

    def test_full_gqa_uses_actual_context_for_attention_and_kv(self):
        with tempfile.TemporaryDirectory() as directory:
            c,_=Replay(self.c,ROOT).snapshot(directory)
            path=Path(directory)/'routes/manifest.json'
            manifest=json.loads(path.read_text())
            manifest['layers'][0].update(kind='moe',num_experts=4,top_k=2)
            for item in manifest['files']:
                raw_path=Path(directory)/item['file']['path']
                raw=json.loads(raw_path.read_text())
                for entry in raw:entry['0']=deepcopy(entry['1'])
                raw_path.write_text(json.dumps(raw))
                item['file']['sha256']=hashlib.sha256(raw_path.read_bytes()).hexdigest()
            path.write_text(json.dumps(manifest))
            c['route_replay']['manifest']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            c['route_replay']['layer_ids']=[0,1,2,3]
            c['schema_version']='moe-scaleout-service/2'
            c['model'].update(layers=4,llm=dict(attention_heads=4,kv_heads=2,head_dim=64,
                vocab_size=100,tied_embeddings=False,weight_bytes=2,device_bytes=1073741824))
            c['compute'].update({key+'_ns_per_token':3 for key in EXTRA_COSTS})
            replay=Replay(c,directory)
            wave=make_wave(c,[0,1,2,3],0,0,1,replay)
            for rank,context in enumerate([17,34,20,46]):
                task=next(n for n in wave['rank_nodes'][rank] if n['name']=='L0.attention')
                self.assertEqual(task['duration_ns'],1000+context*3)
                actual=capacity(c,4,[context]);upper=capacity(c,4)
                self.assertEqual(actual['kv_reserved_bytes_per_rank'],
                    ((context+2+15)//16)*16*actual['kv_bytes_per_token'])
                self.assertLess(actual['kv_reserved_bytes_per_rank'],upper['kv_reserved_bytes_per_rank'])


if __name__=='__main__':unittest.main()
