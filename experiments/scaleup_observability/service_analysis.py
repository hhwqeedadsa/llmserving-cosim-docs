"""Researcher evidence for fixed egress service interventions; no causal labels."""
from copy import deepcopy
from pathlib import Path
import sys

from moe_ub_cosim.record_storage import iter_port_records

from .common import digest, read, require, save, sources
from .exploration_analysis import tempo_graph_signature, pair_metrics
from .observation_prepare import overlap, rank_truth
from .overview import table
from .service_exploration import PORTS, WINDOW, NOMINAL_RATE, NORMAL_ROUND_NS, selected_ports
from .slot_analysis import analyze_case, csv_rows
from .statistics import distribution
from .workflow import verify_complete


def serialization_audit(tx, intervals, nominal):
    changed = 0
    for row in tx:
        rate = next((r['rate_bps'] for r in intervals if r['start_ns'] <= row['time_ns'] < r['end_ns']),nominal)
        expected = (row['packet_bytes']*8*10**9*2 + rate)//(2*rate)
        require(row['busy_until_ns']-row['time_ns']==expected, 'Physical serialization differs from declared rate')
        changed += rate != nominal
    return changed


def port_hits(run, intervals):
    ex = run/'raw/execution'
    rows = []
    for role,(node,port) in PORTS.items():
        folder = ex/'ports'/f'{node}-{port}'
        tx = [r for r in iter_port_records(ex,folder,'egress-queue-audit.jsonl') if r['event']=='tx_start']
        packets = list(iter_port_records(ex,folder,'packet-events.jsonl'))
        selected = [r for r in intervals if (r['node_id'],r['port_id'])==(node,port)]
        count = serialization_audit(tx,selected,NOMINAL_RATE)
        within = [r for r in packets if WINDOW[0]<=r['sim_time_ns']<WINDOW[1]]
        rows.append(dict(role=role,node_id=node,port_id=port,window_ns=list(WINDOW),
            window_started_packets=len(within),window_started_business_packets=sum(r['business_payload_bytes']>0 for r in within),
            window_started_business_bytes=sum(r['business_payload_bytes'] for r in within),
            window_started_packet_bytes=sum(r['counted_packet_bytes'] for r in within),
            packets_at_reduced_rate=count,physical_serialization_checked_packets=len(tx),
            window_started_serialization_ns=sum(r['busy_until_ns']-r['time_ns'] for r in tx if WINDOW[0]<=r['time_ns']<WINDOW[1])))
    return rows


