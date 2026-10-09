"""Researcher E1 analysis; reads internal identities, never a measurement algorithm.

Endpoint distributions are within one layer and one operation. Network window
overlap is descriptive and is not a causal attribution or an avoidable delay.
"""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import statistics
import sys

from moe_ub_cosim.research_storage import stored_rows
from moe_ub_cosim.record_storage import iter_result
from .workflow import cli, digest, host, read, save


def distribution(values):
    values = sorted(values)
    if not values:
        return dict(n=0)
    return dict(n=len(values), minimum=values[0], median=statistics.median(values),
        mean=statistics.mean(values), p95=values[math.ceil(.95*len(values))-1],
        maximum=values[-1], spread=values[-1]-values[0])


def correlation(left, right):
    if len(left) < 2 or len(set(left)) < 2 or len(set(right)) < 2:
        return None
    return statistics.correlation(left, right)


def compute_index(run):
    """Read one bounded, sealed plan; keep only O(layers*ranks) stage IDs."""
    result = {}
    protocol = [json.loads(line) for line in (run/'raw/protocol.jsonl').read_text().splitlines()]
    for call in protocol:
        if call['type'] != 'COMMIT':
            continue
        for submission in call['submissions']:
            path = (run/'raw'/submission['semantic_manifest']).resolve()
            if not path.is_relative_to((run/'raw').resolve()) or path.stat().st_size > 2*2**30:
                raise ValueError('Plan outside declared analysis scope/size')
            with path.open() as stream:
                plan = json.load(stream)
            for graph in plan['rank_graphs']:
                rank = graph['rank_id']
                for stage in graph.get('model_stages',[]):
                    if stage['node_id'] is not None:
                        result[submission['graph_id'],rank,stage['node_id']] = dict(rank_id=rank,
                            layer_id=stage['layer_id'],stage=stage['stage'],declared_duration_ns=stage['duration_ns'])
                for layer in graph.get('layer_phases',[]):
                    node = layer['phase_nodes']['expert']
                    if node is not None:
                        result[submission['graph_id'],rank,node] = dict(rank_id=rank,layer_id=layer['layer_id'],stage='routed_expert')
            del plan
    return result


def operation_stats(endpoints, stages, work):
    first = min(row['start_local_ns'] for row in endpoints)
    ranks = []
    for row in sorted(endpoints, key=lambda r:r['rank_id']):
        rank = row['rank_id']
        local = stages[(rank, row['local_record_id'])]
        ranks.append(dict(rank_id=rank, start_ns=row['start_local_ns'], end_ns=row['end_local_ns'],
            relative_entry_ns=row['start_local_ns']-first,
            relative_finish_ns=row['end_local_ns']-first,
            duration_ns=row['end_local_ns']-row['start_local_ns'],
            stages=local, workload=work[rank]))
    summaries = {key:distribution([r[key] for r in ranks]) for key in
                 ('relative_entry_ns','duration_ns','relative_finish_ns')}
    summaries.update(first_entry_ns=first, last_finish_ns=max(r['end_ns'] for r in ranks),
        last_finishing_ranks=[r['rank_id'] for r in ranks if r['end_ns']==max(x['end_ns'] for x in ranks)],
        correlations=dict(entry_vs_finish=correlation([r['relative_entry_ns'] for r in ranks],[r['relative_finish_ns'] for r in ranks]),
                          duration_vs_finish=correlation([r['duration_ns'] for r in ranks],[r['relative_finish_ns'] for r in ranks])),
        rank_samples_are_independent=False)
    return summaries, ranks


def compute_relations(rank_rows, compute_rows, profile):
    """Compare recorded dependencies and declared work; not hardware calibration."""
    ranks = {(r['layer_id'],r['kind'],r['rank_id']):r for r in rank_rows}
    compute = {(r['layer_id'],r['stage'],r['rank_id']):r for r in compute_rows}
    result = {}
    for lid in sorted({r['layer_id'] for r in rank_rows}):
        participants = sorted(r for layer,kind,r in ranks if layer==lid and kind=='combine')
        expert_after, combine_after, work_error, count, entry = [], [], [], [], []
        propagated_residual = []
        for rank in participants:
            d, c = ranks[lid,'dispatch',rank], ranks[lid,'combine',rank]
            e = compute[lid,'routed_expert',rank]
            n = c['workload']['routed_inputs']
            count.append(n); entry.append(c['start_ns'])
            expert_after.append(e['start_ns']-d['end_ns'])
            combine_after.append(c['start_ns']-e['end_ns'])
            work_error.append(e['end_ns']-e['start_ns']-n*profile['layers'][lid]['routed_expert_per_input_ns'])
            if (lid-1,'combine',rank) in ranks:
                stages = [(lid-1,s) for s in ('shared_routed_add','ffn_residual')]
                stages += [(lid,s) for s in ('attention_norm','mla_prologue','mla_attention','mla_output','attention_residual','ffn_norm','router')]
                chain = []
                for layer,stage in stages:
                    node = compute.get((layer,stage,rank))
                    if node is not None:
                        chain.append(node)
                    elif c['workload']['requests'] != 0:
                        raise ValueError('Missing nonempty local compute stage')
                    # Idle ranks have no local-token model work; the planner
                    # omits these zero-duration nodes, not their expert work.
                work = sum(r['end_ns']-r['start_ns'] for r in chain)
                propagated_residual.append(d['start_ns']-ranks[lid-1,'combine',rank]['end_ns']-work)
        result[str(lid)] = dict(expert_start_minus_dispatch_finish_ns=distribution(expert_after),
            combine_entry_minus_expert_finish_ns=distribution(combine_after),
            expert_duration_minus_declared_work_ns=distribution(work_error),
            routed_inputs_vs_combine_entry_correlation=correlation(count,entry),
            next_dispatch_entry_minus_previous_combine_finish_and_intervening_work_ns=distribution(propagated_residual))
    return result


