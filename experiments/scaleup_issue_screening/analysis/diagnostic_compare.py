"""Bind validated mechanism, business and diagnostic summaries for comparison.

This is private evaluation, never an input generator for diagnosis. Resource
totals distinguish exported bytes, local reads, update events and state bounds.
"""
import argparse
from collections import Counter,defaultdict
import csv
import json
from pathlib import Path
import sys
from .workflow import CONFIG,digest,host,read,save
from .diagnostic_judge import rows,FIELDSETS
from .diagnostic_schema import BITS


def payload_cost(cost,end_ns):
    """Histograms are exported at the end; include their simultaneous burst."""
    common=sum(cost[k] for k in ('common_endpoint_payload_bytes','common_stage_payload_bytes','common_workload_payload_bytes'))
    total=cost['report_bytes']+cost['histogram_bytes']
    return dict(network_payload_bytes=total,common_software_payload_bytes=common,
        combined_payload_bytes=total+common,mean_network_bytes_per_second=total*1e9/end_ns,
        peak_network_bytes_per_1us=cost['peak_report_bytes_per_1us']+cost['histogram_bytes'],
        accounted_measurement_state_bytes=cost['counter_bank_bytes']+cost['histogram_state_bytes']+
            cost['ring_bytes']+cost.get('completion_timing_state_bound_bytes',0)+cost.get('rng_state_bytes',0)+
            cost.get('event_deduplication_state_bytes',0)+cost.get('sampling_control_state_bytes',0),
        state_limit='Counter widths, histogram bins, record ring, completion timing, actual RNG state and bounded collection control. Queue occupancy, last-update timestamps, scheduling/transport and snapshot storage are additional; no ASIC area claim.')


def business_delta(value,baseline):
    old={o['operation_id']:o for o in baseline['operations']}
    endpoint_changes=[]
    for op in value['operations']:
        prior={r['rank_id']:r for r in old[op['operation_id']].get('endpoints',[])}
        delta={r['rank_id']:r['end_local_ns']-prior[r['rank_id']]['end_local_ns'] for r in op.get('endpoints',[])}
        endpoint_changes.append(dict(operation_id=op['operation_id'],
            later_ranks=[r for r,d in delta.items() if d>0],earlier_ranks=[r for r,d in delta.items() if d<0],
            unchanged_ranks=sum(d==0 for d in delta.values()),end_deltas_ns=delta))
    return dict(simulation_delta_ns=value['simulation_end_ns']-baseline['simulation_end_ns'],
        final_operator_delta_ns=max(o['end_ns'] for o in value['operations'])-max(o['end_ns'] for o in old.values()),
        last_model_compute_delta_ns=value['last_model_compute_ns']-baseline['last_model_compute_ns'],
        endpoint_changes=endpoint_changes,endpoint_scope='Observed counterfactual end changes, not individually additive queue-caused loss or only direct receivers',
        operations=[dict(operation_id=o['operation_id'],end_ns=o['end_ns'],
            end_delta_ns=o['end_ns']-old[o['operation_id']]['end_ns'],last_ranks=o['last_ranks'],
            baseline_last_ranks=old[o['operation_id']]['last_ranks']) for o in value['operations']])


