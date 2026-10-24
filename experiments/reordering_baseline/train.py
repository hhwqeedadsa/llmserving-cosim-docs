"""Posthoc source-train accounting from public captures; no truth or rerun."""
import argparse
from collections import Counter
from pathlib import Path
from experiments.reordering_baseline.stage import load, save


def summarize(result):
    transport = result['persistent_transport']
    work = next(w for w in transport['work'] if w['id'] == 99)
    stream = next(s for s in transport['streams'] if s['id'] == work['stream'])
    rates = transport['source_rate_capture']
    samples = [dict(zip(rates['columns'], row)) for row in rates['samples']]
    train = [r for r in samples if r['event'] == 0 and r['stream'] == stream['id']
             and work['begin'] <= r['sequence'] < work['end']]
    capture = result['packet_capture']
    packets = [dict(zip(capture['columns'], row)) for row in capture['events']]
    port = next(p for p in result['ports'] if (p['node'], p['port']) ==
                (stream['src'], stream['source_nic']))
    tx = [e for e in packets if e['event'] == 0 and
          (e['node'], e['port']) == (port['node'], port['port'])]
    service = [(e['time_ns'], e['time_ns'] + round(e['model_bytes']*8*10**9/port['rate_bps']), e)
               for e in tx]
    gaps = []
    for left, right in zip(train, train[1:]):
        due = right['next_avail_ns']; stop = right['time_ns']; owners = Counter()
        assert left['time_ns'] < due <= stop
        for start, end, e in service:
            overlap = max(0, min(stop, end)-max(due, start))
            if overlap:
                owners[str(e['message_id']) if e['ip_protocol'] == 17 else
                       'control:'+str(e['ip_protocol'])] += overlap
        slack = stop-due; busy = sum(owners.values()); assert busy <= slack
        gaps.append(dict(previous_uid=left['packet_uid'], packet_uid=right['packet_uid'],
            previous_tx_ns=left['time_ns'], tx_ns=stop, sampled_next_avail_ns=due,
            adjusted_eligibility_interval_ns=due-left['time_ns'],
            after_eligibility_ns=slack, physical_service_after_eligibility_ns=busy,
            idle_after_eligibility_ns=slack-busy, physical_service_owners_ns=dict(owners)))
    events = transport['dcqcn_reference']['events']
    reference = [e for e in events if e['stream'] == stream['id'] and
                 e['kind'] in ('cnp_received', 'rp_timer', 'rp_alpha', 'rp_bytes') and
                 e['time_ns'] <= train[-1]['time_ns']]
    return dict(work=99, stream=stream['id'], ready_ns=work['ready_ns'],
        first_tx_ns=train[0]['time_ns'], last_tx_ns=train[-1]['time_ns'],
        accepted_ns=work['accepted_ns'], acked_ns=work['acked_ns'],
        packet_count=len(train), bytes=work['bytes'],
        source_train_span_ns=train[-1]['time_ns']-train[0]['time_ns'],
        adjusted_eligibility_intervals_ns=sum(g['adjusted_eligibility_interval_ns'] for g in gaps),
        after_eligibility_ns=sum(g['after_eligibility_ns'] for g in gaps),
        physical_service_after_eligibility_ns=sum(g['physical_service_after_eligibility_ns'] for g in gaps),
        idle_after_eligibility_ns=sum(g['idle_after_eligibility_ns'] for g in gaps),
        source_port=port, first_rate_sample=train[0], last_rate_sample=train[-1],
        reference_transitions=reference, gaps=gaps)


def main():
    p = argparse.ArgumentParser()
    for k in ('network', 'output'): p.add_argument('--'+k, type=Path, required=True)
    a = p.parse_args(); assert not a.output.exists()
    save(a.output, dict(schema_version='reordering-source-train/1',
        selection='posthoc work99, on the retained tied final application frontier',
        cases={name: summarize(load(a.network/name/'result.json'))
               for name in ('live-delayed', 'live-instant')},
        scope='exact disjoint time accounting, not independently removable causal components; idle includes unobserved scheduler wakeups',
        warning='declared uncalibrated DCQCN QP rates are not measured throughput; no unique attribution of idle or application loss'))


if __name__ == '__main__': main()
