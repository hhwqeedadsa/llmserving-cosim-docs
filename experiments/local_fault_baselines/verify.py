"""Independent original-route, DAG, transport, path and contrast checks."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from experiments.local_fault_baselines.checks import check_case,verify_sources
from experiments.local_fault_baselines.routing import check_routes


def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')


def check_dispatch(r,summary,reported):
    t=r['persistent_transport'];c=r['packet_capture'];events=[dict(zip(c['columns'],e)) for e in c['events']]
    packets=[dict(zip(t['source_packet_columns'],e)) for e in t['source_packets']]
    recovery=[dict(zip(t['recovery']['columns'],e)) for e in t['recovery']['events']]
    writes={v['id']:v for v in t['work']};streams={v['id']:v for v in t['streams']}
    required={v['work'] for v in summary['writes'] if v['graph_id']=='g0.w0' and v['name'].startswith('L0.dispatch.')}
    assert {v['work'] for v in reported}==required and len(reported)==len(required)
    for v in reported:
        w=writes[v['work']];sid=w['stream'];first={}
        for packet in packets:
            if packet['stream']==sid and w['begin']<=packet['sequence']<packet['sequence']+packet['payload_bytes']<=w['end']:
                first.setdefault(packet['sequence'],packet)
        lost_ids={e['packet_uid'] for e in events if e['event'] in (2,5) and e['ip_protocol']==17 and e['message_id']==sid}
        lost=[e for e in first.values() if e['packet_uid'] in lost_ids]
        assert (v['initial_packets'],v['initial_packets_lost'],v['initial_payload_lost'])==(len(first),len(lost),sum(e['payload_bytes'] for e in lost))
        feedback=min(e['time_ns'] for e in events if e['event']==1 and e['ip_protocol'] in (252,253) and e['message_id']==sid and e['node']==streams[sid]['src'])
        timeouts=[e['time_ns'] for e in recovery if e['event']==2 and e['message_id']==sid]
        assert v['bytes']==w['bytes'] and v['stream']==sid and v['first_source_tx_ns']==min(e['time_ns'] for e in first.values())
        assert v['first_feedback_rx_ns']==feedback and v['first_timeout_ns']==(timeouts[0] if timeouts else None)
        assert v['timeout_precedes_any_feedback']==bool(timeouts and timeouts[0]<feedback)


def main():
    p=argparse.ArgumentParser()
    for k in ('stage','analysis','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();s=a.stage;assert not a.output.exists();obs=s/'inputs/observed';index=load(obs/'index.json');analysis=load(a.analysis)
    for name,digest in load(s/'inputs/provenance.json')['files'].items():assert sha(s/'inputs'/name)==digest
    waves=verify_sources(s/'inputs/source-inputs',s/'inputs');topo=load(s/'topology/topology.json');reports={}
    for name in index['new_cases']+index['saved_cases']:
        where=s/'inputs/baseline'/name if name in index['saved_cases'] else s/'planned'/name
        r=load((s/'inputs/baseline'/name if name in index['saved_cases'] else s/'network'/name)/'result.json')
        w=load(where/'work.json');m=load(where/'mapping.json');protocol=load(obs/(name+'-protocol.json'));summary=analysis['cases'][name]
        assert m['protocol_sha256']==sha(obs/(name+'-protocol.json'))
        assert (where/'work.json').read_bytes()==(s/'inputs/baseline/saved-healthy/work.json').read_bytes()
        reports[name]=dict(graph_transport=check_case(waves,w,m,r,summary,protocol),routes=check_routes(topo,protocol,w,r))
        check_dispatch(r,summary,analysis['first_dispatch'][name])
        c=r['packet_capture'];assert summary['local_drop_events']==sum(e[c['columns'].index('event')]==5 for e in c['events'])
    for contrast in analysis['contrasts']:
        before=analysis['cases'][contrast['before']];after=analysis['cases'][contrast['after']]
        assert contrast['group_delta_ns']==after['group_completion_ns']-before['group_completion_ns']
        assert contrast['extra_source_payload_bytes']==after['tx_payload_bytes']-before['tx_payload_bytes']
        assert contrast['step_delta_ns']==[b['completion_ns']-a['completion_ns'] for a,b in zip(before['windows'],after['windows'])]
        previous={w['work']:w for w in before['writes']};now={w['work']:w for w in after['writes']}
        assert {w['work'] for w in contrast['writes']}==set(previous)==set(now)
        for row in contrast['writes']:
            old=previous[row['work']];new=now[row['work']];x=old['witness'];y=new['witness']
            assert row['receive_delta_ns']==new['accepted_ns']-old['accepted_ns']
            assert row['ack_delta_ns']==new['acked_ns']-old['acked_ns'] and row['ready_delta_ns']==new['ready_ns']-old['ready_ns']
            assert row['receive_after_ready_delta_ns']==row['receive_delta_ns']-row['ready_delta_ns']
            assert row['witness_before']==x and row['witness_after']==y
            assert row['witness_source_tx_delta_ns']==y['source_tx_ns']-x['source_tx_ns']
            assert row['witness_queue_delta_ns']==y['queue_wait_ns']-x['queue_wait_ns']
            assert row['witness_send_after_ready_delta_ns']==row['witness_source_tx_delta_ns']-row['ready_delta_ns']
            assert row['receive_delta_ns']==sum(row[k] for k in ('ready_delta_ns','witness_send_after_ready_delta_ns','witness_queue_delta_ns','witness_transit_delta_ns'))
    name='live-delayed';where=s/'planned'/name;work=load(where/'work.json');mapping=load(where/'mapping.json');protocol=load(obs/(name+'-protocol.json'))
    result=load(s/'network'/name/'result.json');summary=analysis['cases'][name];rejected=[]
    def reject(label,target,change,check):
        bad=deepcopy(target);change(bad)
        try:check(bad)
        except (AssertionError,KeyError,StopIteration):rejected.append(label)
        else:raise AssertionError('undetected mutation '+label)
    graph=lambda r:check_case(waves,work,mapping,r,summary,protocol)
    reject('actual_ACK',result,lambda r:r['persistent_transport']['work'][0].__setitem__('acked_ns',-1),graph)
    reject('declared_dependency',work,lambda w:next(v for v in w['work'] if v['after']).__setitem__('after',[]),lambda w:check_case(waves,w,mapping,result,summary,protocol))
    def wrong_time(r):
        c=r['packet_capture'];kind=c['columns'].index('event');time=c['columns'].index('time_ns')
        next(e for e in c['events'] if e[kind]==1)[time]+=1
    reject('actual_packet_arrival',result,wrong_time,graph)
    reject('actual_feedback_bytes',result,lambda r:next(v for v in r['persistent_transport']['selective_repeat']['events'] if v['kind']=='feedback_received').__setitem__('extension_hex','00'*16),graph)
    reject('local_candidate_count',result,lambda r:r['link_failures']['local_failover']['events'][0].__setitem__('installed_next_hops',999),lambda r:check_routes(topo,protocol,work,r))
    reject('declared_route_update',result,lambda r:r['link_failures']['events'][1].__setitem__('time_ns',4000),lambda r:check_routes(topo,protocol,work,r))
    reject('initial_lost_bytes',analysis['first_dispatch'][name],lambda x:x[0].__setitem__('initial_payload_lost',-1),lambda x:check_dispatch(result,summary,x))
    assert reports['static-degraded']['routes']['local_drop_causes']=={}
    assert reports['static-degraded']['graph_transport']['physical_drops']==0
    assert load(s/'healthy-compatibility.json')['all_fields_equal'] and load(s/'run.json')['new_actual_networks']==4
    save(a.output,dict(passed=True,cases=reports,raw_route_projection_verified=True,all_original_dependencies_verified=True,
        all_work_byte_identical=True,all_first_dispatch_loss_feedback_verified=True,all_contrasts_verified=True,
        evidence_tamper_classes_rejected=rejected,new_actual_networks=4,saved_baseline_inputs=2,
        scope='reference-network mechanisms only; no complete modern inference/calibration/novelty claim'))


if __name__=='__main__':main()
