"""Run only topology and existing link-input regressions affected by the new CLI."""
import unittest


def main():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite([loader.discover('tests/unit', pattern=pattern) for pattern in
                               ('scaleout_topology_test.py', 'scaleout_link_failures_test.py')])
    assert suite.countTestCases() > 0
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
