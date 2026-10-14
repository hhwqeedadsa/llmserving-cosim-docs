"""Screen sealed normal-run switch observations without restoring simulations.

This researcher tool ranks queue events before consulting business effects.
Adjacent positive windows form an event group, not proven continuous backlog.
"""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import sys

from .consumer_review import Archive
from .duration_bounds import window_bounds
from .organization import zipped_rows
from .workflow import digest, host, read, save


RULE = dict(
    schema_version='issue-network-selection-rule/1',
    sources=['g0-b2-short', 'g0-b4-short', 'g0-b8-short', 'g0-b8-long'],
    candidate_queues=['voq', 'fifo'], max_candidates=2,
    grouping='adjacent positive-area windows on the same directed port and queue',
    ranking=['descending maximum single-window mean waiting bytes',
             'descending event area', 'descending peak waiting bytes',
             'ascending case, switch, port, start'],
    eligible='maximum-area window overlaps a layer 7 or 8 operation envelope',
    selection='one highest-ranked event per queue class; merge if the same resource and interval',
    exclusion='zero-area peaks are instantaneous, not evidence of positive-duration waiting',
    no_business_effect_or_truth_used_for_ranking=True,
    service_bytes_per_ns=50,
    continuous_backlog_inferred=False)


def groups(rows, public):
    """Validate sparse coverage, retain all ports and every positive-area group."""
    from moe_ub_cosim.observation import validate_public_config
    _, configs, logical = validate_public_config(public)
    ports = {(p['switch_id'], p['port_id']): p for p in configs}
    counts = {key: Counter() for key in ports}
    previous = {}; active = {}; events = []; count = 0
    for r in rows:
        key = r['switch_id'], r['port_id']
        if key not in ports:
            raise ValueError('Queue record outside declared coverage')
        p = ports[key]; a = r['local_window_start_ns']; b = r['local_window_end_ns']
        if (a < previous.get(key, p['start_local_ns']) or a >= p['end_local_ns'] or
                (a-p['start_local_ns']) % p['period_ns'] or
                b != min(a+p['period_ns'], p['end_local_ns'])):
            raise ValueError('Unordered, duplicated or unaligned queue window')
        previous[key] = b; count += 1
        for q in ('voq', 'fifo'):
            area = r[q+'_waiting_byte_ns']; peak = r[q+'_waiting_peak_bytes']
            lo, hi = window_bounds(area, peak, b-a)
            c = counts[key]; c[q+'_area'] += area
            c[q+'_peak'] = max(c[q+'_peak'], peak)
            c[q+'_lower_ns'] += lo; c[q+'_upper_ns'] += hi
            c[q+'_instantaneous_peak_windows'] += bool(peak and not area)
            k = key+(q,)
            if k in active and (not area or active[k]['end_ns'] != a):
                events.append(active.pop(k))
            if not area:
                continue
            if k not in active:
                active[k] = dict(switch_id=key[0], port_id=key[1], queue=q,
                    start_ns=a, end_ns=a, area=0, peak=0, positive_windows=0,
                    nonempty_lower_ns=0, nonempty_upper_ns=0,
                    max_window_mean_bytes=-1, peak_window_start_ns=a,
                    left_coverage_truncated=a == p['start_local_ns'])
            e = active[k]; e['end_ns'] = b; e['area'] += area
            e['peak'] = max(e['peak'], peak); e['positive_windows'] += 1
            e['nonempty_lower_ns'] += lo; e['nonempty_upper_ns'] += hi
            if area/(b-a) > e['max_window_mean_bytes']:
                e['max_window_mean_bytes'] = area/(b-a)
                e['peak_window_start_ns'] = a; e['peak_window_end_ns'] = b
    events.extend(active.values())
    for e in events:
        e['right_coverage_truncated'] = e['end_ns'] == ports[e['switch_id'], e['port_id']]['end_local_ns']
        e['peak_drain_equivalent_ns'] = e['peak']/RULE['service_bytes_per_ns']
    return counts, events, count, logical


def rank_key(e):
    return (-e['max_window_mean_bytes'], -e['area'], -e['peak'],
            e['case_id'], e['switch_id'], e['port_id'], e['start_ns'])