def filtered_cost(predictions,ports,end_ns,width):
    """Local predicate filtering preserves every diagnosis, not raw telemetry.

    A 20-byte per-port end marker declares complete coverage, report count and
    overflow. Acquisition/update costs remain those of the underlying mode.
    This engineering reference requires local predicate execution and reliable
    export; it does not assert existing product support.
    """
    peaks=Counter();count=0
    for r in predictions:
        count+=1;peaks[(r.get('reported_at_ns',r['end_ns'])-1)//1000]+=width
    peaks[(end_ns-1)//1000]+=ports*20
    return dict(candidate_records=count,coverage_marker_bytes=ports*20,
        network_payload_bytes=count*width+ports*20,
        peak_network_bytes_per_1us=max(peaks.values(),default=0),
        same_diagnostic_outputs=True,additional_ring_bytes=0,
        assumptions='Local same-rule predicate, loss-checked coverage marker. Same continuously updated counters and original snapshot schedule; no raw-counter or progress-CDF preservation claim.')


def manifest_identity(root,name):
    live=root/'target'/name/'run/manifest.json'
    if live.exists():return digest(live)
    archive=root/'archives'/('target-'+name+'.tar.gz');index=read(str(archive)+'.json')
    if not index['verified'] or not index['expanded_released'] or digest(archive)!=index['archive_sha256']:
        raise ValueError('Archived target is not a verified retained source')
    return index['entries']['run/manifest.json']['sha256']


def filtered_histogram_cost(candidate_ports,ports):
    # Preserve every port's 24-byte final counters; only selected histogram
    # exports (33 uint32 bins plus 4-byte resource ID) may be omitted.
    total=ports*(24+20)+candidate_ports*136
    return dict(candidate_records=candidate_ports,coverage_marker_bytes=ports*20,
        network_payload_bytes=total,peak_network_bytes_per_1us=total,
        same_diagnostic_outputs=True,additional_ring_bytes=0,predicate_bin_reads=ports*33,
        assumptions='Same fixed-phase histogram and final counters. Local end-of-capture threshold filter, reliable coverage markers; full noncandidate histograms are not preserved. Continuous histogram reads/updates unchanged.')


def service_event_score(events,predictions):
    by_port=defaultdict(list)
    for p in predictions:by_port[p['node_id'],p['port_id']].append(p)
    detected=explained=0
    for e in events:
        hits=[p for p in by_port[e['node_id'],e['port_id']] if p['start_ns']<e['end_ns'] and p['end_ns']>e['start_ns']]
        detected+=bool(hits);explained+=any('local_service_deficit' in p['labels'] for p in hits)
    return dict(slow_service_queue_events=len(events),detected_queue_events=detected,
        explained_as_service_events=explained,service_explanation_fraction=explained/len(events) if events else None,
        limits='Any real slow serialization overlapping a qualifying continuous queue episode; mixed episodes may also contain nominal service. Not a report-count accuracy or injection-label test.')


def aggregate_review(review):
    """Publish only aggregate mechanism evidence, never packet/source identities."""
    common={k:review[k] for k in ('case_id','problem','fixed_selection')}
    if 'sides' in review:
        common['sides']=[{k:s[k] for k in ('outlet','all_packet_transmit',
            'whole_business_arrival','whole_business_transmit','per_operation','business_wait_ns')}
            for s in review['sides']]
    else:
        common.update({k:review[k] for k in ('selected_packets','whole_outlet_packets',
            'actual_all_tx_packets','selected_business_packets','interval_wait_metrics',
            'whole_wait_metrics','selected_missing_wire_arrival','selected_business_missing_wire_arrival')})
    return common


def summaries(comparison,comparison_path,historical_path,directory):
    """Write reproducible aggregate JSON/CSV without restricted routing records."""
    directory=Path(directory)
    if directory.exists():raise ValueError('Summary directory must be new')
    historical=read(historical_path)
    if historical['rule_sha256']!=comparison['rule_sha256']:raise ValueError('Historical rule mismatch')
    binding=dict(comparison=dict(path=str(comparison_path),sha256=digest(comparison_path)),
        rule_sha256=comparison['rule_sha256'],study_sha256=comparison['study_sha256'],
        sources=comparison['sources'],summary_program_sha256=digest(__file__))
    mechanisms={p:{} for p in ('local_service','backpressure','multipath')};measurement={}
    for name,c in comparison['cases'].items():
        common={k:c[k] for k in ('condition','manifest_sha256','baseline','capture_end_ns',
            'capture_complete','dropped_fact_records','normal_equivalence',
            'captured_events','covered_public_ports','captured_ports','complete_gates_reviewed',
            'feedback_nodes','unexplained_start_delay_ns','exact_queue_events','qualifying_queue_events',
            'qualifying_slow_service_events','credit_witnesses','ambiguous_counter_progress_pairs')}
        common['business']={k:v for k,v in c['business'].items() if k!='endpoint_changes'}
        common['business']['endpoint_counts']=[dict(operation_id=x['operation_id'],
            later=len(x['later_ranks']),earlier=len(x['earlier_ranks']),unchanged=x['unchanged_ranks'])
            for x in c['business']['endpoint_changes']]
        common['controlled_terminal_feedback_nodes']=len(c['controlled_terminal_witnesses'])
        controls=[{k:v[k] for k in ('requested','actual_started_packets','actual_rates_bps',
            'actual_started_bytes','busy_union_within_interval_ns','actual_destination_packets',
            'wait_metrics','shared_hop_necessary_transfer_count')} for v in c['actual_controls']]
        for problem,cases in mechanisms.items():
            cases[name]=dict(**common,actual_controls=controls,
                fixed_case_reviews=[aggregate_review(v) for v in c['fixed_case_reviews'] if v['problem']==problem])
        measurement[name]=dict(**common,modes=c['modes'],progress_pair_budget=c['progress_pair_budget'])
    for problem,cases in mechanisms.items():
        save(directory/problem/'study.json',dict(schema_version='issue-mechanism-study-summary/1',
            **binding,problem=problem,cases=cases,limits=comparison['limitations']))
    save(directory/'measurement_budget/comparison.json',dict(schema_version='issue-measurement-summary/1',
        **binding,cases=measurement,historical_review=historical,
        historical_source=dict(path=str(historical_path),sha256=digest(historical_path)),
        limits=comparison['limitations']))
    columns=('case','mode','events','detected','missed','slow_events','service_explained',
        'candidate_port_ns','outside_primary_truth_port_ns','delay_median_ns','delay_max_ns',
        'unknown_reports','reports','raw_bytes','filtered_bytes','common_software_bytes',
        'actual_peak_bytes_per_us','worst_peak_bytes_per_us','filtered_peak_bytes_per_us',
        'state_bytes','snapshot_reads','histogram_reads','overflow_records')
    with (directory/'measurement_budget/metrics.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns,lineterminator='\n');writer.writeheader()
        for name,c in measurement.items():
            for mode,m in c['modes'].items():
                cost=m['cost'];p=m['payload_cost'];f=m['local_filtered_reference'];s=m['service_event_score']
                writer.writerow(dict(case=name,mode=mode,events=m['true_queue_events'],
                    detected=m['detected_events'],missed=m['missed_events'],slow_events=s['slow_service_queue_events'],
                    service_explained=s['explained_as_service_events'],candidate_port_ns=m['candidate_port_ns'],
                    outside_primary_truth_port_ns=m['false_candidate_port_ns'],
                    delay_median_ns=(m['delay_to_first_report_ns'] or {}).get('median'),delay_max_ns=(m['delay_to_first_report_ns'] or {}).get('max'),
                    unknown_reports=m['unknown_reports'],reports=m['reports'],raw_bytes=p['network_payload_bytes'],
                    filtered_bytes=f['network_payload_bytes'],common_software_bytes=p['common_software_payload_bytes'],
                    actual_peak_bytes_per_us=p['peak_network_bytes_per_1us'],
                    worst_peak_bytes_per_us=m['worst_generated_network_bytes_per_1us'],
                    filtered_peak_bytes_per_us=f['peak_network_bytes_per_1us'],state_bytes=p['accounted_measurement_state_bytes'],
                    snapshot_reads=cost['counter_snapshot_reads'],histogram_reads=cost['histogram_reads'],
                    overflow_records=cost['overflow_records']))


def compare(root,output,summary_dir=None):
    root,output=Path(root),Path(output)
    if output.exists():raise ValueError('Comparison output must be new')
    if summary_dir is not None and Path(summary_dir).exists():raise ValueError('Summary directory must be new')
    study=read(CONFIG/'scenarios/diagnostic-study.json');result={};sources={};rule_sha=None
    rule=read(CONFIG/'observation/diagnostic-rule.json');configured_rule_sha=digest(CONFIG/'observation/diagnostic-rule.json')
    for spec in study['primary_cases']:
        name=spec['id'];paths=dict(prepared=root/'prepared-v3'/name/'summary.json',
            predictions=root/'judged-final'/name/'summary.json',evaluation=root/'evaluated-final'/name/'summary.json',
            business=root/'business'/name/'summary.json',deep=root/'deep-v3'/name/'summary.json',
            feedback=root/'feedback'/(name+'.json'))
        docs={k:read(p) for k,p in paths.items()};p,j,e,b,d=[docs[k] for k in ('prepared','predictions','evaluation','business','deep')]
        manifest=manifest_identity(root,name)
        if {p['source_manifest_sha256'],e['source_manifest_sha256'],b['manifest_sha256'],d['source_manifest_sha256']}!={manifest}:
            raise ValueError('Comparison source mismatch')
        if e['source_preparation_sha256']!=digest(paths['prepared']) or e['predictions_sha256']!=digest(paths['predictions']):
            raise ValueError('Evaluation binding mismatch')
        if d['prepared_sha256']!=digest(paths['prepared']) or d['business_sha256']!=digest(paths['business']):
            raise ValueError('Deep binding mismatch')
        feedback=docs['feedback']
        if feedback['manifest_sha256']!=manifest or feedback['business_sha256']!=digest(paths['business']) or feedback['deep_sha256']!=digest(paths['deep']):
            raise ValueError('Feedback binding mismatch')
        rule_sha=rule_sha or e['rule_sha256']
        if e['rule_sha256']!=rule_sha or j['rule_sha256']!=rule_sha or rule_sha!=configured_rule_sha:raise ValueError('Rule changed between cases or configured comparison')
        baseline='r06' if spec['source']=='g0-b2-short' else 'r00'
        base=read(root/'business'/baseline/'summary.json')
        if spec['service'] and b['baseline_sha256']!=digest(root/'business'/baseline/'summary.json'):
            raise ValueError('Control lacks invariant-checked baseline')
        eq_summary=None
        if not spec['service']:
            live=root/'target'/name/'equivalence.json'
            if live.exists():eq=read(live);eq_sha=digest(live)
            else:
                from .consumer_review import Archive
                archived=Archive(root/'archives'/('target-'+name+'.tar.gz'))
                for _,data in archived.members(['equivalence.json']):eq=json.loads(data)
                eq_sha=archived.index['entries']['equivalence.json']['sha256']
            if not eq['passed'] or eq['replay_manifest_sha256']!=manifest:raise ValueError('Normal capture lacks equivalence')
            eq_summary={k:eq[k] for k in ('passed','original_archive_sha256','original_manifest_sha256',
                'replay_manifest_sha256','ports_compared','packet_wire_and_start_fingerprints_match','simulation_end_ns')}
            eq_summary.update(equivalence_sha256=eq_sha,original_record_files=len(eq['record_files']))
        sources[name]={k:dict(path=str(v),sha256=digest(v)) for k,v in paths.items()}
        modes={}
        truth_path=paths['prepared'].parent/'private/truth.json'
        if digest(truth_path)!=p['private_sha256']['truth.json']:raise ValueError('Private event evidence changed')
        public=read(paths['prepared'].parent/'input/schema.json')
        keys={(v['switch_id'],v['port_id']) for v in public['ports']}
        slow=[v for v in read(truth_path)['episodes'] if v['qualifies'] and v['slow_service_overlap'] and (v['node_id'],v['port_id']) in keys]
        for mode,v in e['modes'].items():
            spec_mode=j['modes'][mode];extra={}
            records=list(rows(paths['predictions'].parent/(mode+'.jsonl.gz')))
            width=(96+sum(BITS[f] for f in FIELDSETS[spec_mode['fieldset']]))//8
            maximum_records=rule['event_pre_windows']+1 if spec_mode['kind']=='event' else 1
            extra['worst_generated_network_bytes_per_1us']=j['ports']*(160 if spec_mode['kind']=='histogram' else width*maximum_records)
            if spec_mode['kind']!='histogram':
                extra['local_filtered_reference']=filtered_cost(records,j['ports'],j['end_ns'],width)
                extra['local_filtered_reference']['predicate_evaluations']=v['cost']['records']
                extra['local_filtered_reference']['prediction_sha256']=digest(paths['predictions'].parent/(mode+'.jsonl.gz'))
            else:
                extra['local_filtered_reference']=filtered_histogram_cost(len(records),j['ports'])
                extra['local_filtered_reference']['prediction_sha256']=digest(paths['predictions'].parent/(mode+'.jsonl.gz'))
            extra['local_filtered_reference']['worst_generated_network_bytes_per_1us']=extra['worst_generated_network_bytes_per_1us']+j['ports']*20
            pcost=payload_cost(v['cost'],j['end_ns'])
            if pcost['peak_network_bytes_per_1us']>extra['worst_generated_network_bytes_per_1us']:
                raise ValueError('Observed generation exceeds declared raw burst bound')
            if extra['local_filtered_reference']['peak_network_bytes_per_1us']>extra['local_filtered_reference']['worst_generated_network_bytes_per_1us']:
                raise ValueError('Filtered generation exceeds declared burst bound')
            modes[mode]=dict(**v,payload_cost=pcost,service_event_score=service_event_score(slow,records),**extra)
        result[name]=dict(condition=spec,manifest_sha256=manifest,baseline=baseline,
            capture_end_ns=p['capture']['end_ns'],captured_events=sum(p['events'].values()),
            capture_complete=p['capture']['complete'],dropped_fact_records=p['capture']['dropped_records'],normal_equivalence=eq_summary,
            covered_public_ports=p['public_ports'],captured_ports=p['captured_ports'],
            business=business_delta(b,base),complete_gates_reviewed=len(b['reviewed_gates']),
            feedback_nodes=len(feedback['nodes']),unexplained_start_delay_ns=sum(n['unexplained_ready_to_start_ns'] for n in feedback['nodes']),
            controlled_terminal_witnesses=[n for n in feedback['nodes'] if any(
                (v['node_id'],v['port_id']) in {(c['node_id'],c['port_id']) for c in spec['service']}
                for v in n.get('callback',{}).get('terminal_packet_witnesses',[]))],
            exact_queue_events=e['positive_duration_queue_episodes'],qualifying_queue_events=e['qualifying_public_queue_events'],
            qualifying_slow_service_events=e['qualifying_with_slow_service'],credit_witnesses=e['credit_witnesses'],
            ambiguous_counter_progress_pairs=j['counter_progress_ambiguous_pairs'],
            progress_pair_budget='Separate full 1us counter reference; costs counters-1us, never free auxiliary data for histogram or sampled modes',
            fixed_case_reviews=d['cases'],actual_controls=d['controls'],
            modes=modes)
    report=dict(schema_version='issue-diagnostic-comparison/1',rule_sha256=rule_sha,
        study_sha256=digest(CONFIG/'scenarios/diagnostic-study.json'),cases=result,sources=sources,
        program_sha256=digest(__file__),limitations=[
            'Related finite executions; control runs establish mechanism, not production frequency.',
            'Static candidate endpoint scope is not proven downstream business impact.',
            'No root claims means propagation accuracy unmeasured; no credit positive means recall undefined.',
            'Collection is a causal replay of engineering interfaces, not implemented hardware.'])
    save(output,report)
    if summary_dir is not None:summaries(report,output,root/'historical-final.json',summary_dir)
    print('Compared',len(result),'source-bound cases with one frozen rule')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True,type=Path)
    p.add_argument('--summary-dir',type=Path,help='New directory for aggregate per-problem JSON and complete metric CSV; private packet/source identities are excluded')
    p.add_argument('--output',required=True,type=Path);p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:compare(a.root,a.output,a.summary_dir)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],str(a.output)+'-resources',**host())
        if r['status']!='completed':raise SystemExit(r['status'])


if __name__=='__main__':main()
