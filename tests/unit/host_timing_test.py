"""Counterexamples to double counting, invented timeout ends, and malformed nesting."""
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.host_timing import HostTiming, read_trace


HEADER=dict(type='header',schema_version='moe-host-phase-trace/1',writer='native',pid=1,
    wall_clock='CLOCK_MONOTONIC',cpu_clock='CLOCK_PROCESS_CPUTIME_ID',unit='ns')
def begin(i,parent,name,w,c):return dict(type='begin',id=i,parent=parent,name=name,wall_ns=w,cpu_ns=c)
def end(i,w,c):return dict(type='end',id=i,wall_ns=w,cpu_ns=c)


class HostTimingTest(unittest.TestCase):
    def read(self,rows,tail=b''):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'host.jsonl'
            path.write_bytes(b''.join((json.dumps(row)+'\n').encode() for row in [HEADER,*rows])+tail)
            return read_trace(path)

    def test_nested_exclusive_does_not_add_wait_twice(self):
        # Process CPU can exceed wall when multiple threads execute.
        trace=self.read([begin(1,0,'run',10,20),begin(2,1,'exchange',20,40),
            begin(3,2,'wait',21,42),end(3,41,43),end(2,50,50),
            begin(4,1,'load',60,70),end(4,70,90),end(1,110,150),
            dict(type='footer',complete=True,spans=4)])
        phases=trace['phases']
        self.assertEqual(phases['run']['inclusive_wall_ns'],100)
        self.assertEqual(phases['run']['exclusive_wall_ns'],60)
        self.assertEqual(phases['exchange']['exclusive_wall_ns'],10)
        self.assertEqual(phases['run']['exclusive_cpu_ns'],100)
        self.assertEqual(sum(p['exclusive_wall_ns'] for p in phases.values()),100)
        self.assertTrue(trace['trace_complete'])

    def test_timeout_prefix_has_no_root_duration(self):
        trace=self.read([begin(1,0,'run',10,20),begin(2,1,'load',11,21),end(2,31,30)],b'{"type":"end"')
        self.assertTrue(trace['trailing_partial_record'])
        self.assertFalse(trace['trace_complete'])
        self.assertNotIn('run',trace['phases'])
        self.assertEqual([s['name'] for s in trace['open_spans']],['run'])
        self.assertEqual(trace['phases']['load']['inclusive_wall_ns'],20)

    def test_closed_error_trace_is_not_success(self):
        trace=self.read([begin(1,0,'worker',1,1),end(1,10,8),dict(type='footer',complete=False,spans=1)])
        self.assertTrue(trace['footer_present']);self.assertFalse(trace['trace_complete'])

    def test_crossing_duplicate_parent_and_clock_fail(self):
        bad=[
            [begin(1,0,'a',1,1),begin(2,1,'b',2,2),end(1,3,3)],
            [begin(1,0,'a',1,1),end(1,2,2),begin(1,0,'a',3,3)],
            [begin(1,0,'a',1,1),begin(2,0,'b',2,2)],
            [begin(1,0,'a',2,1),end(1,1,2)],
            [begin(1,0,'a',1,2),end(1,2,1)],
            [begin(1,0,'a',1,1),dict(type='footer',complete=True,spans=1)],
            [dict(type='footer',complete=True,spans=0),begin(1,0,'a',1,1)],
            [begin(True,0,'a',1,1)],
        ]
        for rows in bad:
            with self.subTest(rows=rows),self.assertRaises(ValueError):self.read(rows)

    def test_complete_malformed_line_is_not_truncation(self):
        with self.assertRaises(ValueError):self.read([],b'{"bad"\n')

    def test_python_writer_error_footer_and_disabled_no_clock(self):
        from unittest.mock import patch
        with patch('moe_ub_cosim.host_timing.time.monotonic_ns',side_effect=AssertionError):
            with HostTiming().span('disabled'):pass
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'host.jsonl';writer=HostTiming(path)
            with writer.span('worker'):
                with writer.span('load'):pass
            writer.close(False);trace=read_trace(path)
            self.assertFalse(trace['trace_complete'])
            self.assertEqual(len(trace['completed_spans']),2)


if __name__=='__main__':unittest.main()
