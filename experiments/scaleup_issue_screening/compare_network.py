"""Compare one declared directed-service reference without freezing traffic."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import sys

from .analyze import distribution
from .workflow import digest, host, read, save


def packet_rows(path):
    with gzip.open(path,'rt') as stream:
        yield from (json.loads(line) for line in stream)


def packet_key(row):
    return (row['candidate_id'],row['transfer_id'],tuple(sorted(
        (r['message_category'],*r['task_byte_range']) for r in row['classified_ranges'])))


def compare(baseline, treatment, baseline_packets, treatment_packets, ports, case, output):
    a,b=read(baseline/'summary.json'),read(treatment/'summary.json')
    for gates,packets in ((baseline,baseline_packets),(treatment,treatment_packets)):
        seal=read(packets/'summary.json')
        if seal['facts_sha256']!=digest(gates/'summary.json') or seal['packets_sha256']!=digest(packets/'packets.jsonl.gz'):
            raise ValueError('Packet comparison inputs differ from sealed business facts')
    fields=('plan_files','planned_transfers','computation_costs','executed_node_counts',
            'fixed_actual_objects','fixed_attributes','random','backend_sha256')
    for k in fields:
        if a[k]!=b[k]:raise ValueError('Service comparison changed fixed '+k)
    control=read(case/'selection.json')
    if control['baseline_review_sha256']!=digest(baseline/'summary.json'):
        raise ValueError('Treatment was not prepared against this baseline review')
    intervals=b['live_service']['intervals']
    if len(intervals)!=1 or {k:intervals[0][k] for k in control['intervention']['intervals'][0]}!=control['intervention']['intervals'][0]:
        raise ValueError('Actual directed service differs from declaration')
    observed=[]
    for folder in (case/'run/raw/execution/ports').iterdir():
        observed.append((f'run/raw/execution/ports/{folder.name}/observation-audit.json',read(folder/'observation-audit.json')))
    old=read(ports);new=dict(observed)
    if set(old)!=set(new):raise ValueError('Port fingerprint coverage changed')
    network=dict(ports=len(old),changed_wire_or_time_ports=sum(old[k]!=new[k] for k in old),
        same_seed_does_not_imply_same_paths=True,
        totals={label:{field:sum(v[field] for v in values.values()) for field in
            ('all_successful_start_bytes','all_successful_start_packets')} for label,values in [('baseline',old),('treatment',new)]})
    with gzip.open(baseline/'selected-facts.json.gz','rt') as f:af=json.load(f)
    with gzip.open(treatment/'selected-facts.json.gz','rt') as f:bf=json.load(f)
    pa=[r for r in packet_rows(baseline_packets/'packets.jsonl.gz') if r.get('operation_id')=='D.w0.L7.dispatch']
    pb=[r for r in packet_rows(treatment_packets/'packets.jsonl.gz') if r.get('operation_id')=='D.w0.L7.dispatch']
    ib={packet_key(r):r for r in pb}
    if len(ib)!=len(pb):raise ValueError('Repeated business byte packet identity')
    changes=[];unmatched=[]
    for r in pa:
        key=packet_key(r)
        if key not in ib:
            unmatched.append(dict(candidate_id=r['candidate_id'],transfer_id=r['transfer_id'],classified_ranges=r['classified_ranges']))
            continue
        t=ib[key]
        changes.append(dict(candidate_id=r['candidate_id'],transfer_id=r['transfer_id'],
            byte_identity=key[2],before={k:r.get(k) for k in ('arrival_to_fifo_ns','fifo_wait_ns','tx_start_ns','destination_arrival_ns','upstream')},
            after={k:t.get(k) for k in ('arrival_to_fifo_ns','fifo_wait_ns','tx_start_ns','destination_arrival_ns','upstream')}))
    hops=[r for r in changes if r['before']['upstream'] is not None and r['after']['upstream'] is not None]
    network['matched_recorded_previous_hops']=len(hops)
    network['changed_recorded_previous_hops']=sum(
        tuple(r['before']['upstream'][k] for k in ('node_id','port_id'))!=
        tuple(r['after']['upstream'][k] for k in ('node_id','port_id')) for r in hops)
    network['full_paths_captured']=False
    network['fingerprint_change_alone_is_not_proof_of_path_change']=True
    tids=sorted({r['transfer_id'] for r in pa});deliveries=[]
    for tid in tids:
        before=af['actual_transfers'][str(tid)];after=bf['actual_transfers'][str(tid)]
        identity=af['planned_transfers'][str(tid)]
        if identity!=bf['planned_transfers'][str(tid)]:raise ValueError('Paired contribution changed')
        deliveries.append(dict(transfer_id=tid,identity={k:identity[k] for k in
            ('operation_id','src_rank','dst_rank','message_id','message_role','chunk_index','payload_bytes')},
            before={k:before[k] for k in ('send_start_ns','data_ready_ns','transaction_done_ns')},
            after={k:after[k] for k in ('send_start_ns','data_ready_ns','transaction_done_ns')},
            ready_change_ns=after['data_ready_ns']-before['data_ready_ns'],
            duration_change_ns=(after['data_ready_ns']-after['send_start_ns'])-(before['data_ready_ns']-before['send_start_ns'])))
    oa={(o['operation_id'],o['rank_id']):o for o in a['operations']};ob={(o['operation_id'],o['rank_id']):o for o in b['operations']}
    operations=[]
    for key in sorted(set(oa)&set(ob)):
        x,y=oa[key],ob[key]
        operations.append(dict(operation_id=key[0],rank_id=key[1],
            before_finish_ns=x['completion']['finish']['end_ns'],after_finish_ns=y['completion']['finish']['end_ns'],
            change_ns=y['completion']['finish']['end_ns']-x['completion']['finish']['end_ns'],
            before_actual_last=x['is_actual_last'],after_actual_last=y['is_actual_last'],
            before_all_last=x['all_actual_last'],after_all_last=y['all_actual_last'],
            before_latest=x['completion']['latest_completion_dag'],after_latest=y['completion']['latest_completion_dag'],
            before_model_stages=x['model_stages'],after_model_stages=y['model_stages']))
    result=dict(schema_version='issue-network-service-effect/1',passed=True,
        fixed_fields=fields,fixed_values={k:a[k] for k in fields if k not in ('fixed_attributes','plan_files')},
        source_hashes={str(p):digest(p) for p in (baseline/'summary.json',treatment/'summary.json',
            baseline_packets/'summary.json',treatment_packets/'summary.json',ports,case/'selection.json')},
        actual_service=b['live_service'],network=network,
        last_model_compute_ns=dict(before=a['last_model_compute_ns'],after=b['last_model_compute_ns'],
            change=b['last_model_compute_ns']-a['last_model_compute_ns']),
        network_drain=dict(before=a['network_drain'],after=b['network_drain']),
        end_ns=dict(before=a['simulation_end_ns'],after=b['simulation_end_ns']),
        delivery_changes=deliveries,delivery_change_distribution=distribution([d['ready_change_ns'] for d in deliveries]),
        same_outlet_packet_changes=changes,unmatched_baseline_packets=unmatched,
        packet_pair_coverage=dict(baseline=len(pa),treatment=len(pb),matched=len(changes),
            missing_from_treatment_selected_outlets=len(unmatched),missing_is_not_zero_wait=True),
        operations=operations,program_sha256=digest(__file__),
        interpretation=['Directed service total effect includes serialization, actual route changes and future injection feedback.',
            'Packet comparisons are by declared business byte range, not unstable packet UIDs; absent selected-outlet matches are not assigned zero delay.',
            'All paired contributions are retained, including regressions and messages unrelated to the final gate.',
            'This is the 256-request normal reference; it cannot quantify the 1024-request discovery case.'])
    save(output,result)
    print(json.dumps(dict(last_model_compute_ns=result['last_model_compute_ns'],packet_pair_coverage=result['packet_pair_coverage'])))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('baseline','treatment','baseline-packets','treatment-packets','ports','case','output'):
        p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:compare(a.baseline,a.treatment,a.baseline_packets,a.treatment_packets,a.ports,a.case,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],
            str(a.output.resolve())+'-resources',**host(extra_roots=[a.output.parent]))
        if r['status']!='completed':raise SystemExit('Network comparison failed: '+r['status'])


if __name__=='__main__':main()
