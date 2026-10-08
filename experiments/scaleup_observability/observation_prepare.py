"""Research preparation: separate public windows from internal evaluation labels."""
from collections import defaultdict
import csv
import json
from pathlib import Path
import sys

from moe_ub_cosim.research_storage import stored_rows, validate_observed_storage

from .common import digest, read, require, save, sources
from .inputs import verified_case
from .observation_spec import BASE_NS, END_NS, GRIDS, CONDITIONS, COUNT_FIELDS, QUEUE_FIELDS, grid_id, windows
from .slot_analysis import analyze_case, occupancy_intervals
from .slots import set_slots, verify_pair_inputs
from .statistics import merge_intervals
from .workflow import verify_complete


def intersection(left, right):
    """Intersection of sorted, disjoint half-open intervals."""
    result = []; i = j = 0
    while i < len(left) and j < len(right):
        a, b = max(left[i][0], right[j][0]), min(left[i][1], right[j][1])
        if a < b:
            result.append((a, b))
        if left[i][1] <= right[j][1]: i += 1
        else: j += 1
    return result


def overlap(intervals, a, b):
    return sum(max(0, min(y, b)-max(x, a)) for x, y in intervals if x < b and y > a)


def rank_truth(nodes, slots, end):
    occupancy = occupancy_intervals(nodes, slots, end)
    result = {}
    for rank in range(8):
        pending = merge_intervals((n['dependency_ready_ns'], n['start_ns']) for n in nodes
                                 if n['rank_id'] == rank and n['kind'] == 'SEND')
        full = merge_intervals((r['start_ns'], r['end_ns']) for r in occupancy
                               if r['rank_id'] == rank and r['free_slots'] == 0)
        result[rank] = (pending, intersection(pending, full))
    return result


