"""Observed inputs/results only; no original routing truth or verifier import."""
import argparse
from collections import Counter
import json
from pathlib import Path

from experiments.local_fault_baselines.graph_summary import case_summary


def load(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')


def first_dispatch(result,summary):
    t=result['persistent_transport'];c=result['packet_capture']
    events=[dict(zip(c['columns'],e)) for e in c['events']]
    source=[dict(zip(t['source_packet_columns'],e)) for e in t['source_packets']]
    recovery=[dict(zip(t['recovery']['columns'],e)) for e in t['recovery']['events']]
    work={v['id']:v for v in t['work']};streams={v['id']:v for v in t['streams']};output=[]
    for label in summary['writes']:
        if label['graph_id']!='g0.w0' or not label['name'].startswith('L0.dispatch.'):continue
        w=work[label['work']];sid=w['stream'];first={}
        for packet in source:
            if packet['stream']==sid and w['begin']<=packet['sequence']<packet['sequence']+packet['payload_bytes']<=w['end']:
                first.setdefault(packet['sequence'],packet)
        lost={e['packet_uid'] for e in events if e['message_id']==sid and e['ip_protocol']==17 and e['event'] in (2,5)}
        feedback=[e['time_ns'] for e in events if e['message_id']==sid and e['ip_protocol'] in (252,253) and e['event']==1 and e['node']==streams[sid]['src']]
        timeouts=[e for e in recovery if e['event']==2 and e['message_id']==sid]
        initial_lost=[e for e in first.values() if e['packet_uid'] in lost]
        output.append(dict(work=w['id'],stream=sid,bytes=w['bytes'],initial_packets=len(first),
            initial_packets_lost=len(initial_lost),initial_payload_lost=sum(v['payload_bytes'] for v in initial_lost),
            first_source_tx_ns=min(e['time_ns'] for e in first.values()),first_feedback_rx_ns=min(feedback),
            first_timeout_ns=timeouts[0]['time_ns'] if timeouts else None,
            timeout_precedes_any_feedback=bool(timeouts and timeouts[0]['time_ns']<min(feedback))))
    return output


def difference(before,after,a,b):
    previous={w['work']:w for w in a['writes']};writes=[]
    final={(v['work'],v['event']) for v in b['windows'][-1]['frontier']['writes']}
    for w in b['writes']:
        old=previous[w['work']];x=old['witness'];y=w['witness']
        ready=w['ready_ns']-old['ready_ns'];source=y['source_tx_ns']-x['source_tx_ns'];queue=y['queue_wait_ns']-x['queue_wait_ns']
        transit=(y['destination_rx_ns']-y['source_tx_ns']-y['queue_wait_ns'])-(x['destination_rx_ns']-x['source_tx_ns']-x['queue_wait_ns'])
        receive=w['accepted_ns']-old['accepted_ns'];assert receive==source+queue+transit
        writes.append(dict(work=w['work'],stream=w['stream'],name=w['name'],graph_id=w['graph_id'],
            receive_delta_ns=receive,ack_delta_ns=w['acked_ns']-old['acked_ns'],ready_delta_ns=ready,
            receive_after_ready_delta_ns=receive-ready,
            witness_send_after_ready_delta_ns=source-ready,witness_queue_delta_ns=queue,witness_transit_delta_ns=transit,
            witness_source_tx_delta_ns=source,witness_before=x,witness_after=y,
            final_declared_frontier_events=[event for wid,event in sorted(final) if wid==w['work']]))
    return dict(before=before,after=after,group_delta_ns=b['group_completion_ns']-a['group_completion_ns'],
        extra_source_payload_bytes=b['tx_payload_bytes']-a['tx_payload_bytes'],
        step_delta_ns=[y['completion_ns']-x['completion_ns'] for x,y in zip(a['windows'],b['windows'])],writes=writes,
        scope='observed paired interventions and exact receive witness accounting; not additive unique causal components or recoverable gain')


def main():
    p=argparse.ArgumentParser()
    for k in ('observed','planned','network','baseline','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();idx=load(a.observed/'index.json');cases={};dispatch={}
    for name in idx['new_cases']+idx['saved_cases']:
        where=a.baseline/name if name in idx['saved_cases'] else a.planned/name
        result=load((a.baseline/name if name in idx['saved_cases'] else a.network/name)/'result.json')
        summary=case_summary(a.observed,where,result)
        c=result['packet_capture'];counts=Counter(row[c['columns'].index('event')] for row in c['events'])
        summary['local_drop_events']=counts[5]
        summary['cnp_source_model_bytes']=summary['source_model_bytes_by_protocol'].get(255,0)
        cases[name]=summary;dispatch[name]=first_dispatch(result,summary)
    pairs=[('healthy','live-delayed'),('healthy','live-instant'),('healthy','static-degraded'),
           ('saved-fault','live-delayed'),('live-delayed','live-instant'),('static-degraded','live-instant')]
    contrasts=[difference(before,after,cases[before],cases[after]) for before,after in pairs]
    save(a.output,dict(schema_version='local-fault-baselines-analysis/1',cases=cases,first_dispatch=dispatch,contrasts=contrasts,
        scope=idx['scope'],warning='static removal changes routes and capacity jointly; zero control cost ideal; no modern calibration/algorithm benefit claim'))


if __name__=='__main__':main()
