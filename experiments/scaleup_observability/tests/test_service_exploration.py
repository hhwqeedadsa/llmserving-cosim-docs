"""Service-only input isolation, topology selection and boundary semantics."""
from pathlib import Path
import tempfile
import unittest
import yaml

from experiments.scaleup_observability.common import read
from experiments.scaleup_observability.inputs import verified_case
from experiments.scaleup_observability.overview import check_service
from experiments.scaleup_observability.service_exploration import prepare, selected_ports
from experiments.scaleup_observability.service_analysis import serialization_audit


class ServiceExploration(unittest.TestCase):
    def test_input_changes_only_service(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'batch';prepare(root)
            cases=read(root/'batch.json')['cases'];self.assertEqual(len(cases),7)
            baseline=None
            for case in cases:
                _,folder=verified_case(root,case['case_id'])
                config=yaml.safe_load((folder/'scenario.yaml').read_text())
                self.assertEqual(config.pop('port_service')['intervals'],case['port_service'])
                files={p.name:p.read_bytes() for p in folder.iterdir() if p.name!='scenario.yaml'}
                if baseline is None:baseline=(config,files)
                self.assertEqual((config,files),baseline)

    def test_ports_follow_topology_not_input_order(self):
        topology=dict(nodes=[dict(node_id=i,kind=k) for i,k in [(0,'DEVICE'),(1,'DEVICE'),(10,'SWITCH'),(11,'SWITCH'),(20,'SWITCH')]],
            links=[dict(src=11,src_port=8,dst=20,dst_port=2,rate_bps=100),
                   dict(src=1,src_port=0,dst=11,dst_port=0,rate_bps=100),
                   dict(src=20,src_port=1,dst=10,dst_port=9,rate_bps=100),
                   dict(src=0,src_port=0,dst=10,dst_port=3,rate_bps=100)])
        self.assertEqual(selected_ports(topology),dict(l1_up=(10,9,100),l2_down=(20,1,100),access_down=(10,3,100)))

    def test_rate_at_packet_start_and_half_open_interval(self):
        interval=[dict(start_ns=10,end_ns=20,rate_bps=4_000_000_000)]
        rows=[dict(time_ns=t,packet_bytes=8,busy_until_ns=t+duration) for t,duration in [(9,8),(10,16),(19,16),(20,8)]]
        self.assertEqual(serialization_audit(rows,interval,8_000_000_000),2)
        rows[2]['busy_until_ns']=27
        with self.assertRaisesRegex(ValueError,'serialization'):serialization_audit(rows,interval,8_000_000_000)

    def test_default_overview_rejects_undeclared_service(self):
        service=dict(schema_version='moe-ub-port-service/1',execution_end_ns=100,intervals=[
            dict(node_id=8,port_id=4,start_ns=1,end_ns=20,rate_bps=100)])
        net=dict(port_service=service,attributes={'ns3::UbLdstApi::UsePacketSpray':'true'})
        with self.assertRaisesRegex(ValueError,'normal native spray'):check_service(net,None)
        check_service(net,service)
        with self.assertRaisesRegex(ValueError,'declared service'):check_service(net,dict(service,intervals=[]))


if __name__=='__main__':unittest.main()
