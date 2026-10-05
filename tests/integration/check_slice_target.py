"""Streamed target-scale comparisons; never expand billions of zero windows."""
import argparse
from collections import Counter
from itertools import groupby, zip_longest
import json
from pathlib import Path

from moe_ub_cosim.plan_io import file_sha256
from moe_ub_cosim.record_storage import iter_file, iter_result
from moe_ub_cosim.research_storage import stored_rows


def read(path):
    return json.loads(path.read_text())


def equal_rows(left, right, label):
    absent = object(); count = 0
    for a, b in zip_longest(left, right, fillvalue=absent):
        assert a == b, (label, count, a, b)
        count += 1
    return count


def research_equal(left, right, scope, name):
    a = read(left/scope/'storage.json')['fields'][name]
    b = read(right/scope/'storage.json')['fields'][name]
    paths = [left/scope/'records'/a['name'], right/scope/'records'/b['name']]
    # Identical compressed bytes prove identical records. A different gzip
    # header/partition is not a failure: fall back to logical row comparison.
    if file_sha256(paths[0]) == file_sha256(paths[1]):
        assert a['records'] == b['records'] and a['raw_bytes'] == b['raw_bytes']
        return dict(records=a['records'], method='identical_compressed_bytes')
    return dict(records=equal_rows(stored_rows(left/scope, name), stored_rows(right/scope, name), (scope, name)),
                method='logical_record_comparison')


