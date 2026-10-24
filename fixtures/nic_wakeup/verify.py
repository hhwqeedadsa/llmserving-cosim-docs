"""Independent byte/physical/timer acceptance from preserved outputs only."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from fixtures.nic_wakeup.stage import load, save
from fixtures.transaction_selective_payload.audit import audit as transaction_audit
from fixtures.nack_grace_edges.wire_fifo import audit as fifo_audit


def packets(result):
    rows = result['frames']; emitted = {}; arrived = {}; service = defaultdict(list)
    for row in rows:
        key = row['node'], row['uid']
        if row['event'] == 0:
            assert key not in emitted; emitted[key] = row
            duration = round(row['bytes']*8*10**9/result['physical_rate_bps'])
            service[row['node']].append((row['time_ns'], row['time_ns']+duration))
        else:
            key = 1-row['node'], row['uid']; assert key in emitted and key not in arrived
            sent = emitted[key]; arrived[key] = row
            assert row['time_ns'] == sent['time_ns']+round(row['bytes']*8*10**9/result['physical_rate_bps'])+result['channel_delay_ns']
            for field in ('bytes', 'protocol', 'sequence', 'port', 'payload_bytes'): assert row[field] == sent[field]
    assert set(emitted) == set(arrived)
    assert all(a[1] <= b[0] for slots in service.values() for a, b in zip(slots, slots[1:]))
    assert Counter(r['protocol'] for r in emitted.values()) == {17: 17, 252: 17}
    payload = [r for r in rows if r['node'] == 0 and r['event'] == 0 and r['protocol'] == 17]
    for port, total in ((20000, 1000), (20001, 16000)):
        parts = [p for p in payload if p['port'] == port]; offset = 0
        for p in parts: assert p['sequence'] == offset; offset += p['payload_bytes']
        assert offset == total
        acknowledgments = [r for r in rows if r['event'] == 1 and r['node'] == 0 and r['protocol'] == 252 and r['port'] == port]
        assert [r['sequence'] for r in acknowledgments] == [p['sequence']+p['payload_bytes'] for p in parts]
        completion = next(c for c in result['completed'] if c['port'] == port)
        assert completion['bytes'] == completion['snd_una'] == total and completion['time_ns'] == acknowledgments[-1]['time_ns']
    return payload


def witness(result, fixed):
    payload = packets(result); qp = [p for p in payload if p['port'] == 20001]
    expected = qp[0]['time_ns']+round(qp[0]['bytes']*8*10**9/10000000000)
    end = qp[0]['time_ns']+round(qp[0]['bytes']*8*10**9/result['physical_rate_bps'])
    state = next(e for e in result['selections'] if e['time_ns'] == end and not e['busy'])
    a = next(q for q in state['qps'] if q['port'] == 20000); b = next(q for q in state['qps'] if q['port'] == 20001)
    assert a['published'] == a['snd_nxt'] == 1000 and a['snd_una'] == 0 and a['next_avail_ns'] <= end
    assert b['published'] == 16000 and b['snd_nxt'] == 1000 and b['snd_una'] == 0
    assert not b['window_bound'] and not b['paused'] and b['next_avail_ns'] == expected > end
    first_feedback = next(e for e in result['frames'] if e['node'] == 0 and e['event'] == 1 and e['protocol'] == 252)['time_ns']
    assert state['wake_due_ns'] == (expected if fixed else -1)
    assert qp[1]['time_ns'] == (expected if fixed else first_feedback)
    return dict(first_paced_packet_ns=qp[0]['time_ns'], physical_idle_from_ns=end,
        correct_next_packet_ns=expected, actual_next_packet_ns=qp[1]['time_ns'],
        first_actual_ack_rx_ns=first_feedback, pending_wake_after_idle=state['wake_due_ns'],
        paced_completion_ns=next(c['time_ns'] for c in result['completed'] if c['port'] == 20001),
        payload_bytes=17000, data_packets=17, actual_acks=17,
        source_pending_without_ack=True, physical_pairing_and_serialization=True)


def verify(stage):
    old = load(stage/'real-packets-old.json'); fixed = load(stage/'real-packets-fixed.json')
    report = dict(old=witness(old,False), fixed=witness(fixed,True))
    def shapes(r): return sorted(tuple(e[k] for k in ('node','event','protocol','sequence','port','bytes','payload_bytes')) for e in r['frames'])
    assert shapes(old) == shapes(fixed)
    cpu_old = load(stage/'native-cpu-old.json'); cpu_fixed = load(stage/'native-cpu-fixed.json')
    assert cpu_fixed['all_contracts_satisfied'] and len(cpu_fixed['cases']) == 11
    failed = [c['name'] for c in cpu_old['cases'] if not c['contract_satisfied']]
    assert failed == ['empty-unacked','window-blocked','paused','earlier-append','resume-with-stale']
    assert all(not e['pending'] for c in cpu_fixed['cases'] if c['name'] in ('all-blocked','blocked-future') for e in c['scheduled'])
    variable = load(stage/'rate-fixed.json'); assert variable['passed'] and len(variable['cases']) == 2
    assert not load(stage/'rate-old.json')['passed']
    r = load(stage/'public-transaction/result.json'); w = load(stage/'inputs/transaction-work.json'); c = load(stage/'inputs/transaction-protocol.json')
    tx = transaction_audit(r,w,c); phy = fifo_audit(r)
    assert r['fault_injection']['drop_counts'] == [1] and r['transactions']['transport']['nack_grace']['config']['wait_ns'] == 16000
    rejected=[]
    for mode in ('deadline','tx_time','missing_ack','payload'):
        bad=deepcopy(fixed)
        if mode=='deadline':
            next(e for e in bad['selections'] if e['time_ns']==report['fixed']['physical_idle_from_ns'] and not e['busy'])['wake_due_ns']+=1
        elif mode=='tx_time':
            next(e for e in bad['frames'] if e['node']==0 and e['event']==0 and e['protocol']==17 and e['port']==20001 and e['sequence']==1000)['time_ns']+=1
        elif mode=='missing_ack':
            bad['frames'].remove(next(e for e in bad['frames'] if e['node']==0 and e['event']==1 and e['protocol']==252))
        elif mode=='payload':
            next(e for e in bad['frames'] if e['protocol']==17)['payload_bytes']+=1
        try:witness(bad,True)
        except (AssertionError,StopIteration,KeyError):rejected.append(mode)
        else:raise AssertionError('mutation accepted: '+mode)
    return dict(passed=True, native_packet_witness=report, native_cpu_cases=13,
        old_cpu_missing_ready_cases=failed, packet_shapes_equal=True,
        variable_window_rate_order_checked=True, transaction_payload_and_effects=tx,
        transaction_physical_service=phy, mutations_rejected=rejected, network_executions=0,
        scope='functional scheduler correction and small public transaction; no complete application performance or hardware claim')


def main():
    p=argparse.ArgumentParser()
    for k in ('stage','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();save(a.output,verify(a.stage))


if __name__=='__main__':main()
