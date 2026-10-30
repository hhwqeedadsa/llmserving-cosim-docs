"""Independently recheck missing bytes using bitmaps and source physical TX."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import gc
import hashlib
import json
from pathlib import Path


def load(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def case_check(r,case):
    cap=r['packet_capture'];n=r['service_transport']['native']
    assert cap['complete'] and not cap['omitted_events']
    packets=[dict(zip(cap['columns'],row)) for row in cap['events']]
    actual=Counter((x['time_ns'],x['node'],x['message_id'],x['sequence'],x['data_payload_bytes'],x['packet_uid'])
      for x in packets if x['event']==cap['event_codes']['tx'] and x['node']<128 and x['ip_protocol']==17)
    sources=[dict(zip(n['source_packet_columns'],row)) for row in n['source_packets']]
    expected=Counter((x['time_ns'],x['src'],x['stream'],x['sequence'],x['payload_bytes'],x['packet_uid']) for x in sources)
    assert actual==expected
    seen=defaultdict(bytearray)
    for x in sources:
        mask=seen[x['stream']];begin,end=x['sequence'],x['sequence']+x['payload_bytes']
        if end>len(mask):mask.extend(b'\0'*(end-len(mask)))
        x['repeated_bytes']=sum(mask[begin:end]);mask[begin:end]=b'\1'*(end-begin)
    assert all(all(mask) for mask in seen.values())
    assert sum(len(mask) for mask in seen.values())==case['logical_payload_bytes']
    assert sum(x['repeated_bytes'] for x in sources)==case['repeated_payload_bytes']
    sr=n['selective_repeat']['events'];rec=[dict(zip(n['recovery']['columns'],row)) for row in n['recovery']['events']]
    triggers=[('timeout',x['time_ns'],x['message_id'],x) for x in rec if x['event']==n['recovery']['event_codes']['timeout_retransmit']]
    triggers.extend(('fast_retransmit',x['time_ns'],x['stream'],x) for x in sr if x['kind']=='fast_retransmit')
    assert Counter(x[0] for x in triggers)==Counter(case['trigger_counts'])
    assert len(triggers)==len(case['batches'])
    rate={(p['node'],p['port']):p['rate_bps'] for p in r['ports']}
    streams={s['id']:s for s in n['streams']}
    feedback_RX={(x['time_ns'],x['message_id'],x['packet_uid'],x['ip_protocol']) for x in packets
        if x['event']==cap['event_codes']['rx'] and x['node']<128 and x['ip_protocol'] in (252,253)}
    full_count=0
    for b in case['batches']:
        t=b['trigger'];now,sid=t['time_ns'],t['stream']
        assert (t['kind'],now,sid,t['source_event']) in triggers
        stream=streams[sid];assert b['nic']==stream['source_nic'] and t['source']==stream['src']
        if t['kind']=='timeout':
            assert t['ack']==t['source_event']['acknowledged_bytes'] and t['high']==t['source_event']['highest_sent_bytes']
        else:
            assert t['ack']==t['source_event']['cumulative_ack']
        assert t['high']==max(x['sequence']+x['payload_bytes'] for x in sources if x['stream']==sid and x['time_ns']<now)
        feedback=[];ties=[]
        for i,x in enumerate(sr):
            if x['kind']!='feedback_received' or x['stream']!=sid:continue
            if x['time_ns']<now or (x['time_ns']==now and t['kind']=='fast_retransmit' and i<t['selective_index']):feedback.append(x)
            elif x['time_ns']==now:ties.append(x)
        assert feedback==b['source_feedback'] and ties==b['same_ns_unordered_feedback']
        missing=set(range(t['ack'],t['high']))
        for x in feedback:
            assert (x['time_ns'],sid,x['packet_uid'],x['ip_protocol']) in feedback_RX
            raw=bytes.fromhex(x['extension_hex']);assert len(raw)==16 and raw[:4]==b'MSR1'
            assert int.from_bytes(raw[4:8],'big')==x['sack_begin'] and int.from_bytes(raw[8:12],'big')==x['sack_end']
            assert bool(int.from_bytes(raw[12:16],'big'))==x['sack_valid']
            if x['sack_valid']:missing.difference_update(range(x['sack_begin'],x['sack_end']))
        assert len(missing)==b['missing_bytes']
        assert missing=={v for left,right in b['missing_ranges'] for v in range(left,right)}
        future=[x['time_ns'] for x in sr if x['kind']=='feedback_received' and x['stream']==sid and x['time_ns']>now]
        future.extend(tim for _,tim,stream,_ in triggers if stream==sid and tim>now)
        cutoff=min(future,default=r['finish_ns']+1);assert cutoff==b['cutoff_ns']
        selected=[];remaining=set(missing)
        for x in sources:
            if x['stream']!=sid or not now<=x['time_ns']<cutoff or not x['repeated_bytes']:continue
            covered=remaining.intersection(range(x['sequence'],x['sequence']+x['payload_bytes']))
            if covered:selected.append(x);remaining.difference_update(covered)
            if not remaining:break
        assert selected==b['actual_retransmission_packets']
        assert remaining=={v for left,right in b['uncovered_ranges'] for v in range(left,right)}
        complete=bool(missing) and not remaining and not ties
        assert complete==b['fully_covered_before_next_feedback'];full_count+=complete
        first=selected[0]['time_ns'] if selected else None
        last=selected[-1]['time_ns'] if selected else None
        assert first==b['first_retransmission_ns'] and last==b['last_retransmission_start_ns']
        assert b['first_retransmission_delay_ns']==(None if first is None else first-now)
        duration=lambda x:round((x['payload_bytes']+36)*8*10**9/rate[x['src'],x['nic']])
        end=max((x['time_ns']+duration(x) for x in selected),default=None)
        assert end==b['batch_serialization_end_ns']
        own=sum(duration(x) for x in selected);assert own==b['own_serialization_ns']
        occupancy=[]
        if end is not None:
            for x in sources:
                if x['src']!=stream['src'] or x['nic']!=stream['source_nic'] or x['stream']==sid:continue
                amount=max(0,min(end,x['time_ns']+duration(x))-max(now,x['time_ns']))
                if amount:occupancy.append(dict(source_packet=x,overlap_ns=amount))
        assert occupancy==b['other_QP_serialization']
        occupied=sum(x['overlap_ns'] for x in occupancy);assert occupied==b['other_QP_occupied_ns']
        assert b['batch_span_ns']==(None if end is None else end-now)
        assert b['unexplained_or_interpacket_ns']==(None if end is None else end-now-own-occupied)
    assert full_count==case['complete_batches']
    waits=[b for b in case['batches'] if b['first_retransmission_delay_ns'] is not None and b['first_retransmission_delay_ns']>0]
    assert len(waits)==case['positive_first_send_wait_count']
    overlaps=[]
    for i,a in enumerate(waits):
        for b in waits[i+1:]:
            if (a['trigger']['source'],a['nic'])!=(b['trigger']['source'],b['nic']):continue
            assert a['trigger']['stream']!=b['trigger']['stream']
            amount=max(0,min(a['first_retransmission_ns'],b['first_retransmission_ns'])-
                max(a['trigger']['time_ns'],b['trigger']['time_ns']))
            if amount:overlaps.append(amount)
    assert len(overlaps)==len(case['simultaneous_waiting_recoveries'])
    return dict(triggers=len(triggers),complete_batches=full_count,actual_source_packets=len(sources),
        source_feedback_witnesses=sum(len(b['source_feedback']) for b in case['batches']))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('input','analysis','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();f=load(a.input/'frozen.json');analysis=load(a.analysis)
    assert analysis['passed'] and analysis['frozen_sha256']==sha(a.input/'frozen.json')
    results={};tamper=[]
    for name,row in f['cases'].items():
        path=Path(row['directory'])/'result.json';assert sha(path)==row['result_sha256']==analysis['cases'][name]['result_sha256']
        raw=load(path);results[name]=case_check(raw,analysis['cases'][name])
        if name=='prefix-w60':
            for kind,field in [('missing_bytes','missing_bytes'),('first_send_time','first_retransmission_ns'),('other_QP_occupancy','other_QP_occupied_ns')]:
                edited=deepcopy(analysis['cases'][name]);edited['batches'][0][field]+=1
                try:case_check(raw,edited)
                except AssertionError:tamper.append(kind)
                else:raise AssertionError('tampered statistic accepted: '+kind)
        del raw;gc.collect()
    assert len(tamper)==3
    value=dict(passed=True,cases=results,tamper_checks=tamper,analysis_sha256=sha(a.analysis),
      verifier_sha256=sha(Path(__file__)),scope='independent byte maps, source physical TX, real source MSR1 feedback; no receiver truth used for missing ranges')
    with a.output.open('x') as s:json.dump(value,s,indent=2);s.write('\n')
    print(json.dumps(dict(passed=True,cases=results,tamper_checks=tamper)))


if __name__=='__main__':main()
