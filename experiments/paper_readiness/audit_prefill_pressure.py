"""Audit completed and time-censored Prefill runs without restarting their network."""
import argparse
from collections import Counter, defaultdict, deque
import hashlib
import heapq
import json
from pathlib import Path
import re

from check_dispatch_network import ecmp

ROOT = Path(__file__).resolve().parents[2]
HW = 'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3/src/point-to-point/model/rdma-hw.cc'


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def cc_replay(feedback, source, end):
    """Source-specific DCQCN replay; not a GPU/NIC measurement or new simulation.

    ACK CNP bits are reconstructed only from the locked ACK-every-packet
    ReceiveUdp branch and matching endpoint sequence. Equal-time external
    feedback/timer ordering is explicitly ambiguous, never silently certified.
    """
    rate = target = 100000000000
    alpha = 0.0
    first = True
    alpha_feedback = decrease_feedback = False
    stage = 0
    increase_generation = 0
    events = []
    serial = 0
    def schedule(tm,kind,value=None):
        nonlocal serial
        serial += 1
        heapq.heappush(events,(tm,serial,kind,value))
    for tm in feedback:
        schedule(tm,'cnp')
    for index,row in enumerate(source):
        schedule(row[0],'sample',index)
    minimum = rate
    rates = {}
    changes = []
    ambiguous = []
    while events:
        tm,_,kind,value = heapq.heappop(events)
        if tm > end:
            break
        # Samples do not mutate CC state; ties can affect the sampled value too.
        if kind in ('cnp','sample') and any(e[0] == tm and e[2] in ('alpha','decrease','increase') for e in events):
            ambiguous.append(dict(time_ns=tm,kind=kind))
        if kind in ('alpha','decrease','increase') and any(e[0] == tm and e[2] in ('cnp','sample') for e in events):
            ambiguous.append(dict(time_ns=tm,kind=kind))
        old = rate
        if kind == 'cnp':
            alpha_feedback = decrease_feedback = True
            if first:
                alpha = 1.0
                alpha_feedback = False
                schedule(tm+55000,'alpha')
                schedule(tm+4001,'decrease')
                target = rate
                first = False
        elif kind == 'alpha':
            alpha = (15/16)*alpha+(1/16 if alpha_feedback else 0)
            alpha_feedback = False
            schedule(tm+55000,'alpha')
        elif kind == 'decrease':
            schedule(tm+4000,'decrease')
            if decrease_feedback:
                if stage != 0:
                    target = rate  # ClampTargetRate=false
                rate = max(100000000,int(rate*(1-alpha/2)))
                stage = 0
                decrease_feedback = False
                increase_generation += 1
                schedule(tm+1500000,'increase',increase_generation)
        elif kind == 'increase':
            if value != increase_generation:
                continue
            schedule(tm+1500000,'increase',value)
            if stage >= 5:
                target = min(100000000000,target+(5000000 if stage == 5 else 50000000))
            rate = rate//2+target//2
            stage += 1
        elif kind == 'sample':
            rates[value] = rate
        else:
            raise AssertionError(kind)
        minimum = min(minimum,rate)
        if rate != old:
            changes.append(dict(time_ns=tm,rate_bps=rate,kind=kind))
    spacing = []
    for i,(left,right) in enumerate(zip(source,source[1:])):
        # Source model frame = payload + 36B, default interframe gap is zero.
        expected = (left[6]+36)*8*10**9/rates[i]
        spacing.append(dict(start_ns=left[0],next_ns=right[0],observed_gap_ns=right[0]-left[0],
            replay_rate_bps=rates[i],rate_spacing_ns=expected,
            residual_ns=right[0]-left[0]-expected))
    verified = not ambiguous
    if verified:
        assert all(s['residual_ns'] >= -1 for s in spacing), 'Replayed rate inconsistent with actual source packets'
    return dict(feedback_events=len(feedback),minimum_replayed_rate_bps=minimum,
        minimum_replayed_rate_at_actual_tx_bps=min(rates.values(),default=100000000000),
        equal_time_ambiguities=ambiguous,spacing_verified_without_equal_time_ambiguity=verified,
        source_intervals=len(spacing),minimum_spacing_residual_ns=min((s['residual_ns'] for s in spacing),default=0),
        rate_limited_intervals_within_100ns=sum(s['replay_rate_bps'] < 100000000000 and abs(s['residual_ns']) <= 100 for s in spacing),
        floor_rate_intervals_within_1ns=sum(s['replay_rate_bps'] == 100000000 and abs(s['residual_ns']) <= 1 for s in spacing),
        max_source_gap_ns=max((s['observed_gap_ns'] for s in spacing),default=0),
        largest_gap=max(spacing,key=lambda s:s['observed_gap_ns']) if spacing else None,
        rate_changes=changes,rate_changes_scope='reconstructed from fixed source and received feedback, not directly sampled NIC state')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    task,out = args.task,args.output
    assert not out.exists()
    index = load(task/'observed/index.json')
    for name,identity in index['artifacts'].items():
        assert sha(task/'observed'/name) == identity['sha256']
    manifest = load(task/'truth/manifest.json')
    provenance = load(task/'truth/provenance.json')
    assert sha(task/'truth/manifest.json') == provenance['manifest_sha256']
    sources = []
    for item in manifest['files']:
        path = ROOT/item['file']['path']
        assert sha(path) == item['file']['sha256']
        sources.append(load(path))
    assert [len(x[0]['3']) for x in sources] == index['source_token_counts'] == [105,28,10,6]
    # Rebuild all four declared work lists directly from the original files.
    expected = {}
    for case in index['cases']:
        work = []
        refs = []
        for src,raw in enumerate(sources):
            for token,chosen in enumerate(raw[0][str(case['layer'])]):
                for dst in range(4):
                    if dst == src or not any(dst*64 <= expert < (dst+1)*64 for expert in chosen):
                        continue
                    identity = len(work)
                    work.append(dict(id=identity,stream=4*src+dst,bytes=14464,after=[]))
                    refs.append(dict(work=identity,source_rank=src,token=token,destination_rank=dst))
        description = load(task/'observed'/(case['name']+'.json'))
        assert description['work'] == work
        assert refs == next(row['work_source_mapping'] for row in provenance['layers'] if row['layer'] == case['layer'])
        assert [s['queue_depth'] for s in description['streams']] == [case['queue_depth']]*12
        assert [(s['id'],s['src'],s['dst'],s['wire_port'],s['source_nic'],s['destination_nic']) for s in description['streams']] == [
            (4*s+d,2*s,2*d,21000+4*s+d,1,1) for s in range(4) for d in range(4) if s != d]
        expected[case['name']] = description
    assert expected['layer4-depth1']['work'] == expected['layer4-depth8']['work']
    commands = load(task/'run/commands.json')
    assert [(r['label'],r['exit_code']) for r in commands] == [('topology',0),('layer4-depth1',0),('layer4-depth8',1)]
    assert not (task/'run/layer32-depth1').exists() and not (task/'run/layer32-depth8').exists()
    physical = load(task/'run/topology/topology.json')
    links = {}
    adjacency = defaultdict(list)
    for edge in physical['links']:
        for n,p,peer in ((edge['src'],edge['src_port'],edge['dst']), (edge['dst'],edge['dst_port'],edge['src'])):
            links[n,p] = (peer,edge['rate_bps'])
            adjacency[n].append(peer)
    def route(src,dst,wire):
        distances = {dst:0}
        queue = deque([dst])
        while queue:
            node = queue.popleft()
            for peer in adjacency[node]:
                if peer not in distances:
                    distances[peer] = distances[node]+1
                    if peer >= 8:
                        queue.append(peer)
        path = []
        node = src
        while node != dst:
            ports = [k for k,(peer,_) in links.items() if k[0] == node and distances[peer]+1 == distances[node]]
            assert ports
            offset = 0 if node < 8 else ecmp([0x0b000001+src*32,0x0b000001+dst*32,wire|(wire<<16)],11+node)%len(ports)
            selected = ports[offset]
            path.append(selected)
            node = links[selected][0]
        return path
    # Check the actual default chain, not a parameter name guessed from a paper.
    hw = (ROOT/HW).read_text()
    defaults = dict(MinRate='DataRateValue(DataRate("100Mb/s"))',RPTimer='DoubleValue(1500.0)',
        RateDecreaseInterval='DoubleValue(4.0)',AlphaResumInterval='DoubleValue(55.0)',
        EwmaGain='DoubleValue(1.0 / 16)',RateOnFirstCnp='DoubleValue(1.0)',
        ClampTargetRate='BooleanValue(false)',FastRecoveryTimes='UintegerValue(5)',
        RateAI='DataRateValue(DataRate("5Mb/s"))',RateHAI='DataRateValue(DataRate("50Mb/s"))',RateBound='BooleanValue(true)')
    for name,value in defaults.items():
        fragment = hw.split('.AddAttribute("'+name+'"',1)[-1] if '.AddAttribute("'+name+'"' in hw else hw.split('.AddAttribute ("'+name+'"',1)[1]
        assert value in fragment.split('Make',1)[0],name
    fabric = (ROOT/'backend/rdma/fabric.cc').read_text()
    native_main = (ROOT/'backend/rdma/stream_probe.cc').read_text()
    for name in defaults:
        assert 'SetAttribute("'+name+'"' not in fabric
        assert 'SetDefault' not in native_main
    assert 'if (ecnbits)\n\t\t\tseqh.SetCnp();' in hw
    assert 'cnp_received_mlx(s->qp)' in (ROOT/'backend/rdma/persistent_streams.cc').read_text()
    results = []
    rate_rows = []
    total_sources = total_capture = port_checks = 0
    for case in index['cases'][:2]:
        name = case['name']
        result_path = task/'run'/name/'result.json'
        result = load(result_path)
        transport = result['persistent_transport']
        description = expected[name]
        declared_ports = Counter()
        for work in description['work']:
            stream = next(s for s in description['streams'] if s['id'] == work['stream'])
            for src,dst,size in ((stream['src'],stream['dst'],14464+36*15),
                                 (stream['dst'],stream['src'],48*15)):
                for key in route(src,dst,stream['wire_port']):
                    declared_ports[key] += size
        busiest = max(declared_ports,key=lambda key:declared_ports[key]*8*10**9/links[key][1])
        service_bound = declared_ports[busiest]*8*10**9/links[busiest][1]
        inputs = load(task/'run'/name/'inputs.json')
        for path,digest in inputs['hashes'].items():
            assert sha(Path(path)) == digest
        assert result['fault_drops'] == 0
        works = {w['id']:w for w in transport['work']}
        assert works.keys() == {w['id'] for w in description['work']}
        streams = {s['id']:s for s in transport['streams']}
        by_uid = defaultdict(list)
        capture = result['packet_capture']
        assert capture['selection']['nodes'] == [0,2,4,6]
        assert capture['complete'] and capture['omitted_events'] == 0
        assert all(e[1] in (0,2,4,6) and e[3] in (0,1) and e[6] in (17,252) for e in capture['events'])
        for event in capture['events']:
            by_uid[event[4]].append(event)
        source_packets = transport['source_packets']
        data = {row[-1]:row for row in source_packets}
        assert len(data) == len(source_packets)
        assert {uid for uid,events in by_uid.items() if events[0][6] == 17} == set(data)
        received = defaultdict(list)
        acknowledgments = defaultdict(list)
        data_end = {}
        upper,lower = Counter(),Counter()
        source_by_stream = defaultdict(list)
        ce_received = 0
        for row in source_packets:
            tm,sid,src,dst,nic,sequence,payload,uid = row
            source_by_stream[sid].append(row)
            s = streams[sid]
            assert (src,dst,nic) == (s['src'],s['dst'],1)
            assert 0 < payload <= 1000
            events = by_uid[uid]
            tx = [e for e in events if e[3] == 0]
            rx = [e for e in events if e[3] == 1]
            assert len(tx) == 1 and len(rx) <= 1
            assert (tx[0][0],tx[0][1],tx[0][2],tx[0][7],tx[0][8],tx[0][11]) == (tm,src,nic,sequence,payload+36,payload)
            path = route(src,dst,s['wire_port'])
            for key in path:
                upper[key,17] += payload+36
            if rx:
                assert rx[0][1:3] == [dst,1] and rx[0][7:9] == [sequence,payload+36]
                assert rx[0][0] >= tm+len(path)*1000
                received[sid].append((sequence,payload,rx[0][0]))
                for key in path:
                    lower[key,17] += payload+36
                ce_received += rx[0][9] == 3
                data_end[sid,sequence+payload] = rx[0]
        feedback = defaultdict(list)
        for uid,events in by_uid.items():
            if events[0][6] == 17:
                continue
            tx = [e for e in events if e[3] == 0]
            rx = [e for e in events if e[3] == 1]
            assert len(tx) == 1 and len(rx) <= 1 and tx[0][8] == 48
            src = tx[0][1]
            # This endpoint-only capture omits IP addresses. Without its RX an
            # ACK destination can be ambiguous; never infer it from a matching
            # cumulative sequence on whichever stream happens to be first.
            assert len(rx) == 1, 'ACK destination unavailable in selected endpoint capture'
            dst = rx[0][1]
            candidates = [sid for sid,s in streams.items() if s['dst'] == src and s['src'] == dst and
                (sid,tx[0][7]) in data_end and data_end[sid,tx[0][7]][0] <= tx[0][0]]
            assert len(candidates) == 1
            sid = candidates[0]
            s = streams[sid]
            data_rx = data_end[sid,tx[0][7]]
            assert data_rx[0] <= tx[0][0]
            path = route(src,dst,s['wire_port'])
            for key in path:
                upper[key,252] += 48
            if rx:
                assert rx[0][7:9] == tx[0][7:9]
                acknowledgments[sid].append((rx[0][7],rx[0][0]))
                for key in path:
                    lower[key,252] += 48
                if data_rx[9] != 0:
                    feedback[sid].append(rx[0][0])
        wait_count = accepted_count = acked_count = 0
        outgoing,incoming = [0]*4,[0]*4
        for sid,s in streams.items():
            rows = source_by_stream[sid]
            cursor = 0
            for p in rows:
                assert p[5] == cursor
                cursor += p[6]
            assert cursor == s['snd_nxt'] == s['source_payload_bytes']
            assert len(rows) == s['source_data_packets']
            assert s['snd_una'] == max((q for q,_ in acknowledgments[sid]),default=0)
            assert s['qp_creations'] == 1 and s['queue_depth'] == case['queue_depth']
            recv_cursor = 0
            for seq,size,tm in sorted(received[sid]):
                assert seq == recv_cursor
                recv_cursor += size
            members = [works[w['id']] for w in description['work'] if w['stream'] == sid]
            for i,w in enumerate(members):
                assert (w['bytes'],w['ready_ns'],w['ticket']) == (14464,0,i)
                prior = members[i-case['queue_depth']] if i >= case['queue_depth'] else None
                publication = prior['acked_ns'] if prior else 0
                assert w['published_ns'] == publication
                outgoing[s['src']//2] += 1
                incoming[s['dst']//2] += 1
                if publication < 0:
                    # Native Pump assigns sequence intervals at publication,
                    # not at application readiness or reservation.
                    assert (w['begin'],w['end'],w['accepted_ns'],w['acked_ns']) == (0,0,-1,-1)
                    continue
                assert (w['begin'],w['end']) == (i*14464,(i+1)*14464)
                arrived = [tm for seq,size,tm in received[sid] if w['begin'] <= seq < w['end']]
                accepted = max(arrived) if recv_cursor >= w['end'] else -1
                acked = min((tm for seq,tm in acknowledgments[sid] if seq >= w['end']),default=-1)
                assert (w['accepted_ns'],w['acked_ns']) == (accepted,acked)
                wait_count += publication > 0
                accepted_count += accepted >= 0
                acked_count += acked >= 0
            assert s['published_bytes'] == max((w['end'] for w in members if w['published_ns'] >= 0),default=0)
            replay = cc_replay(sorted(feedback[sid]),rows,result['finish_ns'])
            rate_rows.append(dict(case=name,stream=sid,**replay))
        assert acked_count == result['completed_work']
        for p in result['ports']:
            key = p['node'],p['port']
            assert lower[key,17] <= p['data_tx_bytes'] <= upper[key,17]
            assert lower[key,252] <= p['control_tx_bytes'] <= upper[key,252]
            if result['passed']:
                assert p['data_tx_bytes'] == lower[key,17] == upper[key,17]
                assert p['control_tx_bytes'] == lower[key,252] == upper[key,252]
                assert p.get('queue_metrics',{}).get('final_bytes',0) == 0
            port_checks += 1
        if result['passed']:
            assert acked_count == len(works) and result['drained'] and transport['physical_drained'] and transport['closed']
        else:
            assert result['finish_ns'] == 5000000 and not result['drained'] and not transport['closed']
            assert acked_count < len(works)
        expert_counts = Counter(e for raw in sources for row in raw[0][str(case['layer'])] for e in row)
        expert_inputs = [sum(v for e,v in expert_counts.items() if e//64 == rank) for rank in range(4)]
        results.append(dict(case=name,passed=result['passed'],observation_end_ns=result['finish_ns'],
            completion_ns=result['finish_ns'] if result['passed'] else None,work_count=len(works),
            accepted_work=accepted_count,acked_work=acked_count,not_yet_acked_work=len(works)-acked_count,
            source_copies=outgoing,destination_copies=incoming,
            source_copy_max_to_mean=4*max(outgoing)/sum(outgoing),destination_copy_max_to_mean=4*max(incoming)/sum(incoming),
            expert_peak_to_mean=256*max(expert_counts.values())/sum(expert_counts.values()),
            destination_expert_contributions=expert_inputs,
            minimum_full_work_port_service_ns=service_bound,
            busiest_declared_port=dict(node=busiest[0],port=busiest[1],peer=links[busiest][0],
                rate_bps=links[busiest][1],model_bytes=declared_ports[busiest]),
            sent_payload_bytes=sum(row[6] for row in source_packets),declared_payload_bytes=14464*len(works),
            source_packets=len(source_packets),endpoint_capture_events=len(capture['events']),
            received_ce_packets=ce_received,ecn_hook_observations=sum(p['ecn_ce_seen'] for p in result['ports']),
            pfc_pause=sum(p['pfc_pause'] for p in result['ports']),
            max_queue_bytes=max(p.get('queue_metrics',{}).get('peak_bytes',0) for p in result['ports']),
            publication_wait_work_count=wait_count,result_sha256=sha(result_path),
            port_check_scope='exact counts for completed run; delivered/injected path bounds for time-censored prefix'))
        total_sources += len(source_packets)
        total_capture += len(capture['events'])
    out.mkdir()
    save(out/'audit.json',dict(passed=True,cases=results,source_packets_checked=total_sources,
        endpoint_capture_events_checked=total_capture,physical_port_bounds_checked=port_checks,
        raw_requests_checked=4,declared_conditions_checked=4,network_conditions_executed=2,
        network_conditions_complete=1,network_conditions_time_censored=1,network_conditions_not_started=2,
        no_new_network_or_retry=True,whole_run_latency_for_failed_case_unknown=True,
        rate_replay_source_sha256=sha(ROOT/HW),defaults=defaults,
        source_hashes={name:sha(ROOT/name) for name in (HW,'backend/rdma/fabric.cc',
            'backend/rdma/persistent_streams.cc','backend/rdma/stream_probe.cc')},
        hardware_calibrated=False,modern_GIN_modeled=False,goal_complete=False))
    save(out/'rate-replay.json',dict(streams=rate_rows,
        scope='Fixed-source DCQCN reconstructed from observed endpoint ACK sequence and ECN; not directly measured provider state'))
    print(json.dumps(dict(passed=True,cases=results,rate_replays=len(rate_rows)),ensure_ascii=False))


if __name__ == '__main__':
    main()
