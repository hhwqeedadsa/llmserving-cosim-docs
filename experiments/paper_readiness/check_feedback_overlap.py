"""Independent checks for the capped real-route study, using public outputs only."""
import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from check_feedback_factorial import check_rp, notification_times, route

ROOT=Path(__file__).resolve().parents[2]


def load(path):return json.loads(path.read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('task','analysis','output'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();task=a.task;out=a.output;assert not out.exists()
    observed=task/'input/observed';truth=task/'input/truth';runs=task/'run'
    index=load(observed/'index.json');manifest=load(truth/'manifest.json')
    assert index['schema_version']=='moe-feedback-overlap/1'
    assert index['source_token_counts']==[32,28,10,6]
    assert [(c['depth'],c['recovery_ns']) for c in index['cases']]==[(1,55000),(1,110000),(8,55000),(8,110000)]
    for name,identity in index['artifacts'].items():assert sha(observed/name)==identity['sha256']
    raw=[]
    for entry in manifest['files']:
        file=ROOT/entry['file']['path'];assert sha(file)==entry['file']['sha256'];raw.append(load(file))
    assert [len(r[0]['4']) for r in raw]==[105,28,10,6]
    expected=[]
    for src,request in enumerate(raw):
        for token in range(min(32,len(request[0]['4']))):
            for dst in range(4):
                if dst!=src and sum(dst*64<=e<(dst+1)*64 for e in request[0]['4'][token]):
                    expected.append(dict(id=len(expected),stream=src*4+dst,bytes=14464,after=[]))
    assert len(expected)==index['work_count']==180
    execution=load(runs/'execution.json');summary=load(a.analysis/'summary.json');streams_analysis=load(a.analysis/'streams.json')
    commands=load(runs/'commands.json')
    assert [c['label'] for c in commands]==['topology']+[c['name'] for c in execution['executed']]
    assert execution['unexecuted']==[c['name'] for c in index['cases'][len(execution['executed']):]]
    physical=load(runs/'topology/topology.json');totals=Counter();checks=[];timelines={};feedback_sequences={}
    for actual in execution['executed']:
        name=actual['name'];case=next(c for c in index['cases'] if c['name']==name)
        work=load(observed/(name+'.json'));assert work['work']==expected
        declared={s['id']:s for s in work['streams']}
        assert list(declared)==[s*4+d for s in range(4) for d in range(4) if s!=d]
        for sid,s in declared.items():
            assert (s['src'],s['dst'],s['wire_port'],s['source_nic'],s['destination_nic'],s['queue_depth'])==(sid//4*2,sid%4*2,21000+sid,1,1,case['depth'])
        assert work['dcqcn_reference']==dict(mode='figure7_reference/1',np_interval_ns=50000,
            alpha_interval_ns=55000,recovery_interval_ns=case['recovery_ns'],recovery_bytes=10000000,
            min_rate_bps=100000000,additive_bps=5000000,hyper_bps=50000000,
            fast_recovery_rounds=5,gain=1/256,cnp_model_bytes=48,max_events=20000)
        result_path=runs/name/'result.json'
        if actual['exit_code']:
            checks.append(dict(case=name,full_packet_verification=False,reason='failed run, completion unknown'))
            continue
        r=load(result_path);t=r['persistent_transport'];events=t['dcqcn_reference']['events']
        assert r['passed'] and r['drained'] and t['closed'] and t['physical_drained']
        assert r['completed_work']==180 and t['dcqcn_reference']['config']==work['dcqcn_reference']
        assert r['packet_capture']['complete'] and r['packet_capture']['omitted_events']==0
        assert not r['fault_drops'] and all(p['pfc_pause']==p['pfc_resume']==0 for p in r['ports'])
        source=[dict(zip(t['source_packet_columns'],row)) for row in t['source_packets']]
        by_source_uid={p['packet_uid']:p for p in source};assert len(source)==len(by_source_uid)
        samples=[dict(zip(t['source_rate_capture']['columns'],row)) for row in t['source_rate_capture']['samples']]
        ack_sample={s['packet_uid']:s for s in samples if s['event']==1}
        tx_sample={s['packet_uid']:s for s in samples if s['event']==0}
        source_events={e['packet_uid']:e for e in events if e['kind']=='source_sent'}
        assert set(by_source_uid)==set(tx_sample)==set(source_events)
        for uid,packet in by_source_uid.items():
            sample=tx_sample[uid]
            assert (sample['stream'],sample['sequence'],sample['time_ns'])==(packet['stream'],packet['sequence'],packet['time_ns'])
            assert sample['rate_bps']==source_events[uid]['rate_bps']
        assert all(s['ack_cnp']==0 for s in samples if s['event'] in (1,2))
        packets=defaultdict(list)
        for row in r['packet_capture']['events']:
            packet=dict(zip(r['packet_capture']['columns'],row));packets[packet['packet_uid']].append(packet)
        assert {uid for uid,points in packets.items() if points[0]['ip_protocol']==17}==set(by_source_uid)
        generated={e['packet_uid']:e for e in events if e['kind']=='cnp_generated'}
        sent_cnp={e['packet_uid']:e for e in events if e['kind']=='cnp_tx'}
        received_cnp={e['packet_uid']:e for e in events if e['kind']=='cnp_received'}
        assert generated.keys()==sent_cnp.keys()==received_cnp.keys()
        feedback_sequences[name]={kind:[(e['stream'],e['time_ns']) for e in events if e['kind']==kind]
            for kind in ('cnp_generated','cnp_tx','cnp_received')}
        data_received=defaultdict(list);acks=defaultdict(list);marks=defaultdict(list)
        lower=Counter();baseline=Counter();data=Counter();port_rate={}
        txs=defaultdict(list);all_tx=defaultdict(list)
        for uid,points in packets.items():
            assert len(points)==2 and {p['event'] for p in points}=={0,1}
            tx=next(p for p in points if p['event']==0);rx=next(p for p in points if p['event']==1)
            proto=tx['ip_protocol'];size=tx['model_bytes']
            assert (rx['ip_protocol'],rx['model_bytes'])==(proto,size)
            if proto==17:
                packet=by_source_uid[uid];sid=packet['stream'];s=declared[sid]
                assert (tx['sequence'],rx['sequence'],tx['time_ns'],size)==(packet['sequence'],packet['sequence'],packet['time_ns'],packet['payload_bytes']+36)
                src,dst=s['src'],s['dst'];data_received[sid].append(rx);txs[sid].append(tx)
                if rx['ecn']==3:marks[sid].append(rx['time_ns'])
                totals['data_packets']+=1
            elif proto==252:
                sid=ack_sample[uid]['stream'];s=declared[sid];src,dst=s['dst'],s['src']
                assert size==48 and rx['time_ns']==ack_sample[uid]['time_ns'] and rx['sequence']==ack_sample[uid]['sequence']
                acks[sid].append(rx);totals['ack_packets']+=1
            else:
                assert proto==255 and size==48
                sid=generated[uid]['stream'];s=declared[sid];src,dst=s['dst'],s['src']
                assert sent_cnp[uid]['time_ns']==tx['time_ns'] and received_cnp[uid]['time_ns']==rx['time_ns']
                assert generated[uid]['time_ns']<=tx['time_ns']
                totals['cnp_packets']+=1
            assert (tx['node'],rx['node'])==(src,dst)
            path=route(physical,src,dst,s['wire_port'])
            assert rx['time_ns']-tx['time_ns']>=sum(h[4] for h in path)
            for node,port,peer,rate,delay in path:
                lower[node,port]+=size;port_rate[node,port]=rate
                if proto==17:data[node,port]+=size
                if proto!=255:baseline[node,port]+=size
            all_tx[tx['node']].append(tx)
            totals['endpoint_packet_joins']+=1
        for port in r['ports']:
            key=port['node'],port['port']
            assert port['model_tx_bytes']==lower[key]
            assert port['data_tx_bytes']==data[key] and port['control_tx_bytes']==lower[key]-data[key]
            assert port.get('queue_metrics',{}).get('final_bytes',0)==0
            totals['physical_port_checks']+=1
        own_after=low_tx=0;stream_checks=[];credit_count=0
        for sid,s in declared.items():
            generated_times=[e['time_ns'] for e in events if e['stream']==sid and e['kind']=='cnp_generated']
            assert generated_times==notification_times(marks[sid],50000)
            assert [e['time_ns'] for e in events if e['stream']==sid and e['kind']=='np_mark']==sorted(marks[sid])
            selected=[w for w in t['work'] if w['stream']==sid];selected.sort(key=lambda w:w['ticket'])
            declared_selected=[w for w in expected if w['stream']==sid]
            assert [w['id'] for w in selected]==[w['id'] for w in declared_selected]
            prefix=0
            for pos,w in enumerate(selected):
                assert w['begin']==prefix and w['end']==prefix+14464;prefix+=14464
                assert w['accepted_ns']==max(p['time_ns'] for p in data_received[sid] if w['begin']<=p['sequence']<w['end'])
                assert w['acked_ns']==min(p['time_ns'] for p in acks[sid] if p['sequence']>=w['end'])
                assert w['ready_ns']==0 and 0<=w['published_ns']<=w['accepted_ns']<=w['acked_ns']
                if pos>=case['depth']:
                    owner=selected[pos-case['depth']]
                    assert w['published_ns']==owner['acked_ns'];credit_count+=1
                else:assert w['published_ns']==0
                totals['work_callback_credit_checks']+=1
            source_bytes=sum(p['payload_bytes'] for p in source if p['stream']==sid)
            assert source_bytes==prefix
            first=min((e['time_ns'] for e in received_cnp.values() if e['stream']==sid),default=None)
            after=sum(first is not None and p['time_ns']>=first for p in txs[sid])
            sampled=[p for p in samples if p['event']==0 and p['stream']==sid]
            low=sum(p['rate_bps']<100000000000 for p in sampled)
            analysis=next(v for v in streams_analysis if v['case']==name and v['stream']==sid)
            assert (analysis['packets_after_first_cnp'],analysis['tx_samples_below_line_rate'],analysis['first_cnp_rx_ns'])==(after,low,first)
            own_after+=after;low_tx+=low
            stream_checks.append(dict(stream=sid,source_payload_bytes=source_bytes,
                first_cnp_rx_ns=first,packets_after_first_cnp=after,tx_below_line_rate=low))
        state_checks=check_rp(events,work['dcqcn_reference']);totals['reference_state_checks']+=state_checks
        row=next(c for c in summary['cases'] if c['name']==name)
        assert row['status']=='complete' and row['acked_work']==180 and row['result_sha256']==sha(result_path)
        assert row['final_acked_ns']==max(w['acked_ns'] for w in t['work'])
        bound=max(Fraction(size*8*10**9,port_rate[key]) for key,size in baseline.items())
        assert row['minimum_declared_port_service_ns']==float(bound)<=row['final_acked_ns']
        assert row['packets_after_first_cnp']==own_after and row['tx_samples_below_line_rate']==low_tx
        assert row['credit_owner_checks']==credit_count
        timeline=sorted((p['stream'],p['sequence'],p['time_ns'],p['payload_bytes']) for p in source)
        timelines[name]=hashlib.sha256(json.dumps(timeline).encode()).hexdigest()
        # Find a strict pacing witness: NIC idle before actual TX and this QP is
        # selected exactly at its native next_avail while published data exists.
        pacing=[]
        for sid,s in declared.items():
            nic_tx=sorted(all_tx[s['src']],key=lambda p:p['time_ns'])
            times=[p['time_ns'] for p in nic_tx]
            for sample in samples:
                if sample['event']!=0 or sample['stream']!=sid or sample['rate_bps']>=100000000000:continue
                if sample['time_ns']!=sample['next_avail_ns']:continue
                index_tx=bisect_left(times,sample['time_ns'])
                if index_tx==0:continue
                preceding=nic_tx[index_tx-1]
                end=preceding['time_ns']+(preceding['model_bytes']*8*10**9+50000000000)//100000000000
                gap=sample['time_ns']-end
                if gap<=0:continue
                current_work=next(w for w in t['work'] if w['stream']==sid and w['begin']<=sample['sequence']<w['end'])
                if current_work['published_ns']>end:continue
                pacing.append(dict(stream=sid,packet_uid=sample['packet_uid'],time_ns=sample['time_ns'],
                    rate_bps=sample['rate_bps'],next_avail_ns=sample['next_avail_ns'],
                    sequence=sample['sequence'],published_bytes=sample['published_bytes'],
                    work=current_work['id'],work_published_ns=current_work['published_ns'],
                    prior_physical_tx_end_ns=end,source_nic_idle_ns=gap,
                    scope='NIC idle before a paced TX; this is not a sum of removable business delay'))
        final_work=max(t['work'],key=lambda w:w['acked_ns']);sid=final_work['stream'];s=declared[sid]
        candidates=[v for v in source if v['stream']==sid and final_work['begin']<=v['sequence']<final_work['end']]
        first_packet=min(candidates,key=lambda x:x['time_ns']);last_packet=max(candidates,key=lambda x:x['time_ns'])
        last_rx=next(p for p in packets[last_packet['packet_uid']] if p['event']==1)
        final_ack=min((v for v in ack_sample.values() if v['stream']==sid and v['sequence']>=final_work['end']),key=lambda v:v['time_ns'])
        ack_tx=next(v for v in packets[final_ack['packet_uid']] if v['event']==0)
        def no_queue_delay(src,dst,size):
            return sum(delay+(size*8*10**9+rate//2)//rate for _,_,_,rate,delay in route(physical,src,dst,s['wire_port']))
        data_noqueue=no_queue_delay(s['src'],s['dst'],last_rx['model_bytes'])
        ack_noqueue=no_queue_delay(s['dst'],s['src'],48)
        publication_owner=None
        selected=sorted([w for w in t['work'] if w['stream']==sid],key=lambda w:w['ticket'])
        if final_work['ticket']>=case['depth']:
            publication_owner=selected[final_work['ticket']-case['depth']]
        tail=dict(work=final_work['id'],stream=sid,ready_ns=final_work['ready_ns'],
            published_ns=final_work['published_ns'],publication_owner=publication_owner,
            first_source_tx_ns=first_packet['time_ns'],last_source_tx_ns=last_packet['time_ns'],
            last_source_uid=last_packet['packet_uid'],last_source_sequence=last_packet['sequence'],
            last_source_rate_bps=tx_sample[last_packet['packet_uid']]['rate_bps'],
            data_rx_ns=last_rx['time_ns'],ack_tx_ns=ack_tx['time_ns'],ack_rx_ns=final_ack['time_ns'],
            source_to_data_rx_ns=last_rx['time_ns']-last_packet['time_ns'],
            data_rx_to_ack_rx_ns=final_ack['time_ns']-last_rx['time_ns'],
            minimum_last_data_path_ns=data_noqueue,minimum_final_ack_path_ns=ack_noqueue,
            actual_final_data_and_ack_have_no_extra_wait=(last_rx['time_ns']-last_packet['time_ns']==data_noqueue and
                ack_tx['time_ns']==last_rx['time_ns'] and final_ack['time_ns']-ack_tx['time_ns']==ack_noqueue))
        assert tail['ack_rx_ns']==row['final_acked_ns']
        checks.append(dict(case=name,full_packet_verification=True,reference_states=state_checks,
            streams=stream_checks,source_timeline_sha256=timelines[name],
            final_work_witness=tail,
            strict_pacing_witness_count=len(pacing),largest_pacing_witness=max(pacing,key=lambda x:x['source_nic_idle_ns'],default=None)))
    pair_checks=[]
    for comparison in summary['comparisons']:
        lo=f"d{comparison['depth']}-r55000";hi=f"d{comparison['depth']}-r110000"
        l=next(r for r in summary['cases'] if r['name']==lo);h=next(r for r in summary['cases'] if r['name']==hi)
        assert comparison['ack_delta_ns']==h['final_acked_ns']-l['final_acked_ns']
        assert comparison['source_timeline_equal']==(timelines[lo]==timelines[hi])
        low=next(c for c in checks if c['case']==lo)['final_work_witness']
        high=next(c for c in checks if c['case']==hi)['final_work_witness']
        pair_checks.append(dict(depth=comparison['depth'],feedback_generation_tx_rx_sequences_equal=feedback_sequences[lo]==feedback_sequences[hi],
            final_work_same=(low['work'],low['stream'])==(high['work'],high['stream']),
            final_ack_delta_ns=high['ack_rx_ns']-low['ack_rx_ns'],
            last_source_tx_delta_ns=high['last_source_tx_ns']-low['last_source_tx_ns'],
            final_data_and_ack_residuals_equal=(low['source_to_data_rx_ns'],low['data_rx_to_ack_rx_ns'])==
                (high['source_to_data_rx_ns'],high['data_rx_to_ack_rx_ns'])))
    out.mkdir()
    (out/'verification.json').write_text(json.dumps(dict(verified=True,counts=dict(totals),cases=checks,
        pair_checks=pair_checks,unexecuted=execution['unexecuted'],network_runs=0,hardware_calibrated=False,
        endpoint_capture_and_port_projection_only=True,all_declared_completed=execution['completed']),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(verified=True,counts=dict(totals))))


if __name__=='__main__':main()