def coarsened(directory, period, *, attribution=False):
    public = read(directory/'observed/public_config.json')
    ports = {(p['switch_id'], p['port_id']): p for p in public['ports']}
    rows = stored_rows(directory/('truth' if attribution else 'observed'),
                       'attribution_windows.csv' if attribution else 'port_windows.csv')
    def key(row):
        port = ports[row['switch_id'], row['port_id']]
        assert period % port['period_ns'] == 0
        start = port['start_local_ns']
        left = start+((row['local_window_start_ns']-start)//period)*period
        return row['switch_id'], row['port_id'], left, min(left+period, port['end_local_ns'])
    for (switch, port, left, right), grouped in groupby(rows, key):
        bounds = dict(switch_id=switch, port_id=port, direction='egress',
                      local_window_start_ns=left, local_window_end_ns=right)
        if attribution:
            amounts = Counter()
            for row in grouped:
                amounts[row['operation_id']] += row['business_payload_bytes']
            for operation, amount in sorted(amounts.items()):
                yield dict(operation_id=operation, **bounds, business_payload_bytes=amount)
        else:
            packets = size = 0
            for row in grouped:
                packets += row['counted_packets']; size += row['counted_packet_bytes']
            yield dict(**bounds, counted_packets=packets, counted_packet_bytes=size)


def execution_equal(left, right):
    original = read(left/'result.json'); replay = read(right/'result.json'); fields = {}
    assert original['normal_exit'] and replay['normal_exit']
    assert original['network_drain']['idle'] and replay['network_drain']['idle']
    assert original['simulation_end_ns'] == replay['simulation_end_ns'] == 75323418
    assert replay['network_initializations'] == 1
    life = replay['resource_lifecycle']
    assert life['retired_graphs'] == 1
    assert all(life[key] == 0 for key in ('resident_graphs_at_exit', 'resident_transfers_at_exit', 'adapter_transfers_at_exit'))
    assert life['provenance']['resident_packets'] == life['provenance']['resident_transfers'] == 0
    assert len(replay['rank_resources']) == 128
    assert all(row[key] == 0 for row in replay['rank_resources']
               for key in ('compute_slots', 'cpu_slots', 'send_slots', 'pending_events', 'pending_tasks', 'stale_callbacks'))
    assert all(row['finished'] and not row['remaining_nodes'] for row in replay['rank_resources'])
    assert all(row['same_network_objects'] for row in replay['boundary_resources'])
    for name, entry in original['record_storage']['fields'].items():
        other = replay['record_storage']['fields'][name]
        assert entry['records'] == other['records']
        fast = len(entry['files']) == len(other['files']) and all(
            a['records'] == b['records'] and a['raw_bytes'] == b['raw_bytes'] and
            file_sha256(left/'records'/a['name']) == file_sha256(right/'records'/b['name'])
            for a, b in zip(entry['files'], other['files']))
        count = entry['records'] if fast else equal_rows(iter_result(left, name, original), iter_result(right, name, replay), name)
        fields[name] = dict(records=count, method='identical_compressed_bytes' if fast else 'logical_record_comparison')
    ports = 0; packet_files = 0
    for source in sorted((left/'ports').iterdir()):
        target = right/'ports'/source.name
        original_storage, replay_storage = read(source/'record-storage.json'), read(target/'record-storage.json')
        assert read(source/'observation-audit.json') == read(target/'observation-audit.json'), source.name
        for name in ('packet-events.jsonl', 'truth/packet_ranges.jsonl', 'egress-queue-audit.jsonl'):
            a, b = original_storage['fields'][name], replay_storage['fields'][name]
            if file_sha256(left/'records'/a['name']) != file_sha256(right/'records'/b['name']):
                equal_rows(iter_file(left, a), iter_file(right, b), (source.name, name))
            else:
                assert a['records'] == b['records'] and a['raw_bytes'] == b['raw_bytes']
            packet_files += 1
        ports += 1
    assert ports == 16128
    return dict(fields=fields, ports=ports, packet_files=packet_files,
                simulation_end_ns=replay['simulation_end_ns'], ranks_finished=len(replay['rank_resources']),
                network_initializations=replay['network_initializations'], network_idle=True,
                resource_lifecycle=life, exit_resources_cleared=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--historical-export', type=Path, required=True)
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--resampled', type=Path)
    p.add_argument('--replay', type=Path)
    p.add_argument('--original-raw', type=Path)
    p.add_argument('--output-dir', type=Path, required=True)
    a = p.parse_args(); out = a.output_dir.resolve(); out.mkdir(parents=True, exist_ok=False)
    assert read(a.baseline/'manifest.json')['normal_exit']
    results = dict(passed=False, baseline={}, resampled=None, replay=None)
    for scope in ('observed', 'truth', 'metrics'):
        for name in read(a.historical_export/scope/'storage.json')['fields']:
            results['baseline'][scope+'/'+name] = research_equal(a.historical_export, a.baseline, scope, name)
    old, current = read(a.historical_export/'metrics/validation.json'), read(a.baseline/'metrics/validation.json')
    for key in ('unique_delivery_bytes', 'transfers', 'endpoint_records', 'port_windows', 'zero_windows', 'port_audits'):
        assert old[key] == current[key], key
    assert current['transfers'] == 3741764 and len(current['port_audits']) == 16128
    if a.resampled:
        assert read(a.resampled/'manifest.json')['normal_exit']
        source, target = read(a.baseline/'observed/public_config.json'), read(a.resampled/'observed/public_config.json')
        assert len(source['ports']) == len(target['ports'])
        for before, after in zip(source['ports'], target['ports']):
            assert before['period_ns'] == 1000 and after == dict(before, period_ns=10000)
        result = {}
        result['coarsened_port_records'] = equal_rows(coarsened(a.baseline, 10000),
            stored_rows(a.resampled/'observed', 'port_windows.csv'), 'independent 10us port aggregation')
        result['coarsened_attribution_records'] = equal_rows(coarsened(a.baseline, 10000, attribution=True),
            stored_rows(a.resampled/'truth', 'attribution_windows.csv'), 'independent 10us attribution aggregation')
        for scope, name in [('observed', 'endpoint_ops.jsonl'), ('truth', 'operation_registry.jsonl'), ('truth', 'transfer_registry.jsonl')]:
            result[scope+'/'+name] = research_equal(a.baseline, a.resampled, scope, name)
        assert read(a.resampled/'metrics/validation.json')['port_audits'] == current['port_audits']
        results['resampled'] = result
    if a.replay:
        assert a.original_raw is not None and read(a.replay/'manifest.json')['normal_exit']
        results['replay'] = execution_equal(a.original_raw/'execution', a.replay/'raw/execution')
        for scope in ('observed', 'truth', 'metrics'):
            for name in read(a.baseline/scope/'storage.json')['fields']:
                research_equal(a.baseline, a.replay, scope, name)
    results['passed'] = True
    (out/'summary.json').write_text(json.dumps(results, indent=2)+'\n')
    print('PASS: completed target comparisons; see summary for the actual checked phases')


if __name__ == '__main__':
    main()
