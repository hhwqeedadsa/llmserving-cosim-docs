"""Fail before submitting invalid work; do not alter valid network identities."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from moe_ub_cosim.scaleout_messages import MessagePreflight
from moe_ub_cosim.scaleout_rdma import validate_probe
from moe_ub_cosim.scaleout_service import Frontend
from moe_ub_cosim.scaleout_topology import generate
from moe_ub_cosim.simai_service import Frontend as SimaiFrontend, validate as validate_simai

ROOT=Path(__file__).resolve().parents[2]


class MessageChecks(unittest.TestCase):
    def setUp(self):
        folder=ROOT/'examples/scaleout_rdma'
        self.topology,_=generate(json.loads((folder/'small-clos-100g.json').read_text()))
        self.protocol=json.loads((folder/'rdma.json').read_text())

    def message(self, identity=0, **changes):
        return dict(dict(id=identity,src=0,dst=4,bytes=1024,plane=0,ready_ns=0),**changes)

    def test_probe_rejects_collision_and_large_id_alone_is_valid(self):
        validate_probe(self.topology,self.protocol,[self.message(50000)])
        with self.assertRaisesRegex(ValueError,'endpoint/port identity collision'):
            validate_probe(self.topology,self.protocol,[self.message(0),self.message(50000)])

    def test_history_is_retained_across_waves_and_endpoints_separate_ports(self):
        checker=MessagePreflight(self.topology,self.protocol)
        first=[self.message()];checker.accept(first)
        checker.accept([self.message(50000,dst=5)])
        checker.accept([self.message(100000,src=1)])
        with self.assertRaisesRegex(ValueError,'endpoint/port identity collision'):
            checker.accept([self.message(150000)])
        with self.assertRaisesRegex(ValueError,'duplicate message id'):
            checker.accept(first)
        self.assertEqual(first,[self.message()])

    def test_plane_identity_is_separate(self):
        config=json.loads((ROOT/'examples/scaleout_multirail_native/topology.json').read_text())
        config['fabric']['planes']=2
        config['nic']['total_rate_bps']=400000000000
        topology,_=generate(config)
        checker=MessagePreflight(topology,self.protocol)
        checker.accept([self.message(0,src=0,dst=3,plane=0),
                        self.message(50000,src=0,dst=3,plane=1)])

    def test_failed_batch_does_not_reserve_any_identity(self):
        protocol=deepcopy(self.protocol)
        protocol['admission']=dict(max_active_bytes_per_source=1024)
        checker=MessagePreflight(self.topology,protocol)
        with self.assertRaisesRegex(ValueError,'max_active_bytes_per_source'):
            checker.accept([self.message(7),self.message(8,bytes=1025)])
        checker.accept([self.message(7),self.message(8,bytes=1024)])
        with self.assertRaisesRegex(ValueError,'invalid message bytes'):
            checker.accept([self.message(9,bytes=True)])

    def test_destination_share_and_local_bypass(self):
        protocol=deepcopy(self.protocol)
        protocol['admission']=dict(per_destination=dict(max_bytes=1024))
        checker=MessagePreflight(self.topology,protocol)
        with self.assertRaisesRegex(ValueError,'per_destination.max_bytes'):
            checker.accept([self.message(bytes=1025)])
        topology=deepcopy(self.topology);topology['intra_host']=dict(enabled=True)
        protocol['admission']['max_active_bytes_per_source']=1
        local=MessagePreflight(topology,protocol)
        local.accept([self.message(0,dst=1,bytes=4096),self.message(50000,dst=1,bytes=4096)])

    def test_cumulative_message_budget_and_empty_wave(self):
        from moe_ub_cosim.scaleout_limits import DEFAULTS
        protocol=deepcopy(self.protocol);protocol['schema_version']='moe-scaleout-rdma/2'
        protocol['limits']=dict(DEFAULTS,max_messages=1)
        checker=MessagePreflight(self.topology,protocol)
        checker.accept_wave(dict(messages=[]),0)
        checker.accept([self.message()])
        with self.assertRaisesRegex(ValueError,'cumulative native message budget'):
            checker.accept([self.message(1)])

    def test_topology_only_probe_does_not_apply_legacy_port_mapping(self):
        # Persistent streams and RC validate their own explicit wire identities.
        validate_probe(self.topology,self.protocol,[self.message(0),self.message(50000)],
                       match_faults=False,legacy_messages=False)

    def test_llm_frontend_rejects_actual_wave_before_export(self):
        config=json.loads((ROOT/'examples/scaleout_rdma/service.json').read_text())
        protocol=deepcopy(self.protocol);protocol['admission']=dict(max_active_bytes_per_source=1)
        # Use the actual LLMServingSim scheduler and request lifecycle. A wave
        # that exceeds the cap must never reach export or a native COMMIT.
        frontend=Frontend(config,8,1,message_preflight=MessagePreflight(self.topology,protocol))
        event=dict(now_ns=0,completed=[],available_rank_ids=list(range(8)),decision_id=0)
        with tempfile.TemporaryDirectory() as folder,patch('moe_ub_cosim.scaleout_service.export_wave') as export:
            with self.assertRaisesRegex(ValueError,'max_active_bytes_per_source'):
                frontend.decide(event,Path(folder),None)
            export.assert_not_called()

    def test_simai_decision_checks_actual_sparse_wave_before_export(self):
        folder=ROOT/'examples/simai_service'
        config=json.loads((folder/'service.json').read_text())
        protocol=json.loads((folder/'rdma.json').read_text())
        protocol['admission']=dict(policy='fifo',max_active_per_source=1,decision_ns=0,max_active_bytes_per_source=1)
        topology,_=generate(json.loads((folder/'topology.json').read_text()))
        _,planes,replay=validate_simai(config,topology,protocol,folder)
        # Isolate the submission guard; this is not a SimAI runtime validation.
        frontend=SimaiFrontend.__new__(SimaiFrontend)
        frontend.config=config;frontend.service=config['service'];frontend.replay=replay;frontend.planes=planes
        frontend.origin_seconds=1.0;frontend.groups=config['service']['parallel']['groups']
        frontend.steps=[0];frontend.active={};frontend.calls=0;frontend.history=[]
        frontend.message_preflight=MessagePreflight(topology,protocol)
        frontend.schedulers={};frontend.request_identity={}
        for identity,record in replay.records.items():
            q=object();frontend.request_identity[id(q)]=identity
            batch=SimpleNamespace(num_prefill_tokens=0,num_tokens=[1],size=1,requests=[q],on_schedule=lambda now:None)
            frontend.schedulers[record['rank_id']]=SimpleNamespace(on_schedule=lambda batch=batch:[batch])
        event=dict(now_ns=0,completed=[],available_rank_ids=list(range(64)),decision_id=0)
        with tempfile.TemporaryDirectory() as output,patch('moe_ub_cosim.simai_service.export_wave') as export:
            with self.assertRaisesRegex(ValueError,'max_active_bytes_per_source'):
                frontend.decide(event,output,None)
            export.assert_not_called()


if __name__=='__main__':unittest.main()
