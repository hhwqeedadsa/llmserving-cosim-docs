"""Independent route/work/endpoint/credit/NP/RP audit; no network execution."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from check_feedback_factorial import route, notification_times, check_rp

ROOT = Path(__file__).resolve().parents[2]


def load(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def unpack(b, c, v): return [dict(zip(b[c], x)) for x in b[v]]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for k in ('task', 'output'): p.add_argument('--'+k, type=Path, required=True)
    a = p.parse_args(); assert not a.output.exists(); task = a.task
    observed = task/'input/observed'; run = task/'run'
    index = load(observed/'index.json'); execution = load(run/'execution.json')
    summary = load(task/'analysis/summary.json'); topology = load(run/'topology/topology.json')
    assert [c['depth'] for c in index['cases']] == [1, 8, 1024]
    for name, e in index['artifacts'].items():
        assert sha(observed/name) == e['sha256'] and (observed/name).stat().st_size == e['bytes']
    original = []; token_counts = []
    for src, item in enumerate(load(task/'input/truth/manifest.json')['files']):
        path = ROOT/item['file']['path']; assert sha(path) == item['file']['sha256']
        choices = load(path)[0]['4']; token_counts.append(min(32, len(choices)))
        for token in range(min(32, len(choices))):
            assert len(set(choices[token])) == len(choices[token]) == 8
            for peer in range(4):
                if peer != src and any(64*peer <= e < 64*(peer+1) for e in choices[token]):
                    original.append(dict(id=len(original), stream=src*4+peer, bytes=14464, after=[]))
    assert token_counts == [32, 28, 10, 6] and len(original) == 180
    last = {s: max(w['id'] for w in original if w['stream'] == s) for s in {w['stream'] for w in original}}
    expected = original + [dict(w, id=w['id']+180,
        after=[dict(work=last[s], event='acked') for s in sorted(last)]) for w in original]
    assert execution['new_network_runs'] == len(execution['executed']) <= 3 and execution['retry'] is False
    counts = Counter(); checks = []; all_services = []
    for declaration, ex in zip(index['cases'], execution['executed']):
        name = declaration['name']; assert ex['name'] == name
        cfg = load(observed/(name+'.json')); cold_cfg = load(observed/'reference'/(name+'-work.json'))
        assert cold_cfg['work'] == original and cfg['work'] == expected
        normalized = deepcopy(cfg); normalized['work'] = original; normalized['limits']['max_work'] = cold_cfg['limits']['max_work']
        assert normalized == cold_cfg and cfg['limits'] == dict(max_work=360, max_streams=12)
        path = run/name/'result.json'
        if ex['exit_code'] or not path.exists():
            checks.append(dict(name=name, full_verification=False, completion_unknown=True)); continue
        r = load(path); t = r['persistent_transport']; works = t['work']; byid = {w['id']:w for w in works}
        assert r['passed'] and r['drained'] and t['physical_drained'] and t['closed'] and r['completed_work'] == 360
        assert r['fault_drops'] == 0 and r['packet_capture']['complete'] and not r['packet_capture']['omitted_events']
        assert not any(p['pfc_pause'] or p['pfc_resume'] for p in r['ports'])
        assert len(t['streams']) == 12 and all(s['qp_creations'] == 1 and s['source_payload_bytes'] == s['submitted_bytes'] for s in t['streams'])
        inputs = load(run/name/'inputs.json'); cmd = inputs['command']
        assert inputs['hashes'][cmd[0]] == execution['backend_sha256'] == index['backend_sha256']
        for i, path in enumerate((run/'topology/topology.json', observed/declaration['protocol'], observed/(name+'.json')), 1):
            assert inputs['hashes'][cmd[i]] == sha(path)
        cold = load(observed/'reference'/(name+'-result.json')); ct = cold['persistent_transport']
        assert sha(observed/'reference'/(name+'-result.json')) == declaration['cold_result_sha256']
        boundary = max(byid[w['work']]['acked_ns'] for w in expected[180]['after'])
        assert boundary == max(w['acked_ns'] for w in works if w['id'] < 180)
        assert [w for w in works if w['id'] < 180] == ct['work']
        for block, cb, col, val in ((t,ct,'source_packet_columns','source_packets'),
            (r['packet_capture'],cold['packet_capture'],'columns','events'),
            (t['source_rate_capture'],ct['source_rate_capture'],'columns','samples')):
            assert [x for x in unpack(block,col,val) if x['time_ns'] < boundary] == [x for x in unpack(cb,col,val) if x['time_ns'] < boundary]
        events = t['dcqcn_reference']['events']; control = t['dcqcn_reference']['config']
        assert control == cfg['dcqcn_reference']
        assert [e for e in events if e['time_ns'] < boundary] == [e for e in ct['dcqcn_reference']['events'] if e['time_ns'] < boundary]
        counts['rp_state_events'] += check_rp(events, control)
        streams = {s['id']:s for s in cfg['streams']}
        source = unpack(t,'source_packet_columns','source_packets'); assert len(source) == 5400
        source_uid = {s['packet_uid']:s for s in source}; assert len(source_uid) == 5400
        samples = unpack(t['source_rate_capture'],'columns','samples')
        tx_samples = {s['packet_uid']:s for s in samples if s['event'] == 0}
        ack_samples = {s['packet_uid']:s for s in samples if s['event'] == 1}
        assert source_uid.keys() == tx_samples.keys()
        tx_events = {e['packet_uid']:e for e in events if e['kind'] == 'source_sent'}
        assert tx_events.keys() == source_uid.keys()
        for uid, pkt in source_uid.items():
            assert all(pkt[k] == tx_samples[uid][k] for k in ('stream','sequence','time_ns'))
            assert tx_events[uid]['rate_bps'] == tx_samples[uid]['rate_bps']
        generated = {e['packet_uid']:e for e in events if e['kind'] == 'cnp_generated'}
        sent = {e['packet_uid']:e for e in events if e['kind'] == 'cnp_tx'}
        received = {e['packet_uid']:e for e in events if e['kind'] == 'cnp_received'}
        assert generated.keys() == sent.keys() == received.keys()
        groups = defaultdict(list)
        for point in unpack(r['packet_capture'],'columns','events'): groups[point['packet_uid']].append(point)
        port_bytes = Counter(); data_bytes = Counter(); service = [Counter(),Counter()]; rates = {}
        data_rx = defaultdict(list); acks = defaultdict(list); marks = defaultdict(list); second_marks = defaultdict(list)
        stream_split = {sid: sum(14464 for w in original if w['stream']==sid) for sid in streams}
        for uid, points in groups.items():
            assert len(points) == 2 and {x['event'] for x in points} == {0,1}
            tx = next(x for x in points if x['event']==0); rx = next(x for x in points if x['event']==1)
            proto, size = tx['ip_protocol'], tx['model_bytes']; assert (proto,size)==(rx['ip_protocol'],rx['model_bytes'])
            if proto == 17:
                pkt = source_uid[uid]; sid = pkt['stream']; s = streams[sid]; src,dst=s['src'],s['dst']
                assert (tx['time_ns'],tx['sequence'],rx['sequence'],size)==(pkt['time_ns'],pkt['sequence'],pkt['sequence'],pkt['payload_bytes']+36)
                data_rx[sid].append(rx); wi = int(pkt['sequence'] >= stream_split[sid])
                if rx['ecn'] == 3:
                    marks[sid].append(rx['time_ns'])
                    if wi: second_marks[sid].append(rx['time_ns'])
                counts['data_packets'] += 1
            elif proto == 252:
                sid = ack_samples[uid]['stream']; s=streams[sid]; src,dst=s['dst'],s['src']; assert size==48
                assert (rx['time_ns'],rx['sequence']) == (ack_samples[uid]['time_ns'],ack_samples[uid]['sequence'])
                acks[sid].append(rx); wi = int(rx['sequence'] > stream_split[sid]); counts['ack_packets'] += 1
            else:
                assert proto==255 and size==48; sid=generated[uid]['stream']; s=streams[sid]; src,dst=s['dst'],s['src']
                assert generated[uid]['time_ns'] <= tx['time_ns'] == sent[uid]['time_ns'] and rx['time_ns']==received[uid]['time_ns']
                wi=None; counts['cnp_packets'] += 1
            assert (tx['node'],rx['node']) == (src,dst)
            hops=route(topology,src,dst,s['wire_port'])
            assert rx['time_ns']-tx['time_ns'] >= sum(h[4] for h in hops)
            for node,port,peer,rate,delay in hops:
                key=node,port; port_bytes[key]+=size; rates[key]=rate
                if proto==17:data_bytes[key]+=size
                if wi is not None:service[wi][key]+=size
            counts['endpoint_uid_joins'] += 1
        assert {uid for uid,p in groups.items() if p[0]['ip_protocol']==17} == source_uid.keys()
        assert service[0] == service[1]; all_services.extend(service)
        for p in r['ports']:
            key=p['node'],p['port']
            assert (p['model_tx_bytes'],p['data_tx_bytes'],p['control_tx_bytes']) == (port_bytes[key],data_bytes[key],port_bytes[key]-data_bytes[key])
            assert p.get('queue_metrics',{}).get('final_bytes',0)==0; counts['physical_ports']+=1
        for sid,s in streams.items():
            assert [e['time_ns'] for e in generated.values() if e['stream']==sid] == notification_times(marks[sid],50000)
            own = sorted((w for w in works if w['stream']==sid),key=lambda w:w['ticket']); prefix=0
            for i,w in enumerate(own):
                assert w['begin']==prefix and w['end']==prefix+14464; prefix+=14464
                ready = 0 if w['id']<180 else boundary
                assert w['ready_ns']==ready and w['published_ns']==max(ready,own[i-s['queue_depth']]['acked_ns'] if i>=s['queue_depth'] else 0)
                packets = [p for p in source if p['stream']==sid and w['begin']<=p['sequence']<w['end']]
                assert sum(p['payload_bytes'] for p in packets)==14464 and len(packets)==15
                assert w['accepted_ns']==max(p['time_ns'] for p in data_rx[sid] if w['begin']<=p['sequence']<w['end'])
                assert w['acked_ns']==min(p['time_ns'] for p in acks[sid] if p['sequence']>=w['end'])
                counts['work_callbacks_and_credit']+=1
        row = next(c for c in summary['cases'] if c['name']==name)
        assert row['boundary_ns']==boundary and all(row['first_window_prefix'].values()) and row['result_sha256']==sha(run/name/'result.json')
        bound=float(max(Fraction(size*8*10**9,rates[k]) for k,size in service[0].items()))
        for wi, wrow in enumerate(row['windows']):
            ws=[w for w in works if wi*180<=w['id']<(wi+1)*180]
            start=0 if wi==0 else boundary; end=max(w['acked_ns'] for w in ws)
            assert (wrow['ready_ns'],wrow['acked_ns'],wrow['duration_ns'])==(start,end,end-start)
            assert wrow['static_data_ack_service_bound_ns']==bound<=end-start and wrow['source_packets']==2700
            selected=[p for p in samples if p['event']==0 and int(p['sequence']>=stream_split[p['stream']])==wi]
            assert wrow['minimum_rate_at_tx_bps']==min(p['rate_bps'] for p in selected)
        for c in row['carry']:
            sid=c['stream']; tx=[p for p in samples if p['event']==0 and p['stream']==sid and p['sequence']>=stream_split[sid]]
            cnp=[e['time_ns'] for e in received.values() if e['stream']==sid and e['time_ns']>=boundary]
            assert c['first_second_window_tx']==tx[0] and c['first_cnp_after_boundary_ns']==min(cnp,default=None)
            old=[e for e in events if e['stream']==sid and e['time_ns']<boundary]
            assert c['last_logged_before_boundary']==(old[-1] if old else None)
            eligible=[p for p in tx if not cnp or p['time_ns']<min(cnp)]
            assert c['second_window_tx_before_any_post_boundary_cnp']==len(eligible)
            assert c['slow_tx_before_any_post_boundary_cnp']==sum(p['rate_bps']<100000000000 for p in eligible)
            counts['carry_streams']+=1
        critical = max(works,key=lambda w:w['acked_ns'])
        sid = critical['stream']
        chain_packets = [p for p in source if p['stream']==sid and p['sequence']>=stream_split[sid]]
        last_packet = max(chain_packets,key=lambda p:p['time_ns'])
        assert critical['begin'] <= last_packet['sequence'] < critical['end']
        first_tx = min(p['time_ns'] for p in chain_packets)
        spans = dict(ready_to_first_tx=first_tx-boundary,
            first_to_last_tx=last_packet['time_ns']-first_tx,
            last_tx_to_accepted=critical['accepted_ns']-last_packet['time_ns'],
            accepted_to_acked=critical['acked_ns']-critical['accepted_ns'])
        assert sum(spans.values()) == row['windows'][1]['duration_ns']
        critical_trace = dict(work=critical,last_source_packet=last_packet,
            second_window_first_tx=first_tx,nonoverlapping_spans_ns=spans,
            second_window_ce_packets=len(second_marks[sid]),
            second_window_cnp_rx=[e for e in received.values() if e['stream']==sid and e['time_ns']>=boundary])
        checks.append(dict(name=name,full_verification=True,first_window_prefix_identical=True,
            critical_second_window_trace=critical_trace,
            per_window_static_bound_ns=bound,second_window_ce_packets=sum(map(len,second_marks.values())),
            second_window_first_ce_rx_by_stream={str(s):min(x) for s,x in second_marks.items() if x},
            post_boundary_cnp=[dict(stream=e['stream'],generated_ns=e['time_ns'],received_ns=received[uid]['time_ns'],
                first_second_window_ce_rx_ns=min(second_marks[e['stream']],default=None),
                generated_before_any_second_window_ce=not second_marks[e['stream']] or e['time_ns']<min(second_marks[e['stream']]))
                for uid,e in generated.items() if e['time_ns']>=boundary]))
    assert not all_services or all(x==all_services[0] for x in all_services)
    a.output.mkdir(); result=dict(verified=True,all_declared_completed=execution['completed'] and len(checks)==3 and all(c['full_verification'] for c in checks),
        checks=checks,counts=dict(counts),raw_volatile=True,persistent_backup=False,network_reexecuted=False)
    (a.output/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(verified=True, counts=dict(counts))))


if __name__=='__main__': main()
