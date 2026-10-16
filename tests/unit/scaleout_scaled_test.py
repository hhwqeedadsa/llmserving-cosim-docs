"""Scaled service identity, architecture, capacity and topology boundaries."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from tempfile import TemporaryDirectory

from moe_ub_cosim.scaleout_limits import DEFAULTS, limits
from moe_ub_cosim.scaleout_llm import capacity
from moe_ub_cosim.scaleout_service import make_wave, validate
from moe_ub_cosim.scaleout_topology import generate
from moe_ub_cosim.scaleout_verify import node_events, event_time

ROOT=Path(__file__).resolve().parents[2]
EXAMPLES=ROOT/'examples/scaleout_clos128'


class ScaledTest(unittest.TestCase):
    def setUp(self):
        self.c=json.loads((EXAMPLES/'qwen3-128.json').read_text())
        self.r=json.loads((EXAMPLES/'rdma.json').read_text())
        self.tc=json.loads((EXAMPLES/'clos-128.json').read_text())

    def test_limits_are_explicit_and_legacy_defaults_stay_small(self):
        self.assertEqual(limits({'schema_version':'moe-scaleout-rdma/1'}),DEFAULTS)
        bad=deepcopy(self.r);bad['limits']['max_gpus']=True
        with self.assertRaises(ValueError):limits(bad)
        bad=deepcopy(self.r);bad['limits']['address_bytes']=1024**3
        with self.assertRaises(ValueError):limits(bad)

    def test_architecture_count_and_replication_match_reference(self):
        declarations=[capacity(self.c,n) for n in (16,32,128)]
        self.assertTrue(all(x['total_parameters']==30532122624 for x in declarations))
        self.assertTrue(all(x['kv_bytes_per_token']==98304 for x in declarations))
        self.assertGreater(declarations[0]['weight_bytes_per_rank'],declarations[-1]['weight_bytes_per_rank'])
        self.c['model']['llm']['device_bytes']=1024**3
        with self.assertRaisesRegex(ValueError,'device memory'):capacity(self.c,128)

    def test_large_message_tags_are_not_truncated_to_wire_ports(self):
        self.c['model']['layers']=2
        first=make_wave(self.c,list(range(128)),0,0,1)
        second=make_wave(self.c,list(range(128)),0,1,1)
        ids1={m['id'] for m in first['messages']};ids2={m['id'] for m in second['messages']}
        self.assertFalse(ids1 & ids2)
        self.assertGreater(max(ids2),65535)
        self.assertTrue(all(m['id']==m['tag'] for m in second['messages']))

    def test_full_stages_and_context_cost_are_in_graph(self):
        self.c['model']['layers']=1;self.c['parallel']['groups']=[list(range(16))]
        wave=make_wave(self.c,list(range(16)),0,0,1)
        later=make_wave(self.c,list(range(16)),0,1,1)
        tasks={n['name']:n for n in wave['rank_nodes'][0]}
        for name in ('embedding','L0.attention_norm','L0.attention_residual','L0.ffn_norm',
                     'L0.ffn_residual','final_norm','lm_head','sampling'):
            self.assertIn(name,tasks)
        later_attention=next(n for n in later['rank_nodes'][0] if n['name']=='L0.attention')
        self.assertEqual(later_attention['duration_ns']-tasks['L0.attention']['duration_ns'],
                         self.c['compute']['attention_context_ns_per_token'])
        self.assertEqual(tasks['sampling']['deps'],[tasks['lm_head']['id']])

    def test_local_fabric_is_separate_and_does_not_mutate_config(self):
        topology,audit=generate(self.tc)
        self.assertEqual(audit['counts']['gpus'],128)
        self.assertEqual(audit['counts']['core'],4)
        self.assertEqual(len([n for n in topology['nodes'] if n['kind']=='gpu']),128)
        topology['intra_host']['enabled']=False
        self.assertTrue(self.tc['intra_host']['enabled'])
        with self.assertRaisesRegex(ValueError,'GPU budget'):
            legacy=deepcopy(self.r);legacy['schema_version']='moe-scaleout-rdma/1';del legacy['limits']
            validate(self.c,topology,legacy)

    def test_node_evidence_survives_diagnostic_rotation_without_double_counting(self):
        first='issue,sys->id=0, tick=0, node->id=1, node->name=embedding, node->type=5, graph_id=g0.w0\n'
        last='callback,sys->id=0, tick=50, node->id=1, node->name=embedding, node->type=5, graph_id=g0.w0\n'
        with TemporaryDirectory() as temp:
            root=Path(temp);(root/'log').mkdir()
            (root/'log/log.log').write_text(last)
            with self.assertRaisesRegex(ValueError,'missing ASTRA node evidence'):
                event_time(node_events(root),'g0.w0',0,1,'issue')
            (root/'log/astra_nodes.log').write_text(first+last)
            events=node_events(root)
            self.assertEqual(len(events),2)
            self.assertEqual(event_time(events,'g0.w0',0,1,'issue'),0)
            # Do not conceal incomplete primary evidence with legacy logs.
            (root/'log/log.log').write_text(first+last)
            (root/'log/astra_nodes.log').write_text(last)
            with self.assertRaisesRegex(ValueError,'missing ASTRA node evidence'):
                event_time(node_events(root),'g0.w0',0,1,'issue')


if __name__=='__main__':unittest.main()
