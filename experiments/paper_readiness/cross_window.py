"""Fixed two-window persistent-QP mechanism probe; public byte execution only."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil

from feedback_parameters import run as public_run
from prefill_pressure import project

ROOT = Path(__file__).resolve().parents[2]


def load(p): return json.loads(p.read_text())
def save(p, v): p.write_text(json.dumps(v, ensure_ascii=False, indent=2)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def records(block, column_key, value_key):
    return [dict(zip(block[column_key], row)) for row in block[value_key]]


def prepare(a):
    base, out = a.baseline.resolve(), a.output.resolve()
    assert not out.exists()
    old_index = load(base/'input/observed/index.json')
    for name, item in old_index['artifacts'].items():
        assert sha(base/'input/observed'/name) == item['sha256']
    original = []
    manifest = load(a.manifest)
    for src, item in enumerate(manifest['files']):
        p = ROOT/item['file']['path']; assert sha(p) == item['file']['sha256']
        for choices in load(p)[0]['4'][:32]:
            for dst in sorted({e//64 for e in choices}-{src}):
                original.append(dict(id=len(original), stream=src*4+dst, bytes=14464, after=[]))
    assert len(original) == 180
    out.mkdir(); observed = out/'observed'; observed.mkdir(); (out/'truth').mkdir()
    save(out/'truth/manifest.json', manifest)
    shutil.copyfile(Path(__file__).with_name('cross_window_protocol.md'), out/'protocol.md')
    for name in ('topology.json', 'protocol-p001.json'):
        shutil.copyfile(base/'input/observed'/name, observed/name)
    reference = observed/'reference'; reference.mkdir(); cases = []
    previous = load(base/'analysis/summary.json')
    binary_sha = load(base/'run/execution.json')['backend_sha256']
    for depth in (1, 8, 1024):
        name = f'd{depth}-ai40-p001'; cold = load(base/'input/observed'/(name+'.json'))
        assert cold['work'] == original
        result = base/'run'/name/'result.json'
        assert sha(result) == next(c for c in previous['cases'] if c['name'] == name)['result_sha256']
        inputs = base/'run'/name/'inputs.json'
        if not inputs.exists(): inputs = inputs.with_name('prior-inputs.json')
        prior = load(inputs); cmd = prior['command']
        assert prior['hashes'][cmd[0]] == binary_sha
        for i, p in enumerate((base/'run/topology/topology.json', observed/'protocol-p001.json', base/'input/observed'/(name+'.json')), 1):
            assert prior['hashes'][cmd[i]] == sha(p)
        for suffix, p in (('result', result), ('work', base/'input/observed'/(name+'.json')), ('inputs', inputs)):
            shutil.copyfile(p, reference/(name+'-'+suffix+'.json'))
        work = deepcopy(cold); work['limits']['max_work'] = 360
        tails = {w['stream']: w['id'] for w in original}
        dependencies = [dict(work=tails[sid], event='acked') for sid in sorted(tails)]
        work['work'] += [dict(w, id=w['id']+180, after=deepcopy(dependencies)) for w in original]
        save(observed/(name+'.json'), work)
        cases.append(dict(name=name, depth=depth, protocol='protocol-p001.json', baseline_case=None,
                          cold_result_sha256=sha(result)))
    save(observed/'index.json', dict(schema_version='moe-cross-window/1', cases=cases,
        backend_sha256=binary_sha, work_per_window=180, windows=2,
        artifacts={str(p.relative_to(observed)): dict(sha256=sha(p), bytes=p.stat().st_size)
                   for p in sorted(observed.rglob('*')) if p.is_file()},
        independent_requests=False, modern_provider_executed=False, hardware_calibrated=False))
    print(json.dumps(dict(prepared=True, conditions=3, works_per_condition=360)))


def run(a):
    assert sha(a.backend) == load(a.observed/'index.json')['backend_sha256']
    a.reuse_baseline = None
    public_run(a)


def analyze(a):
    assert not a.output.exists()
    index = load(a.observed/'index.json'); execution = load(a.run/'execution.json')
    physical = load(a.run/'topology/topology.json'); cases = []
    for entry in execution['executed']:
        name = entry['name']; cfg = load(a.observed/(name+'.json'))
        path = a.run/name/'result.json'
        if not path.exists():
            cases.append(dict(name=name, status='failed_no_result', exit_code=entry['exit_code'])); continue
        r = load(path); t = r['persistent_transport']; events = t['dcqcn_reference']['events']
        cold = load(a.observed/'reference'/(name+'-result.json')); ct = cold['persistent_transport']
        works = {w['id']: w for w in t['work']}; complete = r['passed'] and r['drained']
        first = [w for wid, w in works.items() if wid < 180]
        boundary = max(w['acked_ns'] for w in first) if all(w['acked_ns'] >= 0 for w in first) else None
        source = records(t, 'source_packet_columns', 'source_packets')
        samples = records(t['source_rate_capture'], 'columns', 'samples')
        endpoints = records(r['packet_capture'], 'columns', 'events')
        before = lambda rows: [e for e in rows if e['time_ns'] < boundary]
        cold_source = records(ct, 'source_packet_columns', 'source_packets')
        prefix = None
        if boundary is not None:
            prefix = dict(work_callbacks=first == ct['work'],
                source=before(source) == before(cold_source),
                endpoint_capture=before(endpoints) == before(records(cold['packet_capture'], 'columns', 'events')),
                rate_capture=before(samples) == before(records(ct['source_rate_capture'], 'columns', 'samples')),
                controller=before(events) == before(ct['dcqcn_reference']['events']))
        # Static service uses one window's exact input. No future runtime state is read.
        single = deepcopy(cfg); single['work'] = single['work'][:180]
        projected, ports, _ = project(physical, single, 11)
        bound = float(max(Fraction((v['data']+v['ack'])*8*10**9, ports[k][1]) for k,v in projected.items()))
        windows = []
        for wi in (0, 1):
            ws = [w for wid,w in works.items() if wi*180 <= wid < (wi+1)*180]
            acked = len(ws) == 180 and all(w['acked_ns'] >= 0 for w in ws)
            ready = 0 if wi == 0 else boundary
            end = max(w['acked_ns'] for w in ws) if acked else None
            packets = [p for p in source if any(w['stream'] == p['stream'] and w['begin'] <= p['sequence'] < w['end'] for w in ws if w['begin'] >= 0)]
            uids = {p['packet_uid'] for p in packets}
            tx = [p for p in samples if p['event'] == 0 and p['packet_uid'] in uids]
            windows.append(dict(window=wi+1, ready_ns=ready, acked_ns=end,
                duration_ns=end-ready if end is not None and ready is not None else None,
                acked_work=sum(w['acked_ns'] >= 0 for w in ws), source_packets=len(packets),
                minimum_rate_at_tx_bps=min((p['rate_bps'] for p in tx), default=None),
                credit_delayed_work=sum(w['published_ns'] > w['ready_ns'] for w in ws),
                static_data_ack_service_bound_ns=bound))
        carry = []
        if boundary is not None:
            for s in cfg['streams']:
                sid = s['id']; prior = [e for e in events if e['stream'] == sid and e['time_ns'] < boundary]
                tx = [p for p in samples if p['stream'] == sid and p['event'] == 0 and p['time_ns'] >= boundary]
                cnp = [e for e in events if e['stream'] == sid and e['kind'] == 'cnp_received' and e['time_ns'] >= boundary]
                first_cnp = min((e['time_ns'] for e in cnp), default=None)
                before_cnp = [p for p in tx if first_cnp is None or p['time_ns'] < first_cnp]
                carry.append(dict(stream=sid, last_logged_before_boundary=prior[-1] if prior else None,
                    first_second_window_tx=tx[0] if tx else None, first_cnp_after_boundary_ns=first_cnp,
                    second_window_tx_before_any_post_boundary_cnp=len(before_cnp),
                    slow_tx_before_any_post_boundary_cnp=sum(p['rate_bps'] < 100000000000 for p in before_cnp)))
        generated = {e['packet_uid']: e for e in events if e['kind'] == 'cnp_generated'}
        cases.append(dict(name=name, depth=cfg['streams'][0]['queue_depth'], exit_code=entry['exit_code'],
            status='complete' if complete else 'censored', result_sha256=sha(path),
            boundary_ns=boundary, first_window_prefix=prefix, windows=windows, carry=carry,
            total_business_ack_ns=max(w['acked_ns'] for w in works.values()) if complete else None,
            physical_drain_ns=r['finish_ns'] if complete else None, observation_end_ns=r['finish_ns'],
            pre_boundary_generated_cnp_received_after_boundary=sum(e['kind']=='cnp_received' and boundary is not None and
                e['time_ns'] >= boundary and generated[e['packet_uid']]['time_ns'] < boundary for e in events),
            controller_event_counts=dict(Counter(e['kind'] for e in events)),
            cnp_after_boundary_generation_is_new_window_label=False))
    a.output.mkdir(); save(a.output/'summary.json', dict(cases=cases, unexecuted=execution['unexecuted'],
        analysis_reads_truth=False, new_network_runs=execution['new_network_runs'],
        synthetic_temporal_repetition=True, independent_requests=False, modern_provider_executed=False,
        hardware_calibrated=False, paper_main_contribution_established=False))
    print(json.dumps(dict(analyzed=True, cases=len(cases))))


def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='stage', required=True)
    q = sub.add_parser('prepare')
    for k in ('baseline', 'manifest', 'output'): q.add_argument('--'+k, type=Path, required=True)
    q = sub.add_parser('run')
    for k in ('observed', 'backend', 'output'): q.add_argument('--'+k, type=Path, required=True)
    q = sub.add_parser('analyze')
    for k in ('observed', 'run', 'output'): q.add_argument('--'+k, type=Path, required=True)
    a = p.parse_args(); globals()[a.stage](a)


if __name__ == '__main__': main()
