"""Independent original-route, packet-path and physical-port verification.

ACK identity is checked as an endpoint/plane/sequence multiset. The public
capture has no ACK QP identifier; this checker does not claim that observation.
"""
import argparse
from collections import Counter,defaultdict,deque
import hashlib
import json
from pathlib import Path


def read(p):return json.loads(p.read_text())
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def check(inputs,plans,network,analysis,out):
    obs=inputs/'observed';truth=inputs/'truth'
    manifest=read(truth/'manifest.json');routes=read(obs/'routes.json')['tokens'];cfg=read(obs/'config.json')
    for src,item in enumerate(manifest['files']):
        p=truth/Path(item['file']['path']).name
        assert digest(p)==item['file']['sha256']
        assert read(p)[0]['4'][:16]==routes[src]
    for record in read(obs/'sources.json'):
        p=obs/'sources'/record['path'];raw=p.read_bytes()
        assert digest(p)==record['sha256']
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==record['git_blob_sha']
        tree=read(truth/('nccl_gin_20261022-tree.json' if record['commit'].startswith('12df') else 'deepep_modern_20261022-tree.json'))
        assert next(r for r in tree['tree'] if r['path']==record['path'])['sha']==record['git_blob_sha']
    # Enumerate the kernel's SM/warp loops, then round-robin the context rows.
    ctx_by_slot={};circle=[1,2,3,4,5,6,7]*5
    for sm in range(8):
        for warp in range(4):
            index=warp*8+sm
            while index<16:
                ctx_by_slot[index]=circle[sm*4+warp]
                index+=8*4
    proj=read(plans/'projection.json')['cases'];identities={}
    original_sets={(src,t,dst) for src,row in enumerate(routes) for t,experts in enumerate(row)
                   for dst in set(e//64 for e in experts) if dst!=src}
    assert len(original_sets)==117
    for case in proj:
        policy=case['policy'];orders=case['permutations'];expected=[]
        for src,order in enumerate(orders):
            assert sorted(order)==list(range(len(routes[src])))
            if policy=='identity':assert order==list(range(len(order)))
            if policy=='rotate':assert order==list(range(src,len(order)))+list(range(src))
            if policy=='local_lpt':
                weights=[len({e//64 for e in r}-{src}) for r in routes[src]]
                filled=set();loads=[0,0]
                for token in sorted(range(len(order)),key=lambda t:(-weights[t],t)):
                    slot=order.index(token)
                    scores={}
                    for s in range(len(order)):
                        if s in filled:continue
                        a,b=loads
                        if ctx_by_slot[s]%2:a,b=a,b+weights[token]
                        else:a,b=a+weights[token],b
                        scores[s]=(max(a,b),a*a+b*b,s)
                    assert scores[slot]==min(scores.values())
                    loads[ctx_by_slot[slot]%2]+=weights[token];filled.add(slot)
            for slot,token in enumerate(order):
                for dst in sorted({e//64 for e in routes[src][token]}-{src}):
                    ctx=ctx_by_slot[slot];sid=src*32+dst*8+ctx
                    expected.append(dict(work=len(expected),src=src,token=token,slot=slot,dst=dst,
                        context=ctx,plane=ctx%2,stream=sid,bytes=14464))
        assert expected==case['identities']
        assert {(x['src'],x['token'],x['dst']) for x in expected}==original_sets
        identities[policy]=expected
    runs=[];all_payload_packets=all_ack_packets=all_port_txs=all_work=0
    tables=read(analysis/'conditions.json');ports_report=read(analysis/'ports.json')
    for rate in cfg['upper_rates']:
        topology=read(network/f'topology-{rate}/topology.json');nodes={n['id']:n for n in topology['nodes']}
        adjacency=defaultdict(list);link_at={}
        for link in topology['links']:
            s,d=link['src'],link['dst'];sp,dp=link['src_port'],link['dst_port']
            plane=nodes[d]['plane'] if nodes[d]['kind']!='gpu' else nodes[s]['plane']
            adjacency[s,plane].append((d,sp,dp));adjacency[d,plane].append((s,dp,sp))
            link_at[s,sp]=link_at[d,dp]=link
        def path(src,dst,plane):
            q=deque([(src,[])]);seen={src}
            while q:
                node,edges=q.popleft()
                if node==dst:return edges
                for nxt,port,reverse in adjacency[node,plane]:
                    if nxt in seen or (nxt<4 and nxt!=dst):continue
                    seen.add(nxt);q.append((nxt,edges+[(node,port,nxt,reverse)]))
            raise AssertionError('unreachable explicit plane')
        for policy in cfg['policies']:
            name=f'{rate}-{policy}';r=read(network/name/'result.json');tr=r['persistent_transport']
            input_work=read(plans/(policy+'.json'));stream_cfg={s['id']:s for s in input_work['streams']}
            assert len(stream_cfg)==len(tr['streams'])==96
            assert r['passed'] and r['native_network_executed'] and r['drained'] and tr['physical_drained'] and tr['closed']
            assert tr['inflight_packets']==0 and r['completed_work']==117
            assert all(s['qp_creations']==1 and s['queue_depth']==32 for s in tr['streams'])
            stream_work=defaultdict(list);work_by_id={w['id']:w for w in tr['work']}
            expected_segments=Counter();segment_to_work={};prefix=Counter()
            for w in input_work['work']:
                wid,sid=w['id'],w['stream'];actual=work_by_id[wid]
                assert w['after']==[] and w['bytes']==14464
                assert actual['ready_ns']==actual['published_ns']==0
                assert actual['begin']==prefix[sid] and actual['end']==prefix[sid]+14464
                for offset in range(0,14464,1000):
                    length=min(1000,14464-offset);seq=prefix[sid]+offset
                    expected_segments[sid,seq,length]+=1;segment_to_work[sid,seq]=wid
                prefix[sid]+=14464;stream_work[sid].append(wid)
            source_cols=tr['source_packet_columns']
            source_rows=[dict(zip(source_cols,x)) for x in tr['source_packets']]
            assert Counter((p['stream'],p['sequence'],p['payload_bytes']) for p in source_rows)==expected_segments
            assert len(source_rows)==1755 and sum(p['payload_bytes'] for p in source_rows)==1692288
            for s in tr['streams']:
                sid=s['id'];assert s['submitted_bytes']==s['published_bytes']==s['snd_nxt']==s['snd_una']==prefix[sid]
                assert s['accepted_work']==s['ack_completed_work']==len(stream_work[sid])
            capture=r['packet_capture'];assert capture['complete']
            events=defaultdict(list);cols=capture['columns'];codes=capture['event_codes']
            for values in capture['events']:
                event=dict(zip(cols,values));assert event['event']!=codes['drop']
                events[event['packet_uid']].append(event)
            data_by_uid={x['packet_uid']:x for x in source_rows};assert len(data_by_uid)==1755
            expected_acks=Counter();received_data=defaultdict(list);actual_acks=Counter();work_ack_candidates=Counter()
            port_bytes=defaultdict(Counter);port_busy=Counter();port_starts=defaultdict(list)
            for uid,rows in events.items():
                txs=[e for e in rows if e['event']==codes['tx']]
                assert txs
                terminal=[e for e in rows if e['event']==codes['rx'] and e['node']<4]
                assert len(terminal)==1
                destination=terminal[0]['node'];origin=txs[0]['node'];plane=txs[0]['port']-1
                assert origin<4 and plane in (0,1)
                expected_path=path(origin,destination,plane)
                assert [(e['node'],e['port']) for e in txs]==[(n,p) for n,p,_,_ in expected_path]
                assert terminal[0]['port']==expected_path[-1][3]
                if uid in data_by_uid:
                    d=data_by_uid[uid];sid=d['stream'];cfg_s=stream_cfg[sid]
                    assert origin==d['src']==cfg_s['src'] and destination==d['dst']==cfg_s['dst']
                    assert plane==cfg_s['plane'] and txs[0]['port']==d['nic']==cfg_s['source_nic']
                    assert txs[0]['time_ns']==d['time_ns']
                    assert all(e['ip_protocol']==17 and e['sequence']==d['sequence']
                               and e['model_bytes']==d['payload_bytes']+36 for e in txs)
                    expected_acks[destination,origin,plane,d['sequence']+d['payload_bytes']]+=1
                    received_data[segment_to_work[sid,d['sequence']]].append(terminal[0]['time_ns'])
                    kind='data';all_payload_packets+=1
                else:
                    assert all(e['ip_protocol']==252 and e['model_bytes']==48 for e in txs)
                    seq=txs[0]['sequence'];assert all(e['sequence']==seq for e in txs)
                    actual_acks[origin,destination,plane,seq]+=1
                    work_ack_candidates[destination,origin,plane,seq,terminal[0]['time_ns']]+=1
                    kind='control';all_ack_packets+=1
                for e in txs:
                    key=e['node'],e['port'];link=link_at[key];n=e['model_bytes']
                    # Time(int64x64) rounds to nearest ns at the pinned resolution.
                    quantum=(n*8000000000*2+link['rate_bps'])//(2*link['rate_bps'])
                    port_bytes[key][kind]+=n;port_busy[key]+=quantum
                    port_starts[key].append((e['time_ns'],quantum));all_port_txs+=1
            assert actual_acks==expected_acks
            used_candidates=Counter()
            for w in tr['work']:
                assert len(received_data[w['id']])==15
                assert w['accepted_ns']==max(received_data[w['id']])
                s=stream_cfg[w['stream']]
                key=s['src'],s['dst'],s['plane'],w['end'],w['acked_ns']
                used_candidates[key]+=1
                assert used_candidates[key]<=work_ack_candidates[key]
                assert w['accepted_ns']<=w['acked_ns']<=r['finish_ns'];all_work+=1
            for key,items in port_starts.items():
                items.sort()
                assert all(t+duration<=next_t for (t,duration),(next_t,_) in zip(items,items[1:]))
            for port in r['ports']:
                key=port['node'],port['port'];assert port['data_tx_bytes']==port_bytes[key]['data']
                assert port['control_tx_bytes']==port_bytes[key]['control']
                assert port['model_tx_bytes']==sum(port_bytes[key].values())
                assert port['pfc_pause']==port['pfc_resume']==0
                if 'queue_metrics' in port:assert port['queue_metrics']['final_bytes']==0
                row=next(x for x in ports_report if (x['condition'],x['node'],x['port'])==(name,*key))
                assert row['serialized_service_ns']==port_busy[key]
            table=next(x for x in tables if x['condition']==name)
            assert table['max_directed_port_service_ns']==max(port_busy.values())
            assert table['finish_ns']==r['finish_ns']==max(w['acked_ns'] for w in tr['work'])
            assert table['source_payload_bytes']==1692288 and table['physical_drained']
            assert table['writes']==117 and table['source_data_packets']==1755
            runs.append(dict(condition=name,checked_source_packets=1755,checked_ack_packets=sum(actual_acks.values()),
                checked_work=117,finish_ns=r['finish_ns'],directed_port_service_ns=max(port_busy.values()),
                result_sha256=digest(network/name/'result.json')))
    result=dict(verified=True,network_conditions=6,source_packets=all_payload_packets,
        ack_packets=all_ack_packets,physical_transmissions=all_port_txs,work_checked=all_work,
        original_source_destination_tokens=117,permutation_policies_verified=3,
        ack_identity_scope='endpoint/plane/sequence multiset and terminal arrival time candidates; no ACK QP id in public capture',
        cases=runs,modern_end_to_end=False,hardware_calibrated=False,new_algorithm_proven=False)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('verified','source_packets','ack_packets','physical_transmissions')}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('inputs','plans','network','analysis','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();check(a.inputs,a.plans,a.network,a.analysis,a.output)
