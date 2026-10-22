"""Independently audit raw routes, actual paths, work callbacks and switch integrals."""
import argparse
from collections import Counter, defaultdict, deque
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p): return json.loads(p.read_text())
def save(p, v): p.write_text(json.dumps(v, ensure_ascii=False, indent=2)+'\n')


def ecmp(words, seed):
    """Independent uint32 transcription of the locked switch's 12-byte hash."""
    mask=2**32-1
    def rotate(v,n): return ((v<<n)|(v>>(32-n)))&mask
    h=seed
    for word in words:
        k=(word*0xcc9e2d51)&mask; k=(rotate(k,15)*0x1b873593)&mask
        h=rotate(h^k,13); h=(h*5+0xe6546b64)&mask
    h^=12; h^=h>>16; h=(h*0x85ebca6b)&mask; h^=h>>13
    h=(h*0xc2b2ae35)&mask; return h^(h>>16)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('prepared', 'run', 'analysis', 'repeat_analysis', 'output'):
        p.add_argument('--'+name.replace('_','-'), type=Path, required=True)
    p.add_argument('--source-root', type=Path, default=ROOT)
    a=p.parse_args(); observed=a.prepared/'observed'; truth=a.prepared/'truth'; idx=load(observed/'index.json'); cfg=idx['config']
    summary=load(a.analysis/'summary.json'); all_rounds=load(a.analysis/'rounds.json')
    assert summary['observed_index_sha256']==sha(observed/'index.json') and not summary['analysis_reads_truth']
    for name,key in (('ports.csv','ports_csv_sha256'),('packets.csv','packets_csv_sha256'),('rounds.json','rounds_sha256')):
        assert sha(a.analysis/name)==summary[key]
    for name in ('summary.json','ports.csv','packets.csv','rounds.json'):
        assert (a.analysis/name).read_bytes()==(a.repeat_analysis/name).read_bytes()
    for name,info in idx['artifacts'].items(): assert sha(observed/name)==info['sha256']
    manifest=load(truth/'manifest.json'); provenance=load(truth/'provenance.json'); maps=load(truth/'placements.json')['placements']
    assert sha(truth/'placements.json')==cfg['placements_sha256']==provenance['placements_sha256']
    assert sha(truth/'manifest.json')==provenance['manifest_sha256']
    choices=[]
    for row in manifest['files']:
        file=a.source_root/row['file']['path']; assert sha(file)==row['file']['sha256']
        raw=load(file); choices.append([raw[e][str(cfg['source_layer'])][0] for e in cfg['decode_entries']])
    assert choices==provenance['selections']
    independent_work={}
    for condition in cfg['placement_conditions']:
        owners=maps[condition][0]; assert Counter(owners)=={r:64 for r in range(4)}
        expected={}; previous=[]
        for step in range(4):
            current=[]
            for src in range(4):
                # Scan rank membership independently of the preparation set expression.
                for dst in range(4):
                    if src==dst or not any(owners[e]==dst for e in choices[src][step]): continue
                    work=step*16+4*src+dst; current.append(work)
                    expected[work]=dict(id=work,stream=4*src+dst,bytes=14464,
                        after=[dict(work=w,event='acked') for w in previous])
            previous=current
        input_work=load(observed/('work-'+condition+'.json'))
        assert input_work['work']==list(expected.values())
        assert [(s['id'],s['src'],s['dst'],s['wire_port'],s['source_nic'],s['destination_nic'],s['queue_depth']) for s in input_work['streams']]==[
            (4*s+d,2*s,2*d,21000+4*s+d,1,1,1) for s in range(4) for d in range(4) if s!=d]
        assert provenance['work_source_mapping'][condition]==[dict(work=w,entry=1+w//16,
            source_rank=(w%16)//4,destination_rank=w%4) for w in expected]
        independent_work[condition]=expected
    with (a.analysis/'packets.csv').open() as f: packet_csv=list(csv.DictReader(f))
    with (a.analysis/'ports.csv').open() as f: port_csv=list(csv.DictReader(f))
    totals=Counter(); sources={}; libraries={}; audited=[]; max_post_dequeue_data=0
    for case_summary in summary['cases']:
        case=case_summary['case']; condition=case_summary['condition']; network=case_summary['network']
        expected=independent_work[condition]; result_path=a.run/case/'result.json'; x=load(result_path); t=x['persistent_transport']
        assert sha(result_path)==case_summary['result_sha256']
        assert x['passed'] and x['native_network_executed'] and x['drained'] and x['fault_drops']==0
        assert t['closed'] and t['physical_drained'] and t['inflight_packets']==0
        assert x['gpu_count']==8 and x['switch_count']==10 and x['planes']==1
        inp=load(a.run/case/'inputs.json'); assert inp['native_commit']=='3964b1d41cb5f7b51004c316b0eae1a3713c2498'
        for name,digest in inp['hashes'].items(): assert sha(Path(name))==digest
        for name in x['loaded_libraries']:
            if 'libns3' in name:
                assert name.startswith('/dev/shm/moe-fault-20261021/native/output/lib/') and 'ub' not in Path(name).name.lower()
                libraries[name]=sha(Path(name))
        physical=load(a.run/('topology-'+network)/'topology.json'); endpoints={}; adjacency=defaultdict(list)
        for link in physical['links']:
            expected_rate=100000000000 if link['tier']=='access' or network=='all100g' else 25000000000
            assert link['rate_bps']==expected_rate and link['delay_ns']==1000
            for n,pn,peer,pp in ((link['src'],link['src_port'],link['dst'],link['dst_port']),
                                  (link['dst'],link['dst_port'],link['src'],link['src_port'])):
                endpoints[n,pn]=(peer,pp,link['rate_bps'],link['delay_ns'],link['tier']); adjacency[n].append(peer)
        assert Counter(n['kind'] for n in physical['nodes'])==dict(gpu=8,tor=4,agg=4,core=2)
        capture=x['packet_capture']; assert capture['complete'] and capture['omitted_events']==0
        assert capture['observations']==len(capture['events']) and len(capture['events'])<100000
        assert capture['columns']==['time_ns','node','port','event','packet_uid','message_id','ip_protocol',
            'sequence','model_bytes','ecn','egress_queue_bytes','data_payload_bytes']
        by_uid=defaultdict(list); queue_state={key:[0,0,0,0,0] for key in endpoints if key[0]>=8}
        port_tx=defaultdict(Counter); packet_queued={}; queue_residence=Counter(); data_queue=Counter()
        for e in capture['events']:
            tm,n,pn,kind,uid,mid,protocol,seq,size,ecn,qbytes,payload=e
            assert mid==-1 and protocol in (17,252) and ecn==0 and kind in (0,1,3,4)
            by_uid[uid].append(e)
            if kind==0:
                port_tx[n,pn]['model']+=size; port_tx[n,pn]['data' if protocol==17 else 'control']+=size
                if protocol==17: port_tx[n,pn]['packets']+=1
            if kind in (3,4):
                key=n,pn; state=queue_state[key]; last,occupancy,area,peak,changes=state
                assert tm>=last; area+=occupancy*(tm-last)
                if kind==3:
                    occupancy+=size; assert (n,pn,uid) not in packet_queued; packet_queued[n,pn,uid]=(tm,size)
                    if protocol==17:data_queue[key]+=size
                else:
                    assert qbytes==occupancy
                    start,bytes_=packet_queued.pop((n,pn,uid)); assert bytes_==size
                    queue_residence[key]+=size*(tm-start); occupancy-=size
                    if protocol==17:
                        data_queue[key]-=size
                        max_post_dequeue_data=max(max_post_dequeue_data,data_queue[key])
                        # Locked switch marks at dequeue, after MMU byte removal;
                        # aggregate hook peak includes the removed frame and ACKs.
                        assert data_queue[key]<=32768
                assert occupancy>=0
                if kind==3: assert occupancy==qbytes
                queue_state[key]=[tm,occupancy,area,max(peak,occupancy),changes+1]
        assert not packet_queued
        for port in x['ports']:
            key=port['node'],port['port']; peer,pp,rate,delay,tier=endpoints[key]
            assert (port['peer'],port['rate_bps'],port['delay_ns'],port['tier'])==(peer,rate,delay,tier)
            assert (port['model_tx_bytes'],port['data_tx_bytes'],port['control_tx_bytes'],port['data_packets'])==tuple(port_tx[key][k] for k in ('model','data','control','packets'))
            if key[0]>=8:
                last,occupancy,area,peak,changes=queue_state[key]; assert occupancy==0 and area==queue_residence[key]
                assert port['queue_metrics']==dict(area_byte_ns=area,peak_bytes=peak,final_bytes=0,changes=changes)
                totals['switch_ports']+=1
            csv_row=next(r for r in port_csv if r['case']==case and int(r['node'])==key[0] and int(r['port'])==key[1])
            for k in ('peer','rate_bps','model_tx_bytes','data_tx_bytes','control_tx_bytes','data_packets'): assert int(csv_row[k])==port[k]
            assert csv_row['tier']==tier and int(csv_row['queue_area_byte_ns'])==port.get('queue_metrics',{}).get('area_byte_ns',0)
            assert int(csv_row['queue_peak_bytes'])==port.get('queue_metrics',{}).get('peak_bytes',0)
        work={w['id']:w for w in t['work']}; assert work.keys()==expected.keys()
        stream={s['id']:s for s in t['streams']}; received=defaultdict(list); acked=defaultdict(list); source_rows={}
        seq_offset=Counter(); work_bytes=Counter(); packet_metrics={}; round_load=defaultdict(Counter)
        for row in t['source_packets']:
            tm,sid,src,dst,nic,seq,payload,uid=row; assert uid not in source_rows
            assert (src,dst,nic)==(stream[sid]['src'],stream[sid]['dst'],1) and seq==seq_offset[sid]
            seq_offset[sid]+=payload; assert 0<payload<=1000
            w=next(w for w in work.values() if w['stream']==sid and w['begin']<=seq<w['end'])
            assert seq+payload<=w['end']; work_bytes[w['id']]+=payload; source_rows[uid]=(row,w['id'])
        assert work_bytes=={w:14464 for w in work}
        for sid,s in stream.items():
            assert s['qp_creations']==s['queue_depth']==1 and s['submitted_bytes']==s['published_bytes']==s['snd_nxt']==s['snd_una']==seq_offset[sid]
            assert s['source_payload_bytes']==seq_offset[sid]
            assert s['source_data_packets']==sum(row[0][1]==sid for row in source_rows.values())
            assert s['accepted_work']==s['ack_completed_work']==sum(w['stream']==sid for w in work.values())
        for uid,events in by_uid.items():
            tx=[e for e in events if e[3]==0]; rx=[e for e in events if e[3]==1]
            assert len(tx)==len(rx) and len(tx)>=2
            first,last=tx[0],rx[-1]; src,dst=first[1],last[1]; assert src<8 and dst<8 and src!=dst
            assert len({e[1] for e in tx})==len(tx) and all(e[1]>=8 for e in tx[1:])
            protocol=first[6]; seq=first[7]
            assert all(e[6:9]==first[6:9] and e[11]==first[11] for e in events)
            serialization=0; propagation=0; waiting=0; waits=[]
            for i,(sent,got) in enumerate(zip(tx,rx)):
                peer,pp,rate,delay,tier=endpoints[sent[1],sent[2]]
                assert (got[1],got[2])==(peer,pp)
                duration=got[0]-sent[0]-delay
                assert abs(duration-sent[8]*8e9/rate)<=1
                serialization+=duration; propagation+=delay
                if i:
                    assert tx[i][1]==rx[i-1][1]
                    enq=next(e for e in events if e[3]==3 and (e[1],e[2])==(sent[1],sent[2]))
                    deq=next(e for e in events if e[3]==4 and (e[1],e[2])==(sent[1],sent[2]))
                    assert enq[0]==rx[i-1][0] and deq[0]==sent[0]
                    waiting+=deq[0]-enq[0]
                    waits.append(dict(node=sent[1],port=sent[2],peer=peer,tier=tier,wait_ns=deq[0]-enq[0]))
            distance={dst:0}; bfs=deque([dst])
            while bfs:
                node=bfs.popleft()
                for peer in adjacency[node]:
                    if peer not in distance:
                        distance[peer]=distance[node]+1
                        if peer>=8:bfs.append(peer)
            assert len(tx)==distance[src] and last[0]-first[0]==serialization+propagation+waiting
            sid_for_hash=next(sid for sid,s in stream.items() if (s['src'],s['dst'])==((src,dst) if protocol==17 else (dst,src)))
            wire=stream[sid_for_hash]['wire_port']
            for sent in tx[1:]:
                candidates=[pn for (n,pn),(peer,pp,rate,delay,tier) in endpoints.items()
                    if n==sent[1] and distance[peer]==distance[n]-1]
                selected=candidates[ecmp([0x0b000001+src*32,0x0b000001+dst*32,wire|(wire<<16)],11+sent[1])%len(candidates)]
                assert selected==sent[2]
                totals['switch_ecmp_choices']+=1
            if protocol==17:
                row,wid=source_rows[uid]
                assert row==[first[0],work[wid]['stream'],src,dst,first[2],seq,first[11],uid]
                assert first[2]==last[2]==1 and first[8]==first[11]+36
                received[wid].append(last[0])
            else:
                sid=next(sid for sid,s in stream.items() if (s['src'],s['dst'])==(dst,src))
                wid=next(w['id'] for w in work.values() if w['stream']==sid and w['begin']<seq<=w['end'])
                if seq==work[wid]['end']:acked[wid].append(last[0])
                assert first[8]==48
            step=wid//16
            for e in tx:round_load[step][e[1],e[2]]+=e[8]
            rec=dict(case=case,uid=uid,work=wid,step=step,protocol=protocol,sequence=seq,src=src,dst=dst,
                model_bytes=first[8],payload_bytes=first[11],first_tx_ns=first[0],last_rx_ns=last[0],hops=len(tx),
                serialization_ns=serialization,propagation_ns=propagation,switch_wait_ns=waiting,
                path='>'.join(str(e[1]) for e in tx)+'>'+str(dst))
            packet_metrics[uid]=dict(**rec,waits=waits)
            saved=next(r for r in packet_csv if r['case']==case and int(r['uid'])==uid)
            assert saved=={k:str(v) for k,v in rec.items()}
            totals['packet_lineages']+=1; totals['physical_transmissions']+=len(tx)
        assert len(source_rows)==sum(e[0][6]==17 for e in by_uid.values())
        for wid,w in work.items():
            assert w['accepted_ns']==max(received[wid]) and w['acked_ns']==min(acked[wid])
            ready=0 if wid//16==0 else max(prev['acked_ns'] for prev in work.values() if prev['id']//16==wid//16-1)
            assert w['ready_ns']==ready and w['published_ns']>=ready
            assert w['published_ns']<=min(r[0][0] for r in source_rows.values() if r[1]==wid)
            assert (w['end']-w['begin'],w['bytes'])==(14464,14464)
            totals['work_callbacks']+=1
        case_rounds=[r for r in all_rounds if r['case']==case];assert len(case_rounds)==4
        for r in case_rounds:
            group=[w for w in work.values() if w['id']//16==r['step']]; finish=max(w['acked_ns'] for w in group)
            assert r['ready_ns']==group[0]['ready_ns'] and r['acked_ns']==finish
            assert r['accepted_ns']==max(w['accepted_ns'] for w in group) and r['duration_ns']==finish-r['ready_ns'] and r['work_count']==len(group)
            assert {c['work'] for c in r['critical']}=={w['id'] for w in group if w['acked_ns']==finish}
            for c in r['critical']:
                w=work[c['work']];data=packet_metrics[c['data_uid']];ack=packet_metrics[c['ack_uid']]
                assert data['work']==ack['work']==w['id'] and data['protocol']==17 and ack['protocol']==252
                assert data['last_rx_ns']==w['accepted_ns'] and ack['last_rx_ns']==w['acked_ns']
                assert c['data_path']==data['path'] and c['ack_path']==ack['path'] and c['data_waits']==data['waits'] and c['ack_waits']==ack['waits']
                comp=c['components'];assert comp['source_tail_offset_ns']==data['first_tx_ns']-r['ready_ns']
                assert comp['ack_generation_gap_ns']==ack['first_tx_ns']-data['last_rx_ns']
                for prefix,packet in (('data',data),('ack',ack)):
                    for key in ('serialization_ns','propagation_ns','switch_wait_ns'):assert comp[prefix+'_'+key]==packet[key]
                assert min(comp.values())>=0 and sum(comp.values())==r['duration_ns']
            b=r['busiest_port'];key=b['node'],b['port'];round_bytes=round_load[r['step']]
            assert b['model_bytes']==round_bytes[key] and b['peer']==endpoints[key][0] and b['tier']==endpoints[key][4]
            assert b['serialization_lower_bound_ns']==max(v*8e9/endpoints[k][2] for k,v in round_bytes.items())
        assert sum(r['duration_ns'] for r in case_rounds)==case_summary['acked_ns']==max(w['acked_ns'] for w in work.values())
        assert case_summary['finish_ns']==x['finish_ns']==max(e[0] for rows in by_uid.values() for e in rows)
        assert case_summary['accepted_ns']==max(w['accepted_ns'] for w in work.values())
        assert case_summary['work_count']==len(work) and case_summary['data_source_packets']==len(source_rows)
        assert case_summary['data_payload_bytes']==len(work)*14464
        assert case_summary['total_queue_area_byte_ns']==sum(s[2] for s in queue_state.values())
        assert case_summary['max_queue_bytes']==max(s[3] for s in queue_state.values())
        assert case_summary['ecn_ce_seen']==case_summary['pfc_pause']==case_summary['fault_drops']==0
        b=case_summary['busiest_port'];key=b['node'],b['port']
        assert b['model_tx_bytes']/b['rate_bps']==max(port['model_tx_bytes']/port['rate_bps'] for port in x['ports'])
        assert (b['peer'],b['tier'],b['rate_bps'],b['model_tx_bytes'])==(endpoints[key][0],endpoints[key][4],endpoints[key][2],port_tx[key]['model'])
        audited.append(dict(case=case,works=len(work),packets=len(by_uid),capture_events=len(capture['events']),result_sha256=sha(result_path)))
        totals['capture_events']+=len(capture['events']);totals['rounds']+=4
    assert len(packet_csv)==totals['packet_lineages'] and len(summary['cases'])==6
    assert len(port_csv)==sum(len(load(a.run/r['case']/'result.json')['ports']) for r in summary['cases'])
    assert (a.run/'repeat/result.json').read_bytes()==(a.run/'all100g-contiguous/result.json').read_bytes()
    execution=load(a.run/'execution.json');assert execution['completed'] and execution['commands']==9
    for row in load(a.run/'commands.json'):assert row['exit_code']==0
    for name in ('backend/rdma/fabric.cc','backend/rdma/persistent_streams.cc','backend/rdma/stream_probe.cc','backend/rdma/gin_work_queue.hh',
        'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3/src/point-to-point/model/switch-node.cc',
        'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3/src/point-to-point/model/switch-mmu.cc'):
        sources[name]=sha(ROOT/name)
    a.output.mkdir();save(a.output/'verification.json',dict(passed=True,**totals,cases=audited,
        summary_sha256=sha(a.analysis/'summary.json'),repeat_result_byte_identical=True,repeat_analysis_byte_identical=True,
        independent_raw_requests=4,independent_routes=16,max_data_bytes_after_dequeue=max_post_dequeue_data,
        native_libraries_sha256=libraries,platform_source_sha256=sources,
        modern_GIN_modeled=False,hardware_calibrated=False,full_inference_validated=False,goal_complete=False))
    print(json.dumps(dict(passed=True,**totals)))


if __name__=='__main__':main()
