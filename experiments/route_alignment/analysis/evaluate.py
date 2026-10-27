"""Evaluation-only checks and metrics; this module may read truth/."""
from collections import Counter, defaultdict

from experiments.route_alignment.analysis.common import (load, TOKEN_BYTES, PAYLOAD,
    DATA_HEADER, ACK_BYTES, connection_paths)


def audit(work, meta, topology, protocol, result):
    assert result['passed'] and result['drained'] and result['native_network_executed']
    tr = result['persistent_transport']
    assert tr['closed'] and tr['physical_drained'] and not tr['inflight_packets']
    assert result['completed_work']==len(work['work']) and result['fault_drops']==0
    capture = result['packet_capture']
    assert capture['complete'] and not capture['omitted_events']
    actual = {w['id']:w for w in tr['work']}; local = {w['id']:w for w in result['local_work']}
    assert set(actual)|set(local)=={w['id'] for w in work['work']}
    assert not set(actual)&set(local)
    for w in work['work']:
        own = (actual if w['kind']=='write' else local)[w['id']]
        if w['kind']=='write':
            assert own['bytes']==w['bytes'] and own['stream']==w['stream']
            assert 0<=own['ready_ns']<=own['published_ns']<=own['accepted_ns']<=own['acked_ns']
            ready = own['ready_ns']
        else:
            assert own['rank']==w['rank'] and own['duration_ns']==w['duration_ns']
            assert 0<=own['ready_ns']<=own['start_ns']
            assert own['completed_ns']-own['start_ns']==w['duration_ns'];ready=own['ready_ns']
        times = []
        for d in w['after']:
            parent = local[d['work']] if d['event']=='completed' else actual[d['work']]
            times.append(parent[d['event']+'_ns'])
        assert ready==max(times,default=0),(w['id'],ready,times)
    streams = {s['id']:s for s in work['streams']}
    runtime = {s['id']:s for s in tr['streams']}; assert set(runtime)==set(streams)
    route_map,_ = connection_paths(topology,work['streams'],protocol['seed'])
    records = defaultdict(list)
    for row in tr['source_packets']:records[row[1]].append(row)
    expected_packets = {}
    for sid, stream in streams.items():
        declared = [w for w in work['work'] if w.get('stream')==sid]
        cursor = 0;expected = []
        for w in declared:
            for off in range(0,w['bytes'],PAYLOAD):expected.append((cursor+off,min(PAYLOAD,w['bytes']-off)))
            cursor += w['bytes']
        assert [(r[5],r[6]) for r in records[sid]]==expected
        state = runtime[sid]
        assert state['submitted_bytes']==state['published_bytes']==state['snd_nxt']==state['snd_una']==cursor
        assert state['source_payload_bytes']==cursor and state['source_data_packets']==len(expected)
        for row in records[sid]:
            assert row[2:5]==[stream['src'],stream['dst'],1]
            expected_packets[row[7]]=(sid,row[5],row[6],row[0])
    events = capture['events'];header_info=capture['wire_headers']
    assert len(events)==len(header_info['event_header_indices'])
    headers = header_info['headers']; tx = {};rx = {}
    for row,index in zip(events,header_info['event_header_indices']):
        assert row[1] in range(8) and row[2]==1 and row[3] in (0,1,3,4)
        if row[3] not in (0,1):continue
        h = headers[index];assert h[0]==row[4] and h[3]==row[6]
        target = tx if row[3]==0 else rx;assert row[4] not in target;target[row[4]]=(row,h)
    assert set(tx)==set(rx)
    cnp_stream = {e['packet_uid']:e['stream'] for e in tr['dcqcn_reference']['events'] if e['kind']=='cnp_generated'}
    counters = Counter();data_counters=Counter();control_counters=Counter();data_uids=set()
    physical_tx=0
    for uid,(row,h) in tx.items():
        other,other_h=rx[uid];assert h==other_h and row[8]==other[8]
        proto=row[6];assert proto in (17,252,255)
        sid=cnp_stream[uid] if proto==255 else row[5]
        s=streams[sid];forward=proto==17;src,dst=(s['src'],s['dst']) if forward else (s['dst'],s['src'])
        assert row[1]==src and other[1]==dst
        assert h[1:3]==[0x0b000001+src*32,0x0b000001+dst*32]
        if proto!=255:assert h[4:6]==[s['wire_port'],s['wire_port']] and h[6]==1
        path=route_map[sid]['forward' if forward else 'backward']
        minimum=sum(e['delay_ns']+int(row[8]*8e9/e['rate_bps']) for e in path)
        assert other[0]-row[0]>=minimum-1
        if forward:
            data_uids.add(uid);ss,seq,size,when=expected_packets[uid]
            assert ss==sid and row[7]==seq and row[11]==size and row[8]==size+DATA_HEADER and row[0]==when
        else:assert row[8]==ACK_BYTES
        for e in path:
            key=e['src'],e['port'];counters[key]+=row[8]
            (data_counters if forward else control_counters)[key]+=row[8];physical_tx+=1
    assert data_uids==set(expected_packets) and len(cnp_stream)==sum(r[0][6]==255 for r in tx.values())
    assert tr['link_transmissions']==physical_tx
    for p in result['ports']:
        key=p['node'],p['port']
        assert p['model_tx_bytes']==counters[key] and p['data_tx_bytes']==data_counters[key]
        assert p['control_tx_bytes']==control_counters[key]
        if p['node']>=8:
            q=p['queue_metrics'];assert q['final_bytes']==0 and q['area_byte_ns']>=0 and q['peak_bytes']>=0
    rounds=[];begin=0
    for r in meta['rounds']:
        dispatch=max([begin,*[actual[i]['acked_ns'] for i in r['dispatch']],
                     *[actual[i]['accepted_ns'] for i in r['dispatch']]])
        expert=max(local[i]['completed_ns'] for i in r['compute'])
        data=max([expert,*[actual[i]['accepted_ns'] for i in r['combine']]])
        complete=local[r['barrier']]['completed_ns']
        rounds.append(dict(step=r['step'],begin_ns=begin,dispatch_acked_ns=dispatch,
            expert_completed_ns=expert,data_ready_ns=data,resources_released_ns=complete,duration_ns=complete-begin))
        begin=complete
    metrics=dict(application_ns=rounds[-1]['resources_released_ns'],finish_ns=result['finish_ns'],rounds=rounds,
        source_data_packets=len(expected_packets),endpoint_packets=len(tx),physical_tx=physical_tx,
        cnp_packets=len(cnp_stream),ecn_observations=sum(p['ecn_ce_seen'] for p in result['ports']),
        queues=[dict(node=p['node'],port=p['port'],**p['queue_metrics']) for p in result['ports'] if p['node']>=8])
    assert metrics['application_ns']<=metrics['finish_ns']
    return metrics
