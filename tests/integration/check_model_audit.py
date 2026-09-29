"""Check pinned source evidence and a CPU layout oracle, never execute DeepEP.

The oracle expresses the Boolean rank mask in DeepEP layout.cu independently
of the reference plan generator. It compares logical tensor bytes only.
"""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import itertools
import json
from pathlib import Path

from moe_ub_cosim.plan import build_plan


ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def layout(tokens):
    """Selected target: four ranks, contiguous pairs of experts, Top-K=2.

    Keep self-rank rows in the layout. Only the later byte projection omits
    them from cross-rank traffic. No imported target or reference helper here.
    """
    matrix = [[0] * 4 for _ in range(4)]
    expert_counts = [0] * 8
    for token in tokens:
        selected = token['expert_ids']
        assert len(selected) == len(set(selected)) == 2
        assert all(0 <= expert < 8 for expert in selected)
        for expert in range(8):
            expert_counts[expert] += sum(choice == expert for choice in selected)
        for rank in range(4):
            mask = any(2 * rank <= choice < 2 * (rank + 1) for choice in selected)
            matrix[token['src_rank']][rank] += int(mask)
    return matrix, expert_counts


def check_layer(plan, transfers):
    c = plan['input']
    assert c['rank_ids'] == list(range(4))
    assert c['expert_placement'] == [0, 0, 1, 1, 2, 2, 3, 3]
    assert c['hidden_dim'] == 4096 and c['top_k'] == 2
    assert c['dispatch_element_bytes'] == c['combine_element_bytes'] == 2
    matrix, expert_counts = layout(c['tokens'])
    rank_counts = [sum(expert_counts[2 * rank:2 * rank + 2]) for rank in range(4)]
    assert plan['expert_input_counts'] == rank_counts
    assert plan['communication_profile_basis'] == 'reference_model'
    assert plan['calibration_status'] == 'unvalidated_reference'
    expected = Counter()
    for src in range(4):
        for dst in range(4):
            if src != dst and matrix[src][dst]:
                expected['dispatch', src, dst] = matrix[src][dst] * 8192
                expected['combine', dst, src] = matrix[src][dst] * 8192
    actual = Counter()
    for transfer in transfers:
        assert transfer['application_metadata_bytes'] == 0
        assert transfer['payload_bytes'] == transfer['business_payload_bytes']
        actual[transfer['kind'], transfer['src_rank'], transfer['dst_rank']] += transfer['payload_bytes']
    assert actual == expected, (actual, expected)
    for graph in plan['rank_graphs']:
        expert = [n for n in graph['nodes'] if n['name'] == 'expert-compute']
        count = rank_counts[graph['rank_id']]
        assert len(expert) == int(count > 0)
        if expert:
            assert expert[0]['duration_ns'] == count * c['compute_profile_ns']['expert_per_input']
            assert len(expert[0]['expert_inputs']) == count
    remote_rows = sum(matrix[i][j] for i in range(4) for j in range(4) if i != j)
    return dict(rank_token_matrix_including_local=matrix, expert_input_counts=expert_counts,
                remote_rows=remote_rows, local_rows=sum(matrix[i][i] for i in range(4)),
                business_bytes=sum(actual.values()),
                target_logical_dispatch_metadata_bytes=remote_rows * 28,
                target_logical_combine_metadata_bytes=remote_rows * 4)


def check_sources(audit, source_dir):
    source_paths = set()
    for entry in audit['source_files']:
        path = source_dir / entry['path']
        data = path.read_bytes()
        assert len(data) == entry['bytes'] and sha(path) == entry['sha256'], str(path)
        blob = b'blob ' + str(len(data)).encode() + b'\0' + data
        assert hashlib.sha1(blob).hexdigest() == entry['git_blob_sha1'], str(path)
        assert '/' + audit['target']['commit'] + '/' in entry['url']
        source_paths.add(entry['path'])
    for ref in audit['source_references'].values():
        assert ref['path'] in source_paths
        assert 1 <= ref['line_start'] <= ref['line_end'] <= len((source_dir / ref['path']).read_text().splitlines())
    for entry in audit['local_files']:
        assert sha(ROOT / entry['path']) == entry['sha256'], entry['path']
    facets = audit['facets']
    assert len(facets) == 8 and {f['id'] for f in facets} == {
        'participants', 'data_representation', 'replication_aggregation', 'peers',
        'chunk_order', 'concurrency_buffers', 'synchronization_completion', 'endpoint_processing'}
    for facet in facets:
        assert facet['source_review'] == 'reviewed' and facet['limit']
        assert facet['evidence'] and set(facet['evidence']) <= set(audit['source_references'])
        assert facet['local_evidence'] and all(ref['location'] and ref['path'] in
            {entry['path'] for entry in audit['local_files']} for ref in facet['local_evidence'])
    v = audit['validation']
    assert v['source_review_complete'] and not v['target_runtime_executed']
    assert not v['target_behavior_reproduced'] and not v['parameter_fitting_performed']
    assert v['thresholds'] is None and v['calibration_status'] == 'unvalidated_reference'
    for field in ('target_stage_timing', 'target_port_windows', 'target_device_compute', 'numerical_aggregation'):
        assert v[field] == 'unverified'


