"""Researcher comparison of declared conditions, graph invariants and execution."""
from copy import deepcopy
from pathlib import Path
import sys

from .common import digest, read, require, save, sources
from .exploration import TEMPO_FACTORS, scaled
from .inputs import verified_case
from .observation_prepare import overlap, rank_truth
from .slot_analysis import analyze_case
from .workflow import verify_complete


def tempo_graph_signature(plan, numerator, denominator):
    # Compare the complete executed node structures after undoing only the
    # declared duration changes. SEND/RECV work, IDs and dependencies stay exact.
    signature = {k:deepcopy(plan[k]) for k in ('model_spec', 'operations', 'transfers',
                                               'expert_input_counts', 'rank_graphs', 'full_model_profile')}
    for graph in signature['rank_graphs']:
        for node in graph['nodes']:
            if node['kind'] == 'COMP' and node.get('name') in ('expert-compute', 'shared_expert'):
                node['duration_ns'] = scaled(node['duration_ns'], denominator, numerator)
        for stage in graph['model_stages']:
            if stage['stage'] == 'shared_expert':
                stage['duration_ns'] = scaled(stage['duration_ns'], denominator, numerator)
    for layer in signature['full_model_profile']['layers']:
        if 'routed_expert_per_input_ns' in layer:
            layer['routed_expert_per_input_ns'] = scaled(layer['routed_expert_per_input_ns'], denominator, numerator)
            layer['per_token_ns']['shared_expert'] = scaled(layer['per_token_ns']['shared_expert'], denominator, numerator)
    return signature


def pair_metrics(before, after):
    old, new = before['stats'], after['stats']
    return dict(before=before['case']['case_id'], after=after['case']['case_id'],
        round_delta_ns=new['round_ns']-old['round_ns'],
        round_reduction_fraction=(old['round_ns']-new['round_ns'])/old['round_ns'],
        full_slot_wait_delta_rank_ns=new['full_slot_wait_rank_ns']-old['full_slot_wait_rank_ns'],
        queue_area_delta_byte_ns=sum(new['network'][k]-old['network'][k] for k in ('summed_voq_byte_ns','summed_fifo_byte_ns')))


def compare(batch, output):
    batch, output = Path(batch).resolve(), Path(output).resolve()
    metadata = read(batch/'batch.json')
    require(metadata['schema_version'] == 'scaleup-exploration-batch/1' and metadata['study'] == 'compute-tempo',
            'Unsupported exploration comparison')
    cases = metadata['cases']
    expected = {(name, n, d, slots) for name,n,d in TEMPO_FACTORS for slots in (1,2)}
    require(len(cases) == 6 and {(c['factor_name'], *c['factor'], c['send_slots_per_rank']) for c in cases} == expected and
            all(c['input_set'] == 4 and c['requests_per_rank'] == 8 and c['request_count'] == 64 for c in cases),
            'Need six fixed tempo conditions')
    rows = []; signatures = []; platforms = []; backends = set(); groups = {}
    for case in cases:
        verified_case(batch, case['case_id'])
        done, summary = verify_complete(batch, case)
        root = batch/'runs'/case['case_id']
        backends.add(done['backend_sha256'])
        platforms.append(read(root/'run/manifest.json')['software']['source_hashes'])
        stats, nodes, _, plan = analyze_case(root, case['send_slots_per_rank'], output/case['case_id'])
        n,d = case['factor']
        signatures.append(tempo_graph_signature(plan,n,d))
        require(stats['compute_work_rank_ns'] == 180356 + scaled(134400,n,d), 'Actual compute work differs from fixed budget')
        intervals = rank_truth(nodes, case['send_slots_per_rank'], summary['round_ns'])
        pending = sum(overlap(a,0,summary['round_ns']) for a,_ in intervals.values())
        full = sum(overlap(b,0,summary['round_ns']) for _,b in intervals.values())
        stats.update(pending_wait_rank_ns=pending, full_slot_wait_rank_ns=full, other_pending_wait_rank_ns=pending-full)
        # Detailed long-wait identities remain in per-case output, not the compact report.
        stats.pop('longest_wait')
        row = dict(case=case, completion=done, stats=stats,
            layer_completion_table_sha256=summary['tables']['layers.csv'],
            transfer_counts=summary['transfers'], actual_native_sizes=summary['raw_native_sizes'])
        rows.append(row); groups[case['factor_name'],case['send_slots_per_rank']] = row
    require(len(backends) == 1 and all(p == platforms[0] for p in platforms), 'Platform/backend changed across conditions')
    require(all(s == signatures[0] for s in signatures), 'Executed graph differs beyond declared expert costs and slots')
    for row in rows[1:]:
        for key in ('fabric_tasks','local_deliveries','unique_fabric_business_bytes','tasks_with_multiple_committed_segments'):
            require(row['transfer_counts'][key] == rows[0]['transfer_counts'][key], 'Communication work changed')
        require(row['actual_native_sizes'] == rows[0]['actual_native_sizes'], 'Actual request granularity changed')
    save(output/'comparison.json',dict(schema_version='scaleup-exploration-comparison/1', study=metadata['study'],
        access='researcher_internal', batch_sha256=digest(batch/'batch.json'), method_sha256=metadata['method_sha256'],
        implementation=sources(), backend_sha256=next(iter(backends)), cases=rows,
        slot_pairs=[dict(factor_name=name, **pair_metrics(groups[name,1],groups[name,2])) for name,_,_ in TEMPO_FACTORS],
        tempo_pairs=[dict(send_slots_per_rank=s, factor_name=name, **pair_metrics(groups['base',s],groups[name,s]))
                     for s in (1,2) for name in ('half','double')],
        checks=dict(exact_node_and_communication_invariants=True, actual_expert_compute_work=True,
                    actual_submit_release_occupancy=True, same_platform_and_backend=True),
        qualification='One seen synthetic input; selected expert costs only; no hardware calibration or direct network root-cause label'))


if __name__ == '__main__': compare(*sys.argv[1:])
