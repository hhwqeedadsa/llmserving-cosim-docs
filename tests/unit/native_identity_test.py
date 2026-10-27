"""Real isolated Git parents/children test declared identity versus actual state."""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from moe_ub_cosim.native_identity import (
    NATIVE_PATH, PARENT_PATH, SUBMODULE_PATH, locked_native_commit, verify_native_source)
from moe_ub_cosim import scaleout_rdma


class NativeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parent = self.root / PARENT_PATH
        self.native = self.root / NATIVE_PATH
        self.native.mkdir(parents=True)
        self.git(self.parent, 'init', '-q')
        self.git(self.native, 'init', '-q')
        (self.native / 'source.cc').write_text('original\n')
        self.git(self.native, 'add', 'source.cc')
        self.git(self.native, 'commit', '-qm', 'source')
        self.commit = self.git(self.native, 'rev-parse', 'HEAD')
        self.git(self.parent, 'update-index', '--add', '--cacheinfo', '160000', self.commit, SUBMODULE_PATH)
        self.git(self.parent, 'commit', '-qm', 'lock child')
        self.row = dict(name='ASTRA-original-ns3-backend', path=NATIVE_PATH,
                        parent_path=PARENT_PATH, submodule_path=SUBMODULE_PATH, checkout_commit=self.commit)
        self.write_lock([self.row])

    @staticmethod
    def git(path, *args):
        result = subprocess.run(['git', '-c', 'user.name=Identity Test', '-c', 'user.email=test@local.invalid',
                                 '-c', 'commit.gpgsign=false', *args], cwd=path, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout.strip()

    def write_lock(self, rows):
        (self.root / 'deps.lock.json').write_text(json.dumps(dict(schema_version='moe-ub-deps/1', dependencies=rows)))

    def test_matching_lock_parent_and_clean_checkout(self):
        self.assertEqual(verify_native_source(self.root), self.commit)

    def test_missing_or_duplicate_entry(self):
        for rows in ([], [self.row, deepcopy(self.row)], [self.row, dict(path=NATIVE_PATH)]):
            self.write_lock(rows)
            with self.assertRaisesRegex(ValueError, 'one locked'):
                locked_native_commit(self.root)

    def test_missing_or_oversized_lock(self):
        path = self.root / 'deps.lock.json'
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'requires deps.lock'):
            locked_native_commit(self.root)
        path.write_bytes(b' ' * 1048577)
        with self.assertRaisesRegex(ValueError, 'at most 1MiB'):
            locked_native_commit(self.root)

    def test_wrong_schema_or_dependency_collection(self):
        for value in ([], {}, dict(schema_version='moe-ub-deps/9', dependencies=[self.row]),
                      dict(schema_version='moe-ub-deps/1', dependencies={})):
            (self.root / 'deps.lock.json').write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, 'invalid native dependency lock'):
                locked_native_commit(self.root)

    def test_wrong_paths_are_rejected(self):
        for key in ('path', 'parent_path', 'submodule_path'):
            self.write_lock([dict(self.row, **{key: '../other'})])
            with self.assertRaisesRegex(ValueError, 'unexpected parent or path'):
                locked_native_commit(self.root)

    def test_not_a_full_lowercase_commit(self):
        for commit in ('main', 'abc1234', 'A' * 40, 'g' * 40, True, None):
            self.write_lock([dict(self.row, checkout_commit=commit)])
            with self.assertRaisesRegex(ValueError, 'full lowercase'):
                locked_native_commit(self.root)

    def test_lock_cannot_follow_an_unrecorded_head(self):
        self.write_lock([dict(self.row, checkout_commit='f' * 40)])
        with self.assertRaisesRegex(ValueError, 'parent gitlink'):
            verify_native_source(self.root)

    def test_checkout_moving_without_parent_update_is_rejected(self):
        (self.native / 'source.cc').write_text('different\n')
        self.git(self.native, 'add', 'source.cc')
        self.git(self.native, 'commit', '-qm', 'another version')
        with self.assertRaisesRegex(ValueError, 'differs from locked'):
            verify_native_source(self.root)

    def test_staged_and_unstaged_changes_rejected(self):
        (self.native / 'source.cc').write_text('dirty\n')
        for staged in (False, True):
            if staged:
                self.git(self.native, 'add', 'source.cc')
            with self.assertRaisesRegex(ValueError, 'source is dirty'):
                verify_native_source(self.root)

    def test_resume_cannot_reuse_another_dependency_version(self):
        directory = self.root / 'output'
        (directory / 'cmake').mkdir(parents=True)
        (directory / 'cmake/CMakeCache.txt').write_text('not a real build\n')
        (directory / 'provenance.json').write_text(json.dumps(dict(native_commit='f' * 40)))
        with mock.patch.object(scaleout_rdma, 'ROOT', self.root):
            with self.assertRaisesRegex(ValueError, 'owned pinned RDMA build'):
                scaleout_rdma.build(directory, resume=True)

    def test_import_does_not_read_a_lock_or_execute_git(self):
        with mock.patch.object(Path, 'read_text', side_effect=AssertionError('unexpected filesystem read')):
            with mock.patch.object(subprocess, 'run', side_effect=AssertionError('unexpected Git invocation')):
                importlib.reload(scaleout_rdma)


if __name__ == '__main__':
    unittest.main()
