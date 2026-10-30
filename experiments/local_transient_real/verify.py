"""Independent raw-route and source-packet checks; never starts a network."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from moe_ub_cosim.service_terminal import service_terminal

CASES=('gbn-normal','gbn-local','sr-normal','sr-local')


def load(p):return json.loads(p.read_bytes())
def digest(raw):return hashlib.sha256(raw).hexdigest()
def need(ok,text):
    if not ok:raise ValueError(text)


def source_account(result):
    native=result['service_transport']['native'];cap=result['packet_capture']
    need(cap['complete'] and cap['omitted_events']==0,'capture truncated')
    streams={s['id']:s for s in native['streams']};actual=Counter();sizes=Counter();counts=Counter();intervals={sid:[] for sid in streams}
    for row in cap['events']:
        p=dict(zip(cap['columns'],row));sid=p['message_id']
        if sid in streams and p['node']==streams[sid]['src'] and p['ip_protocol']==17 and p['event']==0:
            actual[sid,p['time_ns'],p['sequence'],p['data_payload_bytes'],p['packet_uid']]+=1
            sizes[sid]+=p['data_payload_bytes'];counts[sid]+=1
            intervals[sid].append((p['sequence'],p['sequence']+p['data_payload_bytes']))
    native_packets=Counter()
    for row in native['source_packets']:
        p=dict(zip(native['source_packet_columns'],row))
        native_packets[p['stream'],p['time_ns'],p['sequence'],p['payload_bytes'],p['packet_uid']]+=1
    need(actual==native_packets,'source packets differ between hooks')
    for sid,s in streams.items():
        need((counts[sid],sizes[sid])==(s['source_data_packets'],s['source_payload_bytes']),'source count mismatch')
        end=0
        for a,b in sorted(intervals[sid]):
            need(0<=a<=end and a<b<=s['published_bytes'],'source byte coverage has gaps or exceeds publication')
            end=max(end,b)
        need(end==s['published_bytes']==s['snd_una'],'successful source prefix missing')
    return dict(source_packets=sum(counts.values()),source_payload_bytes=sum(sizes.values()),
        unique_source_bytes=sum(s['published_bytes'] for s in streams.values()))


def fault_check(result,hotspot):
    fault=result['data_loss']['configuration']
    need(fault==dict(kind='periodic_destination_data/1',numerator=1,denominator=1,offset=0,
        start_ns=0,end_ns=100000,nodes=[hotspot]),'fault configuration changed')
    count=0;all_data_drops=0
    for row in result['packet_capture']['events']:
        p=dict(zip(result['packet_capture']['columns'],row))
        if p['event']==2 and p['ip_protocol']==17:
            all_data_drops+=1
            need(p['node']==hotspot and 0<=p['time_ns']<100000,'data loss outside fault')
        if p['node']==hotspot and p['ip_protocol']==17 and p['event'] in (1,2) and p['time_ns']<100000:
            need(p['event']==2,'blackout accepted data');count+=1
    need(count>0 and count==all_data_drops==result['fault_drops']==result['data_loss']['dropped_packets'],'fault count differs')
    return count


def original_matrix(raw_by_rank,group,step):
    expected=Counter()
    for rank in group:
        for layer,lid in enumerate((3,4)):
            for expert in raw_by_rank[rank][step+1][str(lid)][0]:
                dst=group[expert//64]
                if dst!=rank:
                    expected[layer,'dispatch',rank,dst]+=7168*2
                    expected[layer,'combine',dst,rank]+=7168*2
    return dict(expected)


def check_wave(wave,expected):
    actual={(m['layer'],m['phase'],m['src'],m['dst']):m['bytes'] for m in wave['messages']}
    need(len(actual)==len(wave['messages']) and actual==expected,'raw-route message matrix differs')


def rejects(function):
    try:function()
    except (ValueError,KeyError):return True
    raise ValueError('tampered evidence was accepted')


def verify(root,output):
    prepared=root/'prepared';prep=load(prepared/'preparation.json');raws={}
    for row in prep['sources']:
        raw=(prepared/'routes'/row['name']).read_bytes();need(digest(raw)==row['sha256'],'raw hash differs')
        raws[row['rank']]=json.loads(raw)
    need(len(raws)==8 and len({row['sha256'] for row in prep['sources']})==8,'duplicated originals')
    results={};frontends={};rows={}
    for case in CASES:
        directory=root/case;r=load(directory/'result.json');frontend=load(directory/'frontend.json')
        service_terminal(r,frontend,require_complete=True)
        count=0;logical=0
        for wave_path in sorted((directory/'graphs').glob('*/wave.json')):
            w=load(wave_path);expected=original_matrix(raws,prep['groups'][w['group_index']],w['decode_step'])
            check_wave(w,expected);count+=len(expected);logical+=sum(expected.values())
        need((count,logical)==(prep['messages'],prep['logical_bytes']),'planned total differs')
        source=source_account(r);need(source['unique_source_bytes']==logical,'unique source total differs')
        drops=fault_check(r,prep['hotspot_rank']) if case.endswith('local') else 0
        need(r['switch_drop_capture']['all_switch_reason_counts']==[0]*4,'unexpected switch drop')
        rows[case]=dict(messages=count,logical_bytes=logical,packet_events=len(r['packet_capture']['events']),
            **source,fault_drops=drops)
        results[case]=r;frontends[case]=frontend
    # Five independent rejection classes. No new simulation or weakened inputs.
    sample=load(root/'sr-local/graphs/g0.w0/wave.json');bad=deepcopy(sample);bad['messages'][0]['bytes']+=1
    expected=original_matrix(raws,prep['groups'][0],0)
    rejection=dict(workload=rejects(lambda:check_wave(bad,expected)))
    wrong=deepcopy(results['sr-local']);wrong['data_loss']['configuration']['nodes']=[7]
    rejection['fault_position']=rejects(lambda:fault_check(wrong,prep['hotspot_rank']))
    wrong_time=deepcopy(results['sr-local']);wrong_time['service']['waves'][0]['finished_ns']+=1
    rejection['completion_time']=rejects(lambda:service_terminal(wrong_time,frontends['sr-local'],require_complete=True))
    wrong_count=deepcopy(results['sr-local']);wrong_count['service_transport']['native']['streams'][0]['source_data_packets']+=1
    rejection['source_count']=rejects(lambda:source_account(wrong_count))
    raw=(prepared/'routes'/prep['sources'][0]['name']).read_bytes()
    rejection['source_hash']=rejects(lambda:need(digest(raw+b' ') == prep['sources'][0]['sha256'],'raw hash differs'))
    record=dict(schema_version='moe-local-transient-verification/1',passed=True,network_runs=0,cases=rows,
        tamper_rejections=rejection,input_sha256=digest((prepared/'preparation.json').read_bytes()),
        scope='independent raw expert matrices/source packet hooks/interval coverage and public terminal checks; not hardware calibration')
    with output.open('x') as f:json.dump(record,f,indent=2);f.write('\n')
    print(json.dumps(record))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();verify(a.root.resolve(),a.output.resolve())
