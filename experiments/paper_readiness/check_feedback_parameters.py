"""Independently validate the fixed 8-condition sensitivity input and packet evidence."""
import argparse
from collections import Counter,defaultdict
from fractions import Fraction
import hashlib
from itertools import product
import json
from pathlib import Path
import re

from check_feedback_factorial import check_rp,notification_times,route

ROOT=Path(__file__).resolve().parents[2]


def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('task','papers','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();task=a.task;out=a.output;assert not out.exists()
    observed=task/'input/observed';runs=task/'run';index=load(observed/'index.json')
    summary=load(task/'analysis/summary.json');execution=load(runs/'execution.json')
    expected_settings=list(product((1,8),(5000000,40000000),(1.0,0.01)))
    assert [(c['depth'],c['additive_bps'],c['pmax']) for c in index['cases']]==expected_settings
    for name,identity in index['artifacts'].items():assert sha(observed/name)==identity['sha256']
    expected=[]
    for src,item in enumerate(load(task/'input/truth/manifest.json')['files']):
        path=ROOT/item['file']['path'];assert sha(path)==item['file']['sha256']
        choices=load(path)[0]['4']
        assert len(choices)==[105,28,10,6][src]
        for token in range(min(32,len(choices))):
            for dst in range(4):
                if dst!=src and any(dst*64<=e<(dst+1)*64 for e in choices[token]):
                    expected.append(dict(id=len(expected),stream=src*4+dst,bytes=14464,after=[]))
    assert len(expected)==180
    physical=load(runs/'topology/topology.json');totals=Counter();checked=[];notices={}
    for actual in execution['executed']:
        name=actual['name'];case=next(c for c in index['cases'] if c['name']==name)
        work=load(observed/(name+'.json'));protocol=load(observed/case['protocol'])
        assert work['work']==expected
        assert protocol['seed']==11
        assert protocol['ecn_by_rate']=={str(rate):dict(kmin_bytes=32768,kmax_bytes=131072,pmax=case['pmax']) for rate in (100000000000,25000000000)}
        streams={s['id']:s for s in work['streams']}
        assert list(streams)==[s*4+d for s in range(4) for d in range(4) if s!=d]
        for sid,s in streams.items():
            assert (s['src'],s['dst'],s['wire_port'],s['queue_depth'],s['source_nic'],s['destination_nic'])==(sid//4*2,sid%4*2,21000+sid,case['depth'],1,1)
        cfg=work['dcqcn_reference']
        assert cfg==dict(mode='figure7_reference/1',np_interval_ns=50000,alpha_interval_ns=55000,
            recovery_interval_ns=55000,recovery_bytes=10000000,min_rate_bps=100000000,
            additive_bps=case['additive_bps'],hyper_bps=50000000,fast_recovery_rounds=5,gain=1/256,
            cnp_model_bytes=48,max_events=20000)
        result_path=runs/name/'result.json'
        if actual['exit_code']:
            checked.append(dict(case=name,full_verification=False,reason='failed, completion unknown'));continue
        r=load(result_path);t=r['persistent_transport'];events=t['dcqcn_reference']['events']
        assert r['passed'] and r['drained'] and t['closed'] and t['physical_drained'] and r['completed_work']==180
        assert t['dcqcn_reference']['config']==cfg and r['fault_drops']==0
        assert r['packet_capture']['complete'] and r['packet_capture']['omitted_events']==0
        assert all(p['pfc_pause']==p['pfc_resume']==0 for p in r['ports'])
        if actual['kind']=='reused_success':
            assert sha(result_path)==actual['result_sha256']
            prior=load(runs/name/'prior-inputs.json');cmd=prior['command']
            assert sha(observed/(name+'.json'))==prior['hashes'][cmd[3]]
            assert sha(observed/case['protocol'])==prior['hashes'][cmd[2]]
            assert sha(runs/'topology/topology.json')==prior['hashes'][cmd[1]]
            assert execution['backend_sha256']==prior['hashes'][cmd[0]]
        else:
            inputs=load(runs/name/'inputs.json');cmd=inputs['command']
            assert sha(observed/(name+'.json'))==inputs['hashes'][cmd[3]]
            assert sha(observed/case['protocol'])==inputs['hashes'][cmd[2]]
            assert sha(runs/'topology/topology.json')==inputs['hashes'][cmd[1]]
            assert execution['backend_sha256']==inputs['hashes'][cmd[0]]
        source={p[-1]:dict(zip(t['source_packet_columns'],p)) for p in t['source_packets']}
        assert len(source)==len(t['source_packets'])==2700
        samples=[dict(zip(t['source_rate_capture']['columns'],p)) for p in t['source_rate_capture']['samples']]
        tx_sample={p['packet_uid']:p for p in samples if p['event']==0}
        ack_sample={p['packet_uid']:p for p in samples if p['event']==1}
        logs={e['packet_uid']:e for e in events if e['kind']=='source_sent'}
        assert source.keys()==tx_sample.keys()==logs.keys()
        assert all(p['ack_cnp']==0 for p in samples if p['event'] in (1,2))
        for uid,pkt in source.items():
            s=tx_sample[uid]
            assert (s['stream'],s['time_ns'],s['sequence'],s['rate_bps'])==(pkt['stream'],pkt['time_ns'],pkt['sequence'],logs[uid]['rate_bps'])
        packets=defaultdict(list)
        for p in r['packet_capture']['events']:
            point=dict(zip(r['packet_capture']['columns'],p));packets[point['packet_uid']].append(point)
        assert {uid for uid,p in packets.items() if p[0]['ip_protocol']==17}==set(source)
        generated={e['packet_uid']:e for e in events if e['kind']=='cnp_generated'}
        sent={e['packet_uid']:e for e in events if e['kind']=='cnp_tx'}
        received={e['packet_uid']:e for e in events if e['kind']=='cnp_received'}
        assert generated.keys()==sent.keys()==received.keys()
        notices[name]={k:[(e['stream'],e['time_ns']) for e in events if e['kind']==k] for k in ('cnp_generated','cnp_tx','cnp_received')}
        per_port=Counter();data_port=Counter();base_port=Counter();rates={};data_rx=defaultdict(list);acks=defaultdict(list);marks=defaultdict(list)
        for uid,points in packets.items():
            assert len(points)==2 and {p['event'] for p in points}=={0,1}
            tx=next(p for p in points if p['event']==0);rx=next(p for p in points if p['event']==1)
            proto=tx['ip_protocol'];size=tx['model_bytes'];assert (proto,size)==(rx['ip_protocol'],rx['model_bytes'])
            if proto==17:
                pkt=source[uid];sid=pkt['stream'];s=streams[sid];src,dst=s['src'],s['dst']
                assert (tx['time_ns'],tx['sequence'],rx['sequence'],size)==(pkt['time_ns'],pkt['sequence'],pkt['sequence'],pkt['payload_bytes']+36)
                data_rx[sid].append(rx)
                if rx['ecn']==3:marks[sid].append(rx['time_ns'])
                totals['data_packets']+=1
            elif proto==252:
                sid=ack_sample[uid]['stream'];s=streams[sid];src,dst=s['dst'],s['src']
                assert size==48 and (rx['time_ns'],rx['sequence'])==(ack_sample[uid]['time_ns'],ack_sample[uid]['sequence'])
                acks[sid].append(rx);totals['ack_packets']+=1
            else:
                assert proto==255 and size==48
                sid=generated[uid]['stream'];s=streams[sid];src,dst=s['dst'],s['src']
                assert generated[uid]['time_ns']<=tx['time_ns']==sent[uid]['time_ns']
                assert rx['time_ns']==received[uid]['time_ns'];totals['cnp_packets']+=1
            assert (tx['node'],rx['node'])==(src,dst)
            path=route(physical,src,dst,s['wire_port'])
            assert rx['time_ns']-tx['time_ns']>=sum(e[4] for e in path)
            for n,port,peer,rate,delay in path:
                per_port[n,port]+=size;rates[n,port]=rate
                if proto==17:data_port[n,port]+=size
                if proto!=255:base_port[n,port]+=size
            totals['endpoint_packet_joins']+=1
        for p in r['ports']:
            key=p['node'],p['port']
            assert (p['model_tx_bytes'],p['data_tx_bytes'],p['control_tx_bytes'])==(per_port[key],data_port[key],per_port[key]-data_port[key])
            assert p.get('queue_metrics',{}).get('final_bytes',0)==0
            totals['physical_ports']+=1
        after=0
        for sid,s in streams.items():
            assert [e['time_ns'] for e in generated.values() if e['stream']==sid]==notification_times(marks[sid],50000)
            assert [e['time_ns'] for e in events if e['kind']=='np_mark' and e['stream']==sid]==sorted(marks[sid])
            selected=sorted([w for w in t['work'] if w['stream']==sid],key=lambda w:w['ticket'])
            prefix=0
            for pos,w in enumerate(selected):
                assert w['begin']==prefix and w['end']==prefix+14464;prefix+=14464
                assert w['ready_ns']==0 and w['published_ns']==(0 if pos<case['depth'] else selected[pos-case['depth']]['acked_ns'])
                assert w['accepted_ns']==max(p['time_ns'] for p in data_rx[sid] if w['begin']<=p['sequence']<w['end'])
                assert w['acked_ns']==min(p['time_ns'] for p in acks[sid] if p['sequence']>=w['end'])
                totals['work_callbacks_credit']+=1
            assert sum(p['payload_bytes'] for p in source.values() if p['stream']==sid)==prefix
            first=min((e['time_ns'] for e in received.values() if e['stream']==sid),default=None)
            after+=sum(first is not None and p['stream']==sid and p['time_ns']>=first for p in source.values())
        state_count=check_rp(events,cfg);totals['rp_state_events']+=state_count
        row=next(r for r in summary['cases'] if r['name']==name)
        bound=max(Fraction(size*8*10**9,rates[k]) for k,size in base_port.items())
        assert row['acked_ns']==max(w['acked_ns'] for w in t['work']) and row['finish_ns']==r['finish_ns']
        assert row['result_sha256']==sha(result_path) and row['minimum_port_service_ns']==float(bound)<=row['acked_ns']
        assert row['post_feedback_packets']==after and row['cnp_count']==len(generated)
        assert row['unique_destination_ce']==sum(map(len,marks.values()))
        assert row['minimum_rate_at_tx_bps']==min(s['rate_bps'] for s in tx_sample.values())
        checked.append(dict(case=name,full_verification=True,rp_state_events=state_count,result_sha256=sha(result_path),execution_kind=actual['kind']))
    for e in summary['effects']:
        lo=next(c for c in summary['cases'] if c['name']==e['low_case']);hi=next(c for c in summary['cases'] if c['name']==e['high_case'])
        assert e['ack_delta_ns']==hi['acked_ns']-lo['acked_ns']
    comparisons=[]
    for depth,pmax in product((1,8),(1.0,0.01)):
        names=[c['name'] for c in index['cases'] if c['depth']==depth and c['pmax']==pmax]
        if all(n in notices for n in names):comparisons.append(dict(depth=depth,pmax=pmax,AI5_to40_CNP_sequences_equal=notices[names[0]]==notices[names[1]]))
    pdf=a.papers/'dcqcn2015.pdf';text=(a.papers/'dcqcn2015.txt').read_text()
    assert sha(pdf)=='879074a33b78ceb93f9f66d59b217fba9e7621320cc68bc47914747cf5cf31f8'
    assert 'α update interval τ 0' in text and 'insensitive to τ 0 value: 110µs' in text
    assert re.search(r'τ0\s+Interval of Equation \(2\)',text)
    assert 'Rate increase step (fixed at 40Mbps)' in text and 'Pmax = 1%' in text
    native=ROOT/'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3/src/point-to-point/model/switch-mmu.cc'
    native_text=native.read_text();assert 'if (egress_bytes[ifindex][qIndex] > kmax[ifindex])\n\t\t\treturn true;' in native_text
    out.mkdir()
    (out/'verification.json').write_text(json.dumps(dict(verified=True,all_declared_completed=execution['completed'],counts=dict(totals),cases=checked,
        comparisons=comparisons,new_network_runs=execution['new_network_runs'],reused_successful_runs=execution['reused_successful_runs'],
        pmax_evidence='hashed public protocol plus fixed Fabric direct assignment, not a hardware readback',
        hardware_calibrated=False,source_audit=dict(pdf_sha256=sha(pdf),text_sha256=sha(a.papers/'dcqcn2015.txt'),
        source_mmu_sha256=sha(native),alpha_and_recovery_distinct=True,probability_above_kmax_is_one=True,
        correction='110us primary-source discussion concerns alpha interval tau-prime, not recovery timer T')),
        ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(verified=True,counts=dict(totals))))


if __name__=='__main__':main()
