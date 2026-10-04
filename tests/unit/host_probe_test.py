"""Exercise actual child limits, retained diagnostics and owned-group cleanup."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from moe_ub_cosim.host_probe import probe


class HostProbeTest(unittest.TestCase):
    def run_case(self,code,**limits):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'probe'
            kwargs=dict(rss_bytes=128*2**20,address_bytes=256*2**20,artifact_bytes=2**20,wall_seconds=5)
            kwargs.update(limits)
            result=probe([sys.executable,'-c',code.replace('OUTPUT',str(out))],out,**kwargs)
            self.assertEqual(result,json.loads((out/'probe.json').read_text()))
            self.assertFalse(result['remaining_live_group_pids'])
            return result,(out/'stdout.log').read_text(),(out/'stderr.log').read_text()

    def test_success_and_failure_are_separate(self):
        r,out,_=self.run_case("print('child result')")
        self.assertEqual(r['status'],'completed');self.assertIn('child result',out)
        r,_,err=self.run_case("raise ValueError('expected fixture error')")
        self.assertEqual((r['status'],r['child_exit_code']),('child_failed',1));self.assertIn('expected fixture error',err)

    def test_wall_limit_cleans_owned_child_processes(self):
        code="import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); time.sleep(60)"
        r,_,_=self.run_case(code,wall_seconds=.6)
        self.assertEqual(r['status'],'wall_limit');self.assertGreaterEqual(r['peak_sampled_processes'],2)
        r,_,_=self.run_case("import subprocess,sys;subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])")
        self.assertEqual(r['status'],'descendants_after_exit')

    def test_rss_and_address_limits(self):
        r,_,_=self.run_case('import time;x=bytearray(48*2**20);time.sleep(4)',rss_bytes=24*2**20)
        self.assertEqual(r['status'],'rss_limit')
        r,_,err=self.run_case('x=bytearray(512*2**20)')
        self.assertEqual(r['status'],'child_failed');self.assertIn('MemoryError',err)

    def test_total_output_budget(self):
        code="from pathlib import Path;import time;p=Path('OUTPUT');[(p/str(i)).write_bytes(b'x'*400000) for i in range(4)];time.sleep(4)"
        r,_,_=self.run_case(code)
        self.assertEqual(r['status'],'artifact_limit')


if __name__=='__main__':unittest.main()
