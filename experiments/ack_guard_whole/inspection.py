# Frozen inspector extension from sequential_externality/analyze.py SHA256 63a340b8643dac9b40a7da7e5f79572b83fed54e0f486fc23292328a10c542d4
# The original inspector remains unchanged for historical frozen studies.
"""Independent source, lifecycle, packet residence and matched-call evaluation."""
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path


def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def write_csv(p,rows):
    assert rows
    with p.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator='\n');writer.writeheader();writer.writerows(rows)


from guard_prefix import prefix

def inspect(path,out,name):
    c,r,f,v=(load(path/n) for n in ('input.json','result.json','frontend.json','verification.json'))
    assert v['passed'] and r['passed'] and r['drained'] and r['gpu_count']==128
    assert [len(g) for g in c['parallel']['groups']]==[32]*4
    assert len(f['requests'])==30 and c['workload']['decode_steps']==2
    assert c['route_replay']['layer_ids']==[3,4]
    capture=r['packet_capture'];assert capture['complete'] and capture['omitted_events']==0
    assert c['whole_call_replication']['schema_version'] in ('moe-whole-call-replication/4','moe-whole-call-replication/5')
    assert c['whole_call_replication']['calls'][1]['hedge_after_ns']==4000000
    manifest_file=path/c['route_replay']['manifest']['path'];assert sha(manifest_file)==c['route_replay']['manifest']['sha256']
    manifest=load(manifest_file);assert len(manifest['files'])==15
    assert manifest['source']['kind']=='user_provided_model_routes' and manifest['source']['dataset_revision'] is None
    raw={};digests=set()
    for file in manifest['files']:
        p=path/file['file']['path'];digest=sha(p);assert digest==file['file']['sha256'] and digest not in digests
        raw[file['file_id']]=load(p);digests.add(digest)
    retired,submits,skipped=prefix(c,r,f,path)
    assert not any(g.startswith('g3.') for g in retired),'healthy replica started: not a single-target intervention'
    if name.startswith('control'):assert set(retired)=={'g0.w0','g0.w1','g2.w0','g2.w1'},'control replica started'
    observed=[json.loads(line) for line in (path/'execution_events.jsonl').read_text().splitlines()]
    native_observed=[json.loads(line[len('MOE_RDMA_EVENT '):]) for line in (path/'native.stdout.log').read_text().splitlines()
        if line.startswith('MOE_RDMA_EVENT ') and json.loads(line[len('MOE_RDMA_EVENT '):])['type']=='OBSERVE']
    assert observed==native_observed
    times={(e['graph_id'],e['rank'],e['node'],e['kind']):e['time_ns'] for e in observed if e['kind'].startswith('node_')}
    groups=c['parallel']['groups'];owner={rank:i for i,g in enumerate(groups) for rank in g}
    mapping={(q['rank_id'],q['slot']):q for q in c['route_replay']['requests']}
    h=c['model']['hidden_size'];align=lambda n,k:(n+k-1)//k*k
    sizes=dict(dispatch=align(2*h+align(8*8+(1+8)*4,32),64),combine=align(2*h+align(8*8,32),64))
    messages={m['id']:m for m in r['service_transport']['messages']};payload=Counter();work=Counter();nodes=0;gate_members=0
    waves={g:load(path/'graphs'/g/'wave.json') for g in retired}
    native={w['graph_id']:w for w in r['service']['waves']}
    assert set(waves)==set(native)=={h['graph_id'] for h in f['history']}
    for graph,wave in waves.items():
        index=wave['group_index'];group=groups[index];primary_index=0 if index<2 else 2;expected=Counter();expected_compute={}
        assert native[graph]['start_ns']==submits[graph] and native[graph]['finished_ns']==retired[graph]['time_ns']
        for layer in wave['routes']:
            sources=Counter();expert=Counter();extras=Counter()
            assert len(layer['tokens'])==(4 if index<2 else 11)
            for t in layer['tokens']:
                original=groups[primary_index][group.index(t['rank'])];q=mapping[original,t['slot']]
                choices=raw[q['file_id']][q['first_decode_entry']+wave['decode_step']][str(c['route_replay']['layer_ids'][layer['layer']])][0]
                assert choices==t['experts'];sources[t['rank']]+=1;dest=Counter(group[e//8] for e in choices)
                for dst,count in dest.items():
                    expert[dst]+=count;extras[dst]+=count-1
                    if dst!=t['rank']:
                        expected[layer['layer'],'dispatch',t['rank'],dst]+=sizes['dispatch']
                        expected[layer['layer'],'combine',dst,t['rank']]+=sizes['combine']
            for rank in group:
                for stage,duration in [('attention',1000*sources[rank]),('pack',30*sources[rank]),
                    ('experts',100*expert[rank]),('combine_reduce',100*extras[rank]),('merge',30*sources[rank])]:
                    expected_compute[rank,f'L{layer["layer"]}.{stage}']=duration
        assert {(m['layer'],m['phase'],m['src'],m['dst']):m['bytes'] for m in wave['messages']}==dict(expected)
        for m in wave['messages']:
            actual=messages[m['id']];assert all(actual[k]==m[k] for k in m)
            assert submits[graph]<=actual['ready_ns']<=actual['receive_ready_ns']<=actual['ack_ns']<=retired[graph]['time_ns']
            payload[index]+=m['bytes']
        for rank,items in wave['rank_nodes'].items():
            rank=int(rank)
            for n in items:
                start=times[graph,rank,n['id'],'node_issue'];end=times[graph,rank,n['id'],'node_complete']
                assert submits[graph]<=start<=end<=retired[graph]['time_ns']
                assert all(start>=times[graph,rank,dep,'node_complete'] for dep in n['deps'])
                if n['kind']=='COMP':assert end-start==n['duration_ns']==expected_compute[rank,n['name']];work[index]+=n['duration_ns']
                nodes+=1
        gates=[g for g in r['service']['phase_completion']['gates'] if g['graph_id']==graph];assert len(gates)==4
        for g in gates:
            assert len(g['members'])==32 and g['released_ns']==max(m['arrived_ns'] for m in g['members'])
            for member in g['members']:
                assert member['arrived_ns']==times[graph,member['rank'],member['node'],'node_issue']
                assert g['released_ns']==times[graph,member['rank'],member['node'],'node_complete'];gate_members+=1
    assert len(messages)==sum(len(w['messages']) for w in waves.values())
    transport=r['service_transport']['native'];streams={s['id']:s for s in transport['streams']}
    assert len(streams)==972 and transport['closed'] and transport['physical_drained']
    assert Counter((p[0],p[2],p[4],p[1],p[5],p[6],p[7]) for p in transport['source_packets'])==Counter(
        (p[0],p[1],p[2],p[5],p[7],p[11],p[4]) for p in capture['events'] if p[1]<128 and p[3]==0 and p[6]==17)
    intervals=defaultdict(list);uid_key={};occurrences=Counter()
    for p in transport['source_packets']:
        stream=streams[p[1]];assert (p[2],p[3],p[4])==(stream['src'],stream['dst'],stream['source_nic'])
        assert owner[p[2]]==owner[p[3]] and p[5]+p[6]<=stream['published_bytes']
        intervals[p[1]].append((p[5],p[5]+p[6]));key=(p[1],p[5],p[6]);attempt=occurrences[key];occurrences[key]+=1
        assert p[7] not in uid_key;uid_key[p[7]]=(*key,attempt)
    accepted={s['stream']:s['receiver_prefix'] for s in transport['selective_repeat']['states']}
    for sid,s in streams.items():
        end=covered=0
        for begin,stop in sorted(intervals[sid]):covered+=max(0,stop-max(end,begin));end=max(end,stop)
        assert covered==s['published_bytes']==s['submitted_bytes']==s['snd_una']==accepted[sid]
    assert sum(payload.values())==sum(accepted.values())==v['receiver_accepted_bytes']
    kv_token=2*2*h*2;reserved={}
    for call in c['whole_call_replication']['calls']:
        reserved[call['replica_group']]=sum(((len(raw[q['file_id']][0]['3'])+q['first_decode_entry']+c['workload']['decode_steps']+14)//16)*16*kv_token
            for q in c['route_replay']['requests'] if q['rank_id'] in groups[call['primary_group']])
    # The reservation rounds context+all declared Decode tokens up to a16-token block.
    releases={release['group_index']:release for release in f['standby_memory']['releases']}
    assert f['standby_memory']['initial_reserved_bytes']==sum(reserved.values()) and f['standby_memory']['final_used_bytes']==0
    assert set(releases)==set(reserved)
    for call in c['whole_call_replication']['calls']:
        i=call['replica_group'];a=f['whole_call_replication']['calls'][call['id']]['attempts']['replica'];done=a['finished_ns'] is not None
        assert releases[i]==dict(group_index=i,bytes=reserved[i],kind='executed_release' if done else 'skipped_release',
            time_ns=a['finished_ns'] if done else a['skipped_ns'])
    source_rows={q['rank_id']*c['workload']['batch_per_rank']+q['slot']:q for q in c['route_replay']['requests']}
    pair_primary={pair['replica_request']:pair['primary_request'] for call in f['whole_call_replication']['calls'].values() for pair in call['request_pairs']}
    for q in f['requests']:
        original=source_rows[pair_primary.get(q['id'],q['id'])];context=len(raw[original['file_id']][0]['3'])+original['first_decode_entry']-1
        assert q['initial_context_tokens']==context
        if q['state']=='NOT_STARTED':assert q['scheduler_state']=='WAITING' and q['computed_tokens']==context-1 and q['reached_tokens']==context and q['intertoken_ns']==[]
        else:assert q['scheduler_state']=='FINISHED' and q['state']=='OBSERVATION_ENDED' and len(q['intertoken_ns'])==2
    # Each switch residence is measured from the same packet's actual MacRx to PhyTxBegin.
    rx={};residence=[];port_groups=defaultdict(set);group_events=defaultdict(list);backup_source=[];backup_switch=[]
    for p in capture['events']:
        now,node,port,kind,uid,sid,protocol,sequence,model_bytes,ecn,queued,payload_bytes=p
        if protocol!=17 or payload_bytes==0:continue
        assert sid in streams and uid in uid_key;group=owner[streams[sid]['src']]
        group_events[group].append((now,node,port,kind,sid,sequence,model_bytes,ecn,queued,payload_bytes))
        if group==1 and node<128 and kind==0:backup_source.append(now)
        if node<128:continue
        if group==1 and kind==1:backup_switch.append(now)
        key=(uid,node)
        if kind==1:assert key not in rx;rx[key]=(now,port)
        elif kind==0:
            assert key in rx and rx[key][0]<=now;arrived,input_port=rx.pop(key)
            stream,seq,size,attempt=uid_key[uid]
            residence.append(dict(stream=stream,sequence=seq,payload_bytes=size,attempt=attempt,group=group,
                node=node,port=port,ingress_port=input_port,rx_ns=arrived,tx_ns=now,residence_ns=now-arrived))
            port_groups[node,port].add(group)
    assert not rx,'data packet remained at a switch without egress/drop'
    assert len(r['queue_events'])==r['queue_observations'] and r['queue_capture']=='events'
    # Native enqueue hooks see post-addition bytes; dequeue hooks fire BEFORE
    # subtraction. Pair both captures in order, including control packets, and
    # reconstruct post-change occupancy rather than integrating mixed samples.
    hooks=[p for p in capture['events'] if p[1]>=128 and p[3] in (3,4)]
    assert len(hooks)==len(r['queue_events'])
    port_queues=defaultdict(list);occupancy=defaultdict(int)
    for raw,p in zip(r['queue_events'],hooks):
        time,node,port,bytes_=raw
        assert raw==[p[0],p[1],p[2],p[10]]
        key=node,port;previous=occupancy[key];packet_bytes=p[8]
        if p[3]==3:
            assert previous+packet_bytes==bytes_;post=bytes_
        else:
            assert previous==bytes_ and bytes_>=packet_bytes;post=bytes_-packet_bytes
        occupancy[key]=post;port_queues[key].append((time,post))
    assert all(value==0 for value in occupancy.values())
    queue_area={}
    for key,events in port_queues.items():
        last=previous=area=peak=0
        for time,bytes_ in events:assert time>=last;area+=previous*(time-last);last=time;previous=bytes_;peak=max(peak,bytes_)
        assert previous==0;queue_area[key]=dict(area_byte_ns=area,peak_bytes=peak)
    write_csv(out/(name+'-switch-residence.csv'),residence)
    write_csv(out/(name+'-queue-integrals.csv'),[dict(node=node,port=port,**value)
        for (node,port),value in sorted(queue_area.items())])
    report=dict(passed=True,source_files=15,logical_requests=dict(target=4,healthy=11),inventory_requests=len(f['requests']),
        execution_requests=sum(q['state']!='NOT_STARTED' for q in f['requests']),active_data_ranks=len({m[k] for m in messages.values() for k in ('src','dst')}),
        result_ready_ns={name:call['result_ready_ns'] for name,call in f['whole_call_replication']['calls'].items()},
        call_winners={name:call['winner'] for name,call in f['whole_call_replication']['calls'].items()},
        attempts_finished_ns=max(e['time_ns'] for e in retired.values()),network_drained_ns=r['finish_ns'],
        groups_executed=sorted({w['group_index'] for w in waves.values()}),unique_payload_bytes=sum(payload.values()),payload_by_group=dict(payload),
        source_payload_bytes=sum(p[6] for p in transport['source_packets']),source_packets=len(transport['source_packets']),
        declared_compute_work_ns=sum(work.values()),phase_member_arrivals=gate_members,verified_nodes=nodes,messages=len(messages),
        packet_events=len(capture['events']),queue_events=len(r['queue_events']),
        fault_drops=r.get('data_loss',{}).get('dropped_packets',0),standby_memory=f['standby_memory'],
        first_backup_source_tx_ns=min(backup_source) if backup_source else None,
        healthy_finish_vs_backup_source_ns=(min(backup_source)-f['whole_call_replication']['calls']['healthy']['result_ready_ns']) if backup_source else None,
        first_backup_switch_rx_ns=min(backup_switch) if backup_switch else None,
        healthy_finish_vs_backup_switch_ns=(min(backup_switch)-f['whole_call_replication']['calls']['healthy']['result_ready_ns']) if backup_switch else None,
        shared_backup_healthy_ports=[list(key) for key,value in port_groups.items() if {1,2}<=value],
        healthy_switch_residence_ns=sum(row['residence_ns'] for row in residence if row['group']==2),
        queue_area_byte_ns=sum(v['area_byte_ns'] for v in queue_area.values()),queue_peak_bytes=max(v['peak_bytes'] for v in queue_area.values()),
        queue_scope='Switch post-change occupancy, normalized enqueue-after/dequeue-before hooks; all protocols; excludes serialization',
        sha256={n:sha(path/n) for n in ('input.json','rdma.json','topology.json','frontend.json','result.json','verification.json')})
    return report,dict(config=c,result=r,frontend=f,waves=waves,residence=residence,events=group_events,port_queues=queue_area,
        healthy_messages=[m for m in r['service_transport']['messages'] if owner[m['src']]==2],
        healthy_gates=[g for g in r['service']['phase_completion']['gates'] if g['graph_id'].startswith('g2.')],
        healthy_node_times={str(key):value for key,value in times.items() if key[0].startswith('g2.')})

