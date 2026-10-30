"""Evaluate saved networks, independently checking guard prefixes and all work."""
import argparse
from copy import deepcopy
import gc
import json
from pathlib import Path

import inspection as audit
from prepare import normalized,files

ROOT=Path(__file__).resolve().parents[2]
load,sha,save=audit.load,audit.sha,audit.save

def compare(control,treatment,baseline,changed):
    assert normalized(control['config'])==normalized(treatment['config'])
    for name in ('rdma.json','topology.json'):assert baseline['sha256'][name]==changed['sha256'][name]
    assert all(treatment['waves'][graph]==wave for graph,wave in control['waves'].items())
    assert baseline['fault_drops']==changed['fault_drops']
    assert baseline['standby_memory']['initial_reserved_bytes']==changed['standby_memory']['initial_reserved_bytes']
    before={tuple(row[k] for k in ('stream','sequence','payload_bytes','attempt','node','port')):row
        for row in control['residence'] if row['group']==2}
    hops=[]
    for row in (r for r in treatment['residence'] if r['group']==2):
        key=tuple(row[k] for k in ('stream','sequence','payload_bytes','attempt','node','port'));old=before.pop(key)
        hops.append(dict(stream=row['stream'],sequence=row['sequence'],node=row['node'],port=row['port'],
            baseline_rx_ns=old['rx_ns'],treatment_rx_ns=row['rx_ns'],baseline_tx_ns=old['tx_ns'],treatment_tx_ns=row['tx_ns'],
            residence_change_ns=row['residence_ns']-old['residence_ns']))
    assert not before
    gates={(g['graph_id'],g['id']):g for g in control['healthy_gates']}
    phases=[dict(graph=g['graph_id'],phase=g['id'],baseline_ns=gates[g['graph_id'],g['id']]['released_ns'],
        treatment_ns=g['released_ns'],change_ns=g['released_ns']-gates[g['graph_id'],g['id']]['released_ns']) for g in treatment['healthy_gates']]
    messages={m['id']:m for m in control['healthy_messages']};deltas=[]
    for message in treatment['healthy_messages']:
        original=messages[message['id']]
        deltas.append(dict(message=message['id'],stream=message['stream'],
            ready_change_ns=message['ready_ns']-original['ready_ns'],
            receive_change_ns=message['receive_ready_ns']-original['receive_ready_ns'],
            ack_change_ns=message['ack_ns']-original['ack_ns']))
    effect=dict(target_saved_ns=baseline['result_ready_ns']['target']-changed['result_ready_ns']['target'],
        target_saved_percent=(baseline['result_ready_ns']['target']-changed['result_ready_ns']['target'])/baseline['result_ready_ns']['target']*100,
        healthy_change_ns=changed['result_ready_ns']['healthy']-baseline['result_ready_ns']['healthy'],
        network_drain_change_ns=changed['network_drained_ns']-baseline['network_drained_ns'],
        extra_effective_bytes=changed['unique_payload_bytes']-baseline['unique_payload_bytes'],
        extra_source_payload_bytes=changed['source_payload_bytes']-baseline['source_payload_bytes'],
        extra_declared_compute_ns=changed['declared_compute_work_ns']-baseline['declared_compute_work_ns'],
        all_packets_exact=control['result']['packet_capture']==treatment['result']['packet_capture'],
        all_queue_events_exact=control['result']['queue_events']==treatment['result']['queue_events'],
        healthy_hops_matched=len(hops),healthy_hops_unmatched=0,
        healthy_residence_change_sum_ns=sum(r['residence_change_ns'] for r in hops),healthy_phase_changes=phases)
    return effect,hops,deltas

