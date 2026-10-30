"""Run related input/old-mode checks without starting networks."""
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests/unit'))
import scaleout_link_failures_test
import scaleout_repair_plan_test
import rc_packet_test

suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(m)
  for m in (scaleout_link_failures_test,scaleout_repair_plan_test,rc_packet_test))
r=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not r.wasSuccessful())
