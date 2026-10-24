"""Native integer reserves, overflow boundaries and no-start input rejection."""
from contextlib import redirect_stdout,redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from moe_ub_cosim.scaleout_fabric_buffers import buffer_report,require_buffers,preflight
from moe_ub_cosim.scaleout_rdma import validate_probe
from moe_ub_cosim.scaleout_topology import generate
from moe_ub_cosim.transaction_session import NativeTransactionSession
from moe_ub_cosim.cli import main

ROOT=Path(__file__).resolve().parents[2]


class FabricBufferTests(unittest.TestCase):
    def setUp(self):
        self.protocol=json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_text())
        self.topology,_=generate(json.loads((ROOT/'examples/scaleout_rdma/small-clos-100g.json').read_text()))

    def test_mixed_rate_integer_rounding_and_strict_total_boundary(self):
        t=dict(schema_version='moe-scaleout-physical-topology/1',nodes=[dict(id=0,kind='gpu'),dict(id=1,kind='gpu'),dict(id=2,kind='tor')],links=[
            dict(src=0,dst=2,src_port=1,dst_port=1,rate_bps=100001234567,delay_ns=1337),
            dict(src=1,dst=2,src_port=1,dst_port=2,rate_bps=40000000000,delay_ns=201)])
        p=dict(switch_buffer_bytes=61343,ecn_by_rate={'100001234567':{},'40000000000':{}})
        r=buffer_report(t,p)['switches'][0]
        self.assertEqual([x['headroom_bytes'] for x in r['ports']],[50136,3015])
        self.assertEqual((r['required_bytes'],r['reserve_bytes']),(61343,8192));self.assertFalse(r['passed'])
        p['switch_buffer_bytes']+=1;r=buffer_report(t,p)['switches'][0]
        self.assertTrue(r['passed']);self.assertEqual((r['shared_capacity_bytes'],r['initial_pfc_threshold_bytes']),(1,0))

    def test_long_access_link_exceeds_fixed_switch_capacity(self):
        next(l for l in self.topology['links'] if l['src']==0)['delay_ns']=250000
        r=buffer_report(self.topology,self.protocol);bad=[x for x in r['switches'] if not x['passed']]
        self.assertEqual([(x['node'],x['required_bytes'],x['buffer_bytes']) for x in bad],[(8,9503884,8388608)])
        with self.assertRaisesRegex(ValueError,'switch 8.*9503884B.*8388608B.*250000ns'):require_buffers(self.topology,self.protocol)
        with self.assertRaisesRegex(ValueError,'headroom/reserve'):
            validate_probe(self.topology,self.protocol,[dict(id=0,src=0,dst=4,bytes=8,plane=0,ready_ns=0)])

    def test_native_integer_overflow_rejected_even_when_modulo_would_fit(self):
        # One port has exactly 2**32 bytes before multiplication by three;
        # native uint32 conversion would erase it, which must not be accepted.
        p=deepcopy(self.protocol);p['ecn_by_rate']['34359738368000000']={}
        t=deepcopy(self.topology);l=t['links'][0];l.update(rate_bps=34359738368000000,delay_ns=1000)
        self.assertFalse(buffer_report(t,p)['switches'][0]['arithmetic_valid'])
        l.update(rate_bps=2**64-1,delay_ns=2**63-1);p['ecn_by_rate'][str(2**64-1)]={}
        self.assertFalse(buffer_report(t,p)['switches'][0]['arithmetic_valid'])
        t=deepcopy(self.topology)
        # Each entry fits uint32, the switch total does not.
        for l in t['links']:l['delay_ns']=100000000
        report=buffer_report(t,self.protocol)
        self.assertTrue(any(not x['arithmetic_valid'] and all(p['arithmetic_valid'] for p in x['ports']) for x in report['switches']))

    def test_reject_invalid_physical_type_port_and_endpoint(self):
        for key,value in [('delay_ns',True),('delay_ns',-1),('rate_bps',True),('rate_bps',0),('src',True),('dst',999),('src_port',0),('dst_port',1025)]:
            t=deepcopy(self.topology);t['links'][0][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):buffer_report(t,self.protocol)
        for kind in ('duplicate','gap'):
            t=deepcopy(self.topology)
            if kind=='duplicate':t['links'].append(deepcopy(t['links'][0]))
            else:t['links'][0]['src_port']=2
            with self.subTest(kind=kind),self.assertRaises(ValueError):buffer_report(t,self.protocol)

    def test_preflight_cli_reports_invalid_capacity_without_backend(self):
        with tempfile.TemporaryDirectory() as temporary:
            d=Path(temporary);(d/'topology.json').write_text(json.dumps(self.topology));protocol=d/'protocol.json';protocol.write_text(json.dumps(self.protocol))
            argv=['scaleout','rdma-preflight','--topology',str(d),'--protocol',str(protocol),'--output',str(d/'good.json')]
            with patch('subprocess.Popen') as child,redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                self.assertEqual(main(argv),0);child.assert_not_called()
            self.assertFalse(json.loads((d/'good.json').read_text())['network_executed'])
            next(l for l in self.topology['links'] if l['src']==0)['delay_ns']=250000
            (d/'topology.json').write_text(json.dumps(self.topology));argv[-1]=str(d/'bad.json')
            with patch('subprocess.Popen') as child,redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                self.assertEqual(main(argv),1);child.assert_not_called()
            self.assertFalse(json.loads((d/'bad.json').read_text())['passed'])
            before=(d/'bad.json').read_bytes()
            with self.assertRaisesRegex(ValueError,'already exists'):preflight(d,protocol,d/'bad.json')
            self.assertEqual(before,(d/'bad.json').read_bytes())

    def test_optional_transaction_dependency_check(self):
        work=dict(schema_version='moe-rdma-transactions/1',connections=[dict(id=i,src=i,dst=4,plane=0,request_port=24000+i*2,response_port=24001+i*2,source_nic=1,destination_nic=1,queue_depth=2) for i in (0,1)],windows=[dict(owner=4,key=9,address=8192,allow_atomic=False,hex='00'*32)],operations=[dict(id=i,connection=i,verb='put',key=9,address=8192,hex=('%02x'%(17+i*17))*8,after=[]) for i in (0,1)])
        with tempfile.TemporaryDirectory() as temporary:
            d=Path(temporary)
            for name,value in [('topology.json',self.topology),('protocol.json',self.protocol),('work.json',work)]: (d/name).write_text(json.dumps(value))
            result=preflight(d,d/'protocol.json',d/'bad.json',d/'work.json')
            self.assertFalse(result['passed']);self.assertIn('remote effect order',result['transaction_check']['error'])
            work['operations'][1]['after']=[dict(operation=0,event='applied')];(d/'work.json').write_text(json.dumps(work))
            self.assertTrue(preflight(d,d/'protocol.json',d/'good.json',d/'work.json')['passed'])

    def test_online_invalid_fabric_rejected_before_process_or_initial_file(self):
        config=json.loads((ROOT/'examples/scaleout_rdma_transactions/bounded-buffers.json').read_text());config['operations']=[]
        next(l for l in self.topology['links'] if l['src']==0)['delay_ns']=250000
        with tempfile.TemporaryDirectory() as temporary:
            d=Path(temporary);(d/'topology.json').write_text(json.dumps(self.topology));(d/'protocol.json').write_text(json.dumps(self.protocol))
            out=d/'native';out.mkdir()
            with patch('subprocess.Popen') as child,self.assertRaisesRegex(ValueError,'switch 8.*headroom/reserve'):
                NativeTransactionSession(d/'topology.json',d/'protocol.json',config,Path('/nonexistent/backend'),out)
            child.assert_not_called();self.assertEqual(list(out.iterdir()),[])


if __name__=='__main__':unittest.main()
