"""Independent evaluator: original routes -> graph -> packets -> public curves.

This evaluator can read source truth. The renderer consumes only its saved
aggregates and public arrival exports; nothing here controls an execution.
"""
import argparse
from collections import Counter,defaultdict
import csv
import hashlib
import json
from pathlib import Path

CASES=('clean400','bottleneck25','loss50','loss75','blackout','persistent')
FIELDS=('tx_payload_bytes','rx_payload_bytes','unique_arrival_bytes','accepted_bytes','confirmed_bytes')


def read(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def rows(p):
    with p.open() as f:return [{k:int(v or 0) for k,v in row.items()} for row in csv.DictReader(f)]


def verify(root,output):
    assert not output.exists();c=read(root/'inputs/service.json');groups=c['service']['parallel']['groups']
    expected={};raw={};planned=0
    for i,q in enumerate(c['routes']['requests']):
        p=root/'inputs'/q['file']['path'];assert sha(p)==q['file']['sha256'];raw[i]=read(p)
    for gi,group in enumerate(groups):
        for step in range(2):
            matrix=Counter()
            for i,q in enumerate(c['routes']['requests']):
                if q['rank_id'] not in group:continue
                for layer in (3,4):
                    for expert in raw[i][1+step][str(layer)][0]:
                        dst=group[expert//(256//len(group))]
                        if dst!=q['rank_id']:matrix[layer-3,'dispatch',q['rank_id'],dst]+=14336
            for (layer,phase,src,dst),n in list(matrix.items()):matrix[layer,'combine',dst,src]=n
            expected[f'g{gi}.w{step}']=matrix;planned+=sum(matrix.values())
    cases={};events_checked=curve_rows=0;wave_hashes={}
    for case in CASES:
        d=root/case;r=read(d/'result.json');s=read(d/'scenario.json');f=read(d/'frontend.json')
        assert r['native_network_executed'] and r['gpu_count']==s['physical_gpu_count']==320
        assert s['server_count']==40 and s['source_requests']==15 and s['largest_actual_batch']==6
        assert [g['ep'] for g in s['ep_groups']]==[256,64]
        assert [g['experts_per_rank_per_layer'] for g in s['ep_groups']]==[1,4]
        assert r['passed']==(case!='persistent')
        if r['passed']:assert read(d/'verification.json')['passed']
        else:assert r['failure']['reason']=='recovery_retry_exhausted' and s['outcome']['state']=='failed'
        meta={};hashes={}
        for p in sorted((d/'graphs').glob('*/wave.json')):
            w=read(p);g=w['graph_id'];hashes[g]=sha(p)
            actual=Counter({(m['layer'],m['phase'],m['src'],m['dst']):m['bytes'] for m in w['messages']})
            assert actual==expected[g] and len(actual)==len(w['messages'])
            for m in w['messages']:
                assert m['id'] not in meta;meta[m['id']]=dict(m,graph=g)
        wave_hashes[case]=hashes
        submitted={m['id']:m for m in r['messages']};assert len(submitted)==len(r['messages']) and set(submitted)<=set(meta)
        if r['passed']:assert submitted.keys()==meta.keys() and len(hashes)==4
        for i,m in submitted.items():assert all(m[k]==meta[i][k] for k in ('src','dst','bytes','plane'))
        first={v['group_index']:v for v in f['history'] if v['step']==0}
        assert set(first)=={0,1} and first[0]['start_ns']==first[1]['start_ns']==0
        assert {k:len(v) for k,v in first[0]['batch_request_ids'].items()}=={'0':6,'128':6}
        assert {k:len(v) for k,v in first[1]['batch_request_ids'].items()}=={'256':3}
        cap=r['packet_capture'];assert cap['complete'] and cap['omitted_events']==0
        prefix=defaultdict(int);confirmed=defaultdict(int);seen=set();packet_counts=defaultdict(Counter);endpoint=defaultdict(Counter)
        totals={phase:dict.fromkeys(FIELDS,0) for phase in ('dispatch','combine')}
        curves={phase:[dict(time_ns=0,**v)] for phase,v in totals.items()}
        loss=r.get('data_loss');eligible=window=dropped=0;rx_ce=Counter();proto_counts=Counter();last=0
        ack_sent=defaultdict(dict);ack_received={}
        for row in cap['events']:
            p=dict(zip(cap['columns'],row));t,node,event,i,proto=(p[k] for k in ('time_ns','node','event','message_id','ip_protocol'))
            assert last<=t<=r['finish_ns'];last=t
            if node<320 and event in (0,1):
                direction='tx' if event==0 else 'rx';kind='data' if proto==17 else 'control'
                endpoint[node][direction+'_'+kind+'_model_bytes']+=p['model_bytes'];endpoint[node][direction+'_'+kind+'_packets']+=1
            if event==0:proto_counts[str(proto)]+=1
            if loss and node<320 and proto==17 and event in (1,2):
                cfg=loss['configuration'];eligible+=1;want=False
                if cfg['start_ns']<=t<cfg['end_ns']:
                    want=(window+cfg['offset'])%cfg['denominator']<cfg['numerator'];window+=1
                assert (event==2)==want;dropped+=event==2
            if i not in meta:continue
            m=meta[i];phase=m['phase'];selected=m['graph']=='g0.w0' and m['layer']==0
            before=dict(totals[phase]);seq,size=p['sequence'],p['data_payload_bytes']
            if proto==17:
                assert size==min(9000,m['bytes']-seq) and seq%9000==0 and size>0
                if node==m['src'] and event==0:
                    packet_counts[i]['tx_packets']+=1;packet_counts[i]['tx_bytes']+=size
                    if selected:totals[phase]['tx_payload_bytes']+=size
                if node==m['dst'] and event==1:
                    packet_counts[i]['rx_bytes']+=size;rx_ce['packets']+=p['ecn']==3
                    if selected:
                        totals[phase]['rx_payload_bytes']+=size
                        if (i,seq) not in seen:totals[phase]['unique_arrival_bytes']+=size
                    seen.add((i,seq))
                    if seq==prefix[i]:
                        prefix[i]+=size
                        if selected:totals[phase]['accepted_bytes']+=size
            elif proto in (252,253) and node==m['src'] and event==1:
                assert 0<=seq<=m['bytes'];delta=max(confirmed[i],seq)-confirmed[i];confirmed[i]+=delta
                if selected:totals[phase]['confirmed_bytes']+=delta
            if proto==252:
                if node==m['dst'] and event==0:ack_sent[i][p['packet_uid']]=(t,seq)
                if node==m['src'] and event==1:ack_received[i,p['packet_uid']]=t
            if selected and before!=totals[phase]:curves[phase].append(dict(time_ns=t,**totals[phase]))
        if loss:
            assert (eligible,window,dropped)==tuple(loss[k] for k in ('eligible_destination_data_packets','window_packets','dropped_packets'))
            assert dropped==r['fault_drops']
        for i,m in submitted.items():
            assert prefix[i]==m['receiver_contiguous_bytes']
            assert packet_counts[i]['tx_packets']==m['source_data_packets'] and packet_counts[i]['tx_bytes']==m['source_data_payload_bytes']
            if r['passed']:assert prefix[i]==confirmed[i]==m['bytes']
        logical=defaultdict(Counter)
        for i,m in meta.items():
            logical[m['src']]['logical_send_bytes']+=m['bytes'];logical[m['dst']]['logical_receive_bytes']+=m['bytes']
            logical[m['src']]['source_tx_payload_bytes']+=packet_counts[i]['tx_bytes']
            logical[m['dst']]['destination_rx_payload_bytes']+=packet_counts[i]['rx_bytes'];logical[m['dst']]['accepted_bytes']+=prefix[i]
        slices={}
        for phase in ('dispatch','combine'):
            audit=root/(case+'-'+phase+'-audit')
            if not audit.exists():
                assert case=='persistent' and phase=='combine';continue
            curves[phase].append(dict(time_ns=r['finish_ns'],**totals[phase]))
            actual=rows(audit/'arrival_curve.csv');assert actual==curves[phase],(case,phase,'curve mismatch')
            for ep in rows(audit/'endpoints.csv'):
                ref=endpoint[ep['rank']]+logical[ep['rank']]
                assert all(ep[k]==ref[k] for k in ep if k!='rank')
            summary=read(audit/'summary.json');sl=summary['slice'];assert sl['accepted_bytes']==totals[phase]['accepted_bytes']
            target=sl['logical_bytes'];slices[phase]=dict(sl,percentile_arrival_ns={str(percent):next((x['time_ns'] for x in actual if x['accepted_bytes']*100>=target*percent),None) for percent in (50,95,99,100)})
            curve_rows+=len(actual)
        # Concurrent means actual unconfirmed message intervals overlap, not separate simulations.
        points=[]
        for m in submitted.values():
            if m['first_tx_ns'] is not None:
                end=m.get('ack_ns') if m.get('ack_ns') is not None else r['finish_ns']
                points.extend(((m['first_tx_ns'],1),(end,-1)))
        active=peak=0
        for _,delta in sorted(points):active+=delta;peak=max(peak,active)
        assert peak>1
        ports=r['ports'];recovery=r['recovery'];codes=recovery['event_codes'];revents=Counter(v[1] for v in recovery['events'])
        inflight=[]
        for event in recovery['events']:
            if event[1]!=codes['timeout_retransmit']:continue
            t,_,source,identity,known,*_=event
            candidates=[(tx,seq,uid,ack_received[identity,uid]) for uid,(tx,seq) in ack_sent[identity].items()
                if tx<t and seq>known and ack_received.get((identity,uid),0)>t]
            if candidates:
                tx,seq,uid,rx=min(candidates)
                inflight.append(dict(timeout_ns=t,message_id=identity,source=source,known_ack_bytes=known,
                    progress_ack_tx_ns=tx,progress_ack_sequence=seq,packet_uid=uid,progress_ack_rx_ns=rx))
        tx=sum(m['source_data_payload_bytes'] for m in submitted.values());submitted_bytes=sum(m['bytes'] for m in submitted.values())
        host=read(d/'execution/probe.json')
        cases[case]=dict(success=r['passed'],finish_ns=r['finish_ns'],failure=r.get('failure'),
            hardware=s,planned_workload_bytes=planned,generated_graphs=len(hashes),generated_graph_bytes=sum(m['bytes'] for m in meta.values()),
            submitted_messages=len(submitted),submitted_bytes=submitted_bytes,accepted_bytes=sum(prefix.values()),
            source_tx_payload_bytes=tx,retransmitted_payload_bytes=tx-sum(m['source_unique_data_payload_bytes'] for m in submitted.values()),
            active_data_ranks=len({v for m in submitted.values() for v in (m['src'],m['dst'])}),
            packet_events=len(cap['events']),peak_unconfirmed_messages=peak,frontend_history=f['history'],slices=slices,
            loss=None if not loss else dict(window_packets=window,dropped_packets=dropped,eligible_packets=eligible,
                window_fraction=dropped/window if window else None,whole_fraction=dropped/eligible if eligible else None),
            congestion=dict(destination_rx_ce_packets=rx_ce['packets'],pfc_pause_port_observations=sum(v['pfc_pause'] for v in ports),
                pfc_resume_port_observations=sum(v['pfc_resume'] for v in ports),
                peak_switch_egress_queue_bytes=max(v.get('queue_metrics',{}).get('peak_bytes',0) for v in ports),
                queue_area_byte_ns=sum(v.get('queue_metrics',{}).get('area_byte_ns',0) for v in ports),
                dcqcn_feedback_messages=sum(m.get('dcqcn_feedback_seen',False) for m in submitted.values()),
                dcqcn_snapshot_unavailable_messages=sum('dcqcn_feedback_seen' not in m for m in submitted.values()),
                protocol_tx_hop_observations=dict(proto_counts)),
            recovery=dict(timeouts=revents[codes['timeout_retransmit']],duplicate_ack_responses=recovery['duplicate_ack_responses'],
                retry_exhausted=revents[codes['retry_exhausted']],timeouts_with_progress_ack_already_inflight=len(inflight),
                inflight_ack_examples=inflight[:5]),host=host)
        events_checked+=len(cap['events'])
    ref=wave_hashes['clean400']
    for case,hashes in wave_hashes.items():assert all(ref[g]==h for g,h in hashes.items()),case+' changed workload'
    assert len({v['submitted_bytes'] for k,v in cases.items() if k!='persistent'})==1
    assert cases['persistent']['accepted_bytes']==0
    verdict=dict(passed=True,source_requests=15,physical_gpus=320,planned_workload_bytes=planned,
        packet_events_checked=events_checked,curve_rows_checked=curve_rows,cases=cases,
        evidence_scope='source and packet audit, not hardware accuracy or optimization benefit')
    output.write_text(json.dumps(verdict,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(passed=True,packet_events_checked=events_checked,curve_rows_checked=curve_rows,
        outcomes={k:dict(success=v['success'],finish_ns=v['finish_ns'],loss=v['loss'],congestion=v['congestion'],recovery=v['recovery']) for k,v in cases.items()}),indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();verify(a.root.resolve(),a.output.resolve())
