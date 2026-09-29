"""Exact clock arithmetic, sampling contracts and observed-only access."""
from copy import deepcopy
from fractions import Fraction
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from moe_ub_cosim.observation import (ENDPOINT_FIELDS, I64_MAX, I64_MIN, PORT_FIELDS,
    backend_observation, local_time, public_config, validate_observation, validate_observed, windows,
    write_csv, write_lines)

ROOT=Path(__file__).resolve().parents[2]


class ObservationExportTest(unittest.TestCase):
    def setUp(self):
        self.config=json.loads((ROOT/'fixtures/m5_observation/run.json').read_text())['observation']

    def test_integer_clock_matches_fraction_floor_and_stays_monotone(self):
        for drift in (-999999999,-500000000,-125000,0,250000,1000000000):
            for offset in (-17001,0,38007):
                clock=dict(drift_ppb=drift,offset_ns=offset)
                times=[0,1,2,3,19,20001,2**53+19]
                got=[local_time(t,clock) for t in times]
                self.assertEqual(got,[math.floor(Fraction(10**9+drift,10**9)*t+offset) for t in times])
                self.assertEqual(got,sorted(got))
        self.assertEqual(local_time(1,dict(drift_ppb=-500000000,offset_ns=-5)),-5)
        with self.assertRaisesRegex(ValueError,'local clock'):local_time(I64_MAX,dict(drift_ppb=1,offset_ns=0))
        with self.assertRaisesRegex(ValueError,'global time'):local_time(-1,dict(drift_ppb=0,offset_ns=0))

    def test_clock_never_enters_backend_config(self):
        a=backend_observation(self.config,list(range(8)),1000000)
        changed=deepcopy(self.config)
        for c in changed['clocks']:c['offset_ns']-=100
        for s in changed['switches']:
            s['start_local_ns']-=100;s['end_local_ns']-=100
        self.assertEqual(a,backend_observation(changed,list(range(8)),1000000))
        self.assertNotIn('clock',json.dumps(a))
        _,ports=validate_observation(self.config,list(range(8)),1000000)
        self.assertEqual(len(ports),3)
        self.assertEqual(ports[0]['start_local_ns'],ports[1]['start_local_ns'])

    def test_reject_invalid_device_sampling_and_clock_bounds(self):
        changes=[lambda c:c['clocks'][0].update(drift_ppb=-10**9),
            lambda c:c['clocks'][0].update(drift_ppb=0.1),
            lambda c:c['clocks'][0].update(offset_ns=I64_MAX),
            lambda c:c['clocks'][0].update(offset_ns=I64_MIN-1),
            lambda c:c['clocks'].pop(),lambda c:c['clocks'].append(deepcopy(c['clocks'][0])),
            lambda c:c['switches'][0]['port_ids'].append(0),
            lambda c:c['switches'][0].update(period_ns=0),
            lambda c:c['switches'][0].update(period_ns=1),
            lambda c:c['switches'][0].update(start_local_ns=-8001),
            lambda c:c['switches'][0].update(end_local_ns=2000000),
            lambda c:c.update(record_provenance=1)]
        for change in changes:
            c=deepcopy(self.config);change(c)
            with self.subTest(config=c),self.assertRaises(ValueError):validate_observation(c,list(range(8)),1000000)

    def make_observed(self,d):
        port=dict(switch_id=8,port_id=4,direction='egress',start_local_ns=-5,end_local_ns=16,period_ns=10)
        (d/'public_config.json').write_text(json.dumps(public_config([0],[port])))
        write_lines(d/'endpoint_ops.jsonl',[dict(rank_id=0,local_record_id=1,kind='dispatch',start_local_ns=-3,end_local_ns=4)])
        rows=[dict(switch_id=8,port_id=4,direction='egress',local_window_start_ns=a,local_window_end_ns=b,
            counted_packets=1 if i==0 else 0,counted_packet_bytes=128 if i==0 else 0) for i,(a,b) in enumerate(windows(port))]
        write_csv(d/'port_windows.csv',PORT_FIELDS,rows)
        return rows

    def test_observed_validator_reads_only_allowlisted_directory(self):
        with tempfile.TemporaryDirectory() as name:
            d=Path(name);self.make_observed(d);opened=[];original=Path.open
            def checked(path,*args,**kwargs):
                self.assertEqual(path.parent,d);opened.append(path.name);return original(path,*args,**kwargs)
            with patch.object(Path,'open',checked):result=validate_observed(d)
            self.assertEqual(set(opened),{'public_config.json','endpoint_ops.jsonl','port_windows.csv'})
            self.assertEqual((result['port_windows'],result['zero_windows']),(3,2))
            self.assertEqual(result['access_scope'],'observed_only')

    def test_missing_zero_window_is_not_zero_and_hidden_data_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            d=Path(name);rows=self.make_observed(d)
            write_csv(d/'port_windows.csv',PORT_FIELDS,rows[:1]+rows[2:])
            with self.assertRaisesRegex(ValueError,'Missing.*port windows'):validate_observed(d)
            self.make_observed(d)
            data=json.loads((d/'public_config.json').read_text());data['offset_ns']=7
            (d/'public_config.json').write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'whitelist'):validate_observed(d)
            self.make_observed(d)
            data=json.loads((d/'endpoint_ops.jsonl').read_text());data['operation_id']='hidden'
            write_lines(d/'endpoint_ops.jsonl',[data])
            with self.assertRaisesRegex(ValueError,'whitelist'):validate_observed(d)
            self.make_observed(d);(d/'truth.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'unexpected observed file'):validate_observed(d)

    def test_no_symlink_escape_from_observed(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);d=root/'observed';d.mkdir();self.make_observed(d)
            hidden=root/'secret.json';hidden.write_text('{}')
            (d/'public_config.json').unlink();(d/'public_config.json').symlink_to(hidden)
            with self.assertRaisesRegex(ValueError,'symlinks'):validate_observed(d)


if __name__=='__main__':unittest.main()
