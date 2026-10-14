"""Credit geometry, complete coverage and private stream validation."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from moe_ub_cosim.network_facts import policy, checked_rows


class NetworkFactsTest(unittest.TestCase):
    def setUp(self):
        self.policy=dict(schema_version='moe-ub-network-facts/1',ports=[dict(node_id=4,port_id=2)],
                         end_ns=20,max_records=100)
        self.capture=dict(schema_version='moe-ub-network-facts-capture/1',policy=self.policy,
            start_ns=0,end_ns=20,execution_end_ns=25,records=4,complete=True,dropped_records=0,
            access_scope='internal_truth_prepare_restricted_inputs_before_diagnosis')
        events=[(0,'initial',dict(voq_bytes=0,fifo_bytes=0,credits=[2],credit_cell_bytes=160)),
                (2,'credit',dict(vl=0,available_cells=1,delta_cells=-1)),
                (3,'credit_decision',dict(in_port=1,vl=0,queue_type='VOQ',packet_uid=42,
                     head_bytes=161,required_cells=2,available_cells=1,blocked=True)),
                (20,'final',dict(boundary_state_from_prior_events=True))]
        self.rows=[dict(sequence=i+1,node_id=4,port_id=2,time_ns=t,event=e,data=d)
                   for i,(t,e,d) in enumerate(events)]

    def read(self,rows=None,capture=None):
        with patch('moe_ub_cosim.network_facts.iter_result',return_value=iter(self.rows if rows is None else rows)):
            return list(checked_rows('.',{'network_facts_capture':capture or self.capture}))

    def test_nonzero_credit_can_block(self):
        self.assertEqual(self.read(),self.rows)

    def test_zero_tick_control_serialization_is_preserved(self):
        rows=deepcopy(self.rows)
        rows[2].update(event='tx',data=dict(packet_uid=42,packet_bytes=6,completion_delay_ns=0,rate_bps=400000000000))
        self.assertEqual(self.read(rows),rows)

    def test_lost_mutation_or_wrong_geometry_rejected(self):
        for field,value in [('available_cells',2),('required_cells',1),('blocked',False),('packet_uid',None)]:
            with self.subTest(field=field):
                rows=deepcopy(self.rows);rows[2]['data'][field]=value
                with self.assertRaises(ValueError):self.read(rows)

    def test_complete_boundaries_and_counts(self):
        for rows in (self.rows[:-1],self.rows[1:],self.rows[::-1]):
            with self.assertRaises(ValueError):self.read(rows)
        for field,value in [('complete',False),('dropped_records',1),('end_ns',19),('records',5)]:
            c=deepcopy(self.capture);c[field]=value
            with self.assertRaises(ValueError):self.read(capture=c)

    def test_policy_limits(self):
        policy(self.policy,20)
        for field,value in [('end_ns',21),('max_records',1),('ports',self.policy['ports']*2)]:
            p=deepcopy(self.policy);p[field]=value
            with self.assertRaises(ValueError):policy(p,20)

if __name__=='__main__':unittest.main()
