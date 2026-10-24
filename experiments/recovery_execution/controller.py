"""Policy consumes receiver applied/local callbacks only, never evaluation truth."""
import argparse
import json
from pathlib import Path

from moe_ub_cosim.transaction_session import NativeTransactionSession

SIZE, STRIDE, RECV, CANONICAL, SOURCE, TIMER = 14336, 16384, 4096, 262144, 1048576, 50


def put(owner, retry=False):
    return dict(id=(100 if retry else 0)+owner, kind='remote',
                connection=owner+(4 if retry else 0), verb='put', key=7,
                address=RECV+(owner+(4 if retry else 0))*STRIDE,
                source=dict(key=8, address=SOURCE, bytes=SIZE),
                after=[dict(operation=TIMER, event='completed')] if retry else [])


class ReceiverPolicy:
    def __init__(self, config):
        assert set(config) == {'owners', 'strategy'}
        self.owners = config['owners']
        assert self.owners == sorted(set(self.owners)) and all(0 <= o < 4 for o in self.owners)
        self.strategy = config['strategy']
        assert self.strategy in ('whole_token', 'owner_retention')
        self.applied, self.copy_done = {}, {}
        self.first, self.timed_out, self.copy_submitted = True, False, False
        self.selected = None
        self.ready_ns, self.covered_ns = None, None
        self.recovery, self.decisions = None, 0

    def decide(self, view):
        assert set(view) == {'decision_id', 'now_ns', 'events'}
        assert view['decision_id'] == self.decisions
        self.decisions += 1
        now = view['now_ns']
        for row in view['events']:
            op, event, when = row['operation'], row['event'], row['time_ns']
            assert when <= now
            if op in self.owners or op-100 in self.owners:
                assert event == 'applied' and op not in self.applied
                self.applied[op] = when
            elif op == TIMER:
                assert event in ('local_admitted', 'local_started', 'completed')
                self.timed_out |= event == 'completed'
            elif op-200 in self.owners:
                assert event in ('local_admitted', 'local_started', 'completed')
                if event == 'completed':self.copy_done[op-200] = when
            else:raise ValueError('unknown receiver observation')
        submissions, action = [], None
        if self.first:
            assert not view['events'] and now == 0
            self.first = False
            submissions = [put(o) for o in self.owners]
            submissions.append(dict(id=TIMER, kind='local', rank=4, duration_ns=100000, after=[]))
            action = dict(kind='initial')
        elif self.selected is None:
            missing = [o for o in self.owners if o not in self.applied]
            if not missing:
                self.selected = {o: o for o in self.owners}
                self.recovery = dict(time_ns=now, missing_original_operations=[],
                                     generic_operation_plan=[], replay_owners=[])
                action = dict(kind='original_coverage', **self.recovery)
            elif self.timed_out:
                replay = self.owners if self.strategy == 'whole_token' else missing
                self.selected = {o: 100+o if o in replay else o for o in self.owners}
                self.recovery = dict(time_ns=now, missing_original_operations=missing,
                                     generic_operation_plan=missing, replay_owners=replay)
                submissions = [put(o, True) for o in replay]
                action = dict(kind='retry', **self.recovery)
        if self.selected is not None and not self.copy_submitted and all(op in self.applied for op in self.selected.values()):
            self.covered_ns = max(self.applied[op] for op in self.selected.values())
            for owner, op in self.selected.items():
                offset = (owner+(4 if op >= 100 else 0))*STRIDE
                submissions.append(dict(id=200+owner, kind='local', rank=4, duration_ns=0,
                    copy=dict(source=dict(key=7, address=RECV+offset, bytes=SIZE),
                              target=dict(key=9, address=CANONICAL+owner*STRIDE)),
                    after=[dict(operation=op, event='applied')]))
            self.copy_submitted = True
            action = dict(kind='canonical_copy', selection=self.selected,
                          coverage_ns=self.covered_ns, previous_action=action)
        if len(self.copy_done) == len(self.owners) and self.ready_ns is None:
            self.ready_ns = max(self.copy_done.values())
            assert not submissions
            action = dict(kind='fragment_ready', time_ns=self.ready_ns)
        return submissions, action

    def summary(self):
        return dict(strategy=self.strategy, owners=self.owners, recovery=self.recovery,
                    selected=self.selected, coverage_ns=self.covered_ns,
                    fragment_ready_ns=self.ready_ns, decisions=self.decisions,
                    fragment_only=True, full_inference=False)


def filtered(event, owners):
    remote = set(owners) | {100+o for o in owners}
    local = {TIMER} | {200+o for o in owners}
    rows = [r for r in event['events'] if
            (r['operation'] in remote and r['event'] == 'applied') or r['operation'] in local]
    return dict(decision_id=event['decision_id'], now_ns=event['now_ns'], events=rows)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('topology', 'protocol', 'initial', 'controller', 'backend', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    initial = json.loads(a.initial.read_text())
    policy = ReceiverPolicy(json.loads(a.controller.read_text()))
    a.output.mkdir(exist_ok=False)
    observed = a.output/'observed'; observed.mkdir()
    session = NativeTransactionSession(a.topology, a.protocol, initial, a.backend, a.output)
    terminal = None
    try:
        with (observed/'decisions.jsonl').open('w', buffering=1) as history:
            while True:
                try:event = session.receive()
                except ValueError as error:
                    if str(error) != 'Scale-Out topology: native transaction session failed; inspect partial result and native.stderr.log':
                        raise
                    if session.child.poll() != 1 or not (a.output/'result.json').is_file():raise
                    terminal = 'native_partial_exit_1'
                    break
                if event is None:raise ValueError('unexpected native success/Close')
                view = filtered(event, policy.owners)
                submissions, action = policy.decide(view)
                history.write(json.dumps(dict(view=view, action=action, submissions=submissions), sort_keys=True)+'\n')
                session.commit(submissions, finish=False)
    finally:
        session.close()
        (observed/'summary.json').write_text(json.dumps(dict(policy.summary(), terminal=terminal,
                 native_exit_code=session.child.returncode), indent=2, sort_keys=True)+'\n')
    assert terminal == 'native_partial_exit_1' and policy.ready_ns is not None, 'output fragment incomplete'
    print(json.dumps(policy.summary(), sort_keys=True))


if __name__ == '__main__':
    main()
