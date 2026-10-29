"""Post-result descriptive checks, not a preregistered new policy or network run."""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path


def read(path):return json.loads(path.read_bytes())


def explain(directory):
    result=read(directory/'result.json');flows={m['id']:m for m in result['messages']}
    sent=defaultdict(dict);received={}
    capture=result['packet_capture']
    assert capture['complete'] and capture['omitted_events']==0
    for values in capture['events']:
        p=dict(zip(capture['columns'],values));identity=p['message_id']
        if identity not in flows or p['ip_protocol']!=252:continue
        m=flows[identity];uid=p['packet_uid']
        if p['node']==m['dst'] and p['event']==0:
            assert uid not in sent[identity],'ACK UID reused at destination TX'
            sent[identity][uid]=(p['time_ns'],p['sequence'])
        if p['node']==m['src'] and p['event']==1:
            assert (identity,uid) not in received,'ACK UID reused at source RX'
            received[identity,uid]=p['time_ns']
    recovery=result['recovery'];code=recovery['event_codes']['timeout_retransmit'];examples=[];timeouts=0
    for values in recovery['events']:
        if values[1]!=code:continue
        time,_,source,identity,known,*_=values;timeouts+=1
        candidates=[(tx,seq,uid,received[identity,uid]) for uid,(tx,seq) in sent[identity].items()
                    if tx<time and seq>known and received.get((identity,uid),-1)>time]
        if candidates:
            tx,seq,uid,rx=min(candidates)
            examples.append(dict(timeout_ns=time,message_id=identity,source=source,known_ack_bytes=known,
                feedback_tx_ns=tx,feedback_rx_ns=rx,feedback_sequence=seq,
                message_bytes=flows[identity]['bytes']))
    return dict(timeouts=timeouts,timeouts_with_advancing_ack_already_inflight=len(examples),examples=examples[:3],
                rtt_sample_count_histogram=dict(Counter(s.get('rtt',{}).get('samples',0) for s in recovery['states'])))


def diagnose(study,baseline,output):
    if output.exists():raise ValueError('new output required')
    paths={'fixed40-normal':baseline/'bottleneck25','fixed40-loss50':baseline/'loss50'}
    paths.update({name:study/name for name in ('fixed80-normal','karn80-normal','fixed80-loss50','karn80-loss50')})
    cases={name:explain(path) for name,path in paths.items()}
    assert cases['fixed40-normal']['timeouts_with_advancing_ack_already_inflight']==86
    a,b=(read(study/name/'result.json') for name in ('fixed80-normal','karn80-normal'))
    comparison=dict(packet_capture_equal=a['packet_capture']==b['packet_capture'],
                    messages_equal=a['messages']==b['messages'],ports_equal=a['ports']==b['ports'],
                    service_equal=a['service']==b['service'])
    value=dict(schema_version='moe-service-recovery-baselines-postcheck/1',network_runs=0,
               scope='post-result descriptive analysis; no counterfactual removable-delay claim',
               cases=cases,fixed80_vs_karn80_normal=comparison,
               script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    with output.open('x') as stream:json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(dict(passed=True,normal_comparison=comparison,
        timeout_ack_inflight_counts={k:v['timeouts_with_advancing_ack_already_inflight'] for k,v in cases.items()})))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('study','baseline-root','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();diagnose(args.study,args.baseline_root,args.output)
