"""The only input contract used by analysis; no imports from preparation/evaluation."""
from pathlib import Path

from .common import digest, read, require, rows

ENDPOINT = {'rank_id', 'local_record_id', 'kind', 'start_local_ns', 'end_local_ns'}
STAGE = {'rank_id', 'local_record_id', 'stage', 'time_local_ns', 'event_count'}
WINDOW = {'switch_id', 'port_id', 'direction', 'local_window_start_ns', 'local_window_end_ns'}
COUNTER = WINDOW | {'counted_packets', 'counted_packet_bytes'}
QUEUE = WINDOW | {f'{kind}_waiting_{metric}_bytes' for kind in ('voq', 'fifo') for metric in ('mean', 'peak')}
RELATION = WINDOW | {'rank_id', 'local_record_id'}
CONDITIONS = {'E1', 'E2', 'E3', 'E4', 'E5', 'E3S', 'E5S'}
STAGES = {'prepare_start', 'prepare_end', 'local_prepare_start', 'local_prepare_end',
          'request_submit_first', 'request_submit_last', 'data_ready', 'initiator_resources_complete',
          'local_publish_first', 'local_publish_last', 'local_resources_complete', 'operation_complete'}


def tables(condition):
    require(condition in CONDITIONS, 'Unknown information condition')
    result = {'endpoint': ENDPOINT, 'stages': STAGE}
    if condition != 'E1':
        result['ports'] = COUNTER
    if condition in {'E4', 'E5', 'E5S'}:
        result['queues'] = QUEUE
    if condition in {'E3', 'E5', 'E3S', 'E5S'}:
        result['relations'] = RELATION
    return result


def record_key(row):
    return row['rank_id'], row['local_record_id']


def port_key(row):
    return row['switch_id'], row['port_id']


def window_key(row):
    return row['switch_id'], row['port_id'], row['local_window_start_ns'], row['local_window_end_ns']


