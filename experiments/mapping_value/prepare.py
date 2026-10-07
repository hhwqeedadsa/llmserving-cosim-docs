"""Trusted projection: platform records -> explicitly allowed analysis inputs."""
import csv
import os
from pathlib import Path

from moe_ub_cosim.research_storage import stored_rows, validate_observed_storage

from .common import digest, read, require, save, write_rows
from .inputs import CONDITIONS, QUEUE, WINDOW, record_key, tables, validate


def anchors(operations, endpoints, group, rank):
    public = {record_key(row):row for row in endpoints}
    result = {}; used = set()
    for operation in operations:
        require(operation['group_id'] == group, 'Unexpected group in first-round input')
        candidates = [e for e in operation['endpoint_records'] if e['rank_id'] == rank]
        require(len(candidates) == 1, 'Operation must have exactly one public anchor')
        key = record_key(candidates[0]); identity = operation['operation_id']
        require(key in public and key not in used and identity not in result and
                public[key]['kind'] == operation['kind'], 'Invalid or repeated anchor binding')
        used.add(key); result[identity] = key
    require(used == {k for k in public if k[0] == rank}, 'Incomplete anchor coverage')
    return result


def project_relations(source, anchor, *, categories=None):
    """No endpoint-time crop, contribution weight, hidden id or cross-rank binding."""
    found = set()
    for row in source:
        if categories is None:
            if row['business_payload_bytes'] <= 0:
                continue
        elif row['message_category'] not in categories or row['application_bytes'] <= 0:
            continue
        require(row['operation_id'] in anchor, 'Attribution lacks anchor binding')
        rank, local = anchor[row['operation_id']]
        found.add((rank,local,*(row[k] for k in sorted(WINDOW))))
        require(len(found) <= 2_000_000, 'Relation budget exceeded')
    for entry in sorted(found):
        yield dict(rank_id=entry[0],local_record_id=entry[1],**dict(zip(sorted(WINDOW),entry[2:])))


def public_static(run, design):
    observation = read(run/'observed/public_config.json')
    require(observation['rank_ids'] == design['ranks'], 'Rank scope differs from experiment design')
    groups = read(run/'raw/deployment.json')['groups']
    require(len(groups) == 1 and groups[0]['group_id'] == design['group_id'], 'Expected one fixed EP group')
    layer = groups[0]
    network = run/'inputs/network'
    with (network/'node.csv').open() as stream:
        nodes = [dict(node_id=int(r['nodeId']),kind=r['nodeType'],ports=int(r['portNum'])) for r in csv.DictReader(stream)]
    with (network/'topology.csv').open() as stream:
        links = [dict(node_a=int(r['nodeId1']),port_a=int(r['portId1']),node_b=int(r['nodeId2']),
                      port_b=int(r['portId2']),rate_bps=int(r['bandwidth'].removesuffix('bps')),
                      delay_ns=int(r['delay'].removesuffix('ns'))) for r in csv.DictReader(stream)]
    return dict(schema_version='mapping-value-public/1',group_id=design['group_id'],ranks=design['ranks'],
        anchor_rank=design['anchor_rank'],rank_nodes={str(r):n for r,n in zip(layer['rank_ids'],layer['rank_to_ub_node'])},
        nodes=nodes,links=links,ports=observation['ports'],queue_ports=design['queue_ports'],
        clock_relation='shared_accurate_ns',**{k:design[k] for k in
            ('time_tolerance_ns','material_round_absolute_ns','material_round_relative','analysis_version')})


def prepare(run, reference, config, output):
    run, reference, output = map(lambda p:Path(p).resolve(), (run,reference,output))
    require(not output.exists(), 'Preparation output already exists')
    design = read(config)
    require(design['schema_version']=='mapping-value-design/1' and design['anchor_rank']==min(design['ranks']), 'Invalid design/anchor')
    # Provenance/configuration stays outside the input directories supplied to analysis.
    for source in {run,reference}:
        manifest = read(source/'manifest.json')
        require(manifest['normal_exit'] and manifest['coverage']['execution_complete'] and
                manifest['coverage']['observation_complete'], 'Complete validated run required')
        validate_observed_storage(source/'observed')
        clocks = read(source/'truth/clock_models.json')['devices']
        require(all(c['offset_ns']==c['drift_ppb']==0 for c in clocks), 'First batch requires accurate common clocks')
    public = public_static(run,design)
    require(public == public_static(reference,design), 'Paired public static inputs differ')
    output.mkdir(parents=True); pool = output/'projection'; pool.mkdir()
    save(pool/'public.json',public)
    for prefix,source in (('',run),('reference_',reference)):
        endpoints = list(stored_rows(source/'observed','endpoint_ops.jsonl'))
        anchor = anchors(stored_rows(source/'truth','operation_registry.jsonl'),endpoints,design['group_id'],design['anchor_rank'])
        write_rows(pool/(prefix+'endpoint.jsonl'),endpoints)
        write_rows(pool/(prefix+'stages.jsonl'),stored_rows(source/'observed','endpoint_stages.jsonl'))
        write_rows(pool/(prefix+'ports.jsonl'),stored_rows(source/'observed','port_windows.csv'))
        selected = {tuple(p) for p in design['queue_ports']}
        write_rows(pool/(prefix+'queues.jsonl'),
            ({k:row[k] for k in QUEUE} for row in stored_rows(source/'observed','queue_windows.csv')
             if (row['switch_id'],row['port_id']) in selected))
        write_rows(pool/(prefix+'relations.jsonl'),project_relations(stored_rows(source/'truth','attribution_windows.csv'),anchor))
        write_rows(pool/(prefix+'state_relations.jsonl'),project_relations(
            stored_rows(source/'truth','message_attribution_windows.csv'),anchor,
            categories={'business_data','dispatch_count','combine_flag','load_ready'}))
    for condition in sorted(CONDITIONS):
        target = output/condition; target.mkdir()
        os.link(pool/'public.json',target/'public.json')
        for prefix in ('','reference_'):
            for table in tables(condition):
                name = 'state_relations' if table=='relations' and condition.endswith('S') else table
                os.link(pool/(prefix+name+'.jsonl'),target/(prefix+table+'.jsonl'))
        save(target/'condition.json',dict(schema_version='mapping-value-input/1',condition=condition,
            files={p.name:digest(p) for p in sorted(target.iterdir())}))
        validate(target)
    save(output/'preparation.json',dict(schema_version='mapping-value-preparation/1',
        run=str(run),reference=str(reference),design_sha256=digest(config),
        run_manifest_sha256=digest(run/'manifest.json'),reference_manifest_sha256=digest(reference/'manifest.json'),
        input_sha256={c:digest(output/c/'condition.json') for c in sorted(CONDITIONS)},
        implementation_sha256={p.name:digest(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
        conditions=sorted(CONDITIONS),relation_scope='whole_logical_call_all_captured_windows',
        state_categories=['dispatch_count','combine_flag','load_ready']))
    return dict(passed=True,output=str(output),conditions=sorted(CONDITIONS))
