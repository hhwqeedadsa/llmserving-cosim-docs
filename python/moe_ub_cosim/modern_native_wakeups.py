"""Callback frontiers for optional modern native next-event wakeups.

A frontier is an OR of conjunctions of actual transitions of already submitted
nodes. It predicts no time or future callback. Drain and control alarms remain
unconditional wakeups. Submission frontiers can defer frontend bookkeeping;
The native adapter uses singleton event frontiers only. Submission frontiers
remain offline candidates; submission equivalence is not ns-3 order equivalence.
"""
from .deepep_plan import require


LOCAL_EVENTS = frozenset(('token_prepared', 'local_expert_reduction_done',
                         'expert_stage_done', 'counts_ready', 'barrier_ready'))
OP_EVENTS = frozenset(('issued', 'accepted', 'visible', 'local_complete'))


def conditions(controller, *, goal='events'):
    """Inspect a quiescent controller; return minimal callback conjunctions.

    ``events`` preserves prompt processing of each enabled modern event.
    ``submissions`` may defer intermediate events until a new native node can
    be submitted. A future unsubmitted operation blocks recursion: it cannot
    be used as a wake condition, even if its symbolic dependencies are known.
    """
    require(goal in ('events', 'submissions'), 'unknown wakeup goal')
    pending = controller.runtime.pending_dependencies()
    memo, visiting = {}, set()

    def callback(identity, event):
        require(identity in controller.tasks, 'wakeup references unsubmitted native work')
        return set() if (identity, event) in controller.callbacks else {(identity, event)}

    def intrinsic(name):
        event = controller.events[name]
        kind = event['kind']
        if kind in OP_EVENTS:
            issued = controller.operations[event['operation']]['events']['issued']
            if issued not in controller.waiting:
                return None  # It must first be submitted by a future advance.
            native = controller.waiting[issued]
            local = controller.native_nodes[native]['kind'] == 'local'
            transition = ('local_started' if local else 'submitted') if kind == 'issued' else (
                'completed' if local or kind == 'local_complete' else 'applied')
            return callback(native, transition)
        if name in controller.waiting:
            return callback(controller.waiting[name], 'completed')
        return None if kind in LOCAL_EVENTS else set()

    def prerequisites(name):
        result = set()
        for parent in pending[name]:
            required = needed(parent)
            if required is None:
                return None
            result.update(required)
        return result

    def needed(name):
        if name not in pending:
            return set()
        if name in memo:
            return memo[name]
        require(name not in visiting, 'cyclic wakeup prerequisites')
        visiting.add(name)
        own, parents = intrinsic(name), prerequisites(name)
        value = None if own is None or parents is None else own | parents
        visiting.remove(name)
        memo[name] = value
        return value

    groups = []
    if goal == 'events':
        for name in controller.runtime.ready():
            event = controller.events[name]
            if event['kind'] == 'issued' and name not in controller.waiting:
                op = controller.operations[event['operation']]
                key = op['call'], op['src']
                require(op['kind'] in ('rank_count', 'expert_count_block') and
                        key in controller.count_preparations, 'controller is not quiescent')
                value = callback(controller.count_preparations[key], 'completed')
            else:
                value = needed(name)
            require(value, 'enabled event has no missing native callback')
            groups.append(frozenset(value))
    else:
        for name in pending:
            event = controller.events[name]
            if name in controller.waiting or event['kind'] not in LOCAL_EVENTS | {'issued'}:
                continue
            value = prerequisites(name)
            if value is None:
                continue
            if event['kind'] == 'issued':
                op = controller.operations[event['operation']]
                key = op['call'], op['src']
                if op['kind'] in ('rank_count', 'expert_count_block') and key in controller.count_preparations:
                    value |= callback(controller.count_preparations[key], 'completed')
            require(value, 'unscheduled native task is already enabled')
            groups.append(frozenset(value))
    # A subset enables at least one action sooner than any strict superset.
    ordered = sorted(set(groups), key=lambda g: (len(g), sorted(g)))
    minimal = []
    for group in ordered:
        if not any(previous <= group for previous in minimal):
            minimal.append(group)
    return [[dict(operation=i, event=e) for i, e in sorted(group)] for group in minimal]


def satisfied(groups, occurred):
    """Pure OR-of-AND predicate; an empty frontier never triggers a wakeup."""
    return any(all((row['operation'], row['event']) in occurred for row in group) for group in groups)
