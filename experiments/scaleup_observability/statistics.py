"""Research summaries with explicit time/byte meanings; no cause labels."""
from bisect import bisect_right
import math

from .common import require


def distribution(values):
    values = sorted(values)
    if not values:
        return dict(count=0, min=None, median=None, p95=None, p99=None, max=None, mean=None)
    def q(f):
        return values[max(0, math.ceil(len(values)*f)-1)]
    return dict(count=len(values), min=values[0], median=q(.5), p95=q(.95), p99=q(.99),
                max=values[-1], mean=sum(values)/len(values))


def merge_intervals(intervals):
    merged = []
    for a, b in sorted(intervals):
        require(a <= b, 'Inverted interval')
        if a == b:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(b, merged[-1][1])
        else:
            merged.append([a,b])
    return merged


def queue_summary(events, busy, end):
    require(end > 0 and events and events[0]['time_ns'] == 0 and events[0]['event_kind'] == 'initial',
            'Detailed queue history with time-zero initial state required')
    require(events[-1]['event_kind'] == 'final' and events[-1]['time_ns'] >= end,
            'Queue history does not cover execution')
    require(all(a['time_ns'] <= b['time_ns'] for a,b in zip(events, events[1:])), 'Unordered queue history')
    busy = merge_intervals((max(0,a), min(end,b)) for a,b in busy if a < end and b > 0)
    result = dict(voq_byte_ns=0, fifo_byte_ns=0, voq_peak_bytes=0, fifo_peak_bytes=0,
                  link_busy_ns=sum(b-a for a,b in busy), queue_nonzero_ns=0)
    gaps = []
    ends = [b for _,b in busy]
    for index, row in enumerate(events):
        a = row['time_ns']
        if a >= end:
            break
        b = min(end, events[index+1]['time_ns'] if index+1 < len(events) else end)
        for kind in ('voq','fifo'):
            value = row[kind+'_waiting_bytes']
            require(type(value) is int and value >= 0, 'Invalid queue bytes')
            result[kind+'_byte_ns'] += value*(b-a)
            result[kind+'_peak_bytes'] = max(result[kind+'_peak_bytes'], value)
        if row['voq_waiting_bytes']+row['fifo_waiting_bytes'] == 0 or a == b:
            continue
        result['queue_nonzero_ns'] += b-a
        cursor = a
        for x,y in busy[bisect_right(ends,a):]:
            if x >= b:
                break
            if x > cursor:
                gaps.append((cursor,min(x,b)))
            cursor = max(cursor,y)
            if cursor >= b:
                break
        if cursor < b:
            gaps.append((cursor,b))
    gaps = merge_intervals(gaps)
    result.update(queued_without_transmission_ns=sum(b-a for a,b in gaps),
                  queued_without_transmission_max_ns=max((b-a for a,b in gaps), default=0),
                  link_busy_fraction=result['link_busy_ns']/end,
                  voq_mean_bytes=result['voq_byte_ns']/end, fifo_mean_bytes=result['fifo_byte_ns']/end)
    return result, gaps


def dependency_rows(graph, execution, ready_ns):
    """Dependency-ready is reconstructed, not a recorded scheduler-ready event."""
    rank = graph['rank_id']
    expected = {n['node_id'] for n in graph['nodes']}
    actual = {node for r,node in execution if r == rank}
    require(expected == actual, 'Node execution coverage differs from plan')
    rows = []
    for node in graph['nodes']:
        timing = execution[rank,node['node_id']]
        require(timing['kind'] == node['kind'], 'Node kind mismatch')
        deps = node['deps']
        require(all(d in expected for d in deps), 'Unknown dependency')
        ready = max([ready_ns]+[execution[rank,d]['end_ns'] for d in deps])
        start, end = timing['start_ns'], timing['end_ns']
        require(ready <= start <= end, 'Execution violates explicit dependency')
        if node['kind'] == 'COMP':
            require(end-start == node['duration_ns'], 'Compute duration differs from declared workload')
        rows.append(dict(rank_id=rank, node_id=node['node_id'], kind=node['kind'],
            layer_id=node.get('layer_id'), stage=node.get('model_stage', node.get('name')),
            dependency_ready_ns=ready, start_ns=start, end_ns=end,
            after_dependencies_before_start_ns=start-ready, duration_ns=end-start))
    return rows