def screen(archive, analysis, output):
    archive, analysis, output = Path(archive), Path(analysis), Path(output)
    if output.exists():
        raise ValueError('Output must be new')
    source = Archive(archive); summary = read(analysis/'summary.json')
    names = ['run/observed/'+n for n in ('public_config.json', 'storage.json',
        'records/queue_windows.csv.gz', 'records/port_windows.csv.gz')]
    names += ['run/inputs/observation.json', 'run/metrics/validation.json', 'run/manifest.json']
    data = dict(source.members(names))
    manifest = json.loads(data[names[-1]])
    if (not manifest['normal_exit'] or not json.loads(data[names[-2]])['passed'] or
            summary['public_manifest_sha256'] != source.used[names[-1]]['sha256']):
        raise ValueError('Original execution or analysis binding failed')
    observation = json.loads(data[names[-3]])
    if any(c['offset_ns'] or c['drift_ppb'] for c in observation['clocks']):
        raise ValueError('Expected original zero-offset unit-rate clocks')
    public = json.loads(data[names[0]]); storage = json.loads(data[names[1]])
    if storage['window_encoding'] != 'nonzero_with_declared_zero_windows':
        raise ValueError('Omitted windows are not declared zero')
    ports, events, records, logical = groups(zipped_rows(data[names[2]]), public)
    if (records != storage['fields']['queue_windows.csv']['records'] or
            logical != storage['logical_windows']):
        raise ValueError('Queue coverage/count mismatch')
    packet_records = 0
    for r in zipped_rows(data[names[3]]):
        key = r['switch_id'], r['port_id']
        if key not in ports:
            raise ValueError('Packet counter outside coverage')
        ports[key]['packets'] += r['counted_packets']
        ports[key]['packet_bytes'] += r['counted_packet_bytes']; packet_records += 1
    if packet_records != storage['fields']['port_windows.csv']['records']:
        raise ValueError('Packet coverage/count mismatch')
    totals = Counter()
    for p in ports.values():
        totals.update({k: v for k, v in p.items() if not k.endswith('_peak')})
    for k, original in [('voq_area', 'voq_waiting_byte_ns'), ('fifo_area', 'fifo_waiting_byte_ns'),
                        ('packet_bytes', 'packet_bytes'), ('packets', 'packets')]:
        if totals[k] != summary['network'][original]:
            raise ValueError('New screening disagrees with original aggregate: '+k)
    for e in events:
        e['case_id'] = summary['case_id']
        e['overlapping_operations'] = [dict(layer_id=o['layer_id'], kind=o['kind'], operation_id=o['operation_id'])
            for o in summary['operations'] if e['peak_window_start_ns'] < o['last_finish_ns'] and
            o['first_entry_ns'] < e['peak_window_end_ns']]
        e['eligible'] = any(o['layer_id'] in (7, 8) for o in e['overlapping_operations'])
    events.sort(key=rank_key)
    output.mkdir(parents=True)
    with gzip.open(output/'events.jsonl.gz', 'wt') as stream:
        for e in events:
            stream.write(json.dumps(e, separators=(',', ':'))+'\n')
    save(output/'ports.json', [dict(switch_id=k[0], port_id=k[1], **v) for k, v in sorted(ports.items())])
    result = dict(schema_version='issue-network-screen/1', case_id=summary['case_id'],
        rule=RULE, archive_sha256=source.index['archive_sha256'], archive_members=source.used,
        original_analysis_sha256=digest(analysis/'summary.json'), program_sha256=digest(__file__),
        observed_ports=len(ports), logical_windows=logical, stored_queue_windows=records,
        complete_declared_coverage=True, omitted_declared_windows_are_zero=True,
        unobserved_device_queues_are_not_zero=True, continuous_backlog_reconstructed=False,
        totals=dict(totals), event_groups=len(events),
        top_by_queue={q:[e for e in events if e['queue']==q][:10] for q in ('voq','fifo')},
        eligible_top_by_queue={q:[e for e in events if e['queue']==q and e['eligible']][:10] for q in ('voq','fifo')},
        events_sha256=digest(output/'events.jsonl.gz'), ports_sha256=digest(output/'ports.json'),
        simulation_executed=False)
    save(output/'summary.json', result)
    print(json.dumps(dict(case_id=result['case_id'], ports=len(ports), events=len(events),
                         eligible_top={q: v[:1] for q,v in result['eligible_top_by_queue'].items()})))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('archive', 'analysis', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    a = p.parse_args()
    if a.worker:
        screen(a.archive, a.analysis, a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        r = probe([sys.executable, '-m', __spec__.name, *sys.argv[1:], '--worker'],
                  str(a.output.resolve())+'-resources', **host(extra_roots=[a.output]))
        if r['status'] != 'completed':
            raise SystemExit('Network screening failed: '+r['status'])


if __name__ == '__main__':
    main()