def compare(batch, output):
    batch,output = Path(batch).resolve(),Path(output).resolve()
    meta = read(batch/'batch.json')
    require(meta['schema_version']=='scaleup-exploration-batch/1' and meta['study']=='port-service','Need service batch')
    cases = meta['cases']
    require(len(cases)==7 and {(c['service_role'],c['rate_percent']) for c in cases}==
        {('normal',100)}|{(role,pct) for role in PORTS for pct in (80,50)},'Need declared seven cases')
    rows=[]; graphs=[]; networks=[]; platforms=[]; backends=set(); overviews={}
    for case in cases:
        require(case['input_set']==4 and case['requests_per_rank']==8 and case['request_count']==64 and
                case['send_slots_per_rank']==2,'Workload changed')
        done,summary = verify_complete(batch,case)
        root = batch/'runs'/case['case_id']
        net = read(root/'run/inputs/network/backend.json')
        require(selected_ports(net['topology'])=={k:(*p,NOMINAL_RATE) for k,p in PORTS.items()},'Topology rule mismatch')
        role,pct = case['service_role'],case['rate_percent']
        expected = [] if role=='normal' else [dict(node_id=PORTS[role][0],port_id=PORTS[role][1],
            start_ns=WINDOW[0],end_ns=WINDOW[1],rate_bps=NOMINAL_RATE*pct//100)]
        require(net['port_service']==dict(schema_version='moe-ub-port-service/1',execution_end_ns=1000000,intervals=expected)
                and case['port_service']==expected,'Service differs from frozen rule')
        semantic={k:deepcopy(net[k]) for k in ('attributes','globals','profiles','topology')}
        networks.append(semantic)
        platforms.append(read(root/'run/manifest.json')['software']['source_hashes']);backends.add(done['backend_sha256'])
        stats,nodes,_,plan = analyze_case(root,2,output/case['case_id'])
        graphs.append(tempo_graph_signature(plan,1,1))
        require(stats['compute_work_rank_ns']==314756,'Compute work changed')
        truth=rank_truth(nodes,2,summary['round_ns'])
        pending=sum(overlap(a,0,summary['round_ns']) for a,_ in truth.values())
        full=sum(overlap(b,0,summary['round_ns']) for _,b in truth.values())
        stats.update(pending_wait_rank_ns=pending,full_slot_wait_rank_ns=full,other_pending_wait_rank_ns=pending-full)
        stats.pop('longest_wait')
        row=dict(case=case,completion=done,stats=stats,transfer_counts=summary['transfers'],
                 actual_native_sizes=summary['raw_native_sizes'],service_hits=port_hits(root/'run',expected))
        rows.append(row);overviews[case['case_id']]=root/'overview'
    require(len(backends)==1 and all(p==platforms[0] for p in platforms),'Platform changed')
    require(all(g==graphs[0] for g in graphs) and all(n==networks[0] for n in networks),'Undeclared graph/network changes')
    baseline = next(r for r in rows if r['case']['service_role']=='normal')
    require(baseline['stats']['round_ns']==NORMAL_ROUND_NS,'Fresh normal differs from declared time reference')
    base=overviews[baseline['case']['case_id']]
    transfers={r['transfer_id']:r for r in csv_rows(base/'transfers.csv',('submit_ns','data_ready_ns','protocol_complete_ns','task_latency_ns'))}
    ports={(r['switch_id'],r['port_id']):r for r in csv_rows(base/'ports.csv',('voq_byte_ns','fifo_byte_ns'))}
    layers={(r['rank_id'],r['layer_id']):r for r in csv_rows(base/'layers.csv',('end_ns',))}
    pairs=[]
    for row in rows:
        for key in ('fabric_tasks','local_deliveries','unique_fabric_business_bytes','tasks_with_multiple_committed_segments'):
            require(row['transfer_counts'][key]==baseline['transfer_counts'][key],'Communication work changed')
        require(row['actual_native_sizes']==baseline['actual_native_sizes'],'Native sizes changed')
        if row is baseline:continue
        name=row['case']['case_id'];root=overviews[name];target=output/name
        delta=[]
        for r in csv_rows(root/'transfers.csv',('submit_ns','data_ready_ns','protocol_complete_ns','task_latency_ns')):
            old=transfers[r['transfer_id']]
            delta.append(dict(transfer_id=r['transfer_id'],operation=r['operation'],message_role=r['message_role'],
                payload_bytes=r['payload_bytes'],locality=r['locality'],
                **{k+'_delta':r[k]-old[k] for k in ('submit_ns','data_ready_ns','protocol_complete_ns','task_latency_ns')}))
        table(target/'transfer-deltas.csv',delta)
        pd=[]
        for r in csv_rows(root/'ports.csv',('voq_byte_ns','fifo_byte_ns')):
            old=ports[r['switch_id'],r['port_id']]
            pd.append(dict(switch_id=r['switch_id'],port_id=r['port_id'],
                queue_area_delta_byte_ns=sum(r[k]-old[k] for k in ('voq_byte_ns','fifo_byte_ns'))))
        table(target/'port-deltas.csv',pd)
        table(target/'layer-deltas.csv',[dict(rank_id=r['rank_id'],layer_id=r['layer_id'],
            end_delta_ns=r['end_ns']-layers[r['rank_id'],r['layer_id']]['end_ns'])
            for r in csv_rows(root/'layers.csv',('end_ns',))])
        pairs.append(dict(service_role=row['case']['service_role'],rate_percent=row['case']['rate_percent'],
            **pair_metrics(baseline,row),task_latency_delta_ns=distribution([d['task_latency_ns_delta'] for d in delta]),
            tasks_later_protocol_complete=sum(d['protocol_complete_ns_delta']>0 for d in delta),
            tasks_longer_latency=sum(d['task_latency_ns_delta']>0 for d in delta),
            ports_increased_queue=sum(r['queue_area_delta_byte_ns']>0 for r in pd),
            tables={p.name:digest(p) for p in target.glob('*-deltas.csv')}))
    save(output/'comparison.json',dict(schema_version='scaleup-service-comparison/1',study='port-service',
        access='researcher_internal',batch_sha256=digest(batch/'batch.json'),method_sha256=meta['method_sha256'],
        implementation=sources(),backend_sha256=next(iter(backends)),cases=rows,service_pairs=pairs,
        checks=dict(same_graph_and_work=True,same_platform_and_backend=True,declared_service_only=True,
                    physical_packet_serialization=True,normal_reference_reproduced=True,actual_submit_release_occupancy=True),
        qualification='Seen synthetic input; local physical service intervention, not a credit-backpressure label; no hardware calibration'))


if __name__=='__main__': compare(*sys.argv[1:])
