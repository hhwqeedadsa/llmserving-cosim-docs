"""Classify sampled endpoint facts. File access is limited to the public input."""
import hashlib
import json
from pathlib import Path
import sys
import time

from .observation_measure import access_guard, check, sha, validate_topology
from .send_state_spec import (CONDITION, COUNT_FIELDS, QUEUE_FIELDS, END_NS, GRIDS, SAMPLING,
                             STATE_FIELDS, classify, grid_id, windows)


def measure(observed, output):
    observed, output = Path(observed).resolve(), Path(output).resolve()
    check({p.name for p in observed.iterdir()} == {'manifest.json', 'windows.jsonl'}, 'Only public package files are allowed')
    check(all(p.is_file() and not p.is_symlink() for p in observed.iterdir()), 'Public symlink forbidden')
    implementation = {name: sha(Path(__file__).with_name(name)) for name in (
        'send_state_measure.py','send_state_spec.py','observation_measure.py','observation_spec.py')}
    access_guard(observed, output); started = time.monotonic()
    public = json.loads((observed/'manifest.json').read_text())
    check(set(public) == {'schema_version','condition','rank_ids','port_ids','topology','fields','interval_ns',
                         'grids','sample_ids','windows_sha256','state_fields','state_sampling'}, 'Public manifest field violation')
    check(public['schema_version'] == 'scaleup-send-state-public/1' and public['condition'] == CONDITION and
          public['interval_ns'] == [0, END_NS] and public['grids'] == [list(g) for g in GRIDS] and
          public['state_fields'] == list(STATE_FIELDS) and public['state_sampling'] == SAMPLING,
          'State information condition or sampling differs')
    check(public['rank_ids'] == list(range(8)) and len(public['port_ids']) == 32 and
          all(isinstance(p,list) and len(p) == 2 and all(type(v) is int and v >= 0 for v in p) for p in public['port_ids']) and
          len({tuple(p) for p in public['port_ids']}) == 32, 'Invalid public ranks/ports')
    validate_topology(public['topology'], public['port_ids'])
    check(public['fields'] == list(COUNT_FIELDS+QUEUE_FIELDS), 'Port information fields differ')
    samples = public['sample_ids']
    check(samples == [f'sample-{i:03}' for i in range(len(samples))] and 0 < len(samples) <= 8, 'Invalid sample identities')
    check(sha(observed/'windows.jsonl') == public['windows_sha256'], 'Public windows changed')
    count = 0; prediction_hash = hashlib.sha256()
    with (observed/'windows.jsonl').open() as source, (output/'predictions.jsonl').open('x') as dest:
        for sample in samples:
            for period, origin in GRIDS:
                for a,b in windows(period, origin):
                    line = source.readline(); check(bool(line), 'Missing public window')
                    row = json.loads(line); key = dict(sample_id=sample,grid=grid_id(period,origin),start_ns=a,end_ns=b)
                    check(set(row) == {*key,'ports','rank_states'} and all(row[k] == v for k,v in key.items()),
                          'Unexpected fields or public window order')
                    check(len(row['ports']) == 32 and all(len(p) == 6 and all(type(v) is int and v >= 0 for v in p)
                          for p in row['ports']), 'Invalid port values')
                    values = row['rank_states']
                    check(len(values) == 8 and all(len(v) == 3 and all(type(n) is int and 0 <= n < 2**63 for n in v)
                          and v[1] <= v[2] < 2**32 and (v[2] or v[0] == 0) for v in values), 'Invalid endpoint counts')
                    states = [classify(v) for v in values]
                    prediction = dict(**key, ranks=[dict(selected=s == 'full_pending', state=s) for s in states])
                    encoded = json.dumps(prediction,separators=(',',':'))+'\n'; dest.write(encoded)
                    prediction_hash.update(encoded.encode()); count += 1
        check(not source.read(), 'Unexpected trailing public windows')
    result = dict(schema_version='scaleup-send-state-predictions/1', condition=CONDITION,
        observed_manifest_sha256=sha(observed/'manifest.json'), implementation=implementation,
        windows=count, port_window_records=count*32, rank_snapshots=count*8, peak_ports_per_window=32, peak_ranks_per_window=8,
        input_bytes=sum((observed/p).stat().st_size for p in ('manifest.json','windows.jsonl')),
        prediction_bytes=(output/'predictions.jsonl').stat().st_size, predictions_sha256=prediction_hash.hexdigest(),
        elapsed_seconds=time.monotonic()-started,
        cause_state='sampled_ready_and_occupancy_only; no_network_or_protocol_root_cause')
    (output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__ == '__main__': measure(*sys.argv[1:])
