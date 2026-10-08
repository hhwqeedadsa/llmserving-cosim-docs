"""Sample real public state rows; never derive exposed counters from truth."""
from bisect import bisect_right

from moe_ub_cosim.research_storage import stored_rows
from moe_ub_cosim.send_state import checked_rows

from .common import read, require
from .send_state_spec import END_NS, STATE_FIELDS


def load_states(observed):
    public = read(observed/'public_config.json')
    require(public['schema_version'] == 'moe-ub-observed/4' and public['rank_ids'] == list(range(8)),
            'Actual endpoint send-state capture is required; no reconstructed substitute')
    descriptor = public['send_state']
    require(all(c['start_local_ns'] == 0 and c['end_local_ns'] >= END_NS for c in descriptor['coverage']),
            'Endpoint state does not cover the declared study interval')
    times = {r: [] for r in range(8)}; values = {r: [] for r in range(8)}
    for row in checked_rows(stored_rows(observed, 'endpoint_send_state.jsonl'), descriptor, list(range(8))):
        rank = row['rank_id']
        times[rank].append(row['time_local_ns'])
        values[rank].append([row[k] for k in STATE_FIELDS])
    return times, values


def snapshot(states, time):
    times, values = states
    result = []
    for rank in range(8):
        index = bisect_right(times[rank], time)-1
        require(index >= 0, 'Missing initial endpoint state')
        result.append(values[rank][index])
    return result


def point_truth(ranks, time):
    # Only the private preparation path calls this independent reconstruction.
    result = []
    for rank in range(8):
        pending, full = ranks[rank]
        result.append('full_pending' if any(a <= time < b for a,b in full) else
                      'other_pending' if any(a <= time < b for a,b in pending) else 'no_pending')
    return result
