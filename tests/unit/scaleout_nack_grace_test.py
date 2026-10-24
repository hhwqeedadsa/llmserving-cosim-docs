"""Strict public input bounds for actual-feedback reordering tolerance."""
from copy import deepcopy
import unittest
from scaleout_selective_repeat_test import SelectiveInputTests
from moe_ub_cosim.scaleout_streams import validate


class NackGraceInputTests(unittest.TestCase):
    def setUp(self):
        base=SelectiveInputTests();base.setUp()
        self.topology,self.protocol,self.config=base.topology,base.protocol,base.config
        self.config['transport_recovery']['reordering']=dict(mode='nack_grace/1',wait_ns=16000,max_events=10000)

    def test_explicit_and_upper_bound(self):
        for value in (1,16000,80000):
            self.config['transport_recovery']['reordering']['wait_ns']=value
            validate(self.topology,self.protocol,self.config)

    def test_strict_reordering(self):
        mutations=[None,[],True,{},dict(mode='rack',wait_ns=10,max_events=2)]
        base=self.config['transport_recovery']['reordering']
        for k in base:
            x=deepcopy(base);del x[k];mutations.append(x)
        for k,values in [('wait_ns',[0,-1,True,1.5,80001]),('max_events',[0,True,100001,1.5]),
                         ('mode',[None,1,True])]:
            for v in values:mutations.append(dict(base,**{k:v}))
        mutations.append(dict(base,truth=True))
        for r in mutations:
            with self.subTest(r=r):
                c=deepcopy(self.config);c['transport_recovery']['reordering']=r
                with self.assertRaises(ValueError):validate(self.topology,self.protocol,c)

    def test_gbn_and_initial_bool(self):
        for key,value in [('mode','go_back_n/1'),('initial_rto_ns',True)]:
            c=deepcopy(self.config);c['transport_recovery'][key]=value
            with self.assertRaises(ValueError):validate(self.topology,self.protocol,c)

if __name__=='__main__':unittest.main()