def base_windows(observed):
    validate_observed_storage(observed)
    public = read(observed/'public_config.json')
    require(public['rank_ids'] == list(range(8)) and len(public['ports']) == 32, 'Need 8 ranks and all 32 ports')
    for p in public['ports']:
        require(p['direction'] == 'egress' and p['period_ns'] == BASE_NS and
                p['start_local_ns'] == 0 and p['end_local_ns'] >= END_NS, 'Incomplete base coverage')
    ports = [(p['switch_id'], p['port_id']) for p in public['ports']]
    require(len(set(ports)) == 32, 'Repeated port')
    ordinal = {p: i for i, p in enumerate(ports)}
    data = [[[0]*6 for _ in ports] for _ in range(END_NS//BASE_NS)]
    for name, fields, offset in (('port_windows.csv', COUNT_FIELDS, 0), ('queue_windows.csv', QUEUE_FIELDS, 2)):
        for row in stored_rows(observed, name):
            a, b = row['local_window_start_ns'], row['local_window_end_ns']
            if a >= END_NS: continue
            require(0 <= a < b <= END_NS and a % BASE_NS == 0 and b-a == BASE_NS, 'Misaligned base window')
            values = data[a//BASE_NS][ordinal[row['switch_id'], row['port_id']]]
            for i, field in enumerate(fields): values[offset+i] = row[field]
    return ports, data


def aggregate(data, a, b):
    require(0 <= a < b <= END_NS and a % BASE_NS == b % BASE_NS == 0, 'Invalid aggregate boundary')
    out = [[0]*6 for _ in range(len(data[0]))]
    for values in data[a//BASE_NS:b//BASE_NS]:
        for source, dest in zip(values, out):
            for k in (0, 1, 2, 4): dest[k] += source[k]
            for k in (3, 5): dest[k] = max(dest[k], source[k])
    return out


def static_topology(root, folder):
    network = root/'resolved/network'
    with (network/'node.csv').open() as stream:
        nodes = [[int(r['nodeId']), r['nodeType'], int(r['portNum']),
                  int(r['allocationDelay'].removesuffix('ns')), int(r['forwardDelay'].removesuffix('ns'))]
                 for r in csv.DictReader(stream)]
    with (network/'topology.csv').open() as stream:
        links = [[int(r['nodeId1']), int(r['portId1']), int(r['nodeId2']), int(r['portId2']),
                  int(r['bandwidth'].removesuffix('bps')), int(r['delay'].removesuffix('ns'))]
                 for r in csv.DictReader(stream)]
    groups = read(folder/'fabric.json')['groups']
    rank_nodes = [[r, n] for g in groups for r, n in zip(g['rank_ids'], g['rank_to_ub_node'])]
    return dict(nodes=nodes, links=links, rank_nodes=rank_nodes)


def prepare(manifest, output):
    manifest, output = Path(manifest).resolve(), Path(output).resolve()
    spec = read(manifest)
    with_states = spec.get('schema_version') == 'scaleup-send-state-sources/1'
    require(set(spec) == {'schema_version', 'cases'} and (with_states or spec['schema_version'] == 'scaleup-observation-sources/1'),
            'Invalid source manifest')
    require(0 < len(spec['cases']) <= 8, 'Need at most eight source cases')
    conditions = CONDITIONS
    if with_states:
        from .send_state_prepare import load_states, snapshot, point_truth
        from .send_state_spec import STATE_CONDITIONS, CONDITION, STATE_FIELDS, SAMPLING, ROLES
        conditions = STATE_CONDITIONS
        require(len(spec['cases']) == 4, 'State study requires two fixed input pairs')
    truth_dir = output/'truth'; truth_dir.mkdir()
    streams = {}
    for condition in conditions:
        folder = output/'observed'/condition; folder.mkdir(parents=True)
        streams[condition] = (folder/'windows.jsonl').open('x')
    bindings = []; platforms = []; backends = set(); seen = set(); pairs = defaultdict(dict)
    declared_ports = declared_topology = None
    try:
        with (truth_dir/'windows.jsonl').open('x') as truth_out:
            for index, ref in enumerate(spec['cases']):
                require(set(ref) == {'batch', 'case_id', 'role'} and ref['role'] in
                        (('reference', 'holdout') if with_states else ('development', 'holdout')),
                        'Invalid source binding')
                batch = (manifest.parent/ref['batch']).resolve()
                key = (str(batch), ref['case_id']); require(key not in seen, 'Repeated source case'); seen.add(key)
                case, folder = verified_case(batch, ref['case_id'])
                require(case['requests_per_rank'] == 8 and case['request_count'] == 64, 'Need B8 inputs')
                if with_states:
                    require(case['input_set'] in ROLES and ref['role'] == ROLES[case['input_set']], 'State input role mismatch')
                else:
                    require((case['input_set'] in (0, 1, 2) and ref['role'] == 'development') or
                            (case['input_set'] == 3 and ref['role'] == 'holdout'), 'Input role mismatch')
                done, summary = verify_complete(batch, case)
                root = batch/'runs'/case['case_id']
                require(0 < summary['round_ns'] <= END_NS, 'Execution exceeds fixed study interval; no extension')
                clocks = read(folder/'observation.json')['clocks']
                require(all(c['offset_ns'] == c['drift_ppb'] == 0 for c in clocks), 'Study requires synchronous clocks')
                platforms.append(read(root/'run/manifest.json')['software']['source_hashes'])
                backends.add(done['backend_sha256'])
                slots = case.get('send_slots_per_rank', 1)
                sample = f'sample-{index:03}'
                stats, nodes, _, plan = analyze_case(root, slots, truth_dir/sample)
                pair = pairs[case['input_set']]
                require(slots not in pair, 'Duplicate slot condition for input')
                pair[slots] = (folder, plan)
                ranks = rank_truth(nodes, slots, summary['round_ns'])
                ports, data = base_windows(root/'run/observed')
                if with_states: states = load_states(root/'run/observed')
                topology = static_topology(root, folder)
                if declared_ports is None: declared_ports = ports
                if declared_topology is None: declared_topology = topology
                require(ports == declared_ports, 'Public port set differs')
                require(topology == declared_topology, 'Static topology differs')
                totals = aggregate(data, 0, END_NS)
                for period, origin in GRIDS:
                    sums = [[0]*6 for _ in ports]
                    for a, b in windows(period, origin):
                        key = dict(sample_id=sample, grid=grid_id(period, origin), start_ns=a, end_ns=b)
                        values = aggregate(data, a, b)
                        for source, dest in zip(values, sums):
                            for k in (0, 1, 2, 4): dest[k] += source[k]
                            for k in (3, 5): dest[k] = max(dest[k], source[k])
                        for condition, stream in streams.items():
                            exposed = [p[:2] for p in values] if condition == 'counts' else values
                            row = dict(**key, ports=exposed)
                            if with_states and condition == CONDITION: row['rank_states'] = snapshot(states, a)
                            stream.write(json.dumps(row, separators=(',', ':'))+'\n')
                        labels = []
                        for rank in range(8):
                            pending, full = ranks[rank]
                            wait, blocked = overlap(pending, a, b), overlap(full, a, b)
                            require(0 <= blocked <= wait <= b-a, 'Truth duration partition failed')
                            labels.append([blocked, wait-blocked, b-a-wait])
                        row = dict(**key, ranks=labels, active_ns=max(0, min(b, summary['round_ns'])-a))
                        if with_states: row['point_states'] = point_truth(ranks, a)
                        truth_out.write(json.dumps(row, separators=(',', ':'))+'\n')
                    require(sums == totals, 'Coarsened count/area/peak conservation failed')
                bindings.append(dict(sample_id=sample, role=ref['role'], input_set=case['input_set'],
                    send_slots_per_rank=slots, round_ns=summary['round_ns'], source=key_from_ref(ref, batch),
                    completion=done, batch_sha256=digest(batch/'batch.json'),
                    observed_storage_sha256=digest(root/'run/observed/storage.json'),
                    reconstructed_wait_node_ns=stats['send_wait_total_node_ns'],
                    reconstructed_wait_rank_ns=sum(overlap(p, 0, END_NS) for p, _ in ranks.values()),
                    full_slot_wait_rank_ns=sum(overlap(f, 0, END_NS) for _, f in ranks.values())))
    finally:
        for stream in streams.values(): stream.close()
    require(len(backends) == 1 and all(p == platforms[0] for p in platforms), 'Platform/backend differs across cases')
    if with_states: require(set(pairs) == set(ROLES), 'State input sets differ from frozen method')
    for pair in pairs.values():
        require(set(pair) == {1, 2}, 'Each input must contain the one/two-slot pair')
        verify_pair_inputs(pair[1][0], pair[2][0])
        require(set_slots(pair[1][1], 2) == pair[2][1], 'Paired executed graphs differ beyond slots')
    observed_hashes = {}
    for condition in conditions:
        folder = output/'observed'/condition
        public = dict(schema_version='scaleup-observation-public/1', condition=condition,
            rank_ids=list(range(8)), port_ids=declared_ports,
            topology=declared_topology,
            fields=COUNT_FIELDS+(QUEUE_FIELDS if condition != 'counts' else ()),
            interval_ns=[0, END_NS], grids=GRIDS, sample_ids=[b['sample_id'] for b in bindings],
            windows_sha256=digest(folder/'windows.jsonl'))
        if with_states and condition == CONDITION:
            public.update(schema_version='scaleup-send-state-public/1', state_fields=STATE_FIELDS, state_sampling=SAMPLING)
        save(folder/'manifest.json', public)
        observed_hashes[condition] = digest(folder/'manifest.json')
    save(truth_dir/'manifest.json', dict(schema_version='scaleup-observation-truth/2' if with_states else 'scaleup-observation-truth/1',
        label_fields=['slot_full_wait_ns', 'other_ready_wait_ns', 'no_pending_ready_send_ns'],
        sources_sha256=digest(manifest), implementation=sources(), bindings=bindings,
        observed_manifest_sha256=observed_hashes, windows_sha256=digest(truth_dir/'windows.jsonl'),
        qualification='Explicit-dependency reconstruction verified against SEND submit/release; not direct ready trace'))


def key_from_ref(ref, batch):
    return dict(batch=str(batch), case_id=ref['case_id'])


if __name__ == '__main__': prepare(*sys.argv[1:])
