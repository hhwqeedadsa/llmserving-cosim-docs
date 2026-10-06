"""Read versioned, lossless bounded backend records without materializing a run."""
import gzip
import json
from pathlib import Path
import re
import zlib

from .plan import integer, require


def validate_policy(policy):
    fields = {'schema_version', 'max_compressed_bytes', 'max_buffer_bytes', 'max_record_bytes'}
    require(isinstance(policy, dict) and set(policy) == fields and
            policy['schema_version'] == 'moe-ub-record-storage/1', 'Invalid record storage policy')
    for key in fields - {'schema_version'}:
        integer(policy[key], key, 1, 2**63-1)
    require(policy['max_buffer_bytes'] <= 2**30 and policy['max_record_bytes'] <= 64*2**20 and
            policy['max_record_bytes'] + 65536 <= policy['max_buffer_bytes'], 'Invalid record buffer bounds')
    return policy


def iter_file(execution, ref, max_record_bytes=1048576, *, parse_json=True):
    require(isinstance(ref, dict) and set(ref) == {'name', 'records', 'raw_bytes', 'compressed_bytes', 'crc32'},
            'Invalid compressed record reference')
    require(isinstance(ref['name'], str) and re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.gz', ref['name']),
            'Invalid record file name')
    for key in ('records', 'raw_bytes', 'compressed_bytes'):
        integer(ref[key], key, 0, 2**63-1)
    integer(ref['crc32'], 'crc32', 0, 2**32-1)
    integer(max_record_bytes, 'max_record_bytes', 1, 64*2**20)
    path = Path(execution) / 'records' / ref['name']
    require(path.resolve().parent == (Path(execution) / 'records').resolve(), 'Record escapes its directory')
    require(path.stat().st_size == ref['compressed_bytes'], 'Compressed record size mismatch')
    count = raw_bytes = crc = 0
    with gzip.open(path, 'rb') as stream:
        while row := stream.readline(max_record_bytes + 1):
            require(len(row) <= max_record_bytes and row.endswith(b'\n') and b'\r' not in row,
                    'Oversized or incomplete compressed record')
            count += 1; raw_bytes += len(row); crc = zlib.crc32(row, crc)
            require(count <= ref['records'] and raw_bytes <= ref['raw_bytes'], 'Record totals exceeded')
            yield json.loads(row) if parse_json else row[:-1].decode('utf-8')
    require((count, raw_bytes, crc) == (ref['records'], ref['raw_bytes'], ref['crc32']), 'Record totals or CRC mismatch')


def iter_result(execution, field, report=None):
    execution = Path(execution)
    if report is None:
        report = json.loads((execution / 'result.json').read_text())
    if report['schema_version'] == 'moe-ub-service-result/1':
        yield from report[field]
        return
    require(report['schema_version'] == 'moe-ub-service-result/2', 'Unknown service result schema')
    storage = report['record_storage']
    require(storage['schema_version'] == 'moe-ub-result-records/1' and storage['directory'] == 'records',
            'Unknown result record storage')
    policy = validate_policy(storage['policy'])
    section = storage['fields'][field]
    require(set(section) == {'records', 'files'} and isinstance(section['files'], list), 'Invalid record parts')
    count = 0; names = set()
    for ref in section['files']:
        require(ref['name'] not in names, 'Repeated record part'); names.add(ref['name'])
        for row in iter_file(execution, ref, policy['max_record_bytes']):
            count += 1
            require(count <= section['records'], 'Result record count exceeded')
            yield row
    require(count == section['records'], 'Result record count mismatch')


def iter_port_records(execution, port, name):
    """Yield the original logical rows of old JSONL or new compressed/sparse files."""
    execution, port = Path(execution), Path(port)
    manifest_path = port / 'record-storage.json'
    if not manifest_path.exists():
        with (port / name).open() as stream:
            for line in stream:
                yield json.loads(line)
        return
    manifest = json.loads(manifest_path.read_text())
    require(manifest['schema_version'] in ('moe-ub-port-records/1','moe-ub-port-records/2') and
            manifest['path_base'] == 'execution_directory' and manifest['records_directory'] == 'records',
            'Unknown port record storage')
    if name in ('observed/endpoint_ops.jsonl', 'truth/operation_registry.jsonl', 'truth/transfer_registry.jsonl'):
        registry = json.loads((port / 'registry-records.json').read_text())
        require(registry['schema_version'] == 'moe-ub-observation-registry/2' and
                registry['path_base'] == 'execution_directory' and registry['result'] == 'result.json',
                'Unknown shared record registry')
        yield from iter_result(execution, registry['fields'][name])
    elif name in ('observed/port_counters.jsonl', 'truth/port_byte_truth.jsonl'):
        yield from _windows(execution, port, manifest, name)
    else:
        report = json.loads((execution / 'result.json').read_text())
        policy = validate_policy(report['record_storage']['policy'])
        yield from iter_file(execution, manifest['fields'][name], policy['max_record_bytes'])


def _windows(execution, port, manifest, name):
    require(manifest['window_encoding'] == 'sparse_nonzero_with_declared_zero_windows', 'Unknown window encoding')
    config = json.loads((port / 'observed/public_config.json').read_text())
    start, end, period = (config[k] for k in ('observation_start_local_ns', 'observation_end_local_ns', 'window_period_ns'))
    integer(start, 'window start', 0); integer(end, 'window end', start+1); integer(period, 'window period', 1)
    total = (end-start-1)//period+1
    require(total == manifest['logical_windows'], 'Logical window count mismatch')
    observed = name == 'observed/port_counters.jsonl'
    records = iter_file(execution, manifest['fields']['port-windows.jsonl' if observed else 'truth-windows.jsonl'])
    pending = next(records, None)
    for index in range(total):
        left, right = start + index*period, min(end, start + (index+1)*period)
        if observed:
            row = dict(switch_id=config['switch_id'], port_id=config['port_id'], direction='egress',
                local_window_start_ns=left, local_window_end_ns=right, counted_packets=0, counted_packet_bytes=0)
            fields = {'counted_packets', 'counted_packet_bytes'}
        else:
            row = dict(window_start_sim_ns=left, window_end_sim_ns=right, business_payload_bytes=0,
                application_metadata_bytes=0, data_protocol_overhead_bytes=0, control_packet_bytes=0, unattributed_bytes=0)
            fields = set(row)-{'window_start_sim_ns', 'window_end_sim_ns'} | {'padding_bytes'}
            if manifest['padding_field']:row['padding_bytes'] = 0
        if pending is not None:
            require(set(pending) == fields | {'index'} and type(pending['index']) is int and
                    index <= pending['index'] < total, 'Invalid or unordered sparse window')
            if pending['index'] == index:
                for key in fields:
                    integer(pending[key], key, 0)
                    if key != 'padding_bytes' or manifest['padding_field']:row[key] = pending[key]
                    else:require(pending[key] == 0, 'Unexpected padding in a legacy window')
                if observed:require(row['counted_packets'] > 0 and row['counted_packet_bytes'] > 0, 'Empty stored window')
                pending = next(records, None)
        yield row
    require(pending is None, 'Sparse window outside declared interval')
