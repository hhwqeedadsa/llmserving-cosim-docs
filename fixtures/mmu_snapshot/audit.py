"""Independent native queue-event reconstruction; stdlib, no platform imports."""
import argparse
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path


def audit(result):
    snapshot = result['mmu_snapshot']
    assert snapshot['time_ns'] == result['finish_ns']
    capture = result['packet_capture']
    assert capture['complete'] and capture['selection']['start_ns'] == 0
    assert capture['selection']['end_ns'] >= result['finish_ns']
    nodes = set(snapshot['selected_nodes'])
    assert not capture['selection']['nodes'] or nodes <= set(capture['selection']['nodes'])
    queue = defaultdict(lambda: [0]*8)
    observed = 0
    for values in capture['events']:
        e = dict(zip(capture['columns'], values))
        if e['node'] not in nodes or e['event'] not in (3, 4):
            continue
        # This functional fixture uses native UDP priority 3 and control q0.
        assert e['ip_protocol'] in (17, 252, 253, 254, 255)
        priority = 3 if e['ip_protocol'] == 17 else 0
        row = queue[e['node'], e['port']]
        row[priority] += e['model_bytes'] * (1 if e['event'] == 3 else -1)
        assert min(row) >= 0, 'dequeue without preceding enqueue'
        observed += 1
    assert [s['node'] for s in snapshot['switches']] == snapshot['selected_nodes']
    checks = 0
    data = control = 0
    for switch in snapshot['switches']:
        incoming = outgoing = expected_shared = 0
        assert len(switch['ports']) == switch['physical_port_count']
        for port in switch['ports']:
            expected = queue[switch['node'], port['port']]
            assert expected == port['queue_bytes'], 'queue differs from actual enqueue/dequeue events'
            for q in range(1, 8):
                assert port['egress_mmu_bytes'][q] == expected[q], 'egress MMU differs from actual queue'
                incoming += port['ingress_bytes'][q] + port['headroom_used_bytes'][q]
                outgoing += port['egress_mmu_bytes'][q]
                expected_shared += max(port['ingress_bytes'][q]-switch['reserve_bytes'], 0)
            data += sum(expected[1:]); control += expected[0]; checks += 1
        assert incoming == outgoing and expected_shared == switch['shared_used_bytes']
    return dict(schema_version='independent-switch-buffer-audit/1',
        checked_ports=checks, queue_events=observed, final_queued_data_bytes=data,
        final_queued_control_bytes=control, independent_queue_reconstruction_matches=True,
        native_run_passed=result['passed'], network_finish_ns=result['finish_ns'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--result', type=Path, required=True);p.add_argument('--output', type=Path, required=True)
    a=p.parse_args();raw=json.loads(a.result.read_text());report=audit(raw)
    rejected=[]
    for kind in ('changed_queue', 'changed_shared', 'missing_port'):
        value=deepcopy(raw);switch=value['mmu_snapshot']['switches'][0]
        if kind == 'changed_queue': switch['ports'][0]['queue_bytes'][3] += 1
        elif kind == 'changed_shared': switch['shared_used_bytes'] += 1
        else: switch['ports'].pop()
        try:
            audit(value)
        except AssertionError:
            rejected.append(kind)
        else:
            raise AssertionError('tampering accepted: '+kind)
    report['tampering_rejected']=rejected
    a.output.write_text(json.dumps(report, sort_keys=True, indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
