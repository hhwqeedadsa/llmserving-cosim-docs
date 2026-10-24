"""Compact results, including healthy regression and changed tied frontier."""
import argparse
from pathlib import Path
from experiments.nic_wakeup_effect.stage import load,save


def summarize(a, eligibility):
    rows={}
    for name,c in a['cases'].items():
        rows[name]=dict(application_completion_ns=c['group_completion_ns'],physical_finish_ns=c['physical_finish_ns'],
            source_payload_bytes=c['tx_payload_bytes'],unique_payload_bytes=c['unique_payload_bytes'],
            repeated_payload_bytes=c['tx_payload_bytes']-c['unique_payload_bytes'],timeouts=c['timeouts'],
            final_declared_frontier=[dict(work=w['work'],event=w['event'],name=w['name']) for w in c['windows'][-1]['frontier']['writes']])
    pairs={}
    for v in ('old','fixed'):
        pairs[v]=dict(instant_minus_delayed_ns=rows[v+'-live-instant']['application_completion_ns']-rows[v+'-live-delayed']['application_completion_ns'],
            delayed_fault_increment_ns=rows[v+'-live-delayed']['application_completion_ns']-rows[v+'-healthy']['application_completion_ns'],
            instant_fault_increment_ns=rows[v+'-live-instant']['application_completion_ns']-rows[v+'-healthy']['application_completion_ns'])
    trains={name:{k:r[k] for k in ('work','stream','ready_ns','first_tx_ns','last_tx_ns','accepted_ns','acked_ns','packet_count','bytes',
        'source_train_span_ns','adjusted_eligibility_intervals_ns','after_eligibility_ns','physical_service_after_eligibility_ns','idle_after_eligibility_ns')}
        for name,r in a['work99_source_trains'].items()}
    for name,row in trains.items():
        row['first_declared_rate_bps']=a['work99_source_trains'][name]['first_rate_sample']['rate_bps']
        row['piecewise_eligibility']={k:eligibility['cases'][name][k] for k in ('eligible_ns','busy_while_eligible_ns','idle_while_eligible_ns','retrospective_overcount_ns')}
    contrasts=[]
    for p in a['contrasts']:
        contrasts.append(dict(before=p['before'],after=p['after'],application_delta_ns=p['group_delta_ns'],payload_delta_bytes=p['extra_source_payload_bytes'],
            selected_writes=[{k:w[k] for k in ('work','name','ready_delta_ns','receive_delta_ns','ack_delta_ns','witness_send_after_ready_delta_ns',
                'witness_queue_delta_ns','witness_transit_delta_ns','final_declared_frontier_events')} for w in p['writes'] if w['work'] in (24,72,94,99,108,123,132)]))
    return dict(schema_version='nic-wakeup-effect-summary/1',new_networks=3,immutable_old_results=3,cases=rows,within_version_contrasts=pairs,
        work99_source_trains=trains,paired_interventions=contrasts,scope=a['scope'],
        limitations='one fixed legacy sample; local correctness repair is not an application acceleration, hardware result or paper novelty')


def main():
    p=argparse.ArgumentParser()
    for k in ('analysis','eligibility','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();save(a.output,summarize(load(a.analysis),load(a.eligibility)))


if __name__=='__main__':main()
