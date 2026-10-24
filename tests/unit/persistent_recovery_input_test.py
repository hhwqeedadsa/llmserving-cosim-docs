"""Strict opt-in and fault identity validation for cumulative persistent streams."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from moe_ub_cosim.scaleout_streams import validate
from moe_ub_cosim.scaleout_transactions import validate as transactions
ROOT=Path(__file__).resolve().parents[2]
class RecoveryInput(unittest.TestCase):
 def setUp(self):
  self.t=dict(schema_version='moe-scaleout-physical-topology/1',nodes=[dict(id=i,kind='gpu') for i in range(8)]+[dict(id=8,kind='tor',plane=0)],links=[dict(src=i,dst=8,src_port=1,dst_port=i+1,rate_bps=100000000000) for i in range(8)])
  self.p=json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_text())
  self.w=json.loads((ROOT/'examples/scaleout_rdma_streams/work.json').read_text())
  self.r=dict(mode='go_back_n/1',initial_rto_ns=80000,max_rto_ns=640000,max_retries=3,max_events=10000)
  self.w['transport_recovery']=self.r
 def test_cumulative_offsets_need_not_be_mtu_aligned(self):
  self.p['faults']=[dict(node=4,port=0,message_id=0,kind='data',sequence=999,count=1,start_ns=0,end_ns=5000000)]
  validate(self.t,self.p,self.w)
 def test_explicit_opt_in(self):
  self.p['faults']=[dict(node=4,port=0,message_id=0,kind='ack',sequence=999,count=1,start_ns=0,end_ns=5000000)]
  del self.w['transport_recovery']
  with self.assertRaises(ValueError):validate(self.t,self.p,self.w)
 def test_recovery_bounds(self):
  for k,v in [('initial_rto_ns',True),('initial_rto_ns',0),('max_rto_ns',79999),('max_retries',17),('max_retries',False),('max_events',100001),('mode','go_back_n'),('history',{}),('unknown',1)]:
   with self.subTest(k=k,v=v):
    w=deepcopy(self.w);w['transport_recovery'][k]=v
    with self.assertRaises(ValueError):validate(self.t,self.p,w)
 def test_unknown_fault_stream(self):
  self.p['faults']=[dict(node=4,port=0,message_id=99,kind='data',sequence=0,count=1,start_ns=0,end_ns=5000000)]
  with self.assertRaises(ValueError):validate(self.t,self.p,self.w)
 def test_karn_bounds(self):
  for r in [dict(kind='karn',min_rto_ns=80001,granularity_ns=1),dict(kind='karn',min_rto_ns=20000,granularity_ns=True),dict(kind='unknown',min_rto_ns=20000,granularity_ns=1)]:
   w=deepcopy(self.w);w['transport_recovery']['rtt_estimator']=r
   with self.assertRaises(ValueError):validate(self.t,self.p,w)
 def test_transaction_stream_identity(self):
  w=json.loads((ROOT/'examples/scaleout_rdma_transactions/work.json').read_text());w['transport_recovery']=self.r
  self.p['faults']=[dict(node=4,port=0,message_id=1,kind='ack',sequence=48,count=1,start_ns=0,end_ns=5000000)]
  transactions(self.t,self.p,w)
if __name__=='__main__':unittest.main()
