"""Reject silent effective-option drift before invoking a costly compiler."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from moe_ub_cosim.native_compilation import inspect_commands
from moe_ub_cosim.scaleout_rdma import build


class NativeCompilationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'commands.json'
        names = ('fabric.cc', 'persistent_streams.cc', 'remote_transactions.cc', 'transaction_session.cc',
            'simulator.cc', 'default-simulator-impl.cc', 'qbb-net-device.cc', 'rdma-hw.cc', 'switch-node.cc')
        self.rows = [dict(file='/source/'+name, arguments=['c++', '-Wall', '-Wpedantic', '-O3', '-c', '/source/'+name]) for name in names]

    def check(self, rows=None):
        self.path.write_text(json.dumps(self.rows if rows is None else rows))
        return inspect_commands(self.path)

    def test_accepted_commands_retain_all_critical_units(self):
        value = self.check()
        self.assertEqual(value['translation_units'], 9)
        self.assertEqual(value['effective_optimization'], '-O3')
        self.assertEqual(len(value['critical_units']), 9)

    def test_release_name_does_not_override_last_o0(self):
        self.rows[0]['arguments'].extend(['-DNS3_BUILD_PROFILE_RELEASE', '-O0'])
        with self.assertRaisesRegex(ValueError, 'effective -O3'): self.check()

    def test_last_optimization_option_is_effective(self):
        self.rows[0]['arguments'].insert(1, '-O0')
        self.assertEqual(self.check()['effective_optimization'], '-O3')

    def test_command_string_with_quoted_paths(self):
        for row in self.rows:
            row['file'] = '/a directory/'+Path(row['file']).name
            row['command'] = 'c++ -Wall -Wpedantic -O3 -c "'+row['file']+'"'
            del row['arguments']
        self.assertTrue(self.check()['passed'])

    def test_missing_warning_or_optimization(self):
        for flag in ('-Wall', '-Wpedantic', '-O3'):
            rows = deepcopy(self.rows); rows[0]['arguments'].remove(flag)
            with self.assertRaises(ValueError): self.check(rows)

    def test_uninterpreted_flags_and_response_files(self):
        for flag in ('-O99', '@secret-options', '-Xclang', '-Xcompiler', '&&'):
            rows = deepcopy(self.rows); rows[0]['arguments'].append(flag)
            with self.assertRaises(ValueError): self.check(rows)

    def test_development_oracle_or_ub_source(self):
        rows = deepcopy(self.rows); rows[0]['arguments'].append('-DMOE_VERIFY_PROGRESS_UPDATES')
        with self.assertRaisesRegex(ValueError, 'development'): self.check(rows)
        rows = deepcopy(self.rows); rows[0]['file'] = '/unified-bus/fabric.cc'
        rows[0]['arguments'][-1] = rows[0]['file']
        with self.assertRaisesRegex(ValueError, 'UB source'): self.check(rows)

    def test_missing_critical_unit_or_empty_database(self):
        for rows in (self.rows[:-1], [], {}):
            with self.assertRaises(ValueError): self.check(rows)

    def test_command_cannot_claim_another_source_or_disable_warnings(self):
        rows = deepcopy(self.rows); rows[0]['arguments'][-1] = '/another.cc'
        with self.assertRaisesRegex(ValueError, 'source differs'): self.check(rows)
        for flag in ('-w', '-Wno-all', '-ffast-math'):
            rows = deepcopy(self.rows); rows[0]['arguments'].append(flag)
            with self.assertRaisesRegex(ValueError, 'unsafe math or disabled warnings'): self.check(rows)

    def test_explicit_compile_budget_checked_before_git_or_output(self):
        output = Path(self.temp.name)/'not-created'
        for value in (True, 59, 1801, 3.5):
            with self.assertRaisesRegex(ValueError, 'compile_wall_seconds'): build(output, compile_wall_seconds=value)
            self.assertFalse(output.exists())


if __name__ == '__main__': unittest.main()