def analyze(directory, output, validate=True):
    directory, output = Path(directory).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    run = directory/'run'
    if validate:
        cli(output, 'validate', 'validate', '--run', run)
    source = read(directory/'prepared/service.json')
    case = read(directory/'case.json')
    execution = read(run/'raw/execution/result.json')
    compute = compute_index(run)
    actual_compute = []
    rank_completion = Counter()
    node_counts = Counter()
    for node in iter_result(run/'raw/execution','node_execution',execution):
        rank_completion[node['rank_id']] = max(rank_completion[node['rank_id']], node['end_ns'])
        node_counts[node['kind']] += 1
        key = node['graph_id'],node['rank_id'],node['node_id']
        if key in compute:
            actual_compute.append(dict(**compute.pop(key),start_ns=node['start_ns'],end_ns=node['end_ns']))
    if compute:
        raise ValueError('Missing executed compute-stage nodes')
    public = read(run/'observed/public_config.json')
    endpoints = {(r['rank_id'],r['local_record_id']):r for r in stored_rows(run/'observed','endpoint_ops.jsonl')}
    stages = defaultdict(dict)
    for row in stored_rows(run/'observed','endpoint_stages.jsonl'):
        stages[row['rank_id'],row['local_record_id']][row['stage']] = dict(time_ns=row['time_local_ns'], count=row['event_count'])
    operations = list(stored_rows(run/'truth','operation_registry.jsonl'))
    workloads = {}
    expert_counts = {}
    ranks = sorted(public['rank_ids'])
    layers = {layer['layer_id']:layer for group in read(directory/'prepared/layers.json')['groups'] for layer in group['layers']}
    ids = sorted({o['layer_id'] for o in operations})
    for layer in ids:
        work = {r:dict(requests=0, context_tokens=0, routed_inputs=0, same_chip_vectors=0, network_vectors=0) for r in ranks}
        placement = layers[layer]['expert_placement']
        chips = {r['rank_id']:r['chip_id'] for r in layers[layer]['deployment']['ranks']}
        expert = Counter({e:0 for e in range(len(placement))})
        for request in source['requests']:
            rank = request['rank_id']
            work[rank]['requests'] += 1
            work[rank]['context_tokens'] += request['prompt_tokens']+request['generated_tokens']
            for e in request['decode_routes'][0][str(layer)]:
                expert[e] += 1
                dst = placement[e]
                work[dst]['routed_inputs'] += 1
                work[rank]['same_chip_vectors' if chips[rank]==chips[dst] else 'network_vectors'] += 1
        workloads[layer] = work
        expert_counts[layer] = dict(distribution=distribution(list(expert.values())), by_expert=dict(expert))
    summaries, rank_rows = [], []
    for op in sorted(operations, key=lambda r:(r['layer_id'],r['kind'])):
        members = [endpoints[r['rank_id'],r['local_record_id']] for r in op['endpoint_records']]
        summary, rows = operation_stats(members, stages, workloads[op['layer_id']])
        summary.update(layer_id=op['layer_id'], kind=op['kind'], operation_id=op['operation_id'])
        summaries.append(summary)
        rank_rows.extend(dict(layer_id=op['layer_id'],kind=op['kind'],**row) for row in rows)
    # Keep only O(ports) accumulators, not a history for every logical window.
    ports = defaultdict(Counter)
    for row in stored_rows(run/'observed','port_windows.csv'):
        value = ports[row['switch_id'],row['port_id']]
        value['packets'] += row['counted_packets']
        value['packet_bytes'] += row['counted_packet_bytes']
        value['nonzero_packet_windows'] += 1
    envelope_queue = {s['operation_id']:Counter() for s in summaries}
    for row in stored_rows(run/'observed','queue_windows.csv'):
        value = ports[row['switch_id'],row['port_id']]
        area = row['voq_waiting_byte_ns']+row['fifo_waiting_byte_ns']
        value['voq_byte_ns'] += row['voq_waiting_byte_ns']
        value['fifo_byte_ns'] += row['fifo_waiting_byte_ns']
        value['peak_voq_bytes'] = max(value['peak_voq_bytes'],row['voq_waiting_peak_bytes'])
        value['peak_fifo_bytes'] = max(value['peak_fifo_bytes'],row['fifo_waiting_peak_bytes'])
        for op in summaries:
            if row['local_window_start_ns'] < op['last_finish_ns'] and op['first_entry_ns'] < row['local_window_end_ns']:
                envelope_queue[op['operation_id']]['overlapping_window_queue_byte_ns'] += area
    attribution = Counter()
    for row in stored_rows(run/'truth','attribution_windows.csv'):
        attribution[row['operation_id']] += row['business_payload_bytes']
    for op in summaries:
        op['network_business_byte_hops'] = attribution[op['operation_id']]
        op['overlapping_network_windows'] = dict(envelope_queue[op['operation_id']])
    observed_ports = {(p['switch_id'],p['port_id']) for p in public['ports']}
    if not set(ports) <= observed_ports:
        raise ValueError('Stored data outside declared port coverage')
    network = dict(observed_ports=len(observed_ports), ports_with_nonzero_records=len(ports),
        zero_record_ports=len(observed_ports-set(ports)),
        packet_bytes=sum(p['packet_bytes'] for p in ports.values()),
        packets=sum(p['packets'] for p in ports.values()),
        voq_waiting_byte_ns=sum(p['voq_byte_ns'] for p in ports.values()),
        fifo_waiting_byte_ns=sum(p['fifo_byte_ns'] for p in ports.values()),
        max_voq_waiting_bytes=max(p['peak_voq_bytes'] for p in ports.values()),
        max_fifo_waiting_bytes=max(p['peak_fifo_bytes'] for p in ports.values()),
        queue_area_is_not_saved_time=True, source_device_queues_observed=False)
    summary = dict(schema_version='issue-screening-e1-analysis/1', case_id=case['case_id'],
        case_sha256=digest(directory/'case.json'), public_manifest_sha256=digest(run/'manifest.json'),
        scope='researcher_internal_analysis_not_a_measurement_algorithm',
        requests=len(source['requests']), analyzed_layers=case.get('analyzed_layers',[7,8]),
        simulation_end_ns=execution['simulation_end_ns'], service_finished_ns=execution['service_finished_ns'],
        rank_last_node_end_ns=dict(rank_completion), executed_node_counts=dict(node_counts),
        network_drain=execution['network_drain'], operations=summaries, experts=expert_counts, network=network,
        compute_relations=compute_relations(rank_rows,actual_compute,source['groups'][0]['full_model_profile']),
        analysis_program_sha256=digest(Path(__file__)),
        independent_source_groups=1, rank_observations_are_independent_repetitions=False)
    save(output/'summary.json', summary)
    with (output/'ranks.jsonl').open('w') as stream:
        for row in rank_rows:
            stream.write(json.dumps(row)+'\n')
    with (output/'compute.jsonl').open('w') as stream:
        for row in sorted(actual_compute,key=lambda r:(r['layer_id'],r['rank_id'],r['start_ns'],r['stage'])):
            stream.write(json.dumps(row)+'\n')
    with (output/'ports.jsonl').open('w') as stream:
        for switch, port in sorted(observed_ports):
            values = {key:ports[switch,port][key] for key in ('packets','packet_bytes','nonzero_packet_windows',
                'voq_byte_ns','fifo_byte_ns','peak_voq_bytes','peak_fifo_bytes')}
            stream.write(json.dumps(dict(switch_id=switch,port_id=port,observed=True,**values))+'\n')
    print(json.dumps(dict(passed=True, case_id=case['case_id'], simulation_end_ns=execution['simulation_end_ns'], output=str(output))))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    a = p.parse_args()
    if a.worker:
        analyze(a.case_dir, a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        policy = host()
        policy['artifact_roots'].append(str(a.output.resolve()))
        report = probe([sys.executable,'-m','experiments.scaleup_issue_screening.analyze',
            '--worker','--case-dir',str(a.case_dir.resolve()),'--output',str(a.output.resolve())],
            str(a.output.resolve())+'-resources', **policy)
        if report['status'] != 'completed':
            raise SystemExit('Analysis failed: '+report['status'])


if __name__ == '__main__':
    main()
