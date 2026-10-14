"""Independent evaluation against private continuous episodes and witnesses."""
import argparse
from collections import Counter,defaultdict
from pathlib import Path
import sys
from .diagnostic_judge import rows
from .workflow import digest,host,read,save


def union(intervals):
    result=[]
    for a,b in sorted(intervals):
        if b<=a:continue
        if result and a<=result[-1][1]:result[-1]=(result[-1][0],max(b,result[-1][1]))
        else:result.append((a,b))
    return result


def overlap(a,b):return a[0]<b[1] and b[0]<a[1]


def score(events,predictions,credit,port_ns):
    truth=defaultdict(list);pred=defaultdict(list)
    for e in events:truth[e['node_id'],e['port_id']].append(e)
    for p in predictions:pred[p['node_id'],p['port_id']].append(p)
    detected=missed=0;latency=[];correct_service=wrong_service=0;unknown=0
    candidate_time=false_time=0;groups=false_groups=0
    for key in set(truth)|set(pred):
        intervals=union((e['start_ns'],e['end_ns']) for e in truth[key])
        for e in truth[key]:
            hits=[p for p in pred[key] if overlap((e['start_ns'],e['end_ns']),(p['start_ns'],p['end_ns']))]
            if hits:
                detected+=1;latency.append(max(0,min(p.get('reported_at_ns',p['end_ns']) for p in hits)-e['start_ns']))
            else:missed+=1
        for a,b in union((p['start_ns'],p['end_ns']) for p in pred[key]):
            groups+=1;length=b-a;candidate_time+=length
            match=sum(max(0,min(b,y)-max(a,x)) for x,y in intervals)
            false_time+=length-match
            if not match:false_groups+=1
        for p in pred[key]:
            matched=[e for e in truth[key] if overlap((e['start_ns'],e['end_ns']),(p['start_ns'],p['end_ns']))]
            if 'local_service_deficit' in p['labels']:
                if any(e['slow_service_overlap'] for e in matched):correct_service+=1
                else:wrong_service+=1
            if any('unknown' in label for label in p['labels']):unknown+=1
    credit_hits=0
    for c in credit:
        hits=[p for p in pred[c['node_id'],c['port_id']] if 'credit_gate_witness_root_unknown' in p['labels'] and
            any(p['start_ns']<=t<p['end_ns'] for t in c['failed_checks_ns'])]
        credit_hits+=bool(hits)
    latency.sort()
    return dict(true_queue_events=len(events),detected_events=detected,missed_events=missed,
        detection_fraction=detected/len(events) if events else None,
        candidate_interval_groups=groups,false_groups_without_qualifying_queue=false_groups,
        candidate_port_ns=candidate_time,false_candidate_port_ns=false_time,coverage_port_ns=port_ns,
        false_candidate_fraction_of_port_time=false_time/port_ns,
        delay_to_first_report_ns=dict(median=latency[len(latency)//2],max=max(latency)) if latency else None,
        service_label_report_checks=dict(correct_overlap=correct_service,unsupported_by_qualifying_truth=wrong_service),
        credit_witness_events=len(credit),credit_witness_detected=credit_hits,
        credit_recall=credit_hits/len(credit) if credit else None,
        root_location_claims=0,credit_root_unresolved=len(credit),
        unknown_reports=unknown,reports=len(predictions),unknown_report_fraction=unknown/len(predictions) if predictions else None,
        direct_candidate_rank_width=dict(max=max((len(p['possible_ranks']) for p in predictions),default=0),
            mean=sum(len(p['possible_ranks']) for p in predictions)/len(predictions) if predictions else None),
        scope='Events are independent of window grouping. Interval candidates are conservative; true short queues below the registered threshold are counted outside the primary truth set.')


def evaluate(prepared,predictions,output):
    prepared,predictions,output=map(Path,(prepared,predictions,output))
    if output.exists():raise ValueError('Evaluation output must be new')
    source=read(prepared/'summary.json');pred=read(predictions/'summary.json')
    if pred['input_sha256']!=source['input_sha256']:raise ValueError('Diagnosis is not bound to prepared inputs')
    for name,sha in source['private_sha256'].items():
        if digest(prepared/'private'/name)!=sha:raise ValueError('Private evidence changed')
    meta=read(prepared/'input/schema.json');keys={(p['switch_id'],p['port_id']) for p in meta['ports']}
    truth=read(prepared/'private/truth.json')
    events=[e for e in truth['episodes'] if e['qualifies'] and (e['node_id'],e['port_id']) in keys]
    credit=[e for e in truth['credit_checks'] if (e['node_id'],e['port_id']) in keys]
    pstats=read(prepared/'private/ports.json');updates=Counter()
    for p in pstats:
        if (p['node_id'],p['port_id']) in keys:updates.update(p['events'])
    results={}
    for mode,spec in pred['modes'].items():
        records=list(rows(predictions/(mode+'.jsonl.gz')))
        value=score(events,records,credit,pred['coverage_port_ns'])
        cost=dict(pred['costs'][mode]);level=spec['fieldset'];kind=spec['kind']
        cost['continuous_tx_counter_updates']=updates['tx']
        cost['continuous_queue_counter_updates']=updates['voq']+updates['fifo'] if level!='counters' else 0
        cost['continuous_arrival_counter_updates']=updates['fifo_enqueue'] if level!='counters' else 0
        cost['continuous_serial_work_updates']=updates['tx'] if level in ('service','credit') else 0
        cost['completion_timing_state_bound_bytes']=sum(p['max_pending_serializations']*12 for p in pstats
            if (p['node_id'],p['port_id']) in keys) if level in ('service','credit') else 0
        cost['continuous_credit_decision_updates']=updates['credit_decision'] if level=='credit' else 0
        if kind=='histogram':cost['required_existing_occupancy_state_updates']=updates['voq']+updates['fifo']
        cost['report_payload_plus_histogram_bytes']=cost.get('report_bytes',0)+cost.get('histogram_bytes',0)
        value['cost']=cost;results[mode]=value
    output.mkdir(parents=True)
    save(output/'summary.json',dict(schema_version='issue-diagnostic-evaluation/1',source_preparation_sha256=digest(prepared/'summary.json'),
        predictions_sha256=digest(predictions/'summary.json'),rule_sha256=pred['rule_sha256'],
        source_manifest_sha256=source['source_manifest_sha256'],coverage_port_ns=pred['coverage_port_ns'],
        positive_duration_queue_episodes=len(truth['episodes']),qualifying_public_queue_events=len(events),
        qualifying_with_slow_service=sum(e['slow_service_overlap'] for e in events),
        qualifying_with_credit_witness=sum(e['failed_credit_witness'] for e in events),credit_witnesses=len(credit),
        modes=results,source_update_events=dict(updates),program_sha256=digest(__file__),
        denominator_limits='Same finite related execution; not production frequency or independent windows. No credit events means recall is undefined, not perfect. Root propagation accuracy is unmeasured without root claims/adequate witnesses.'))
    print('Evaluated',len(events),'queue events,',len(credit),'credit witnesses')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('prepared','predictions','output'):p.add_argument('--'+n,required=True,type=Path)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:evaluate(a.prepared,a.predictions,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],str(a.output)+'-resources',**host())
        if r['status']!='completed':raise SystemExit(r['status'])

if __name__=='__main__':main()
