"""Independently verify selected train against raw bytes, rates and source NIC."""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
from experiments.reordering_baseline.stage import load, save


def check(result, reported):
    t = result['persistent_transport']; w = next(x for x in t['work'] if x['id'] == 99)
    st = next(x for x in t['streams'] if x['id'] == w['stream'])
    sources = [dict(zip(t['source_packet_columns'], x)) for x in t['source_packets']]
    sent = [x for x in sources if x['stream'] == w['stream'] and w['begin'] <= x['sequence'] < w['end']]
    assert len(sent) == 29 and sum(x['payload_bytes'] for x in sent) == w['bytes'] == 57344
    assert sent[0]['sequence'] == w['begin'] and sent[-1]['sequence']+sent[-1]['payload_bytes'] == w['end']
    assert all(x['sequence']+x['payload_bytes'] == y['sequence'] for x, y in zip(sent, sent[1:]))
    assert sent[0]['time_ns'] == w['ready_ns'] == reported['first_tx_ns'] == reported['ready_ns']
    assert reported['accepted_ns'] == w['accepted_ns'] and reported['acked_ns'] == w['acked_ns']
    assert reported['last_tx_ns'] == sent[-1]['time_ns'] and reported['packet_count'] == len(sent)
    rates = t['source_rate_capture']; samples = [dict(zip(rates['columns'], x)) for x in rates['samples']]
    by_uid = {x['packet_uid']: x for x in samples if x['event'] == 0}
    c = result['packet_capture']; events = [dict(zip(c['columns'], x)) for x in c['events']]
    port = next(p for p in result['ports'] if (p['node'], p['port']) == (st['src'], st['source_nic']))
    assert port == reported['source_port'] and port['pfc_pause'] == port['pfc_resume'] == 0
    physical = [x for x in events if x['event'] == 0 and (x['node'], x['port']) == (port['node'], port['port'])]
    intervals = [(e['time_ns'], e['time_ns']+round(e['model_bytes']*8*10**9/port['rate_bps']), e) for e in physical]
    assert all(a[1] <= b[0] for a, b in zip(intervals, intervals[1:]))
    assert len(reported['gaps']) == len(sent)-1
    sums = Counter()
    for x, y, g in zip(sent, sent[1:], reported['gaps']):
        before, after = by_uid[x['packet_uid']], by_uid[y['packet_uid']]
        assert after['time_ns'] == y['time_ns'] and before['time_ns'] == x['time_ns']
        assert after['sequence'] == y['sequence'] and before['sequence'] == x['sequence']
        # In these trains the only rate change is an RP recovery timer. Check
        # every transition rather than treating a snapshot as continuous rate.
        changes = [e for e in t['dcqcn_reference']['events'] if e['stream'] == st['id'] and
                   x['time_ns'] < e['time_ns'] <= y['time_ns'] and
                   e['kind'] in ('cnp_received', 'rp_timer', 'rp_bytes')]
        assert all(e['kind'] == 'rp_timer' for e in changes)
        assert after['rate_bps'] == (changes[-1]['rate_bps'] if changes else before['rate_bps'])
        assert after['next_avail_ns'] == x['time_ns']+round(before['model_bytes']*8*10**9/after['rate_bps'])
        due = after['next_avail_ns']; end = y['time_ns']; owners = Counter()
        assert g['packet_uid'] == y['packet_uid'] and g['previous_uid'] == x['packet_uid']
        assert (g['previous_tx_ns'], g['tx_ns'], g['sampled_next_avail_ns']) == (x['time_ns'], end, due)
        for start, stop, e in intervals:
            if stop <= due or start >= end: continue
            n = min(stop, end)-max(start, due)
            owners[str(e['message_id']) if e['ip_protocol'] == 17 else 'control:'+str(e['ip_protocol'])] += n
        assert dict(owners) == g['physical_service_owners_ns']
        values = dict(adjusted_eligibility_interval_ns=due-x['time_ns'],
            after_eligibility_ns=end-due, physical_service_after_eligibility_ns=sum(owners.values()),
            idle_after_eligibility_ns=end-due-sum(owners.values()))
        assert all(g[k] == v >= 0 for k, v in values.items()); sums.update(values)
    assert reported['source_train_span_ns'] == sent[-1]['time_ns']-sent[0]['time_ns']
    assert reported['source_train_span_ns'] == sums['adjusted_eligibility_interval_ns']+sums['after_eligibility_ns']
    assert reported['adjusted_eligibility_intervals_ns'] == sums.pop('adjusted_eligibility_interval_ns')
    assert all(reported[k] == v for k, v in sums.items())
    assert reported['first_rate_sample'] == by_uid[sent[0]['packet_uid']]
    assert reported['last_rate_sample'] == by_uid[sent[-1]['packet_uid']]
    expected = [e for e in t['dcqcn_reference']['events'] if e['stream'] == st['id'] and
                e['kind'] in ('cnp_received', 'rp_timer', 'rp_alpha', 'rp_bytes') and e['time_ns'] <= sent[-1]['time_ns']]
    assert reported['reference_transitions'] == expected


def main():
    p = argparse.ArgumentParser()
    for k in ('network', 'analysis', 'output'): p.add_argument('--'+k, type=Path, required=True)
    a = p.parse_args(); assert not a.output.exists(); report = load(a.analysis)
    raw = {name: load(a.network/name/'result.json') for name in report['cases']}
    for name, result in raw.items(): check(result, report['cases'][name])
    rejected = []
    for field in ('sampled_next_avail_ns', 'idle_after_eligibility_ns', 'physical_service_after_eligibility_ns'):
        bad = deepcopy(report['cases']['live-instant']); bad['gaps'][0][field] += 1
        try: check(raw['live-instant'], bad)
        except AssertionError: rejected.append(field)
        else: raise AssertionError('mutation accepted: '+field)
    save(a.output, dict(passed=True, cases=2, consecutive_gaps=56,
        source_train_bytes_and_contiguous_no_retry=True, no_initial_admission_delay=True,
        adjusted_eligibility_deadlines_independently_rebuilt=True,
        physical_serialization_and_idle_partition=True, pfc_pause_events=0,
        mutations_rejected=rejected, network_executions=0,
        warning='idle cause needs scheduler membership/wakeup evidence; not a hardware or unique application-cause claim'))


if __name__ == '__main__': main()
