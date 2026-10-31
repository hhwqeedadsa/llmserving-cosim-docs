from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from moe_ub_cosim.deepep_indexed import execute
from moe_ub_cosim.legacy_native import lower, minimum_reference_events, validate_record_capacity, minimum_posting_span, run
from moe_ub_cosim.scaleout_topology import generate

ROOT=Path(__file__).resolve().parents[2]


class LegacyLowering(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        config=json.loads((ROOT/'examples/deepep_indexed/input.json').read_bytes())
        config['model']=dict(experts=4,top_k=1,hidden_size=128,dispatch_format='bf16')
        config['execution']['ranks']=2;config['execution']['max_tokens_per_rank']=4
        config['calls']=[dict(name='sparse',tokens=[[[2],[0],[2]],[]])]
        path=self.root/'input.json';path.write_text(json.dumps(config));execute(path,self.root/'plan')
        topo=json.loads((ROOT/'examples/scaleout_clos128/clos-128.json').read_bytes());topo['intra_host']['enabled']=False
        self.topology=generate(topo)[0]
        self.protocol=json.loads((ROOT/'examples/scaleout_clos128/rdma.json').read_bytes())
        # The raw Clos example uses 9KB packets and a larger host budget. Match
        # the bounded legacy entrypoint, independently of its prepare helper.
        self.protocol['payload_bytes']=1024
        self.protocol['limits'].update(wall_seconds=120,rss_bytes=2*2**30,
            address_bytes=4*2**30,result_json_bytes=256*2**20)
        self.binding=dict(schema_version='moe-legacy-native-binding/1',rank_to_gpu=[0,64],queue_depth=32,
            publication='legacy_sync_batch4/1',memory_model='single_span_zero_cost_visibility_expert_copy/1')

    def test_sparse_tokens_use_expert_slot_indices(self):
        work=lower(self.root/'plan',self.binding,self.topology,self.protocol)
        dispatch=[o for o in work['operations'] if o[2]==0 and o[3]=='data' and o[6]==2]
        self.assertEqual([(o[7],o[11]) for o in dispatch],[(0,0),(2,1)])
        self.assertEqual(work['limits']['streams'],8)
        self.assertEqual(len(work['operations']),22)
        self.assertEqual(sum(o[3]=='count' and o[9]==-1 for o in work['operations']),6)
        self.assertEqual(sum(o[3]=='flag' for o in work['operations']),8)
        self.assertEqual(sum(o[4]==o[5] for o in work['operations']),10)

    def test_binding_rejections(self):
        for key,value in [('rank_to_gpu',[0,0]),('rank_to_gpu',[0,500]),('rank_to_gpu',[0,[]]),
                          ('rank_to_gpu',[0,True]),('rank_to_gpu',None),('queue_depth',3),
                          ('publication','modern_direct'),('memory_model','gpu_calibrated')]:
            binding=deepcopy(self.binding);binding[key]=value
            with self.assertRaises(ValueError):lower(self.root/'plan',binding,self.topology,self.protocol)

    def test_physical_topology_and_gpu_execution_are_distinct(self):
        topology=deepcopy(self.topology);topology['nodes']=[n for n in topology['nodes'] if n['id']!=127]
        with self.assertRaisesRegex(ValueError,'128/256/512'):lower(self.root/'plan',self.binding,topology,self.protocol)
        topology=deepcopy(self.topology);topology['intra_host']['enabled']=True
        with self.assertRaisesRegex(ValueError,'bypass'):lower(self.root/'plan',self.binding,topology,self.protocol)

    def test_record_admission_counts_frame_headers_replies_and_packet_boundaries(self):
        work=lower(self.root/'plan',self.binding,self.topology,self.protocol)
        # Independent byte-level fragmentation oracle, including reverse replies.
        for mtu in (1,35,36,256,1024):
            frames=[]
            for op in work['operations']:
                if op[4]==op[5]:continue
                frames.append(bytes(32+op[8]))
                if op[3]!='data':frames.append(bytes(36))
            expected=sum(len(list(range(0,len(frame),mtu))) for frame in frames)
            self.assertEqual(minimum_reference_events(work,mtu),expected)
        minimum=minimum_reference_events(work,self.protocol['payload_bytes'])
        with self.assertRaisesRegex(ValueError,'mandatory source sends'):
            validate_record_capacity(work,self.protocol,dict(dcqcn_reference=dict(max_events=minimum-1)))
        self.assertEqual(validate_record_capacity(work,self.protocol,
                         dict(dcqcn_reference=dict(max_events=minimum))),minimum)

    def test_publication_tail_must_fit_before_any_ack_is_possible(self):
        work=lower(self.root/'plan',self.binding,self.topology,self.protocol)
        self.assertEqual(minimum_posting_span(work),3)  # Two remote puts, then their count.
        binding=deepcopy(self.binding);binding['queue_depth']=2
        with self.assertRaisesRegex(ValueError,'publication span=3'):
            lower(self.root/'plan',binding,self.topology,self.protocol)
        binding['queue_depth']=4
        self.assertEqual(minimum_posting_span(lower(self.root/'plan',binding,self.topology,self.protocol)),3)
        local_work=deepcopy(work)
        for op in local_work['operations']:op[5]=op[4]
        self.assertEqual(minimum_posting_span(local_work),1)  # Local copies never wait for a doorbell.
        with self.assertRaisesRegex(ValueError,'unpublished posting tail'):
            minimum_posting_span(dict(ranks=2,experts=4,operations=[
                ['test',0,0,'data',0,1,2,0,272,0,[],0]]))

    def runtime_paths(self):
        topology=self.root/'topology';topology.mkdir()
        (topology/'topology.json').write_text(json.dumps(self.topology))
        binding=self.root/'binding.json';binding.write_text(json.dumps(self.binding))
        protocol=self.root/'protocol.json';protocol.write_text(json.dumps(self.protocol))
        source=json.loads((ROOT/'examples/scaleout_rdma_transactions/selective-chain.json').read_bytes())
        transport=self.root/'transport.json'
        transport.write_text(json.dumps({key:source[key] for key in ('transport_recovery','dcqcn_reference')}))
        backend=self.root/'backend';backend.write_text('CPU test sentinel; must not be executed');backend.chmod(0o700)
        return self.root/'plan',binding,topology,protocol,transport,backend

    def test_validate_only_has_no_native_dispatch_or_completion_result(self):
        paths=self.runtime_paths();output=self.root/'validation'
        with patch('moe_ub_cosim.legacy_native.monitored',side_effect=AssertionError('unexpected native launch')) as launch:
            result=run(*paths,output,validate_only=True)
        launch.assert_not_called()
        self.assertTrue(result['passed'] and result['validation_only'])
        self.assertFalse(result['native_admission_executed'] or result['native_network_executed'])
        self.assertEqual(result['per_call_tokens'],[3])
        self.assertEqual(result['minimum_posting_span'],3)
        self.assertEqual(result['endpoints'],128)
        self.assertFalse((output/'result.json').exists() or (output/'execution-monitor').exists())
        receipt=json.loads((output/'inputs.json').read_bytes())
        self.assertNotIn('command',receipt)
        self.assertIn('planned_native_command',receipt)

    def test_normal_mode_retains_native_dispatch_after_identical_checks(self):
        paths=self.runtime_paths();output=self.root/'native'
        with patch('moe_ub_cosim.legacy_native.monitored',side_effect=RuntimeError('CPU dispatch sentinel')) as launch:
            with self.assertRaisesRegex(RuntimeError,'CPU dispatch sentinel'):run(*paths,output)
        launch.assert_called_once()
        receipt=json.loads((output/'inputs.json').read_bytes())
        self.assertIn('command',receipt)
        self.assertNotIn('planned_native_command',receipt)
        self.assertFalse((output/'validation.json').exists())

    def test_validate_only_does_not_bypass_capacity_or_plan_checks(self):
        paths=self.runtime_paths()
        original=json.loads(paths[4].read_bytes())
        inadequate=deepcopy(original);inadequate['dcqcn_reference']['max_events']=1
        paths[4].write_text(json.dumps(inadequate))
        with patch('moe_ub_cosim.legacy_native.monitored') as launch:
            with self.assertRaisesRegex(ValueError,'mandatory source sends'):
                run(*paths,self.root/'capacity-rejected',validate_only=True)
        launch.assert_not_called()
        self.assertFalse((self.root/'capacity-rejected'/'validation.json').exists())
        paths[4].write_text(json.dumps(original))
        with (paths[0]/'operations.jsonl').open('a') as stream:stream.write('[]\n')
        with patch('moe_ub_cosim.legacy_native.monitored') as launch:
            with self.assertRaises(ValueError):
                run(*paths,self.root/'plan-rejected',validate_only=True)
        launch.assert_not_called()
        self.assertFalse((self.root/'plan-rejected'/'validation.json').exists())


if __name__=='__main__':unittest.main()
