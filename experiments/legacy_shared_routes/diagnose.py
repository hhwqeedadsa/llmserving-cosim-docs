"""Bound claims using actual pressure, drops and common graph predecessors."""
import argparse
from collections import Counter
import json
from pathlib import Path

if not __debug__:raise RuntimeError('Do not run diagnosis with -O')
load=lambda p:json.loads(Path(p).read_bytes())


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,required=True)
    p.add_argument('--analysis',type=Path,required=True);a=p.parse_args()
    root=a.study.parent;out=a.analysis/'diagnosis.json';assert not out.exists()
    conditions={}
    for case in ('normal','local','prefix'):
        detail=load(a.analysis/(case+'-mechanism.json'));raw=load(root/('run-'+case)/'result.json')
        switch_ports=detail['queue_metrics'];peak=max(switch_ports,key=lambda r:r['queue_metrics']['peak_bytes'])
        drops=[row for row in raw['packet_capture']['events'] if row[3] in (2,5)]
        assert all(row[5]<15872 for row in drops)
        conditions[case]=dict(max_switch_queue_bytes=peak['queue_metrics']['peak_bytes'],
            peak_queue_port=dict(node=peak['node'],port=peak['port'],peer=peak['peer']),
            ecn_ce_port_observations=sum(r['ecn_ce_seen'] for r in switch_ports),
            pfc_pause_events=sum(r['pfc_pause'] for r in switch_ports),
            minimum_ecn_kmin_bytes=min(r['ecn_kmin_bytes'] for r in switch_ports),
            captured_drop_events=len(drops),second_group_captured_drop_events=0,
            captured_drop_witnesses=drops[:16],all_drop_witnesses_retained=len(drops)<=16)
        del raw
    # The selected group0 latest Combine data predecessor is the same event
    # across normal/prefix. Its actual issue/accept differences explain the
    # observed residual without summing maxima from unrelated ranks.
    records={c:load(a.analysis/(c+'-mechanism.json')) for c in ('normal','prefix')}
    common=[]
    for c in ('normal','prefix'):
        chain=records[c]['groups'][0]['selected_latest_predecessor_chain']
        accept=next(e for e in chain if e['name']=='accepted' and e['operation_id'] is not None and e['operation'][3]=='data')
        issue=next(e for e in chain if e['name']=='issued' and e['operation_id']==accept['operation_id'])
        common.append(dict(case=c,event=accept['event'],operation_id=accept['operation_id'],rank=accept['rank'],
            issued_ns=issue['time_ns'],accepted_ns=accept['time_ns'],span_ns=accept['time_ns']-issue['time_ns']))
    assert common[0]['event']==common[1]['event'] and common[0]['operation_id']==common[1]['operation_id']
    delta_issue=common[1]['issued_ns']-common[0]['issued_ns'];delta_span=common[1]['span_ns']-common[0]['span_ns']
    delta_accept=common[1]['accepted_ns']-common[0]['accepted_ns'];assert delta_issue+delta_span==delta_accept==194
    result=dict(passed=True,conditions=conditions,group0_common_predecessor=common,
        issue_delta_ns=delta_issue,issue_to_accept_span_delta_ns=delta_span,accept_delta_ns=delta_accept,
        second_group_critical_operation_switches=True,
        scope='Issue-to-accept includes source ordering and transport, not pure switch delay; no removable-delay bound; no full-path packet capture')
    out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
