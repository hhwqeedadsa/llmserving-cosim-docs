"""Independent object-pair/state oracle for the recording-API host execution."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

PREFIX = 'DOCA_VERBS_QP_ATTR_'
MASKS = [set(PREFIX+s for s in words.split()) for words in (
    'NEXT_STATE ALLOW_REMOTE_WRITE ALLOW_REMOTE_READ PKEY_INDEX PORT_NUM',
    'NEXT_STATE RQ_PSN DEST_QP_NUM PATH_MTU AH_ATTR MIN_RNR_TIMER MAX_DEST_RD_ATOMIC ATOMIC_MODE',
    'NEXT_STATE SQ_PSN ACK_TIMEOUT RETRY_CNT RNR_RETRY MAX_QP_RD_ATOMIC')]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def check(source, extracted, actual, output):
    manifest = json.loads((source/'sources.json').read_text())
    assert len(manifest) == 4
    for row in manifest:
        data = (source/row['project']/row['path']).read_bytes()
        tree = json.loads((source/(row['project']+'-tree.json')).read_text())
        entries = {r['path']: r for r in tree['tree']}
        assert not tree['truncated']
        assert sha(data) == row['sha256'] and len(data) == row['bytes']
        assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest() == row['blob']
        assert entries[row['path']]['sha'] == row['blob']
    host = next(r for r in manifest if r['path'].endswith('gin_host_gdaki.cc'))
    assert host['commit'] == '12df1a11afad322be5a204a2db890161cbf8131d'
    assert host['sha256'] == 'd554a0295c463782d60a36b746ed8e998c7e427493f00e6b6813f9d945fdd361'
    lines = (source/'nccl'/host['path']).read_text().splitlines(keepends=True)
    excerpts = json.loads((extracted/'excerpts.json').read_text())
    assert len(excerpts) == 6
    for row in excerpts:
        raw = (extracted/(row['name']+'.inc')).read_bytes()
        # Original slice may end before the final line's newline.
        interval = ''.join(lines[row['first_line']-1:row['last_line']]).encode()
        assert interval.startswith(raw) and len(interval)-len(raw) <= 2
        assert sha(raw) == row['sha256']
    # These static checks establish the declared no-companion scope. They do
    # not execute the initializer or all of CreateContext.
    init = (source/'nccl/src/include/nccl_device/host.h').read_text()
    assert '0,                                           /* ginCounterCount */' in init
    ep = (source/'deepep/csrc/kernels/comm/context.cpp').read_text()
    assert 'reqs = NCCL_DEV_COMM_REQUIREMENTS_INITIALIZER;' in ep
    assert 'reqs.ginCounterCount' not in ep
    data = json.loads(actual.read_text())
    pair_count = transitions = 0

    def verify_transitions(rows, source_qpn, target_qpn, own_gid=None, lag=None, atomic=(16,16)):
        nonlocal transitions
        assert len(rows) == 3
        for state, event in enumerate(rows, 1):
            assert event['qpn'] == source_qpn and event['error'] == 0
            assert event['fields']['next_state'] == state
            expected = MASKS[state-1].copy()
            if lag and state == 1:
                expected.add(PREFIX+'LAG_TX_PORT_AFFINITY')
            assert set(event['mask']) == expected
            fields = event['fields']
            assert fields['dest_qp_num'] == target_qpn
            assert fields['path_mtu'] == 4096 and fields['port_num'] == 1
            assert fields['rq_psn'] == fields['sq_psn'] == 0
            assert fields['allow_remote_read'] == fields['allow_remote_write'] == 1
            assert fields['rnr_retry'] == 7 and fields['min_rnr_timer'] == 12
            assert fields['ack_timeout'] == 20 and fields['retry_cnt'] == 7
            assert fields['atomic_mode'] == 1 and fields['pkey_index'] == 0
            if own_gid is not None:
                assert fields['ah_attr']['gid'] == own_gid
                assert fields['ah_attr']['dlid'] == 71
            if lag:
                assert fields['lag_tx_port_affinity'] == lag
            else:
                assert 'lag_tx_port_affinity' not in fields
            if state >= 2:
                assert fields['max_dest_rd_atomic'] == atomic[0]
            if state == 3:
                assert fields['max_rd_atomic'] == atomic[1]
            transitions += 1

    assert len(data['pairs']) == 39
    for case in data['pairs']:
        r, c, rank = case['ranks'], case['contexts'], case['rank']
        assert case['status'] == 0
        assert case['created'] == case['destroyed'] == 2*c
        assert len(case['modifications']) == 6*c
        grouped = {}
        for event in case['modifications']:
            grouped.setdefault(event['qpn'], []).append(event)
        # Independently enumerate object rows: each main peer table is followed
        # by a responder row. QPN is an arbitrary object identifier in the probe.
        objects = [('main', ctx, peer) for ctx in range(c) for peer in range(r)]
        objects += [('responder', ctx, rank) for ctx in range(c)]
        qpn = {obj: 1000+i for i, obj in enumerate(objects)}
        active = set()
        for ctx in range(c):
            sender = qpn['main', ctx, rank]
            responder = qpn['responder', ctx, rank]
            assert sender != responder
            active.update((sender, responder))
            verify_transitions(grouped[sender], sender, responder, case['own_gid'])
            verify_transitions(grouped[responder], responder, sender, case['own_gid'])
            pair_count += 1
        assert set(grouped) == active
    null = data['null_guard']
    assert null['status'] == 0 and null['created'] == null['destroyed'] == 2
    assert set(e['qpn'] for e in null['modifications']) == {1001, 1008}
    for row in data['mtu']:
        assert row['status'] == 0
        assert row['bytes'] == min({1:256,2:512,3:1024,4:2048,5:4096}[row['local']],
                                   {1:256,2:512,3:1024,4:2048,5:4096}[row['remote']])
    assert len(data['mtu']) == 25
    controls = {r['name']: r for r in data['connect_controls']}
    assert len(controls) == 11
    for name, row in controls.items():
        if name.startswith('mtu_bad') or name == 'ordering_mismatch':
            assert row['status'] == (2 if name == 'ordering_mismatch' else 1)
            assert row['created'] == row['destroyed'] == row['modify_calls'] == 0
        elif name.startswith('fail_'):
            n = {'fail_init':1, 'fail_rtr':2, 'fail_rts':3}[name]
            assert row['status'] == 9 and row['created'] == row['destroyed'] == 1
            assert row['modify_calls'] == n
            assert [e['error'] for e in row['modifications']] == [0]*(n-1)+[9]
        else:
            assert row['status'] == 0 and row['created'] == row['destroyed'] == 1
            verify_transitions(row['modifications'], 1000, 9001,
                               lag=2 if name == 'remote_lag' else None,
                               atomic=(7,9) if name == 'override_atomic' else (16,16))
    assert controls['ib_local']['ah']['addr_type'] == 2
    assert controls['ib_local']['ah']['hop_limit'] == 0
    for name in ('ib_grh', 'ib_other_subnet'):
        assert controls[name]['ah']['addr_type'] == 1 and controls[name]['ah']['hop_limit'] == 64
    assert controls['ib_other_subnet']['ah']['dlid'] == 91
    # Resource arithmetic output is compared with concrete objects, not the
    # C++ expression that produced it. No packets/work queues are allocated.
    assert len(data['resources']) == 30
    for case in data['resources']:
        r, k, connections = case['ranks'], case['per_connection'], case['connections']
        allocated = case['allocated']
        assert allocated >= case['requested'] > allocated-connections
        assert allocated == k*connections
        objects = [('main', ctx, peer) for ctx in range(k) for peer in range(r)]
        objects += [('responder', ctx, 0) for ctx in range(k)]
        counts = Counter(o[0] for o in objects)
        out = case['counter0']
        assert out['nqps'] == len(objects)
        assert out['nqps_for_comm'] == out['nqps_for_comm_this_rank'] == counts['main']
        assert out['rankOff'] == 0 and out['connectedNRanks'] == r
        assert out['ncontexts'] == out['nqps_per_rank'] == counts['responder']
        assert not out['needCompanion'] and out['ncompanion_qps'] == 0
        companion = [(side,ctx,peer) for side in ('sender','receiver')
                     for ctx in range(k) for peer in range(r)]
        assert case['counter1_control']['needCompanion']
        assert case['counter1_control']['ncompanion_qps'] == len(companion)
    scale = []
    for ep in (4,32,128,1024):
        # Values taken from actual executed resource arithmetic cases for EP32,
        # 128,1024; EP4 included as a small reference.
        row = next(x for x in data['resources'] if x['ranks'] == ep
                   and x['requested'] == 17 and x['connections'] == 2)
        per_gpu = row['counter0']['nqps']*2
        scale.append(dict(cluster_gpus=1024, ep=ep, groups=1024//ep,
            requested_contexts=17, allocated_contexts=row['allocated'], connections=2,
            cross_rank_main_per_gpu=(ep-1)*row['allocated'],
            self_main_per_gpu=row['allocated'], self_responder_per_gpu=row['allocated'],
            total_qps_per_gpu=per_gpu, total_qps_cluster=1024*per_gpu,
            allocation_estimate_only=True, thousand_gpu_execution=False))
    summary = dict(verified=True, sources=4, original_excerpts=6,
        self_cases=len(data['pairs']), self_pairs=pair_count,
        checked_successful_transitions=transitions, null_guard=True,
        mtu_combinations=25, connect_controls=11, resource_cases=30,
        vendor_runtime_executed=False, network_executed=False,
        physical_loopback_path_verified=False, cost_calibrated=False,
        paper_contribution_established=False, scale_projection=scale,
        actual_sha256=sha(actual.read_bytes()))
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k:summary[k] for k in ('verified','self_pairs','resource_cases')}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('source','extracted','actual','output'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    check(a.source, a.extracted, a.actual, a.output)
