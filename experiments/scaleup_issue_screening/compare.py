"""Compare the four predeclared E1 cases; no candidate or root-cause labels."""
import argparse
import json
from pathlib import Path

from .analyze import compute_relations, correlation, distribution
from .workflow import CONFIG, digest, read, save


def rows(path):
    with path.open() as stream:
        return [json.loads(line) for line in stream]


def compare_initial(short, long, short_hashes, long_hashes, layers):
    initial = []
    for layer in layers:
        if short_hashes[str(layer)] != long_hashes[str(layer)]:
            raise ValueError('Short/long request, route, context or placement mismatch')
        for kind in ('dispatch','combine'):
            keys = sorted(k for k in short if k[:2]==(layer,kind))
            if not keys or set(keys) != {k for k in long if k[:2]==(layer,kind)}:
                raise ValueError('Short/long participant coverage mismatch')
            if any(short[k]['workload'] != long[k]['workload'] for k in keys):
                raise ValueError('Short/long workload mismatch')
            deltas = {field:distribution([long[k][field]-short[k][field] for k in keys])
                      for field in ('relative_entry_ns','duration_ns','relative_finish_ns')}
            relative = {field:correlation([short[k][field] for k in keys],[long[k][field] for k in keys])
                        for field in ('relative_entry_ns','duration_ns','relative_finish_ns')}
            initial.append(dict(layer_id=layer,kind=kind,participants=len(keys),
                workload_equal=True,long_minus_short_ns=deltas,short_long_correlations=relative))
    return initial


def compare(root, output):
    root, output = Path(root), Path(output)
    if output.exists():
        raise ValueError('Comparison output must be new')
    batch = read(CONFIG/'batch.json')
    profile = read(CONFIG/'source-base.json')['service']['groups'][0]['full_model_profile']
    cases, rank_rows, summaries = {}, {}, {}
    table = []
    for spec in batch['cases']:
        name = spec['case_id']
        directory = root/name
        summary = read(directory/'summary.json')
        summaries[name] = summary
        if summary['case_id'] != name or summary['requests'] != spec['requests']:
            raise ValueError('Wrong case supplied')
        ranks, compute = rows(directory/'ranks.jsonl'), rows(directory/'compute.jsonl')
        cases[name] = dict(source_summary_sha256=digest(directory/'summary.json'),
            requests=summary['requests'], simulation_end_ns=summary['simulation_end_ns'],
            network=summary['network'], compute_relations=compute_relations(ranks,compute,profile))
        rank_rows[name] = {(r['layer_id'],r['kind'],r['rank_id']):r for r in ranks}
        for op in summary['operations']:
            if op['layer_id'] in batch['analyzed_layers']:
                table.append(dict(case_id=name,layer_id=op['layer_id'],kind=op['kind'],
                    entry_spread_ns=op['relative_entry_ns']['spread'],
                    median_duration_ns=op['duration_ns']['median'],max_duration_ns=op['duration_ns']['maximum'],
                    finish_spread_ns=op['relative_finish_ns']['spread'],
                    operation_envelope_ns=op['last_finish_ns']-op['first_entry_ns']))
    short, long = rank_rows['g0-b8-short'], rank_rows['g0-b8-long']
    initial = compare_initial(short,long,summaries['g0-b8-short']['semantic_workload_sha256_by_layer'],
        summaries['g0-b8-long']['semantic_workload_sha256_by_layer'],batch['analyzed_layers'])
    save(output,dict(schema_version='issue-screening-e1-comparison/1',cases=cases,operation_table=table,
        initial_state_comparison=initial,independent_source_groups=1,
        scope='descriptive_same_workload_initial_state_comparison_not_steady_state_proof',
        analysis_program_sha256=digest(Path(__file__)),cost_profile_source_sha256=digest(CONFIG/'source-base.json')))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--analysis-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    compare(a.analysis_root,a.output)


if __name__=='__main__':
    main()
