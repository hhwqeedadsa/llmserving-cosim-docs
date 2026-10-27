"""Exact storage choices must not relax network and process budget checks."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.scaleout_rdma import validate_protocol, validate_probe
from moe_ub_cosim.scaleout_topology import generate

ROOT=Path(__file__).resolve().parents[2]


class CounterStorageTest(unittest.TestCase):
    def setUp(self):
        self.config=json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_text())
        self.topology,_=generate(json.loads((ROOT/'examples/scaleout_rdma/small-clos-100g.json').read_text()))
        self.batch=[dict(id=0,src=0,dst=4,bytes=1000,plane=0,ready_ns=0)]

    def test_default_and_both_explicit_choices(self):
        validate_probe(self.topology,self.config,self.batch)
        for mode in ('legacy_dense','port_dense'):
            validate_probe(self.topology,dict(self.config,switch_counter_storage=mode),self.batch)

    def test_invalid_modes_rejected(self):
        for mode in (None,False,True,0,1,[],{},'sparse','PORT_DENSE',''):
            with self.subTest(mode=mode),self.assertRaisesRegex(ValueError,'switch_counter_storage'):
                validate_protocol(dict(self.config,switch_counter_storage=mode))

    def test_legacy_memory_gate_is_preserved(self):
        # Tighten an explicit v2 declaration to distinguish the two exact sizes.
        from moe_ub_cosim.scaleout_limits import DEFAULTS
        config=deepcopy(self.config);config.update(schema_version='moe-scaleout-rdma/2',limits=dict(DEFAULTS))
        config['limits'].update(rss_bytes=512*2**20,address_bytes=2**30)
        for mode in (None,'legacy_dense'):
            candidate=deepcopy(config)
            if mode:candidate['switch_counter_storage']=mode
            with self.assertRaisesRegex(ValueError,'switch counters'):
                validate_probe(self.topology,candidate,self.batch)
        validate_probe(self.topology,dict(config,switch_counter_storage='port_dense'),self.batch)

    def test_port_mode_keeps_other_graph_checks(self):
        config=dict(self.config,switch_counter_storage='port_dense')
        broken=deepcopy(self.batch);broken[0]['dst']=1279
        with self.assertRaises(ValueError):validate_probe(self.topology,config,broken)
        config['ecn_by_rate']={}
        with self.assertRaises(ValueError):validate_probe(self.topology,config,self.batch)


if __name__=='__main__':unittest.main()
