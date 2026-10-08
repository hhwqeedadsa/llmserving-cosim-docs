"""Frozen additional information and sampling semantics; no internal readers."""
from .observation_spec import CONDITIONS, COUNT_FIELDS, QUEUE_FIELDS, END_NS, GRIDS, grid_id, windows

CONDITION = 'counts-queues-send-state'
STATE_CONDITIONS = (*CONDITIONS, CONDITION)
STATE_FIELDS = ('ready_send_count', 'active_send_count', 'send_slot_capacity')
STATES = ('full_pending', 'other_pending', 'no_pending', 'undetermined')
SAMPLING = 'last_update_at_or_before_window_start_after_all_same_time_updates'
ROLES = {3: 'reference', 4: 'holdout'}


def classify(values):
    ready, active, capacity = values
    if capacity == 0:
        return 'undetermined'
    if ready == 0:
        return 'no_pending'
    return 'full_pending' if active == capacity else 'other_pending'
