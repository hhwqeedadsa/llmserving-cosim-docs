"""Small checks for network screening coverage, grouping and honest bounds."""
import unittest
from moe_ub_cosim.observation import public_config
from .network_screen import groups, rank_key


class NetworkChecks(unittest.TestCase):
    def test_relocated_business_review_rejects_wrong_slice_before_reading_run(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from .network_run_review import review
        with TemporaryDirectory() as tmp:
            root=Path(tmp);case=root/'case';case.mkdir();bundle=root/'relocated';bundle.mkdir()
            (case/'selection.json').write_text(json.dumps(dict(slice='/absent-original',slice_sha256='wrong')))
            (bundle/'slice.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'Explicit slice differs'):
                review(case,root/'output',bundle)
            self.assertFalse((root/'output').exists())

    def test_busy_overlap_is_clipped_and_not_double_counted(self):
        from .network_packets import overlap
        self.assertEqual(overlap([(0,8),(5,12),(12,12),(15,20)],3,18),12)
        self.assertEqual(overlap([(0,4),(6,10)],4,6),0)
        with self.assertRaises(ValueError):overlap([],5,4)

    def test_packet_comparison_uses_business_ranges_not_uid(self):
        from .compare_network import packet_key
        r=dict(candidate_id='N1',transfer_id=10,packet_uid=100,
            classified_ranges=[dict(message_category='business_data',task_byte_range=[0,4096])])
        self.assertEqual(packet_key(r),packet_key(dict(r,packet_uid=500)))
        self.assertNotEqual(packet_key(r),packet_key(dict(r,transfer_id=11)))

    def test_capture_and_service_keep_other_resources_and_direction(self):
        from copy import deepcopy
        from .network_control import capture_ports, change_service
        topology=dict(nodes=[dict(node_id=0,kind='DEVICE'),dict(node_id=1,kind='SWITCH'),
            dict(node_id=2,kind='SWITCH'),dict(node_id=3,kind='DEVICE')],links=[
                dict(src=0,src_port=0,dst=1,dst_port=0,rate_bps=400,delay_ns=20),
                dict(src=1,src_port=1,dst=2,dst_port=0,rate_bps=400,delay_ns=20),
                dict(src=2,src_port=1,dst=3,dst_port=0,rate_bps=400,delay_ns=20)])
        candidate=dict(switch_id=2,port_id=1)
        ports,direct=capture_ports(topology,[candidate])
        self.assertEqual(ports,[(1,1),(2,1)])
        self.assertEqual(direct,[dict(node_id=3,port_id=0,target=2)])
        original=dict(schema_version='moe-ub-backend-config/2',topology=topology,attributes=dict(worker='1',quota='32'),profiles=dict(operation='STORE'))
        before=deepcopy(original);after=change_service(original,candidate,10,30,800)
        self.assertEqual(before,original)
        self.assertEqual(after['schema_version'],'moe-ub-backend-config/3')
        self.assertEqual({k:v for k,v in after.items() if k not in ('port_service','schema_version')},
                         {k:v for k,v in original.items() if k!='schema_version'})
        self.assertEqual(after['port_service']['intervals'],[dict(node_id=2,port_id=1,start_ns=10,end_ns=30,rate_bps=800)])
        with self.assertRaises(ValueError):change_service(original,candidate,10,30,200)

    def public(self):
        return public_config([0], [dict(switch_id=1,port_id=p,direction='egress',
            start_local_ns=0,end_local_ns=50,period_ns=10) for p in (0,1)])

    def row(self, a, area, peak=10):
        return dict(switch_id=1,port_id=0,local_window_start_ns=a,local_window_end_ns=a+10,
            voq_waiting_byte_ns=area,voq_waiting_peak_bytes=peak,
            fifo_waiting_byte_ns=0,fifo_waiting_peak_bytes=0)

    def test_omitted_zero_breaks_event_without_inventing_continuity(self):
        ports, events, count, logical = groups([self.row(0,30),self.row(10,40),self.row(30,10)], self.public())
        self.assertEqual((count,logical,len(ports),len(events)),(3,10,2,2))
        self.assertEqual((events[0]['nonempty_lower_ns'],events[0]['nonempty_upper_ns']),(7,20))
        self.assertEqual((events[0]['start_ns'],events[0]['end_ns']),(0,20))
        self.assertTrue(events[0]['left_coverage_truncated'])
        self.assertEqual(ports[1,1],{})

    def test_instantaneous_peak_is_not_positive_wait(self):
        ports,events,_,_=groups([self.row(0,0,100)],self.public())
        self.assertEqual(events,[])
        self.assertEqual(ports[1,0]['voq_instantaneous_peak_windows'],1)

    def test_reject_duplicate_outside_and_impossible_integral(self):
        for rows in ([self.row(0,30),self.row(0,30)], [self.row(50,30)], [self.row(0,101)]):
            with self.assertRaises(ValueError): groups(rows,self.public())

    def test_duration_end_boundary_and_queue_classes_are_separate(self):
        row=self.row(40,20);row.update(fifo_waiting_byte_ns=50,fifo_waiting_peak_bytes=10)
        _,events,_,_=groups([row],self.public())
        self.assertEqual(len(events),2)
        self.assertTrue(all(e['right_coverage_truncated'] for e in events))
        self.assertEqual(sum(e['area'] for e in events),70)

    def test_rank_does_not_consult_business_finish_or_selected_rank(self):
        row=dict(max_window_mean_bytes=10,area=100,peak=50,case_id='a',switch_id=1,port_id=0,start_ns=0)
        self.assertEqual(rank_key(row),rank_key(dict(row,business_impact=10000,rank_id=70)))
        self.assertLess(rank_key(dict(row,max_window_mean_bytes=11)),rank_key(row))

    def test_dispatch_preserves_all_reorders_and_the_nonlatest_gate(self):
        from .consumer_review import completion_conditions
        names=['dispatch-entry','dispatch-counts-and-data-ready','dispatch-reorder',
               'dispatch-reorder','dispatch-operator-complete','combine-token-reduction']
        deps=[[],[0],[1],[1],[2,3],[4]];ends=[0,10,12,12,12,50]
        plan={i:dict(node_id=i,name=n,kind='COMP',layer_id=7,deps=deps[i]) for i,n in enumerate(names)}
        times={i:dict(start_ns=max((ends[d] for d in deps[i]),default=0),end_ns=ends[i]) for i in plan}
        r=completion_conditions(plan,times,{}, {},4,7,kind='dispatch')
        self.assertEqual(r['finish']['latest_dependency_ties'],[2,3])
        self.assertEqual(len(r['all_reorder_nodes']),2)
        self.assertEqual(r['all_token_reductions'],[])
        self.assertEqual(r['all_data_status_gates'][0]['node_id'],1)


if __name__=='__main__': unittest.main()
