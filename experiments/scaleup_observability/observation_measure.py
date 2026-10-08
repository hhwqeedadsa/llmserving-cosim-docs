"""Counts/queue candidate windows. Reads only the supplied public package."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from .observation_spec import CONDITIONS, COUNT_FIELDS, QUEUE_FIELDS, GRIDS, END_NS, grid_id, windows


def check(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def access_guard(observed, output):
    """Accidental file-access guard, not an adversarial OS sandbox. Worker only."""
    observed, output = Path(observed).resolve(), Path(output).resolve()
    check(not output.is_relative_to(observed) and not observed.is_relative_to(output),
          'Input and output must be disjoint')
    def audit(event, args):
        if event == 'open':
            name, mode, flags = args
            if isinstance(name, int):
                raise PermissionError('File descriptor reads are outside observed scope')
            path = Path(os.fsdecode(name)).resolve()
            writing = flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC)
            allowed = path.is_relative_to(output) if writing else path.is_relative_to(observed)
            if not allowed:
                raise PermissionError(f'Outside measurement file scope: {path}')
        if event in ('subprocess.Popen', 'os.system', 'socket.__new__'):
            raise PermissionError('External access is outside measurement scope')
    sys.addaudithook(audit)


def selected(condition, ports):
    if condition == 'counts':
        return any(p[1] > 0 for p in ports)
    return any(p[2] + p[4] > 0 for p in ports)


def validate_topology(topology, port_ids):
    check(set(topology) == {'nodes', 'links', 'rank_nodes'}, 'Static topology field violation')
    nodes = topology['nodes']
    check(nodes and all(len(n) == 5 and n[1] in ('DEVICE', 'SWITCH') and
          all(type(n[i]) is int and n[i] >= 0 for i in (0, 2, 3, 4)) for n in nodes), 'Invalid static nodes')
    by_id = {n[0]: n for n in nodes}
    check(len(by_id) == len(nodes), 'Repeated static node')
    check(all(n in by_id and by_id[n][1] == 'SWITCH' and 0 <= p < by_id[n][2] for n, p in port_ids),
          'Observed port missing from topology')
    mapping = topology['rank_nodes']
    check(len(mapping) == 8 and all(len(m) == 2 and all(type(v) is int for v in m) for m in mapping) and
          sorted(m[0] for m in mapping) == list(range(8)) and
          all(m[1] in by_id and by_id[m[1]][1] == 'DEVICE' for m in mapping), 'Invalid rank/node mapping')
    for link in topology['links']:
        check(len(link) == 6 and all(type(v) is int and v >= 0 for v in link) and link[4] > 0 and
              all(link[i] in by_id and link[i+1] < by_id[link[i]][2] for i in (0, 2)), 'Invalid static link')


def measure(observed, output):
    observed, output = Path(observed).resolve(), Path(output).resolve()
    check({p.name for p in observed.iterdir()} == {'manifest.json', 'windows.jsonl'},
          'Only the two public package files are allowed')
    check(all(p.is_file() and not p.is_symlink() for p in observed.iterdir()), 'Public symlink forbidden')
    implementation = {p.name: sha(p) for p in (Path(__file__), Path(__file__).with_name('observation_spec.py'))}
    access_guard(observed, output)
    started = time.monotonic()
    public = json.loads((observed/'manifest.json').read_text())
    check(set(public) == {'schema_version', 'condition', 'rank_ids', 'port_ids', 'topology', 'fields',
                         'interval_ns', 'grids', 'sample_ids', 'windows_sha256'}, 'Public manifest field violation')
    check(public['schema_version'] == 'scaleup-observation-public/1', 'Unsupported public package')
    condition = public['condition']
    check(condition in CONDITIONS and public['interval_ns'] == [0, END_NS] and
          public['grids'] == [list(g) for g in GRIDS], 'Information condition or interval differs')
    check(public['rank_ids'] == list(range(8)) and len(public['port_ids']) == 32 and
          all(isinstance(p, list) and len(p) == 2 and all(type(v) is int and v >= 0 for v in p)
              for p in public['port_ids']) and len({tuple(p) for p in public['port_ids']}) == 32,
          'Invalid rank/port declaration')
    validate_topology(public['topology'], public['port_ids'])
    fields = list(COUNT_FIELDS + (QUEUE_FIELDS if condition == 'counts-queues' else ()))
    check(public['fields'] == fields, 'Information fields differ')
    samples = public['sample_ids']
    check(samples == [f'sample-{i:03}' for i in range(len(samples))] and 0 < len(samples) <= 8,
          'Invalid sample identities')
    check(sha(observed/'windows.jsonl') == public['windows_sha256'], 'Public windows changed')
    count = 0
    prediction_hash = hashlib.sha256()
    with (observed/'windows.jsonl').open() as source, (output/'predictions.jsonl').open('x') as dest:
        for sample in samples:
            for period, origin in GRIDS:
                for a, b in windows(period, origin):
                    line = source.readline()
                    check(bool(line), 'Missing public window')
                    row = json.loads(line)
                    key = dict(sample_id=sample, grid=grid_id(period, origin), start_ns=a, end_ns=b)
                    check(set(row) == {*key, 'ports'} and all(row[k] == v for k, v in key.items()),
                          'Unexpected fields or public window order')
                    ports = row['ports']
                    check(len(ports) == 32 and all(len(p) == len(fields) and
                          all(type(v) is int and v >= 0 for v in p) for p in ports), 'Invalid port values')
                    prediction = dict(**key, selected=selected(condition, ports), state='undetermined')
                    encoded = json.dumps(prediction, separators=(',', ':'))+'\n'
                    dest.write(encoded)
                    prediction_hash.update(encoded.encode())
                    count += 1
        check(not source.read(), 'Unexpected trailing public windows')
    result = dict(schema_version='scaleup-observation-predictions/1', condition=condition,
        observed_manifest_sha256=sha(observed/'manifest.json'), implementation=implementation,
        windows=count, port_window_records=count*32, peak_ports_per_window=32,
        input_bytes=sum((observed/p).stat().st_size for p in ('manifest.json', 'windows.jsonl')),
        prediction_bytes=(output/'predictions.jsonl').stat().st_size,
        predictions_sha256=prediction_hash.hexdigest(),
        elapsed_seconds=time.monotonic()-started, cause_state='undetermined')
    (output/'summary.json').write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    measure(*sys.argv[1:])
