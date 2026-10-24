"""Remote effect ordering through preparation, completion, and carrier order."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from moe_ub_cosim.scaleout_transactions import validate

ROOT=Path(__file__).resolve().parents[2]
class EffectOrder(unittest.TestCase):
    def setUp(self):
        self.t=dict(schema_version='moe-scaleout-physical-topology/1',
            nodes=[dict(id=i,kind='gpu') for i in range(8)]+[dict(id=8,kind='tor',plane=0)],
            links=[dict(src=i,dst=8,src_port=1,dst_port=i+1,rate_bps=100000000000) for i in range(8)])
        self.p=json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_text())
        self.w=json.loads((ROOT/'examples/scaleout_rdma_transactions/submission-prefix.json').read_text())
        self.w['connections'][1].update(src=0,dst=4)
        self.w['operations']=[dict(id=0,kind='remote',connection=0,verb='put',key=8,
            address=8192,hex='11'*8,aggregate_requests=False,after=[])]
    def put(self, i, connection, parent, event, address=8192):
        return dict(id=i,kind='remote',connection=connection,verb='put',key=8,address=address,
                    hex='22'*8,aggregate_requests=False,after=[dict(operation=parent,event=event)])
    def test_prepared_cross_connection_rejected(self):
        self.w['operations'].append(self.put(1,1,0,'prepared'))
        with self.assertRaisesRegex(ValueError,'remote effect order'):validate(self.t,self.p,self.w)
    def test_local_timer_cannot_upgrade_prepared(self):
        self.w['operations'].extend([dict(id=1,kind='local',rank=0,duration_ns=20000,
            after=[dict(operation=0,event='prepared')]),self.put(2,1,1,'completed')])
        with self.assertRaisesRegex(ValueError,'remote effect order'):validate(self.t,self.p,self.w)
    def test_remote_completion_events_order_cross_connection(self):
        for event in ['applied','request_acked','completed']:
            with self.subTest(event=event):
                w=deepcopy(self.w);w['operations'].append(self.put(1,1,0,event));validate(self.t,self.p,w)
    def test_prepared_same_carrier_is_ordered(self):
        self.w['operations'].append(self.put(1,0,0,'prepared'));validate(self.t,self.p,self.w)
    def test_later_same_carrier_completion_covers_predecessor(self):
        self.w['operations'].extend([self.put(1,0,0,'prepared',8208),self.put(2,1,1,'request_acked')])
        validate(self.t,self.p,self.w)
    def test_later_same_carrier_preparation_does_not_cover_predecessor(self):
        self.w['operations'].extend([self.put(1,0,0,'prepared',8208),self.put(2,1,1,'prepared')])
        with self.assertRaisesRegex(ValueError,'remote effect order'):validate(self.t,self.p,self.w)
    def test_array_order_does_not_order_carrier_submission(self):
        self.w['operations'].append(self.put(1,0,0,'prepared'))
        self.w['operations'][1]['after']=[]
        with self.assertRaisesRegex(ValueError,'remote effect order'):validate(self.t,self.p,self.w)
    def test_local_completion_preserves_actual_remote_completion(self):
        self.w['operations'].extend([dict(id=1,kind='local',rank=0,duration_ns=20000,
            after=[dict(operation=0,event='request_acked')]),self.put(2,1,1,'completed')])
        validate(self.t,self.p,self.w)
if __name__=='__main__':unittest.main()
