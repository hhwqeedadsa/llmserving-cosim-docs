"""Complete request range coverage must fail on omissions, overlaps or truncation."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from moe_ub_cosim.native_queue import checked_requests, policy


class RequestAuditTest(unittest.TestCase):
    def fixtures(self):
        transfer=dict(graph_id='g0',transfer_id=100,src_rank=2,dst_rank=7,src_ub_node=0,bytes=4097,operation='STORE')
        identity=dict(graph_id='g0',transfer_id=100,src_rank=2,dst_rank=7,producer=3)
        rows=[dict(sequence=i+1,time_ns=10+i,node_id=0,identity=deepcopy(identity),operation='STORE',offset=o,bytes=b)
              for i,(o,b) in enumerate([(0,4096),(4096,1)])]
        config=dict(schema_version='moe-ub-native-queue-config/2',rank_ids=[7],end_ns=5,
                    max_records=100,max_snapshot_items=32,max_request_records=10)
        report=dict(simulation_end_ns=50,native_queue_capture=dict(policy=config,request_audit=dict(
            schema_version='moe-ub-native-request-audit/1',scope='all_native_STORE_requests_entire_execution',
            complete=True,dropped_records=0,records=2,end_ns=50)))
        return transfer,rows,report

    def test_whole_execution_includes_nonselected_initiators(self):
        t,rows,report=self.fixtures();policy(report['native_queue_capture']['policy'],[2,7],50)
        with patch('moe_ub_cosim.native_queue.iter_result',side_effect=lambda _,field,_report:iter([t] if field=='transfers' else rows)):
            self.assertEqual(list(checked_requests(None,report)),rows)

    def test_reject_incomplete_or_invalid_range_evidence(self):
        for kind in ('omission','overlap','gap','size','node','count','late','truncated','limit'):
            t,rows,report=self.fixtures()
            if kind=='omission':rows.pop()
            if kind=='overlap':rows[-1]['offset']=0
            if kind=='gap':rows[-1]['offset']=4097
            if kind=='size':rows[-1]['bytes']=2
            if kind=='node':rows[0]['node_id']=9
            if kind=='count':report['native_queue_capture']['request_audit']['records']=3
            if kind=='late':rows[-1]['time_ns']=51
            if kind=='truncated':report['native_queue_capture']['request_audit']['complete']=False
            if kind=='limit':report['native_queue_capture']['policy']['max_request_records']=1
            with self.subTest(kind=kind),patch('moe_ub_cosim.native_queue.iter_result',side_effect=lambda _,field,_report:iter([t] if field=='transfers' else rows)):
                with self.assertRaises(ValueError):list(checked_requests(None,report))


if __name__=='__main__':unittest.main()
