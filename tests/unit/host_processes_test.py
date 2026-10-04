"""Detached compiler/service children are included without touching other jobs."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from moe_ub_cosim.host_processes import ProcessTree


class ProcessTreeTest(unittest.TestCase):
    def test_detached_orphan_rss_and_cleanup_preserve_preexisting_child(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            with tempfile.TemporaryDirectory() as directory:
                ready = Path(directory) / 'ready'
                tree = ProcessTree()
                child = None
                try:
                    leaf = "import os,sys,time; data=bytearray(48*1024*1024); open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(30)"
                    parent = "import subprocess,sys; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],start_new_session=True)"
                    child = subprocess.Popen([sys.executable, '-c', parent, leaf, str(ready)], start_new_session=True)
                    tree.attach(child.pid)
                    child.wait(timeout=5)
                    deadline = time.monotonic() + 5
                    while not ready.exists() and time.monotonic() < deadline:
                        time.sleep(.01)
                    pid = int(ready.read_text())
                    rows = tree.collect()
                    self.assertNotIn(unrelated.pid, [row['pid'] for row in rows])
                    selected = next(row for row in rows if row['pid'] == pid)
                    self.assertGreater(selected['rss_bytes'], 48 * 1024 * 1024)
                    tree.terminate(signal.SIGKILL)
                    deadline = time.monotonic() + 5
                    while tree.collect() and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertFalse(tree.collect())
                    tree.reap()
                    self.assertFalse(Path(f'/proc/{pid}').exists())
                    self.assertIsNone(unrelated.poll())
                finally:
                    tree.terminate(signal.SIGKILL)
                    if child is not None:
                        child.wait(timeout=5)
                    tree.close()
        finally:
            unrelated.terminate(); unrelated.wait(timeout=5)

    def test_pid_reuse_does_not_inherit_ownership(self):
        tree = ProcessTree()
        try:
            tree.owned = {987654: 12}
            self.assertEqual(tree.collect({987654: dict(parent=1, start=13, state='S', rss_bytes=1024)}), [])
            self.assertEqual(tree.owned, {})
        finally:
            tree.close()


if __name__ == '__main__':
    unittest.main()
