"""Causal frontiers, dynamic ordering and read-only views; no native execution."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from moe_ub_cosim.modern_deepep_runtime import ModernDeepEPRuntime
from moe_ub_cosim.modern_deepep_windows import ModernDeepEPWindowRuntime
from moe_ub_cosim.modern_native_wakeups import conditions, satisfied

ROOT = Path(__file__).resolve().parents[2]


def config():
    return json.loads((ROOT/'examples/modern_deepep_partial_order/input.json').read_text())


def drain(runtime, held):
    # Explicit test-only declarations, never a provider or time prediction.
    while ready := [event for event in runtime.ready() if event not in held]:
        runtime.fire(ready[0])


def controller():
    # A remote write has been submitted; target application and source
    # completion are distinct prerequisites of a later local event.
    events = {
        'write.issued': dict(kind='issued', operation='write'),
        'write.visible': dict(kind='visible', operation='write'),
        'write.local_complete': dict(kind='local_complete', operation='write'),
        'compute': dict(kind='expert_stage_done'),
    }
    pending = {'write.visible': [], 'write.local_complete': [],
               'compute': ['write.visible', 'write.local_complete']}
    return SimpleNamespace(runtime=SimpleNamespace(pending_dependencies=lambda: deepcopy(pending),
                               ready=lambda: ['write.visible', 'write.local_complete']),
                           events=events, operations={'write': dict(events={'issued': 'write.issued'})},
                           waiting={'write.issued': 0}, native_nodes=[dict(kind='remote')],
                           tasks={0: {}}, callbacks={(0, 'submitted'): 0}, count_preparations={})


class DependencyWakeupTests(unittest.TestCase):
    def test_dependency_copy_cannot_advance_or_mutate_runtime(self):
        for runtime in (ModernDeepEPRuntime(config()), ModernDeepEPWindowRuntime(config())):
            before = runtime.snapshot()
            view = runtime.pending_dependencies()
            self.assertEqual({name for name, deps in view.items() if not deps}, set(runtime.ready()))
            view.clear()
            self.assertEqual(runtime.snapshot(), before)
            name = runtime.ready()[0]
            runtime.fire(name)
            self.assertNotIn(name, runtime.pending_dependencies())

    def test_dispatch_cross_destination_slot_order_is_exposed(self):
        runtime = ModernDeepEPRuntime(config())
        issue = 'm0.dispatch.data.s0.t1.d1.issued'
        held = 'm0.slot.s0.d2.t1'
        drain(runtime, {held})
        self.assertIn(held, runtime.pending_dependencies()[issue])
        runtime.fire(held)
        self.assertNotIn(held, runtime.pending_dependencies()[issue])

    def test_combine_dependency_added_by_actual_slot_assignment(self):
        cfg = config()
        cfg['model']['experts'] = 4
        cfg['execution'].update(ranks=2, num_sms=1, dispatch_warps=2, combine_warps=1,
                                call_contexts=3, requested_contexts=3, local_devices=[[0, 1], [0, 1]])
        cfg['calls'] = [dict(name='two', tokens=[[[2, 3], [2, 3]], []])]
        runtime = ModernDeepEPRuntime(cfg)
        held = 'm0.slot.s0.d1.t0'
        previous_issue = 'm0.combine.data.s1.d0.t1.issued'
        later_reduction = 'm0.combine.s1.d0.t0.reduced'
        self.assertNotIn(previous_issue, runtime.pending_dependencies()[later_reduction])
        drain(runtime, {held})
        runtime.fire(held)
        drain(runtime, {previous_issue})
        self.assertIn(previous_issue, runtime.pending_dependencies()[later_reduction])
        runtime.fire(previous_issue)
        self.assertNotIn(previous_issue, runtime.pending_dependencies()[later_reduction])

    def test_event_policy_does_not_join_visibility_with_source_completion(self):
        c = controller()
        before = deepcopy(c.callbacks)
        groups = conditions(c)
        self.assertEqual(groups, [[dict(operation=0, event='applied')], [dict(operation=0, event='completed')]])
        self.assertTrue(satisfied(groups, {(0, 'applied')}))
        self.assertEqual(c.callbacks, before)

    def test_submission_policy_waits_for_both_actual_conditions(self):
        groups = conditions(controller(), goal='submissions')
        self.assertEqual(groups, [[dict(operation=0, event='applied'), dict(operation=0, event='completed')]])
        self.assertFalse(satisfied(groups, {(0, 'applied')}))
        self.assertFalse(satisfied(groups, {(0, 'completed')}))
        self.assertTrue(satisfied(groups, {(0, 'applied'), (0, 'completed')}))
        self.assertFalse(satisfied([], {(0, 'completed')}))

    def test_future_unsubmitted_work_is_not_a_callback_condition(self):
        c = controller()
        c.waiting.clear()
        self.assertEqual(conditions(c, goal='submissions'), [])

    def test_inconsistent_callback_reference_and_unknown_goal_rejected(self):
        c = controller()
        c.tasks.clear()
        with self.assertRaisesRegex(ValueError, 'unsubmitted'):
            conditions(c)
        with self.assertRaisesRegex(ValueError, 'unknown wakeup goal'):
            conditions(controller(), goal='predict_completion')

    def test_archive_rejects_six_unsafe_forms_before_writing(self):
        import hashlib
        import io
        import tarfile
        import tempfile
        from fixtures.dependency_wakeup.checkpoint import restore, SCHEMA
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            for kind in ('parent', 'absolute', 'duplicate', 'symlink', 'prefix', 'hash'):
                with self.subTest(kind=kind):
                    name = '../escape' if kind == 'parent' else '/escape' if kind == 'absolute' else 'file'
                    identities = {name: dict(bytes=1, sha256=hashlib.sha256(b'a').hexdigest())}
                    if kind == 'hash':
                        identities[name]['sha256'] = '0'*64
                    archive = directory/(kind+'.tar.gz')
                    with tarfile.open(archive, 'x:gz') as output:
                        member = tarfile.TarInfo(name)
                        member.size = 1
                        if kind == 'symlink':
                            member.type, member.linkname, member.size = tarfile.SYMTYPE, '/outside', 0
                        output.addfile(member, io.BytesIO(b'a') if member.isfile() else None)
                        if kind == 'duplicate':
                            output.addfile(member, io.BytesIO(b'a'))
                        if kind == 'prefix':
                            child = name+'/child'
                            member = tarfile.TarInfo(child)
                            member.size = 1
                            output.addfile(member, io.BytesIO(b'a'))
                            identities[child] = identities[name]
                        data = json.dumps(dict(schema_version=SCHEMA, files=identities)).encode()
                        member = tarfile.TarInfo('manifest.json')
                        member.size = len(data)
                        output.addfile(member, io.BytesIO(data))
                    extracted = directory/(kind+'-restored')
                    with self.assertRaises(AssertionError):
                        restore(archive, extracted)
                    self.assertFalse(extracted.exists())


if __name__ == '__main__':
    unittest.main()
