"""Audit the stopped first case from exact TX/RX, callback, EOF and no new TX."""
import argparse
import json
from pathlib import Path
import struct


def read(p):return json.loads(p.read_text())
def rows(p):return [json.loads(x) for x in p.read_text().splitlines()]
def check(ok,why):
    if not ok:raise AssertionError(why)


def audit(s,tamper=None):
    c=s/'cases/fast-shared';r=read(c/'truth/result.json');t=r['transactions'];tr=t['transport']
    observed=rows(c/'observed/decisions.jsonl');control=rows(c/'truth/control.jsonl')
    if tamper=='invent_committed_new':control.append(dict(direction='frontend',message=dict(submissions=observed[-1]['submissions'])))
    if tamper=='wrong_memory':t['windows'][-1]['hex']='22'*32
    if tamper=='early_callback':observed[2]['view']['events'][0]['time_ns']-=1
    check(not r['passed'] and r['finish_ns']==100000 and r['execution_error']=='RDMA: frontend EOF or oversized session command','actual EOF class')
    check(len(observed)==5 and len(control)==9,'uncommitted rejected final command')
    check(len(t['operations'])==len(tr['work'])==1 and t['operations'][0]['id']==0,'rejected write was published')
    check(not tr['recovery']['failures'] and not t['active_source_leases'],'not a source exhaustion failure')
    for i,o in enumerate(observed):
        n=control[2*i];check(n['direction']=='native','callback identity');v=n['message']
        filtered=[e for e in v['events'] if 'alarm' in e or e.get('event')=='applied']
        check(o['view']==dict(decision_id=i,now_ns=v['now_ns'],events=filtered),'fabricated observation')
        if i<4:
            commit=control[2*i+1];check(commit['direction']=='frontend' and commit['message']['submissions']==o['submissions'],'actual commit mismatch')
    rejected=observed[-1]['submissions'];check(len(rejected)==1 and rejected[0]['id']==1 and rejected[0]['after']==[] and rejected[0]['address']==8192,'rejected candidate identity')
    check('overlapping non-atomic memory writes require remote effect order' in (s/'monitors/fast-shared/stderr.log').read_text(),'actual Python rejection reason')
    packet=tr['source_payload_packets'];check(len(packet)==1,'unexpected new source TX')
    raw=bytes.fromhex(packet[0]['hex']);check(struct.unpack('>IIQQII',raw[:32])==(0x4d525431,1,0,8192,9,8) and raw[32:]==b'\x11'*8,'wire bytes')
    source=dict(zip(tr['source_packet_columns'],tr['source_packets'][0]));check(source['src']==0 and source['stream']==0,'source TX identity')
    cap=r['packet_capture'];events=[dict(zip(cap['columns'],e)) for e in cap['events']]
    rx=[e for e in events if e['node']==4 and e['event']==1 and e['ip_protocol']==17]
    check(cap['complete'] and len(rx)==1 and rx[0]['packet_uid']==source['packet_uid'] and rx[0]['time_ns']==6036,'actual target RX')
    check(t['target_events'][0]['time_ns']==6036 and t['target_events'][0]['body_hex']=='11'*8,'actual target write')
    window=next(w for w in t['windows'] if w['owner']==4);check(window['hex']=='11'*8+'00'*24,'target bytes')
    operation=t['operations'][0];check(operation['applied_ns']==6036 and operation['completed_ns']==operation['request_acked_ns']==12060,'old request completed normally')
    check(any(e['node']==0 and e['event']==1 and e['ip_protocol']==252 and e['time_ns']==12060 for e in events),'old completion actual ACK')
    check(not tr['payload_pool']['held'],'old payload not reclaimed')
    for name in ('late-shared','late-isolated','late-copy-out','late-receive-order'):
        check(not (s/'cases'/name).exists(),'original matrix continued')
    return dict(condition='fast-shared',status='stopped_on_frontend_admission',source_packets=1,old_applied_ns=6036,old_completed_ns=12060,rejected_new_ns=100000,new_committed=False,stale_write_hypothesis_tested=False,source_exhaustion_tested=False,remaining_original_conditions_unexecuted=4)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=audit(a.stage);tamper=[]
    for kind in ('invent_committed_new','wrong_memory','early_callback'):
        try:audit(a.stage,kind)
        except AssertionError as e:tamper.append(dict(kind=kind,rejected=True,reason=str(e)))
        else:raise AssertionError('tamper accepted')
    a.output.write_text(json.dumps(dict(result=result,tampering=tamper),sort_keys=True,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
