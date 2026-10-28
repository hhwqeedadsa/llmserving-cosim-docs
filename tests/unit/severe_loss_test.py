"""Closed periodic loss inputs and an explicit simulated-hardware summary."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.scaleout_rdma import validate_protocol,validate_probe
from moe_ub_cosim.scaleout_topology import generate
from moe_ub_cosim.run_summary import describe,write
from moe_ub_cosim.scaleout_data_loss import audit

ROOT=Path(__file__).resolve().parents[2]


class LossChecks(unittest.TestCase):
    def setUp(self):
        self.p=json.loads((ROOT/'examples/simai_severe_loss/half-burst.json').read_bytes())
        self.t,_=generate(json.loads((ROOT/'examples/simai_service/topology.json').read_bytes()))

    def test_valid_ratio_and_extended_explicit_capture(self):
        validate_protocol(self.p)
        validate_probe(self.t,self.p,[dict(id=0,src=0,dst=32,bytes=14336,plane=0,ready_ns=0)])
        self.p['data_loss']['numerator']=self.p['data_loss']['denominator']
        validate_protocol(self.p)

    def test_bad_loss_or_observation_inputs_rejected(self):
        for key,value in [('numerator',0),('numerator',3),('denominator',True),('offset',2),('end_ns',0),
                          ('nodes',[0,0]),('nodes',[True]),('kind','random'),('extra',1)]:
            p=deepcopy(self.p);p['data_loss'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):validate_protocol(p)
        p=deepcopy(self.p);p['drop_once']=dict(message_id=0,dst_gpu=1,sequence=0)
        with self.assertRaises(ValueError):validate_protocol(p)
        p=deepcopy(self.p);p['packet_capture']['max_events']=1000001
        with self.assertRaises(ValueError):validate_protocol(p)

    def test_switch_is_not_a_destination_gpu(self):
        self.p['data_loss']['nodes']=[64]
        self.p['limits']['max_gpus']=128
        with self.assertRaisesRegex(ValueError,'physical GPU'):
            validate_probe(self.t,self.p,[dict(id=0,src=0,dst=32,bytes=14336,plane=0,ready_ns=0)])

    def test_summary_distinguishes_physical_and_actual_batch(self):
        c=json.loads((ROOT/'examples/simai_severe_loss/service.json').read_bytes())
        c['service']['workload']['batch_per_rank']=6
        with tempfile.TemporaryDirectory() as folder:
            d=Path(folder)
            for name,value in [('input',c),('topology',self.t),('rdma',self.p)]:
                (d/(name+'.json')).write_text(json.dumps(value))
            v=write(d)
            self.assertEqual((v['physical_gpu_count'],v['server_count'],v['source_requests']),(64,8,2))
            self.assertEqual((v['ep_groups'][0]['ep'],v['ep_groups'][0]['experts_per_rank_per_layer']),(64,4))
            self.assertEqual((v['batch_cap_per_rank'],v['largest_actual_batch']),(6,1))
            self.assertEqual(v['outcome']['state'],'not_started_or_no_result')
            self.assertEqual(v,describe(d));self.assertTrue((d/'scenario.md').is_file())

    def test_loss_replay_uses_arrivals_not_message_identity(self):
        c=self.p['data_loss'];c=deepcopy(c);c.update(start_ns=10,end_ns=20)
        columns=['time_ns','node','ip_protocol','event','message_id']
        r=dict(gpu_count=2,finish_ns=30,fault_drops=2,data_loss=dict(configuration=c,
            eligible_destination_data_packets=6,window_packets=4,dropped_packets=2),
            packet_capture=dict(complete=True,omitted_events=0,selection=dict(start_ns=0,end_ns=31,nodes=[]),
                columns=columns,events=[[9,0,17,1,8],[10,0,17,2,8],[11,1,17,1,99],
                    [12,0,252,1,8],[13,0,17,2,4],[14,1,17,1,99],[20,0,17,1,2]]))
        self.assertEqual(audit(r)['window_loss_fraction'],.5)
        self.assertEqual(audit(r)['whole_observed_loss_fraction'],1/3)
        for change in ('counter','pattern','capture'):
            v=deepcopy(r)
            if change=='counter':v['data_loss']['dropped_packets']=1
            if change=='pattern':v['packet_capture']['events'][1][3]=1
            if change=='capture':v['packet_capture']['omitted_events']=1
            with self.subTest(change=change),self.assertRaises(ValueError):audit(v)


if __name__=='__main__':unittest.main()