def validate(directory):
    """Check exact names/fields, scope, references, hashes and finite table sizes."""
    directory = Path(directory)
    require(directory.is_dir() and not directory.is_symlink(), 'Input must be a real directory')
    manifest = read(directory/'condition.json')
    require(set(manifest) == {'schema_version', 'condition', 'files'} and
            manifest['schema_version'] == 'mapping-value-input/1', 'Unexpected input manifest fields')
    names = tables(manifest['condition'])
    expected = {'public.json'} | {prefix+name+'.jsonl' for prefix in ('', 'reference_') for name in names}
    require(set(manifest['files']) == expected, 'Input table whitelist differs from condition')
    require({p.name for p in directory.iterdir()} == expected | {'condition.json'}, 'Unexpected input files/directories')
    for name, sha in manifest['files'].items():
        path = directory/name
        require(path.is_file() and not path.is_symlink(), 'Input must contain regular files, without symlinks')
        require(path.stat().st_size <= 512*2**20, 'Input file budget exceeded')
        require(digest(path) == sha, f'Input digest mismatch: {name}')
    public = read(directory/'public.json')
    require(set(public) == {'schema_version', 'group_id', 'ranks', 'anchor_rank', 'rank_nodes',
        'nodes', 'links', 'ports', 'queue_ports', 'clock_relation', 'time_tolerance_ns',
        'material_round_absolute_ns', 'material_round_relative', 'analysis_version'}, 'Unexpected public fields')
    require(public['schema_version'] == 'mapping-value-public/1' and
            public['clock_relation'] == 'shared_accurate_ns' and
            public['analysis_version'] == 'candidate-evidence-v1', 'Unsupported public input semantics')
    ranks = public['ranks']
    require(ranks and all(type(r) is int and r >= 0 for r in ranks) and ranks == sorted(set(ranks)) and
            public['anchor_rank'] == min(ranks), 'Invalid public group/anchor')
    require(set(public['rank_nodes']) == {str(r) for r in ranks} and
            all(type(n) is int and n >= 0 for n in public['rank_nodes'].values()), 'Invalid endpoint placement')
    for node in public['nodes']:
        require(set(node) == {'node_id', 'kind', 'ports'} and node['kind'] in {'DEVICE', 'SWITCH'}, 'Invalid public node')
        require(type(node['node_id']) is int and type(node['ports']) is int and node['ports'] > 0, 'Invalid node values')
    for link in public['links']:
        require(set(link) == {'node_a', 'port_a', 'node_b', 'port_b', 'rate_bps', 'delay_ns'} and
                all(type(v) is int and v >= 0 for v in link.values()) and link['rate_bps'] > 0, 'Invalid public link')
    for name in ('time_tolerance_ns', 'material_round_absolute_ns'):
        require(type(public[name]) is int and public[name] >= 0, 'Invalid time threshold')
    require(type(public['material_round_relative']) in (float,int) and
            0 <= public['material_round_relative'] <= 1, 'Invalid relative threshold')
    ports = {}
    for port in public['ports']:
        require(set(port) == {'switch_id','port_id','direction','start_local_ns','end_local_ns','period_ns'} and
                port['direction'] == 'egress', 'Invalid public port')
        require(all(type(v) is int and v >= 0 for k,v in port.items() if k != 'direction') and
                port['period_ns'] > 0 and port['end_local_ns'] > port['start_local_ns'], 'Invalid port range')
        require(port_key(port) not in ports, 'Repeated public port')
        ports[port_key(port)] = port
    queue_ports = {tuple(p) for p in public['queue_ports']}
    require(queue_ports <= ports.keys(), 'Queue scope exceeds captured ports')
    for prefix in ('', 'reference_'):
        endpoints = {}
        for row in rows(directory/(prefix+'endpoint.jsonl')):
            require(set(row) == ENDPOINT and row['rank_id'] in ranks and row['kind'] in ('dispatch','combine'), 'Invalid endpoint fields')
            require(all(type(row[k]) is int and row[k] >= 0 for k in ENDPOINT-{'kind'}) and
                    row['local_record_id'] > 0 and row['end_local_ns'] >= row['start_local_ns'], 'Invalid endpoint interval')
            key = record_key(row); require(key not in endpoints, 'Duplicate endpoint'); endpoints[key] = row
        require(endpoints, 'Missing endpoint observations')
        for name, fields in names.items():
            if name == 'endpoint':
                continue
            seen = set(); stage_index = {}
            for row in rows(directory/(prefix+name+'.jsonl')):
                require(set(row) == fields, f'Forbidden or missing {name} fields')
                for key,value in row.items():
                    if key not in ('stage','direction'):
                        require(type(value) in (int,float) and value >= 0 and value < float('inf'), 'Invalid numeric record')
                        if not key.endswith('_mean_bytes'):
                            require(type(value) is int, 'Expected integer record field')
                if name in ('stages','relations'):
                    require(record_key(row) in endpoints, 'Unknown endpoint reference')
                if name == 'stages':
                    require(row['stage'] in STAGES and row['event_count'] > 0, 'Unknown stage/count')
                    key = (*record_key(row),row['stage']); stage_index[key] = row['time_local_ns']
                else:
                    require(port_key(row) in ports and row['direction']=='egress', 'Unknown port/direction')
                    p = ports[port_key(row)]; left = row['local_window_start_ns']; right = row['local_window_end_ns']
                    require(p['start_local_ns'] <= left < p['end_local_ns'] and
                            (left-p['start_local_ns']) % p['period_ns'] == 0 and
                            right == min(left+p['period_ns'],p['end_local_ns']), 'Window outside declared coverage')
                    key = window_key(row)
                    if name == 'relations':
                        require(row['rank_id'] == public['anchor_rank'], 'Relation must use the public anchor')
                        key = (*record_key(row),*key)
                    if name == 'queues':
                        require(port_key(row) in queue_ports, 'Queue fields beyond selected ports')
                        for kind in ('voq','fifo'):
                            require(row[f'{kind}_waiting_mean_bytes'] <= row[f'{kind}_waiting_peak_bytes'], 'Queue mean exceeds peak')
                    if name == 'ports':
                        require(row['counted_packets'] > 0 and row['counted_packet_bytes'] >= row['counted_packets'], 'Invalid sparse counter')
                require(key not in seen, f'Duplicate {name} record'); seen.add(key)
            if name == 'stages':
                for key,ep in endpoints.items():
                    require(stage_index.get((*key,'operation_complete')) == ep['end_local_ns'], 'Missing stage completion')
                    for a,b in [('prepare_start','prepare_end'),('local_prepare_start','local_prepare_end')]:
                        require(((*key,a) in stage_index) == ((*key,b) in stage_index), 'Incomplete preparation stage')
                        if (*key,a) in stage_index:
                            require(stage_index[(*key,a)] <= stage_index[(*key,b)], 'Invalid preparation stage order')
    return public, manifest
