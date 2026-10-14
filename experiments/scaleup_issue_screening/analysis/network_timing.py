"""Internal all-rank timing comparison for two sealed finite network replays."""
from pathlib import Path

from collections import Counter

from moe_ub_cosim.record_storage import iter_result, iter_port_records
from .analyze import distribution
from .workflow import digest, read


def wire_totals(case):
    execution = Path(case)/'run/raw/execution'
    totals = Counter(); audit = Counter(); ports = 0
    fields = ('business_payload_bytes','application_metadata_bytes','padding_bytes',
              'data_protocol_overhead_bytes','control_packet_bytes','unattributed_bytes')
    for port in sorted((execution/'ports').iterdir()):
        for row in iter_port_records(execution, port, 'aggregate-windows.jsonl'):
            if sum(row[f] for f in fields) != row['counted_packet_bytes']:
                raise ValueError('Wire byte categories do not conserve')
            totals.update({f:row[f] for f in (*fields,'counted_packet_bytes','counted_packets')})
        current = read(port/'observation-audit.json')
        audit.update({k:current[k] for k in ('all_successful_start_bytes','all_successful_start_packets')})
        ports += 1
    if (totals['counted_packet_bytes'] != audit['all_successful_start_bytes'] or
            totals['counted_packets'] != audit['all_successful_start_packets']):
        raise ValueError('Wire category coverage differs from complete port audits')
    return dict(ports=ports, **totals)


def timing_changes(before, after, keys):
    def indexed(rows):
        result = {}
        for row in rows:
            key = tuple(row[k] for k in keys)
            if key in result:
                raise ValueError('Duplicate business timing identity')
            if row['end_ns'] < row['start_ns']:
                raise ValueError('Invalid business timing interval')
            result[key] = row
        return result
    a, b = indexed(before), indexed(after)
    if set(a) != set(b):
        raise ValueError('Business timing coverage changed')
    rows = []
    for key in sorted(a):
        x, y = a[key], b[key]
        rows.append(dict(identity={k:x[k] for k in keys},
            before_start_ns=x['start_ns'], before_end_ns=x['end_ns'],
            after_start_ns=y['start_ns'], after_end_ns=y['end_ns'],
            start_change_ns=y['start_ns']-x['start_ns'],
            end_change_ns=y['end_ns']-x['end_ns'],
            duration_change_ns=(y['end_ns']-y['start_ns'])-(x['end_ns']-x['start_ns'])))
    changes = [row['end_change_ns'] for row in rows]
    return dict(rows=rows, count=len(rows),
        earlier=sum(x < 0 for x in changes), unchanged=sum(x == 0 for x in changes),
        later=sum(x > 0 for x in changes), end_change_ns=distribution(changes),
        start_change_ns=distribution([row['start_change_ns'] for row in rows]),
        duration_change_ns=distribution([row['duration_change_ns'] for row in rows]))


def compare_all(baseline_case, treatment_case, baseline_review, treatment_review, bundle=None):
    cases = [Path(baseline_case), Path(treatment_case)]
    reviews = [baseline_review, treatment_review]
    selections = [read(case/'selection.json') for case in cases]
    for case, review in zip(cases, reviews):
        if (digest(case/'selection.json') != review['selection_sha256'] or
                digest(case/'run/manifest.json') != review['manifest_sha256']):
            raise ValueError('All-rank timing case differs from the sealed gate review')
    for key in ('slice_sha256', 'original_archive_sha256'):
        if selections[0][key] != selections[1][key]:
            raise ValueError('All-rank timing source differs')
    if read(cases[0]/'observation.json') != read(cases[1]/'observation.json'):
        raise ValueError('All-rank timing observation or clocks changed')
    bundle = Path(bundle or selections[0]['slice'])
    if digest(bundle/'slice.json') != selections[0]['slice_sha256']:
        raise ValueError('All-rank timing slice differs')
    specs = []
    for name, identity in baseline_review['plan_files'].items():
        path = bundle/name
        if digest(path) != identity:
            raise ValueError('All-rank timing graph differs')
        plan = read(path)
        for graph in plan['rank_graphs']:
            specs.extend(dict(graph_id=plan['graph_id'], rank_id=graph['rank_id'],
                layer_id=s['layer_id'], stage=s['stage'], node_id=s['node_id'])
                for s in graph['model_stages'] if s['node_id'] is not None)
        del plan
    needed = {(s['graph_id'], s['rank_id'], s['node_id']) for s in specs}
    endpoints, stages = [], []
    for case in cases:
        execution = case/'run/raw/execution'
        report = read(execution/'result.json')
        if not report['normal_exit'] or not report['network_drain']['idle']:
            raise ValueError('All-rank timing execution is incomplete')
        raw = {(r['rank_id'], r['local_record_id']):r
               for r in iter_result(execution, 'endpoint_ops', report)}
        operations = []
        for op in iter_result(execution, 'operation_registry', report):
            for member in op['endpoint_records']:
                row = raw.pop((member['rank_id'], member['local_record_id']))
                operations.append(dict(operation_id=op['operation_id'], rank_id=member['rank_id'],
                    layer_id=op['layer_id'], kind=op['kind'],
                    start_ns=row['start_local_ns'], end_ns=row['end_local_ns']))
        if raw:
            raise ValueError('Endpoint operation records were not fully paired')
        endpoints.append(operations)
        times = {}
        for row in iter_result(execution, 'node_execution', report):
            key = row['graph_id'], row['rank_id'], row['node_id']
            if key in needed:
                if key in times:
                    raise ValueError('Repeated executed model stage node')
                times[key] = row
        if set(times) != needed:
            raise ValueError('Missing executed model stage timing')
        stages.append([dict(spec,
            start_ns=times[spec['graph_id'], spec['rank_id'], spec['node_id']]['start_ns'],
            end_ns=times[spec['graph_id'], spec['rank_id'], spec['node_id']]['end_ns']) for spec in specs])
    return dict(schema_version='issue-network-all-rank-timing/1',
        endpoint_operations=timing_changes(*endpoints, ('operation_id','rank_id','layer_id','kind')),
        model_stages=timing_changes(*stages, ('graph_id','rank_id','layer_id','stage','node_id')),
        all_port_wire_totals=[wire_totals(case) for case in cases],
        cases=[dict(selection_sha256=digest(case/'selection.json'),
                    manifest_sha256=digest(case/'run/manifest.json')) for case in cases],
        program_sha256=digest(__file__),
        interpretation=['All participating ranks and model stages in the unchanged frozen graph, not only selected or globally last consumers.',
            'Endpoint times use the unchanged rank-local clocks; model-stage times use simulation nanoseconds.',
            'Timing changes alone are not a completion-dependency explanation or an independent statistical sample.'])
