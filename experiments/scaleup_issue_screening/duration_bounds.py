"""Bound queue-nonempty duration from archived, sealed aggregate observations.

This is a researcher summary, not a reconstruction of continuous busy periods.
The input archive is read once, without restoring graphs or simulation outputs.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

from .analyze import distribution
from .workflow import digest, host, read, save


def window_bounds(area, peak, width):
    if min(area, peak) < 0 or width <= 0 or area > peak * width:
        raise ValueError('Inconsistent queue integral/peak/window')
    # Integer floor deliberately rounds outward, without assuming simulator
    # time resolution. A positive instantaneous peak may have zero area.
    return (area // peak if area else 0, width if area else 0)


def analyze(archive, output):
    archive, output = Path(archive), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    metadata = read(str(archive)+'.json')
    if digest(archive) != metadata['archive_sha256']:
        raise ValueError('Archive digest mismatch')
    names = ('run/observed/public_config.json', 'run/observed/storage.json',
             'run/observed/records/queue_windows.csv.gz', 'run/manifest.json',
             'run/metrics/validation.json', 'run/inputs/observation.json')
    extracted = {}
    with tarfile.open(archive, 'r|gz') as stream:
        for member in stream:
            if member.name not in names:
                continue
            if not member.isfile() or member.name in extracted:
                raise ValueError('Missing regular unique evidence member')
            data = stream.extractfile(member).read()
            expected = metadata['entries'][member.name]
            if len(data) != expected['bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
                raise ValueError('Evidence member differs from inventory')
            extracted[member.name] = data
    if set(extracted) != set(names):
        raise ValueError('Archive is missing required observations')
    public = json.loads(extracted[names[0]])
    storage = json.loads(extracted[names[1]])
    manifest = json.loads(extracted[names[3]])
    if not manifest['normal_exit'] or not json.loads(extracted[names[4]])['passed']:
        raise ValueError('Original run did not validate')
    if any(c['offset_ns'] or c['drift_ppb'] for c in json.loads(extracted[names[5]])['clocks']):
        raise ValueError('Duration report requires the declared zero-offset unit-rate clocks')
    from moe_ub_cosim.observation import validate_public_config
    _, ports, logical_windows = validate_public_config(public)
    if storage['window_encoding'] != 'nonzero_with_declared_zero_windows' or storage['logical_windows'] != logical_windows:
        raise ValueError('Missing complete sparse-zero declaration')
    configs = {(p['switch_id'],p['port_id']):p for p in ports}
    rows = {k:dict(switch_id=k[0],port_id=k[1],window_count=0,
        observation_start_ns=p['start_local_ns'],observation_end_ns=p['end_local_ns'],
        **{q+'_'+f:0 for q in ('voq','fifo') for f in
           ('nonempty_lower_ns','nonempty_upper_ns','positive_area_windows','max_positive_window_run_ns')})
        for k,p in configs.items()}
    previous = {}; run_start = {}; last_positive = {}; count = 0
    # Omitted windows are zero only under the sealed explicit sparse encoding.
    # No values are imputed outside the declared public port/time coverage.
    with gzip.GzipFile(fileobj=io.BytesIO(extracted[names[2]])) as stream:
        for line in stream:
            r = json.loads(line); count += 1
            key = r['switch_id'], r['port_id']; p = configs[key]; target = rows[key]
            left, right = r['local_window_start_ns'], r['local_window_end_ns']
            if (left < previous.get(key,p['start_local_ns']) or
                    (left-p['start_local_ns']) % p['period_ns'] or left >= p['end_local_ns'] or
                    right != min(left+p['period_ns'],p['end_local_ns'])):
                raise ValueError('Queue windows are duplicated, unaligned or unordered')
            previous[key] = right; target['window_count'] += 1
            for q in ('voq','fifo'):
                area, peak = r[q+'_waiting_byte_ns'], r[q+'_waiting_peak_bytes']
                lower, upper = window_bounds(area,peak,right-left)
                target[q+'_nonempty_lower_ns'] += lower
                target[q+'_nonempty_upper_ns'] += upper
                if area:
                    target[q+'_positive_area_windows'] += 1
                    tkey = key+(q,)
                    if last_positive.get(tkey) != left:
                        run_start[tkey] = left
                    last_positive[tkey] = right
                    target[q+'_max_positive_window_run_ns'] = max(
                        target[q+'_max_positive_window_run_ns'],right-run_start[tkey])
    if count != storage['fields']['queue_windows.csv']['records']:
        raise ValueError('Incomplete stored queue observations')
    table = output/'ports.jsonl'
    with table.open('w') as stream:
        for k in sorted(rows):stream.write(json.dumps(rows[k])+'\n')
    result = dict(schema_version='issue-screening-queue-duration-bounds/1',
        archive_sha256=metadata['archive_sha256'],input_members={n:metadata['entries'][n] for n in names},
        program_sha256=digest(__file__),ports=len(rows),stored_windows=count,windows=logical_windows,
        definition='per-port time with waiting bytes > 0, VOQ and FIFO separately',
        bound='sum floor(area/peak) <= nonempty time <= sum width of positive-area windows',
        precision='outward lower rounding < 1 ns per positive window; actual uncertainty is upper minus lower',
        continuous_backlog_reconstructed=False,device_queues_observed=False,
        positive_window_runs_are_continuous_backlog=False,complete_declared_coverage=True,
        distributions={q:{f:distribution([r[q+'_'+f] for r in rows.values()]) for f in
            ('nonempty_lower_ns','nonempty_upper_ns','max_positive_window_run_ns')} for q in ('voq','fifo')},
        ports_sha256=digest(table))
    save(output/'summary.json',result)
    print(json.dumps({k:result[k] for k in ('ports','windows','complete_declared_coverage')}))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:analyze(a.archive,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        report=probe([sys.executable,'-m',__spec__.name,'--worker','--archive',str(a.archive.resolve()),
            '--output',str(a.output.resolve())],str(a.output.resolve())+'-resources',**host(extra_roots=[a.output]))
        if report['status']!='completed':raise SystemExit('Duration analysis failed: '+report['status'])


if __name__=='__main__':main()