def augment(summary,detail):
    whole=detail['frontend']['whole_call_replication'];summary['calls']=whole['calls']
    summary['source_retransmitted_payload_bytes']=summary['source_payload_bytes']-summary['unique_payload_bytes']
    guard=whole.get('source_ack_guard');summary['guard']=deepcopy(guard)
    if guard:
        starts=[d for d in guard['decisions'] if d['reason'] in ('source_timeout','fallback')]
        summary['guard_launches']=[]
        timeout=next(c['source_ack_timeout_ns'] for c in whole['configuration']['calls'] if c['id']=='target')
        for d in starts:
            nominal=[dict(message=mid,deadline_ns=guard['messages'][str(mid)]['post_ns']+timeout,
                delay_to_actual_check_ns=d['time_ns']-guard['messages'][str(mid)]['post_ns']-timeout) for mid in d['overdue_messages']]
            summary['guard_launches'].append(dict(actual_ns=d['time_ns'],reason=d['reason'],nominal=nominal))
    summary['native_timer_events']=detail['result']['service']['control_wakeup']['events']
    return summary

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--frozen',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();frozen=load(a.frozen);assert frozen['passed']
    assert sha(Path(__file__).with_name('protocol.md'))==frozen['method_sha256']
    for path,digest in frozen['source_sha256'].items():assert sha(ROOT/path)==digest
    assert sha(ROOT/'experiments/sequential_externality/analyze.py')==frozen['ancestor_inspector_sha256']
    assert sha(Path(frozen['backend']['path']))==frozen['backend']['sha256']
    for path,digest in frozen['loaded_ns3_libraries'].items():assert sha(Path(path))==digest
    assert files(Path(frozen['prepared']))==frozen['prepared_sha256']
    for case in frozen['inherited_cases'].values():assert files(Path(case['output']))==case['sha256']
    calibration=frozen['calibration'];assert sha(Path(calibration['source']))==calibration['source_sha256']
    rows=calibration['messages'].values();maximum=max(m['ack_ns']-m['post_ns'] for m in rows)
    assert maximum==calibration['normal_max_post_ack_ns'] and (maximum+999)//1000*1000==calibration['source_ack_timeout_ns']
    assert min(m['post_ns'] for m in rows)+calibration['source_ack_timeout_ns']==calibration['fixed_early_deadline_ns']
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);cases={};effects={};attribution={}
    for condition in ('normal','local'):
        name='control-'+condition;baseline,control=audit.inspect(Path(frozen['inherited_cases'][name]['output']),out,name)
        cases[name]=augment(baseline,control)
        for policy in ('fixed','guarded'):
            name=policy+'-'+condition;summary,detail=audit.inspect(Path(frozen['new_cases'][name]['output']),out,name)
            cases[name]=augment(summary,detail)
            effects[name],hops,messages=compare(control,detail,baseline,summary)
            audit.write_csv(out/(name+'-healthy-matched.csv'),hops)
            audit.write_csv(out/(name+'-healthy-messages.csv'),messages)
            audit.write_csv(out/(name+'-healthy-gates.csv'),effects[name]['healthy_phase_changes'])
            del detail;gc.collect()
        fixed,guarded=(cases[policy+'-'+condition] for policy in ('fixed','guarded'))
        fa,ga=(s['calls']['target']['attempts']['replica'] for s in (fixed,guarded))
        attribution[condition]=dict(fixed_start_ns=fa['submitted_ns'],guarded_start_ns=ga['submitted_ns'],
            same_start=fa['submitted_ns']==ga['submitted_ns'],same_target_result=fixed['result_ready_ns']['target']==guarded['result_ready_ns']['target'],
            same_healthy_result=fixed['result_ready_ns']['healthy']==guarded['result_ready_ns']['healthy'],
            same_source_payload=fixed['source_payload_bytes']==guarded['source_payload_bytes'],
            guarded_minus_fixed_effective_bytes=guarded['unique_payload_bytes']-fixed['unique_payload_bytes'],
            guarded_minus_fixed_compute_ns=guarded['declared_compute_work_ns']-fixed['declared_compute_work_ns'])
        del control;gc.collect()
    for case in frozen['inherited_cases'].values():assert files(Path(case['output']))==case['sha256']
    result=dict(passed=True,cases=cases,effects=effects,attribution=attribution,calibration=calibration,
        analyzer_sha256=sha(Path(__file__)),new_network_executions=4,inherited_networks_read_only=2,network_reruns=0,
        new_algorithm_benefit_proven=False,overall_goal_complete=False,
        scope='Finite two-layer two-Decode true expert choices; declared compute/layout and preexisting equivalent model/KV. One normal and known local fault, no holdout/p99/full671B/production throughput/hardware calibration.')
    save(out/'analysis.json',result)
    metrics=[dict(case=name,target_ns=s['result_ready_ns']['target'],healthy_ns=s['result_ready_ns']['healthy'],
        drain_ns=s['network_drained_ns'],unique_payload_bytes=s['unique_payload_bytes'],source_payload_bytes=s['source_payload_bytes'],
        retransmitted_payload_bytes=s['source_retransmitted_payload_bytes'],declared_compute_ns=s['declared_compute_work_ns'],
        backup_start_ns=s['calls']['target']['attempts']['replica']['submitted_ns'],target_winner=s['call_winners']['target']) for name,s in cases.items()]
    audit.write_csv(out/'summary.csv',metrics)
    print(json.dumps(dict(passed=True,cases=metrics,attribution=attribution)))

if __name__=='__main__':main()
