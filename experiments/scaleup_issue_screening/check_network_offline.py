"""Synthetic checks for offline bounds; no simulation or research-gain assertions."""
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from .network_offline import transfer_bounds
from .network_bounds import judge_input, simplify, judge
from .network_information import RULE
from .workflow import digest, read, save


def inputs():
    data=dict(schema_version='issue-network-information-input/1',rule=RULE,time_resolution_ns=1,
        endpoints=[dict(operation=o,rank=r,start_ns=0,end_ns=100) for o in ('x','y') for r in (0,1,2)],
        flows=[dict(operation=o,src=r,dst=1,payload_bytes=10) for o in ('x','y') for r in (0,2)],
        windows=[dict(sample='s0',switch_id=10,port_id=1,receiver=1,start_ns=10,end_ns=20,
                      fifo_byte_ns=100,voq_byte_ns=0)])
    semantics=dict(schema_version='issue-network-end-semantics/1',input_sha256='unused',
        clock='aligned_simulation_ns',operations=[dict(operation=o,source_end_bounds_remote_delivery=True) for o in ('x','y')])
    relation=dict(schema_version='issue-network-ideal-relation/1',input_sha256='unused',start_ns=10,end_ns=20,records=[])
    return data,semantics,relation


class CandidateBounds(unittest.TestCase):
    def test_real_mixture_and_unknown_application_are_not_forced_unique(self):
        from .network_bound_review import evaluate, TRUTH_SCOPES
        d,s,_=inputs();predictions=judge_input(d,s)
        t=dict(sample='s0',primary=False,candidate='synthetic',known_waiting_operations=['x','y'],
            complete_fifo_operations=['x'],partial_arrival_to_fifo_operations=['y'],
            app_packets_enqueued_without_upstream=1,protocol_packets_with_fifo_wait=0)
        e=evaluate(d,predictions,dict(samples=[t]))
        self.assertEqual(e['all']['scopes'][TRUTH_SCOPES[0]]['known_mixture_windows'],1)
        self.assertEqual(e['all']['scopes'][TRUTH_SCOPES[0]]['unique_known'],0)
        self.assertEqual(e['all']['scopes'][TRUTH_SCOPES[1]]['unconfirmed_candidate_occurrences'],1)
        for f in TRUTH_SCOPES:t[f]=[]
        e=evaluate(d,predictions,dict(samples=[t]))
        self.assertEqual(e['all']['positive_queue_application_unknown'],1)

    def test_one_source_ends_another_remains(self):
        d,s,_=inputs();d['endpoints'][0]['end_ns']=10
        self.assertIn('x',judge_input(d,s)[0]['possible_operations'])
        d['endpoints'][2]['end_ns']=10
        self.assertNotIn('x',judge_input(d,s)[0]['possible_operations'])
        self.assertIn('x',judge_input(d,s,'old')[0]['possible_operations'])

    def test_async_return_is_not_remote_completion(self):
        d,s,_=inputs()
        for r in d['endpoints']:r['end_ns']=5 if r['rank']!=1 else 100
        for r in s['operations']:r['source_end_bounds_remote_delivery']=False
        self.assertEqual(judge_input(d,s)[0]['possible_operations'],['x','y'])

    def test_destination_start_is_not_arrival_lower_bound(self):
        d,s,_=inputs()
        for r in d['endpoints']:
            if r['rank']==1:r['start_ns']=90
        self.assertEqual(judge_input(d,s)[0]['possible_operations'],['x','y'])

    def test_half_open_and_conservative_rounding(self):
        d,s,_=inputs()
        for r in d['endpoints']:
            if r['rank']!=1:r['end_ns']=10
        self.assertEqual(judge_input(d,s)[0]['possible_operations'],[])
        for r in d['endpoints']:
            if r['rank']!=1:r['end_ns']=11
        d['time_resolution_ns']=10
        self.assertEqual(judge_input(d,s)[0]['possible_operations'],['x','y'])
        for r in d['endpoints']:
            if r['rank']!=1:r.update(start_ns=20,end_ns=100)
        self.assertEqual(judge_input(d,s)[0]['possible_operations'],[])

    def test_missing_is_not_zero_and_uncovered_future_is_retained(self):
        d,s,m=inputs()
        self.assertEqual(judge_input(d,s,extra=m)[0]['possible_operations'],['x','y'])
        for r in d['endpoints']:r['end_ns']=20
        simple=simplify(d,m)
        self.assertEqual(judge_input(d,s,extra=simple)[0]['possible_operations'],[])
        simple['records'].pop()
        self.assertEqual(judge_input(d,s,extra=simple)[0]['possible_operations'],['y'])
        simple['records'][0]['capture_complete']=False
        self.assertEqual(judge_input(d,s,extra=simple)[0]['possible_operations'],['x','y'])

    def test_queue_can_hold_until_a_future_transmission_window(self):
        d,s,m=inputs();d['windows'].append(dict(d['windows'][0],sample='s1',start_ns=20,end_ns=30))
        m['end_ns']=30
        for r in d['endpoints']:r['end_ns']=30
        m['records']=[dict(operation='x',switch_id=10,port_id=1,start_ns=20,end_ns=30)]
        result=judge_input(d,s,extra=m)
        self.assertEqual(result[0]['possible_operations'],['x'])
        self.assertEqual(result[0]['operation_reasons']['x']['offline_capture_horizon_ns'],10)

    def test_start_window_end_is_not_wire_finish(self):
        d,s,m=inputs();d['windows'].append(dict(d['windows'][0],sample='s1',start_ns=20,end_ns=30))
        m['end_ns']=30
        for r in d['endpoints']:r['end_ns']=30
        # A packet starts in [10,20) and may still be on the wire at 25.
        # This is an output-queue bound, not a claim that the wire is idle.
        m['records']=[dict(operation='x',switch_id=10,port_id=1,start_ns=10,end_ns=20)]
        result=judge_input(d,s,extra=m)
        self.assertEqual(result[0]['possible_operations'],['x'])
        self.assertEqual(result[1]['possible_operations'],[])

    def test_full_and_simple_match_all_outputs_for_both_baselines(self):
        d,s,m=inputs()
        for r in d['endpoints']:r['end_ns']=20
        m['records']=[dict(operation='x',switch_id=10,port_id=1,start_ns=10,end_ns=20)]
        for mode in ('old','strong'):
            self.assertEqual(judge_input(d,s,mode,m),judge_input(d,s,mode,simplify(d,m)))
        m['records'][0]['end_ns']=21
        with self.assertRaises(ValueError):simplify(d,m)

    def test_zero_queue_never_proves_an_operation_finished(self):
        d,s,_=inputs();d['windows'][0]['fifo_byte_ns']=0
        r=judge_input(d,s)[0]
        self.assertEqual(r['possible_operations'],['x','y'])
        self.assertFalse(r['plus_queue_waiting'])

    def test_judge_opens_only_explicit_inputs_and_own_code(self):
        d,s,m=inputs()
        with TemporaryDirectory() as tmp:
            root=Path(tmp);inp=root/'input.json';sem=root/'semantics.json';rel=root/'relation.json';out=root/'out.json'
            save(inp,d);s['input_sha256']=m['input_sha256']=digest(inp);save(sem,s);save(rel,m)
            allowed={inp,sem,rel,Path(__file__).with_name('network_bounds.py').resolve()}
            original=Path.open
            def guarded(path,mode='r',*args,**kwargs):
                if 'r' in mode:self.assertIn(path,allowed)
                return original(path,mode,*args,**kwargs)
            with patch.object(Path,'open',guarded):judge(inp,sem,out,relation=rel)
            self.assertEqual(read(out)['samples'][0]['possible_operations'],['x','y'])
        d['truth']='forbidden'
        with self.assertRaises(ValueError):judge_input(d,s)
        d.pop('truth');s['archive']='forbidden'
        with self.assertRaises(ValueError):judge_input(d,s)