def check_generated():
    base = read(ROOT / 'fixtures/m5_multilayer/layer.json')
    cases = {}

    def check(label, config):
        plan = build_plan(config)
        cases[label] = check_layer(plan, plan['transfers'])

    check('base', base)
    same = deepcopy(base)
    same['tokens'][0]['expert_ids'] = [2, 3]
    check('same-rank-experts', same)
    assert cases['base']['business_bytes'] == 114688
    assert cases['same-rank-experts']['business_bytes'] == 98304
    chunks = deepcopy(base)
    chunks['chunk_bytes'] = 3072
    check('byte-chunks', chunks)
    assert cases['byte-chunks'] == cases['base']
    sparse = deepcopy(base)
    sparse['tokens'] = sparse['tokens'][:1]
    check('zero-token-participants', sparse)
    local = deepcopy(sparse)
    local['tokens'][0]['expert_ids'] = [0, 1]
    check('pure-local', local)
    assert cases['pure-local']['business_bytes'] == 0 and cases['pure-local']['local_rows'] == 1
    for src, experts in itertools.product(range(4), itertools.combinations(range(8), 2)):
        c = deepcopy(sparse)
        c['tokens'][0].update(src_rank=src, expert_ids=list(experts))
        check(f'source-{src}-experts-{experts[0]}-{experts[1]}', c)
    # Empty services do not submit a layer plan; only check the empty oracle.
    assert layout([]) == ([[0] * 4 for _ in range(4)], [0] * 8)
    return cases


def check_research(regression_dir):
    summary = read(regression_dir / 'summary.json')
    assert summary['passed'] and summary['research_runs'] == 8 and summary['unobserved_runs'] == 1
    assert len(summary['failures']) == 10 and all(v['exit_code'] == 1 for v in summary['failures'].values())
    for file, digest in summary['source_hashes'].items():
        assert sha(ROOT / file) == digest, file
    runs = {}
    for label, expected in summary['metrics'].items():
        path = regression_dir / label
        manifest = read(path / 'manifest.json')
        assert manifest['normal_exit'] and not manifest['full_model_execution_validated']
        assert manifest['calibration_status'] == manifest['communication_calibration_status'] == 'unvalidated_reference'
        assert manifest['communication_profile_basis'] == 'reference_model'
        assert manifest['routing_source'] == 'synthetic_indexed_file'
        assert manifest['compute_sources'] == ['per_layer_fixed_reference']
        assert manifest['compute_scope'] == 'consecutive_moe_layer_segment'
        for file, digest in manifest['artifact_hashes'].items():
            assert sha(path / file) == digest, str(path / file)
        for file, digest in manifest['source_hashes'].items():
            assert sha(ROOT / file) == digest, file
        report = read(path / 'logs/raw-run/execution/result.json')
        actual = {t['transfer_id']: t for t in report['transfers']}
        assert len(actual) == len(report['transfers']) == expected['writes']
        assert report['service_finished_ns'] == expected['finished_ns']
        checked, layers = set(), []
        for file in sorted((path / 'graphs').glob('*/plan.json')):
            plan = read(file)
            for child in plan['layers']:
                operations = {op['operation_id'] for op in child['operations']}
                transfers = [t for t in plan['transfers'] if t['operation_id'] in operations]
                layers.append(dict(graph_id=plan['graph_id'], layer_id=child['input']['layer_id'],
                                   **check_layer(child, transfers)))
                for t in transfers:
                    tid = t['transfer_id']
                    assert tid not in checked
                    checked.add(tid)
                    event = actual[tid]
                    assert event['src_rank'] == t['src_rank'] and event['dst_rank'] == t['dst_rank']
                    assert event['bytes'] == event['committed_bytes'] == t['payload_bytes']
                    assert event['submitted_tasks'] == event['send_callbacks'] == event['receive_callbacks'] == 1
                    assert not event['context_active']
                    assert event['send_done_ns'] == event['transaction_done_ns'] >= event['data_ready_ns']
        assert checked == set(actual)
        totals = {key: sum(layer[key] for layer in layers) for key in (
            'remote_rows', 'local_rows', 'business_bytes',
            'target_logical_dispatch_metadata_bytes', 'target_logical_combine_metadata_bytes')}
        assert totals['business_bytes'] == expected['unique_bytes']
        runs[label] = dict(metrics=expected, byte_totals=totals, layer_layouts=layers,
                           manifest_sha256=sha(path / 'manifest.json'),
                           project_commit=manifest['project_commit'], backend_sha256=manifest['backend_sha256'])
    return runs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--regression-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    audit_path = ROOT / 'fixtures/m5_model_audit/audit.json'
    audit = read(audit_path)
    check_sources(audit, args.source_dir)
    cases = check_generated()
    runs = check_research(args.regression_dir)
    result = dict(schema_version='moe-ub-target-audit-result/1', functional_checks_passed=True,
                  audit_sha256=sha(audit_path), checker_sha256=sha(Path(__file__)),
                  source_dir=str(args.source_dir.resolve()), regression_dir=str(args.regression_dir.resolve()),
                  regression_summary_sha256=sha(args.regression_dir / 'summary.json'),
                  source_files_checked=len(audit['source_files']), generated_layout_cases=len(cases),
                  empty_layout_oracle_checked=True, cases=cases, research_runs=runs,
                  target_validation=audit['validation'],
                  byte_comparison_scope='CPU source-derived oracle; conditional caller merge; logical stores are not wire traffic')
    (args.output_dir / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('cases', 'research_runs')}, indent=2))


if __name__ == '__main__':
    main()
