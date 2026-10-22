"""Independent input, endpoint packet, port-service and feedback checks. No network runs."""
import argparse
from collections import Counter, defaultdict, deque
import hashlib
from itertools import product
import json
import math
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[2]


def load(path):return json.loads(path.read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def murmur(words, seed):
    # Byte-oriented x86 Murmur3, independently structured from analytical projection.
    data = struct.pack('<III', *words)
    def rot(x,n):return ((x<<n)|(x>>(32-n))) & 0xffffffff
    h = seed
    for offset in range(0,len(data),4):
        k = int.from_bytes(data[offset:offset+4], 'little')
        k = rot(k*0xcc9e2d51 & 0xffffffff,15)*0x1b873593 & 0xffffffff
        h = (rot(h^k,13)*5+0xe6546b64)&0xffffffff
    h ^= len(data)
    h ^= h>>16
    h = h*0x85ebca6b & 0xffffffff
    h ^= h>>13
    h = h*0xc2b2ae35 & 0xffffffff
    return h^(h>>16)


def route(physical, src, dst, wire):
    edges = defaultdict(list)
    adjacency = defaultdict(list)
    for link in physical['links']:
        for n,p,t in ((link['src'],link['src_port'],link['dst']),
                      (link['dst'],link['dst_port'],link['src'])):
            edges[n].append((p,t,link['rate_bps'],link['delay_ns']))
            adjacency[n].append(t)
    distances={dst:0}
    todo=deque([dst])
    while todo:
        node=todo.popleft()
        for neighbor in adjacency[node]:
            if neighbor not in distances:
                distances[neighbor]=distances[node]+1
                if neighbor>=8:todo.append(neighbor)
    node=src
    result=[]
    while node!=dst:
        choices=[e for e in edges[node] if distances[e[1]]==distances[node]-1]
        if node<8:
            assert len(choices)==1
            selected=choices[0]
        else:
            h=murmur([0x0b000001+src*32,0x0b000001+dst*32,wire*65537],11+node)
            selected=choices[h%len(choices)]
        p,peer,rate,delay=selected
        result.append((node,p,peer,rate,delay))
        node=peer
    return result


def notification_times(mark_times, interval):
    expected=[]
    deadline=None
    pending=False
    for tm in sorted(mark_times):
        while deadline is not None and deadline<=tm:
            if pending:
                expected.append(deadline)
                deadline+=interval
                pending=False
            else:deadline=None
        if deadline is None:
            expected.append(tm)
            deadline=tm+interval
        else:pending=True
    if pending:expected.append(deadline)
    return expected


def check_rp(events, config):
    states={}
    checked=0
    for e in events:
        stream=e['stream']
        s=states.setdefault(stream,dict(rate_bps=100000000000,target_bps=100000000000,
            alpha=1.0,timer_rounds=0,byte_rounds=0,remaining_bytes=10000000,
            alpha_due_ns=-1,recovery_due_ns=-1))
        kind=e['kind'];tm=e['time_ns']
        if kind=='cnp_received':
            s['target_bps']=s['rate_bps']
            s['rate_bps']=max(100000000,math.floor(s['rate_bps']*(1-s['alpha']/2)))
            s['alpha']=(255/256)*s['alpha']+1/256
            s['timer_rounds']=s['byte_rounds']=0
            s['remaining_bytes']=10000000
            s['alpha_due_ns']=tm+55000
            s['recovery_due_ns']=tm+config['recovery_interval_ns']
        elif kind=='rp_timer':
            assert tm==s['recovery_due_ns']
            s['timer_rounds']+=1
            s['recovery_due_ns']+=config['recovery_interval_ns']
            if max(s['timer_rounds'],s['byte_rounds'])>=5:
                step=config['hyper_bps'] if min(s['timer_rounds'],s['byte_rounds'])>5 else config['additive_bps']
                s['target_bps']=min(100000000000,s['target_bps']+step)
            s['rate_bps']=max(100000000,min(100000000000,(s['rate_bps']+s['target_bps'])//2))
        elif kind=='rp_alpha':
            assert tm==s['alpha_due_ns']
            s['alpha']*=255/256
            s['alpha_due_ns']+=55000
        elif kind=='source_sent' and s['alpha_due_ns']>=0:
            s['remaining_bytes']-=e['model_bytes']
            assert s['remaining_bytes']>0, 'This short study does not cross byte threshold'
        elif kind=='rp_bytes':
            raise AssertionError('Unexpected byte recovery')
        for key,value in s.items():
            if key=='alpha':assert abs(e[key]-value)<1e-14,(stream,kind,key,e[key],value)
            else:assert e[key]==value,(stream,kind,key,e[key],value)
        checked+=1
    return checked


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('task','analysis','output'):parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args();task=args.task;out=args.output
    assert not out.exists()
    observed=task/'input/observed';truth=task/'input/truth';runs=task/'run'
    index=load(observed/'index.json');manifest=load(truth/'manifest.json')
    for name,identity in index['artifacts'].items():assert sha(observed/name)==identity['sha256']
    originals=[]
    for entry in manifest['files']:
        path=ROOT/entry['file']['path']
        assert sha(path)==entry['file']['sha256']
        originals.append(load(path))
    # Independently derive destinations by scanning each expert range.
    expected=[]
    for src,request in enumerate(originals):
        for token in range(6):
            choices=request[0]['4'][token]
            for dst in range(4):
                if dst!=src and any(dst*64<=e<(dst+1)*64 for e in choices):
                    expected.append(dict(id=len(expected),stream=src*4+dst,bytes=14464,after=[]))
    assert len(expected)==index['work_count']
    settings=list(product((1,8),(5000,50000),(5000,55000),(48,512)))
    assert [tuple(c[k] for k in ('depth','np_ns','recovery_ns','cnp_bytes')) for c in index['cases']]==settings
    summary=load(args.analysis/'summary.json')
    physical=load(runs/'topology/topology.json');counts=Counter();case_checks=[]
    commands=load(runs/'commands.json')
    assert [c['label'] for c in commands]==['topology']+[c['name'] for c in index['cases']]
    assert all(c['exit_code']==0 for c in commands)
    timeline_hashes={}
    packet_timelines={}
    endpoint_transmissions={}
    for case in index['cases']:
        name=case['name'];description=load(observed/(name+'.json'))
        assert description['work']==expected
        streams={s['id']:s for s in description['streams']}
        assert list(streams)==[s*4+d for s in range(4) for d in range(4) if s!=d]
        for sid,s in streams.items():
            assert (s['src'],s['dst'],s['wire_port'],s['source_nic'],s['destination_nic'],s['queue_depth'])==(sid//4*2,sid%4*2,21000+sid,1,1,case['depth'])
        r=load(runs/name/'result.json');t=r['persistent_transport'];events=t['dcqcn_reference']['events']
        assert r['passed'] and r['drained'] and t['closed'] and t['physical_drained']
        assert r['packet_capture']['complete'] and r['packet_capture']['omitted_events']==0
        assert not r['fault_drops']
        assert all(p['pfc_pause']==p['pfc_resume']==0 for p in r['ports'])
        assert t['dcqcn_reference']['config']==description['dcqcn_reference']
        assert description['dcqcn_reference']==dict(mode='figure7_reference/1',
            np_interval_ns=case['np_ns'],alpha_interval_ns=55000,
            recovery_interval_ns=case['recovery_ns'],recovery_bytes=10000000,
            min_rate_bps=100000000,additive_bps=5000000,hyper_bps=50000000,
            fast_recovery_rounds=5,gain=1/256,cnp_model_bytes=case['cnp_bytes'],max_events=10000)
        source={p[-1]:dict(zip(t['source_packet_columns'],p)) for p in t['source_packets']}
        sampled=[dict(zip(t['source_rate_capture']['columns'],p)) for p in t['source_rate_capture']['samples']]
        ack_samples={p['packet_uid']:p for p in sampled if p['event']==1}
        tx_samples={p['packet_uid']:p for p in sampled if p['event']==0}
        sent_logs={e['packet_uid']:e for e in events if e['kind']=='source_sent'}
        assert set(tx_samples)==set(source)==set(sent_logs)
        for uid,packet in source.items():
            sample=tx_samples[uid]
            assert (sample['stream'],sample['time_ns'],sample['sequence'])==(packet['stream'],packet['time_ns'],packet['sequence'])
            assert sample['rate_bps']==sent_logs[uid]['rate_bps']
        assert all(p['ack_cnp']==0 for p in sampled if p['event'] in (1,2))
        packets=defaultdict(list)
        for p in r['packet_capture']['events']:
            row=dict(zip(r['packet_capture']['columns'],p));packets[row['packet_uid']].append(row)
        cnps={e['packet_uid']:e for e in events if e['kind']=='cnp_generated'}
        all_port_bytes=Counter();data_port_bytes=Counter();base_port_bytes=Counter();ce_by_stream=defaultdict(list)
        tx_by_stream=defaultdict(list);rx_by_stream=defaultdict(list);ack_by_stream=defaultdict(list)
        for uid,observations in packets.items():
            assert len(observations)==2 and {p['event'] for p in observations}=={0,1}
            tx=next(p for p in observations if p['event']==0)
            rx=next(p for p in observations if p['event']==1)
            proto=tx['ip_protocol'];size=tx['model_bytes']
            assert (rx['ip_protocol'],rx['model_bytes'])==(proto,size)
            if proto==17:
                tag=source[uid]['stream'];s=streams[tag]
                assert tx['time_ns']==source[uid]['time_ns'] and tx['node']==s['src'] and rx['node']==s['dst']
                assert tx['sequence']==rx['sequence']==source[uid]['sequence']
                assert size==source[uid]['payload_bytes']+36
                tx_by_stream[tag].append(tx['time_ns']);rx_by_stream[tag].append(rx)
                if rx['ecn']==3:ce_by_stream[tag].append(rx['time_ns'])
                counts['data_packets']+=1
            elif proto==252:
                tag=ack_samples[uid]['stream'];s=streams[tag]
                assert size==48 and rx['time_ns']==ack_samples[uid]['time_ns']
                assert rx['sequence']==ack_samples[uid]['sequence']
                ack_by_stream[tag].append(rx);counts['ack_packets']+=1
            else:
                assert proto==255
                tag=cnps[uid]['stream'];s=streams[tag]
                assert size==case['cnp_bytes']
                for kind,point in (('cnp_tx',tx),('cnp_received',rx)):
                    actual=next(e for e in events if e['kind']==kind and e['packet_uid']==uid)
                    assert actual['time_ns']==point['time_ns'] and actual['stream']==tag
                assert cnps[uid]['time_ns']<=tx['time_ns']
                counts['cnp_packets']+=1
            src,dst=(s['src'],s['dst']) if proto==17 else (s['dst'],s['src'])
            assert (tx['node'],rx['node'])==(src,dst)
            hops=route(physical,src,dst,s['wire_port'])
            # Only endpoint capture: port totals below independently test this path ledger.
            assert rx['time_ns']-tx['time_ns']>=sum(delay for _,_,_,_,delay in hops)
            for node,port,_,_,_ in hops:
                all_port_bytes[node,port]+=size
                if proto==17:data_port_bytes[node,port]+=size
                if proto!=255:base_port_bytes[node,port]+=size
            counts['endpoint_packets']+=1
        for p in r['ports']:
            key=p['node'],p['port']
            assert all_port_bytes[key]==p['model_tx_bytes']
            assert data_port_bytes[key]==p['data_tx_bytes']
            assert p['control_tx_bytes']==all_port_bytes[key]-data_port_bytes[key]
            assert p.get('queue_metrics',{}).get('final_bytes',0)==0
            counts['port_conservation_checks']+=1
        for sid in streams:
            generated=[e['time_ns'] for e in events if e['stream']==sid and e['kind']=='cnp_generated']
            assert generated==notification_times(ce_by_stream[sid],case['np_ns'])
            marks=[e['time_ns'] for e in events if e['stream']==sid and e['kind']=='np_mark']
            assert marks==sorted(ce_by_stream[sid])
            selected=sorted([w for w in t['work'] if w['stream']==sid],key=lambda w:w['begin'])
            for pos,w in enumerate(selected):
                last_rx=max(p['time_ns'] for p in rx_by_stream[sid] if w['begin']<=p['sequence']<w['end'])
                first_ack=min(p['time_ns'] for p in ack_by_stream[sid] if p['sequence']>=w['end'])
                assert (w['accepted_ns'],w['acked_ns'])==(last_rx,first_ack)
                assert w['ready_ns']==0 and w['published_ns']<=last_rx<=first_ack
                if pos>=case['depth']:assert w['published_ns']>=selected[pos-case['depth']]['acked_ns']
                counts['work_callback_checks']+=1
        counts['rp_state_events']+=check_rp(events,description['dcqcn_reference'])
        row=next(v for v in summary['cases'] if v['name']==name)
        assert row['result_sha256']==sha(runs/name/'result.json')
        assert row['acked_ns']==max(w['acked_ns'] for w in t['work'])
        assert row['finish_ns']==r['finish_ns']
        independent_bound=max(base_port_bytes[p['node'],p['port']]*8*10**9/p['rate_bps'] for p in r['ports'])
        assert row['minimum_port_service_ns']==independent_bound<=row['acked_ns']
        assert row['cnp_count']==len(cnps) and row['unique_destination_ce']==sum(map(len,ce_by_stream.values()))
        # Inspect when feedback can still influence THIS connection's actual data.
        timing=[]
        for sid in streams:
            incoming=[e['time_ns'] for e in events if e['stream']==sid and e['kind']=='cnp_received']
            timing.append(dict(stream=sid,last_data_tx_ns=max(tx_by_stream[sid]),
                first_cnp_rx_ns=min(incoming,default=None),
                data_packets_after_first_cnp=sum(tm>=min(incoming) for tm in tx_by_stream[sid]) if incoming else 0))
        timeline=[(v['stream'],v['sequence'],v['time_ns'],v['payload_bytes']) for v in source.values()]
        timeline.sort()
        packet_timelines[name]={(v['stream'],v['sequence']):v for v in source.values()}
        endpoint_transmissions[name]=[p for points in packets.values() for p in points if p['event']==0]
        timeline_hashes[name]=hashlib.sha256(json.dumps(timeline).encode()).hexdigest()
        case_checks.append(dict(case=name,stream_feedback_timing=timing,
            source_timeline_sha256=timeline_hashes[name],source_rate_samples=len(sampled),
            actual_tx_samples=len([v for v in sampled if v['event']==0]),
            tx_samples_below_line_rate=sum(v['rate_bps']<100000000000 for v in sampled if v['event']==0)))
    # Recovery pair invariance is evaluated, never required to obtain a positive study result.
    paired=[]
    for effect in summary['paired_effects']:
        a=next(c for c in summary['cases'] if c['name']==effect['low_case'])
        b=next(c for c in summary['cases'] if c['name']==effect['high_case'])
        assert effect['ack_delta_ns']==b['acked_ns']-a['acked_ns']
        paired.append(dict(factor=effect['factor'],low=effect['low_case'],high=effect['high_case'],
            source_timelines_equal=timeline_hashes[effect['low_case']]==timeline_hashes[effect['high_case']]))
    # First actual divergence in a predeclared frame-size pair: a physical service witness.
    left,right='d8-n5000-r5000-b48','d8-n5000-r5000-b512'
    before,after=packet_timelines[left],packet_timelines[right]
    changed=[key for key in before if before[key]['time_ns']!=after[key]['time_ns']]
    witness=None
    if changed:
        key=min(changed,key=lambda k:before[k]['time_ns'])
        a,b=before[key],after[key]
        predecessors=[]
        for name,packet in ((left,a),(right,b)):
            tx=max((p for p in endpoint_transmissions[name] if p['node']==packet['src'] and p['time_ns']<packet['time_ns']),key=lambda p:p['time_ns'])
            serialization_ns=(tx['model_bytes']*8*10**9+50000000000)//100000000000
            predecessors.append(dict(case=name,node=tx['node'],protocol=tx['ip_protocol'],
                preceding_tx_ns=tx['time_ns'],preceding_bytes=tx['model_bytes'],
                rounded_serialization_ns=serialization_ns,data_tx_ns=packet['time_ns'],
                data_follows_frame_end=packet['time_ns']==tx['time_ns']+serialization_ns))
        witness=dict(stream=key[0],sequence=key[1],source_gpu=a['src'],
            source_packet_delay_ns=b['time_ns']-a['time_ns'],predecessors=predecessors)
    out.mkdir()
    (out/'verification.json').write_text(json.dumps(dict(verified=True,conditions=16,
        counts=dict(counts),cases=case_checks,paired_timeline_checks=paired,first_cost_divergence=witness,
        raw_input_reads_only_in_checker=True,network_runs=0,
        scope='Endpoint UID joins, independent ECMP/port conservation and RP/NP functional state; not hardware calibration'),indent=2)+'\n')
    print(json.dumps(dict(verified=True,counts=dict(counts))))


if __name__=='__main__':main()
