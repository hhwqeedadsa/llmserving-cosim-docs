"""Independent endpoint packets, feedback, byte prefixes and causal FIFO replay.

For the healthy finite public capacity program. All endpoint events are required;
intermediate paths are checked by shortest-hop conservation, not invented traces.
No platform or simulator modules are imported.
"""
import argparse
from collections import Counter,defaultdict,deque
import json
from pathlib import Path


def need(ok,why):
    if not ok:raise AssertionError(why)


def audit(r,w):
    t=r['persistent_transport'];need(r['passed'] and r['drained'] and t['closed'] and t['physical_drained'],'drain')
    cap=r['packet_capture'];n=r['gpu_count'];need(cap['complete'] and not cap['omitted_events'] and
        set(cap['selection']['nodes'])==set(range(n)) and cap['selection']['start_ns']==0 and cap['selection']['end_ns']>r['finish_ns'],'all endpoint capture')
    if w['schema_version'].endswith('/4'):need(t['execution_limits']==w['limits'] and t['link_transmissions']<=w['limits']['max_link_transmissions'],'effective limits')
    streams={s['id']:s for s in w['streams']};wire_stream={s['wire_port']:s['id'] for s in w['streams']}
    remote={x['id']:x for x in t['work']};local={x['id']:x for x in r['local_work']};ops={x['id']:x for x in w['work']}
    need(len(ops)==len(w['work'])==r['completed_work'] and set(remote)=={i for i,o in ops.items() if o['kind']=='write'} and
        set(local)==set(ops)-set(remote),'work coverage')
    rows=defaultdict(list);totals=Counter()
    for x in remote.values():rows[x['stream']].append(x)
    for sid,s in streams.items():
        pos=0
        for ticket,row in enumerate(sorted(rows[sid],key=lambda x:x['ticket'])):
            op=ops[row['id']];need(op['stream']==sid and row['ticket']==ticket and row['begin']==pos and row['bytes']==op['bytes'] and row['end']==pos+op['bytes'],'per-stream work byte ranges')
            pos=row['end']
        totals[sid]=pos
    source={};packets=[dict(zip(t['source_packet_columns'],p)) for p in t['source_packets']]
    for p in packets:
        sid=p['stream'];s=streams[sid];uid=p['packet_uid'];begin=p['sequence'];end=begin+p['payload_bytes']
        need(uid not in source and p['src']==s['src'] and p['dst']==s['dst'] and p['nic']==s['source_nic'] and 0<=begin<end<=totals[sid],'actual source identity')
        owners=[x for x in rows[sid] if x['begin']<=begin<end<=x['end']];need(len(owners)==1,'packet inside one published work')
        need(p['time_ns']>=owners[0]['published_ns'],'no packet before publication');source[uid]=p
    wire=cap['wire_headers'];need(len(wire['event_header_indices'])==len(cap['events']),'wire coverage')
    tx=set();rx=set();prefix=Counter();accept=defaultdict(list);acks=defaultdict(list);ack_tx={};ack_rx={};data_bytes=Counter();data_frames={}
    actual_sends=Counter()
    for i,row in enumerate(cap['events']):
        e=dict(zip(cap['columns'],row));h=dict(zip(wire['columns'],wire['headers'][wire['event_header_indices'][i]]))
        uid=e['packet_uid'];sid=wire_stream[h['source_port']];kind=e['event'];now=e['time_ns'];node=e['node'];s=streams[sid]
        need(e['message_id'] in (-1,sid),'optional internal association agrees with actual wire identity')
        e['derived_stream']=sid
        need(h['packet_uid']==uid and h['ip_protocol']==e['ip_protocol'] and h['ports_present'] and
            h['source_port']==h['destination_port']==s['wire_port'],'actual wire ports')
        if kind not in (0,1):continue
        need(e['ip_protocol'] in (17,252,253),'unexpected control class in healthy fixture')
        if e['ip_protocol']==17:
            need(uid in source,'source packet recorded');p=source[uid];begin=p['sequence'];end=begin+p['payload_bytes']
            need(e['sequence']==begin and e['data_payload_bytes']==p['payload_bytes'],'actual wire byte range')
            data_frames[uid]=e['model_bytes']
            if kind==0:
                need(uid not in tx and node==s['src'] and e['port']==s['source_nic'] and now==p['time_ns'],'physical source TX');tx.add(uid);actual_sends[sid]+=1;data_bytes[sid]+=p['payload_bytes']
            else:
                need(uid not in rx and node==s['dst'] and e['port']==s['destination_nic'],'physical data RX');rx.add(uid)
                # Legacy native in-order receiver discards holes; no invented SR cache.
                if begin==prefix[sid]:prefix[sid]=end
                elif begin>prefix[sid]:pass
                else:need(end<=prefix[sid],'partial duplicate overlap')
                accept[sid].append((now,prefix[sid]))
        elif kind==0:
            need(uid not in ack_tx and node==s['dst'] and e['port']==s['destination_nic'],'physical feedback TX');ack_tx[uid]=e
        else:
            need(uid not in ack_rx and node==s['src'] and e['port']==s['source_nic'],'physical feedback RX');ack_rx[uid]=e;acks[sid].append((now,e['sequence']))
    need(tx==rx==set(source) and set(ack_tx)==set(ack_rx),'healthy endpoint packet conservation')
    for uid,e in ack_tx.items():
        v=ack_rx[uid];need(e['derived_stream']==v['derived_stream'] and e['sequence']==v['sequence'] and e['model_bytes']==v['model_bytes'] and e['time_ns']<=v['time_ns'],'actual feedback arrival')
    need(len(ack_tx)==len(packets),'one ACK/NACK for every received packet')
    for i,op in ops.items():
        x=remote[i] if i in remote else local[i]
        ready=max((remote[d['work']][d['event']+'_ns'] if d['work'] in remote else local[d['work']]['completed_ns'] for d in op['after']),default=0)
        need(x['ready_ns']==ready,'real dependency readiness')
        if i in remote:
            sid=x['stream'];end=x['end'];need(x['accepted_ns']==min(time for time,p in accept[sid] if p>=end) and
                x['acked_ns']==min(time for time,p in acks[sid] if p>=end),'actual byte-prefix acceptance/ACK')
        else:need(x['rank']==op['rank'] and x['duration_ns']==op['duration_ns'] and x['completed_ns']==x['start_ns']+op['duration_ns'],'declared local duration')
    for sid,s in streams.items():
        prior=0
        for x in sorted(rows[sid],key=lambda x:x['ticket']):
            blocking=next((y['acked_ns'] for y in rows[sid] if y['ticket']==x['ticket']-s['queue_depth']),0)
            need(x['published_ns']==max(x['ready_ns'],prior,blocking),'queue credit publication');prior=x['published_ns']
        need(prefix[sid]==totals[sid] and max(p for _,p in acks[sid])==totals[sid],'final stream prefix')
    local_by_rank=defaultdict(list)
    for x in r['local_work']:local_by_rank[x['rank']].append(x)
    for rank,items in local_by_rank.items():
        last=0
        for x in sorted(items,key=lambda x:(x['ready_ns'],x['id'])):
            need(x['start_ns']==max(last,x['ready_ns']),'local FIFO');last=x['completed_ns']
    need(len(t['streams'])==len(streams),'stream state coverage')
    for s in t['streams']:
        sid=s['id'];need(s['qp_creations']==1 and s['submitted_bytes']==s['published_bytes']==s['snd_una']==totals[sid] and
            s['source_data_packets']==actual_sends[sid] and s['source_payload_bytes']==data_bytes[sid] and
            s['accepted_work']==s['ack_completed_work']==len(rows[sid]),'persistent lifetime state')
    # Check total traffic on all physical ports against the actual endpoint
    # packets and independently enumerated shortest-hop lengths. No ECMP path
    # is inferred from static topology; this checks all shortest alternatives.
    graph=defaultdict(list)
    for p in r['ports']:
        graph[p['node']].append(p['peer'])
        if p['node']>=n:need(p['queue_metrics']['final_bytes']==0,'switch port queue drain')
    distance={}
    for dst in {s['dst'] for s in streams.values()}:
        d={dst:0};q=deque([dst])
        while q:
            u=q.popleft()
            for v in graph[u]:
                if v not in d:
                    d[v]=d[u]+1
                    if v>=n:q.append(v)
        distance[dst]=d
    expected_data=sum(data_frames[uid]*distance[p['dst']][p['src']] for uid,p in source.items())
    expected_ack=sum(e['model_bytes']*distance[streams[e['derived_stream']]['dst']][streams[e['derived_stream']]['src']] for e in ack_tx.values())
    need(sum(p['data_tx_bytes'] for p in r['ports'])==expected_data and sum(p['control_tx_bytes'] for p in r['ports'])==expected_ack,'all-link byte conservation')
    transmissions=sum(p['data_packets'] for p in r['ports'])+sum(distance[streams[e['derived_stream']]['dst']][streams[e['derived_stream']]['src']] for e in ack_tx.values())
    if w['schema_version'].endswith('/4'):need(t['link_transmissions']==transmissions,'all-link transmission count')
    return dict(passed=True,gpus=n,streams=len(streams),work=len(ops),writes=len(remote),local_nodes=len(local),
        source_packets=len(source),source_payload_bytes=sum(data_bytes.values()),physical_link_transmissions=transmissions,
        finish_ns=r['finish_ns'],all_endpoint_packets_and_actual_prefix_callbacks_checked=True,
        intermediate_path_check='shortest-hop byte conservation, not full intermediate packet capture',hardware_calibrated=False,full_MoE_inference=False)


def main():
    p=argparse.ArgumentParser()
    for name in ('result','work','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();r=json.loads(a.result.read_text());w=json.loads(a.work.read_text());report=audit(r,w)
    with a.output.open('x') as f:json.dump(report,f,indent=2,sort_keys=True);f.write('\n')
    print(json.dumps(report))


if __name__=='__main__':main()