class SourceBounds(unittest.TestCase):
    def test_delivery_and_protocol_retirement_are_different(self):
        t=dict(operation_id='x',src_rank=0,dst_rank=1)
        endpoints={('x',0):dict(start_local_ns=0,end_local_ns=20),
                   ('x',1):dict(start_local_ns=0,end_local_ns=40)}
        a=dict(send_start_ns=5,data_ready_ns=20,transaction_done_ns=50)
        self.assertTrue(all(transfer_bounds(t,a,endpoints).values()))
        a['data_ready_ns']=21
        self.assertEqual(transfer_bounds(t,a,endpoints),
            dict(source_start=True,destination_end=True,source_end=False))

    def test_all_counterexamples_are_exposed(self):
        t=dict(operation_id='x',src_rank=0,dst_rank=1)
        endpoints={('x',0):dict(start_local_ns=5,end_local_ns=10),
                   ('x',1):dict(start_local_ns=0,end_local_ns=20)}
        a=dict(send_start_ns=4,data_ready_ns=21)
        self.assertFalse(any(transfer_bounds(t,a,endpoints).values()))


class PublicRecovery(unittest.TestCase):
    def example(self):
        d,s,_=inputs()
        d['windows'].append(dict(d['windows'][0],sample='s1',start_ns=20,end_ns=30))
        for r in d['endpoints']:r['end_ns']=30
        c=dict(schema_version='issue-network-public-counts/1',input_sha256='unused',start_ns=10,end_ns=30,
            windows=[dict(sample='s0',counted_packets=0,counted_packet_bytes=0,capture_complete=True),
                     dict(sample='s1',counted_packets=1,counted_packet_bytes=100,capture_complete=True)])
        return d,s,c

    def test_short_silence_does_not_certify_completion(self):
        from .network_recovery import recover_input
        d,s,c=self.example();r=recover_input(d,s,c)
        self.assertEqual(r['samples'][0]['possible_operations'],['x','y'])
        self.assertTrue(all(i['last_possible_tx_window_end_ns']==30 for i in r['intervals']))

    def test_missing_and_future_coverage_stay_unknown(self):
        from .network_recovery import recover_input
        d,s,c=self.example();c['windows'].pop()
        self.assertEqual(recover_input(d,s,c)['samples'][0]['possible_operations'],['x','y'])
        d,s,c=self.example()
        for r in c['windows']:r.update(counted_packets=0,counted_packet_bytes=0)
        for r in d['endpoints']:r['end_ns']=40
        self.assertEqual(recover_input(d,s,c)['samples'][0]['possible_operations'],['x','y'])

    def test_wire_bytes_are_not_subtracted_as_application_payload(self):
        from .network_recovery import recover_input
        d,s,c=self.example();a=recover_input(d,s,c)
        c['windows'][1]['counted_packet_bytes']=10**6
        self.assertEqual(a,recover_input(d,s,c))

    def test_late_empty_counter_tail_can_certify_a_bound(self):
        from .network_recovery import recover_input
        d,s,c=self.example()
        c['windows'][0].update(counted_packets=1,counted_packet_bytes=100)
        c['windows'][1].update(counted_packets=0,counted_packet_bytes=0)
        self.assertEqual(recover_input(d,s,c)['samples'][1]['possible_operations'],[])

    def test_recovers_only_from_explicit_inputs(self):
        from .network_recovery import recover
        d,s,c=self.example()
        with TemporaryDirectory() as tmp:
            root=Path(tmp);inp=root/'input.json';sem=root/'semantics.json';cnt=root/'counts.json';out=root/'out.json'
            save(inp,d);s['input_sha256']=c['input_sha256']=digest(inp);save(sem,s);save(cnt,c)
            allowed={inp,sem,cnt,*[Path(__file__).with_name(n).resolve() for n in ('network_recovery.py','network_bounds.py')]}
            original=Path.open
            def guarded(path,mode='r',*args,**kwargs):
                if 'r' in mode:self.assertIn(path,allowed)
                return original(path,mode,*args,**kwargs)
            with patch.object(Path,'open',guarded):recover(inp,sem,cnt,out)
            self.assertEqual(read(out)['samples'][0]['possible_operations'],['x','y'])
        c['truth']='forbidden'
        from .network_recovery import recover_input
        with self.assertRaises(ValueError):recover_input(d,s,c)


if __name__=='__main__':unittest.main()
