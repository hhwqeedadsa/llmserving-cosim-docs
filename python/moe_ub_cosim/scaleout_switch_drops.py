"""Passive switch drop details; counters and selected detail have distinct scope."""
from collections import Counter

from .scaleout_topology import fields, integer, require
from .scaleout_limits import limits

REASONS = dict(ingress_admission=1, egress_admission=2, no_route=3, link_down=4)
COLUMNS = ['time_ns', 'node', 'ingress_port', 'egress_port', 'priority', 'reason',
           'packet_uid', 'message_id', 'ip_protocol', 'sequence', 'model_bytes', 'data_payload_bytes']


def validate_options(config):
    if 'switch_drop_capture' not in config:
        return
    c = config['switch_drop_capture']
    fields(c, 'nodes start_ns end_ns max_events', 'switch drop capture')
    require(isinstance(c['nodes'], list) and len(c['nodes']) <= 256, 'invalid switch drop nodes')
    for node in c['nodes']:
        integer(node, 'switch drop node', 0, 1279)
    require(len(set(c['nodes'])) == len(c['nodes']), 'duplicate switch drop node')
    integer(c['start_ns'], 'switch drop start', 0, limits(config)['max_sim_ns']-1)
    integer(c['end_ns'], 'switch drop end', c['start_ns']+1, limits(config)['max_sim_ns'])
    integer(c['max_events'], 'switch drop max_events', 1, 100000)


def validate_topology(topology, config):
    validate_options(config)
    if 'switch_drop_capture' not in config:
        return
    nodes = {n['id']: n for n in topology['nodes']}
    require(all(n in nodes and nodes[n]['kind'] != 'gpu' for n in config['switch_drop_capture']['nodes']),
            'switch drop selection must contain actual switches')


def analyze(result, topology, protocol, *, require_complete=False):
    """Check saved records without generating events or guessing drop causes.

    Independent packet/queue conservation requires complete packet capture and
    is a separate check; this validates metadata, bounds and summary consistency.
    """
    validate_topology(topology, protocol)
    if 'switch_drop_capture' not in protocol:
        require('switch_drop_capture' not in result, 'undeclared switch drop capture')
        return None
    c = protocol['switch_drop_capture']
    d = result.get('switch_drop_capture', {})
    require(d.get('schema_version') == 'native-switch-drop-capture/1' and d.get('selection') == c,
            'missing or mismatched switch drop capture')
    require(d.get('columns') == COLUMNS and d.get('reason_codes') == REASONS, 'switch drop schema differs')
    totals = d.get('all_switch_reason_counts')
    require(isinstance(totals, list) and len(totals) == 4, 'missing switch drop reason counts')
    for n in totals:
        integer(n, 'switch drop count', 0, 2**63-1)
    events = d.get('events')
    require(isinstance(events, list) and len(events) <= c['max_events'], 'switch drop detail exceeds bound')
    counts = Counter()
    nodes = {n['id']: n for n in topology['nodes']}
    ports = {n: set() for n in nodes}
    for link in topology['links']:
        for side in ('src', 'dst'):
            ports[link[side]].add(link[side+'_port'])
    previous = -1
    stop = result['finish_ns']
    for row in events:
        require(isinstance(row, list) and len(row) == 12 and all(type(x) is int for x in row),
                'malformed switch drop row')
        time, node, ingress, egress, priority, reason, uid, identity, proto, sequence, size, payload = row
        require(previous <= time <= stop and c['start_ns'] <= time < c['end_ns'], 'switch drop outside detail window')
        require(node in nodes and nodes[node]['kind'] != 'gpu' and (not c['nodes'] or node in c['nodes']),
                'switch drop outside switch selection')
        require(ingress == 0 or ingress in ports[node], 'unknown switch drop ingress')
        require(0 <= priority <= 7 and reason in REASONS.values() and uid >= 0 and identity >= -1 and
                0 <= proto <= 255 and sequence >= -1 and size > 0 and 0 <= payload <= size,
                'invalid switch drop fields')
        require((reason == REASONS['no_route'] and egress == -1) or
                (reason != REASONS['no_route'] and egress in ports[node]), 'switch drop reason/egress differs')
        require(reason not in (1, 2) or priority != 0, 'priority zero bypasses MMU admission')
        counts[reason] += 1
        previous = time
    observations, omitted = d.get('observations'), d.get('omitted_events')
    integer(observations, 'switch drop observations', len(events), 2**63-1)
    integer(omitted, 'switch drop omitted', 0, 2**63-1)
    require(type(d.get('complete')) is bool and omitted == observations-len(events) and
            d['complete'] == (omitted == 0), 'switch drop completeness differs')
    require(all(counts[i+1] <= totals[i] for i in range(4)) and observations <= sum(totals),
            'switch drop counters below detail')
    full_scope = not c['nodes'] and c['start_ns'] == 0 and c['end_ns'] > stop
    if full_scope:
        require(sum(totals) == observations, 'full-window switch drop count differs')
        if d['complete']:
            require([counts[i+1] for i in range(4)] == totals, 'full-window reason counts differ')
    require(not require_complete or d['complete'], 'truncated switch drop detail')
    return dict(complete=d['complete'], selected_observations=observations, saved_events=len(events),
                all_switch_drops=sum(totals), all_switch_reason_counts=totals,
                scope='all-switch aggregate counters plus selected detail; not device/injection loss totals')


def attach(report, result, topology, protocol):
    value = analyze(result, topology, protocol, require_complete=True)
    if value is not None:
        report['switch_drops'] = value
    return report
