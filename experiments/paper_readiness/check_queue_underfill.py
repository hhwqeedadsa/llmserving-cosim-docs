"""Independent cold-SQ work bound and endpoint/state checks; never run a network."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from check_feedback_factorial import route, notification_times, check_rp

ROOT = Path(__file__).resolve().parents[2]


def load(path): return json.loads(path.read_text())
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('task','output'): parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--explanation',type=Path)
    parser.add_argument('--input',type=Path,help='Corrected source accounting input; network declarations must be byte-identical to the original')
    args=parser.parse_args();task=args.task;assert not args.output.exists()
    observed=task/'input/observed';runs=task/'run'
    inputs=args.input or task/'input'
    if args.input:
        for path in observed.iterdir():assert path.read_bytes()==(inputs/'observed'/path.name).read_bytes()
    index=load(observed/'index.json');execution=load(runs/'execution.json');summary=load(task/'analysis/summary.json')
    assert [r['depth'] for r in index['cases']]==[1,8,1024]
    for name,entry in index['artifacts'].items():assert sha(observed/name)==entry['sha256']
    # Independent expansion by expert-interval membership, rather than producer set division.
    writes=[];provider=Counter()
    for src,item in enumerate(load(inputs/'truth/manifest.json')['files']):
        path=ROOT/item['file']['path'];assert sha(path)==item['file']['sha256']
        choices=load(path)[0]['4'];assert len(choices)==[105,28,10,6][src]
        for token in range(min(32,len(choices))):
            for dst in range(4):
                if src!=dst and any(64*dst<=e<64*(dst+1) for e in choices[token]):
                    writes.append(dict(id=len(writes),stream=src*4+dst,bytes=14464,after=[]))
                    provider[src,dst]+=1  # Direct Dispatch token put.
                    provider[dst,src]+=1  # Locally reduced Combine returns in the reverse direction.
        for peer in range(4):
            provider[src,peer]+=4  # Dispatch/Combine entry/exit; self GIN included.
            provider[src,peer]+=1  # Expert count block, including self.
            if peer!=src:provider[src,peer]+=1  # Rank count self uses local-store bypass.
    capacity=load(inputs/'truth/capacity.json');source=load(inputs/'truth/source-audit.json')
    assert len(writes)==180 and sum(provider.values())==capacity['total_provider_work']==452
    assert max(provider.values())==capacity['maximum_per_sender_peer']<1024
    assert {(r['src'],r['dst']):r['cold_sq_work_upper_bound'] for r in capacity['per_sender_peer_upper_bounds']}==dict(provider)
    for entry in source['sources']:
        path=ROOT/entry['path'];raw=path.read_bytes()
        assert sha(path)==entry['sha256'] and len(raw)==entry['bytes']
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==entry['git_blob_sha']
        manifest=next(r for r in load(ROOT/entry['manifest'])['sources'] if r['path']==entry['path'].split('/sources/',1)[1])
        assert manifest['sha256']==entry['sha256'] and manifest['commit']==entry['commit']
        for excerpt in entry['excerpts']:
            lines=raw.decode().splitlines();start=excerpt['first_line']-1;n=len(excerpt['text'].splitlines())
            assert '\n'.join(lines[start:start+n])==excerpt['text']
    texts={Path(r['path']).name:(ROOT/r['path']).read_text() for r in source['sources']}
    assert 'constexpr int kDefaultQPDepth = 1024;' in texts['compiled.cuh']
    assert 'reqs.ginQueueDepth = qp_depth > 0 ? qp_depth : kDefaultQPDepth;' in texts['context.cpp']
    assert 'qp_init_attr.sq_nwqe = gdaki_ctx->qp_sq_size;' in texts['gin_host_gdaki.cc']
    assert 'if (wqe_idx >= nwqes)' in texts['doca_gpunetio_dev_verbs_qp.cuh']
    assert 'qp, wqe_idx - nwqes' in texts['doca_gpunetio_dev_verbs_qp.cuh']
    onesided=texts['doca_gpunetio_dev_verbs_onesided.cuh']
    assert 'qp, num_chunks, code_opt' in onesided and 'num_chunks = num_chunks > 1 ? num_chunks : 1;' in onesided
    assert '#define DOCA_GPUNETIO_VERBS_MAX_TRANSFER_SIZE_SHIFT 30' in texts['doca_gpunetio_verbs_def.h']
    assert max(14464,14400,512,8)<2**30
    base=load(observed/'d8-ai40-p001.json');counts=Counter();checks=[];services=[]
    protocol=load(observed/'protocol-p001.json');physical=load(runs/'topology/topology.json')
    assert protocol['seed']==11 and protocol['ecn_by_rate']=={
        str(rate):dict(kmin_bytes=32768,kmax_bytes=131072,pmax=0.01) for rate in (100000000000,25000000000)}
    for case,actual in zip(index['cases'],execution['executed']):
        name=case['name'];assert actual['name']==name
        work=load(observed/(name+'.json'));normalized=deepcopy(work)
        for stream in normalized['streams']:stream['queue_depth']=8
        assert normalized==base and work['work']==writes
        assert all(s['queue_depth']==case['depth'] for s in work['streams'])
        result=runs/name/'result.json'
        if actual['exit_code']!=0:
            checks.append(dict(case=name,full_packet_verification=False,completion_unknown=True,result_available=result.exists()))
            continue
        r=load(result);t=r['persistent_transport'];events=t['dcqcn_reference']['events'];cfg=t['dcqcn_reference']['config']
        assert cfg==work['dcqcn_reference'] and cfg['additive_bps']==40000000
        assert r['passed'] and r['drained'] and r['completed_work']==180 and t['closed'] and t['physical_drained']
        assert r['packet_capture']['complete'] and not r['packet_capture']['omitted_events'] and r['fault_drops']==0
        assert all(not(p['pfc_pause'] or p['pfc_resume']) for p in r['ports'])
        prior=load(runs/name/('prior-inputs.json' if actual['kind']=='reused_success' else 'inputs.json'))
        command=prior['command']
        for i,path in enumerate((None,runs/'topology/topology.json',observed/'protocol-p001.json',observed/(name+'.json'))):
            assert (execution['backend_sha256'] if i==0 else sha(path))==prior['hashes'][command[i]]
        if actual['kind']=='reused_success':assert sha(result)==actual['result_sha256']
        streams={s['id']:s for s in work['streams']}
        source_packets={p[-1]:dict(zip(t['source_packet_columns'],p)) for p in t['source_packets']}
        assert len(source_packets)==len(t['source_packets'])==2700
        samples=[dict(zip(t['source_rate_capture']['columns'],p)) for p in t['source_rate_capture']['samples']]
        tx_samples={p['packet_uid']:p for p in samples if p['event']==0}
        ack_samples={p['packet_uid']:p for p in samples if p['event']==1}
        assert tx_samples.keys()==source_packets.keys()
        tx_logs={e['packet_uid']:e for e in events if e['kind']=='source_sent'}
        assert tx_logs.keys()==source_packets.keys()
        for uid,pkt in source_packets.items():
            assert (pkt['stream'],pkt['sequence'],pkt['time_ns'])==(tx_samples[uid]['stream'],tx_samples[uid]['sequence'],tx_samples[uid]['time_ns'])
            assert tx_logs[uid]['rate_bps']==tx_samples[uid]['rate_bps']
        generated={e['packet_uid']:e for e in events if e['kind']=='cnp_generated'}
        sent={e['packet_uid']:e for e in events if e['kind']=='cnp_tx'}
        received={e['packet_uid']:e for e in events if e['kind']=='cnp_received'}
        assert generated.keys()==sent.keys()==received.keys()
        packets=defaultdict(list)
        for values in r['packet_capture']['events']:
            point=dict(zip(r['packet_capture']['columns'],values));packets[point['packet_uid']].append(point)
        ports=Counter();data_ports=Counter();base_ports=Counter();rates={};data_rx=defaultdict(list);acks=defaultdict(list);marks=defaultdict(list)
        for uid,points in packets.items():
            assert len(points)==2 and {p['event'] for p in points}=={0,1}
            tx=next(p for p in points if p['event']==0);rx=next(p for p in points if p['event']==1)
            proto=tx['ip_protocol'];size=tx['model_bytes'];assert (proto,size)==(rx['ip_protocol'],rx['model_bytes'])
            if proto==17:
                packet=source_packets[uid];sid=packet['stream'];s=streams[sid];src,dst=s['src'],s['dst']
                assert (tx['time_ns'],tx['sequence'],rx['sequence'],size)==(packet['time_ns'],packet['sequence'],packet['sequence'],packet['payload_bytes']+36)
                data_rx[sid].append(rx)
                if rx['ecn']==3:marks[sid].append(rx['time_ns'])
                counts['data_packets']+=1
            elif proto==252:
                sid=ack_samples[uid]['stream'];s=streams[sid];src,dst=s['dst'],s['src']
                assert size==48 and (rx['time_ns'],rx['sequence'])==(ack_samples[uid]['time_ns'],ack_samples[uid]['sequence'])
                acks[sid].append(rx);counts['ack_packets']+=1
            else:
                assert proto==255 and size==48;sid=generated[uid]['stream'];s=streams[sid];src,dst=s['dst'],s['src']
                assert generated[uid]['time_ns']<=tx['time_ns']==sent[uid]['time_ns'] and rx['time_ns']==received[uid]['time_ns']
                counts['cnp_packets']+=1
            assert (tx['node'],rx['node'])==(src,dst)
            hops=route(physical,src,dst,s['wire_port']);assert rx['time_ns']-tx['time_ns']>=sum(p[4] for p in hops)
            for node,port,peer,rate,delay in hops:
                ports[node,port]+=size;rates[node,port]=rate
                if proto==17:data_ports[node,port]+=size
                if proto!=255:base_ports[node,port]+=size
            counts['endpoint_packet_joins']+=1
        assert {uid for uid,p in packets.items() if p[0]['ip_protocol']==17}==source_packets.keys()
        for p in r['ports']:
            key=p['node'],p['port']
            assert (p['model_tx_bytes'],p['data_tx_bytes'],p['control_tx_bytes'])==(ports[key],data_ports[key],ports[key]-data_ports[key])
            assert p.get('queue_metrics',{}).get('final_bytes',0)==0;counts['physical_ports']+=1
        after=0
        for sid in streams:
            assert [e['time_ns'] for e in generated.values() if e['stream']==sid]==notification_times(marks[sid],50000)
            works=sorted([w for w in t['work'] if w['stream']==sid],key=lambda w:w['ticket']);prefix=0
            for position,w in enumerate(works):
                assert w['begin']==prefix and w['end']==prefix+14464;prefix+=14464
                assert w['ready_ns']==0 and w['published_ns']==(0 if position<case['depth'] else works[position-case['depth']]['acked_ns'])
                assert w['accepted_ns']==max(p['time_ns'] for p in data_rx[sid] if w['begin']<=p['sequence']<w['end'])
                assert w['acked_ns']==min(p['time_ns'] for p in acks[sid] if p['sequence']>=w['end']);counts['work_callbacks_credit']+=1
            assert sum(p['payload_bytes'] for p in source_packets.values() if p['stream']==sid)==prefix
            first=min((e['time_ns'] for e in received.values() if e['stream']==sid),default=None)
            after+=sum(first is not None and p['stream']==sid and p['time_ns']>=first for p in source_packets.values())
        counts['rp_state_events']+=check_rp(events,cfg)
        row=next(c for c in summary['cases'] if c['name']==name)
        bound=max(Fraction(size*8*10**9,rates[k]) for k,size in base_ports.items());services.append(dict(base_ports))
        assert row['acked_ns']==max(w['acked_ns'] for w in t['work']) and row['finish_ns']==r['finish_ns']
        assert row['result_sha256']==sha(result) and row['minimum_port_service_ns']==float(bound)<=row['acked_ns']
        assert row['post_feedback_packets']==after and row['cnp_count']==len(generated)
        assert row['unique_destination_ce']==sum(map(len,marks.values()))
        assert row['minimum_rate_at_tx_bps']==min(p['rate_bps'] for p in tx_samples.values())
        if case['depth']==1024:assert all(w['published_ns']==0 for w in t['work']) and row['credit_delayed_work']==0
        checks.append(dict(case=name,full_packet_verification=True,all_work_published_at_zero=all(w['published_ns']==0 for w in t['work']),result_sha256=sha(result)))
    assert all(p==services[0] for p in services)
    assert execution['new_network_runs']==1 and execution['reused_successful_runs']==2
    explanation_checked=0
    if args.explanation:
        feedback=load(args.explanation/'feedback.json')
        for case in feedback['cases']:
            result=load(runs/case['case']/'result.json');t=result['persistent_transport']
            events=t['dcqcn_reference']['events'];source_packets=[dict(zip(t['source_packet_columns'],p)) for p in t['source_packets']]
            for row in case['streams']:
                txs=[p for p in source_packets if p['stream']==row['stream']]
                times=[e['time_ns'] for e in events if e['kind']=='cnp_received' and e['stream']==row['stream']]
                last=max(p['time_ns'] for p in txs)
                assert last==row['last_data_tx_ns'] and row['first_cnp_rx_ns']==min(times,default=None)
                assert row['cnp_before_last_data_tx']==len([tm for tm in times if tm<last])
                assert row['cnp_at_last_data_tx']==len([tm for tm in times if tm==last])
                assert row['cnp_after_last_data_tx']==len([tm for tm in times if tm>last])
                explanation_checked+=1
            ack=max(w['acked_ns'] for w in t['work'])
            assert case['last_business_ack_ns']==ack and case['physical_drain_ns']==result['finish_ns']
            assert case['drain_after_business_ack_ns']==result['finish_ns']-ack
            assert case['cnp_after_global_business_ack']==sum(e['time_ns']>ack for e in events if e['kind']=='cnp_received')
    args.output.mkdir()
    (args.output/'verification.json').write_text(json.dumps(dict(verified=True,
        all_declared_completed=execution['completed'],cases=checks,counts=dict(counts),
        sources_verified=len(source['sources']),conditional_cold_provider_work=452,
        maximum_sender_peer_work=capacity['maximum_per_sender_peer'],source_requested_sq_depth=1024,
        actual_sq_readback=False,software_credit_is_hardware_sq=False,
        static_physical_port_service_equal=True,new_network_runs=1,reused_successful_runs=2,
        modern_library_executed=False,hardware_calibrated=False,
        explanation_streams_checked=explanation_checked),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(verified=True,all_declared_completed=execution['completed'],counts=dict(counts))))


if __name__=='__main__':main()
