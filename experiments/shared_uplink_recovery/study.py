"""Public-command comparison of mature recovery on a fixed shared Clos uplink."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import gc
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CASES = dict(global20=(20000, False), local20=(20000, True),
             global60=(60000, False), local60=(60000, True), global0=(0, False))


def load(p):
    return json.loads(Path(p).read_bytes())


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(p, value):
    with Path(p).open('x') as s:
        json.dump(value, s, ensure_ascii=False, indent=2)
        s.write('\n')


def hashes(p):
    return {str(f.relative_to(p)): sha(f) for f in sorted(p.rglob('*')) if f.is_file()}


def public(command, receipt):
    save(receipt, dict(command=list(map(str, command)), cwd=str(ROOT), status='started'))
    with receipt.with_suffix('.stdout.log').open('x') as out, receipt.with_suffix('.stderr.log').open('x') as err:
        r = subprocess.run(command, cwd=ROOT, stdout=out, stderr=err)
    v = load(receipt)
    v.update(exit_code=r.returncode, status='completed' if r.returncode == 0 else 'failed')
    receipt.write_text(json.dumps(v, indent=2)+'\n')
    if r.returncode:
        raise SystemExit(f'Failed command retained at {receipt}')


def normalize(c):
    c = deepcopy(c)
    c['route_replay']['manifest'] = '<independently-checked-route-identity>'
    return c


def route_identity(c, base):
    ref = c['route_replay']['manifest']
    path = base/ref['path']
    assert sha(path) == ref['sha256']
    manifest = load(path)
    for row in manifest['files']:
        assert sha(path.parent/row['file']['path']) == row['file']['sha256']
        row['file']['path'] = '<content-addressed>'
    return manifest


def prepare(a):
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    build = a.build.resolve()
    prov = load(build/'provenance.json')
    for p, h in prov['sources'].items():
        assert sha(ROOT/p) == h, p
    backend = build/'output/moe-rdma/ns3.42-moe-rdma-service'
    libs = sorted((build/'output/lib').glob('libns3*.so'))
    assert len(libs) == 8
    normal = a.normal.resolve()
    old = load(normal/'result.json')
    assert old['passed'] and old['drained'] and old['fault_drops'] == 0
    assert load(normal/'verification.json')['passed']
    p = out/'input'
    public([sys.executable, '-B', str(ROOT/'examples/scaleout_message_buffers/prepare.py'),
            '--source-manifest', str(a.source_manifest.resolve()), '--source-base', str(a.source_base.resolve()),
            '--route-files', '15', '--output', str(p)], out/'prepare-command.json')
    c = load(p/'service-buffers.json')
    c['inflight_helpers']['policy'] = 'observe_only/1'
    c['transport']['transport_recovery']['initial_rto_ns'] = 17000
    assert route_identity(c, ROOT) == route_identity(load(normal/'input.json'), normal)
    assert normalize(c) == normalize(load(normal/'input.json'))
    save(p/'service-fixed.json', c)
    topology = load(p/'topology/topology.json')
    assert topology == load(normal/'topology.json')
    edge = min((e for e in topology['links'] if e['tier'] == 'tor_agg'), key=lambda e: (e['src'], e['dst']))
    assert (edge['src'], edge['dst']) == (128, 132)
    base = load(p/'rdma-buffers.json')
    del base['data_loss']
    assert base == load(normal/'rdma.json')
    for name, (delay, local) in CASES.items():
        protocol = deepcopy(base)
        protocol['link_failures'] = dict(mode='switch_links/1', events=[dict(
            src=edge['src'], dst=edge['dst'], time_ns=4000, route_update_delay_ns=delay)])
        if local:
            protocol['local_failover'] = dict(mode='live_ecmp/1')
        save(p/(name+'.json'), protocol)
    routes = {row['file']['path']: row['file']['sha256'] for row in load(p/'buffer-source-manifest.json')['files']}
    assert len(routes) == len(set(routes.values())) == 15
    assert all(sha(p) == h for p, h in routes.items())
    sources = [*sorted((ROOT/'python/moe_ub_cosim').glob('*.py')),
               *sorted((ROOT/'examples').glob('*/prepare.py')), *sorted(Path(__file__).parent.glob('*.py')),
               Path(__file__).with_name('README.md')]
    save(out/'frozen.json', dict(schema_version='moe-shared-uplink-study/1', network_executed=False,
        platform_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        prepared=str(p), prepared_sha256=hashes(p), backend=str(backend), backend_sha256=sha(backend),
        compiled_sources_sha256=prov['sources'], libraries_sha256={str(x): sha(x) for x in libs},
        source_sha256={str(x.relative_to(ROOT)): sha(x) for x in sources}, routes_sha256=routes,
        normal=str(normal), normal_sha256={n: sha(normal/n) for n in
            ('input.json', 'rdma.json', 'topology.json', 'frontend.json', 'result.json', 'verification.json')},
        normal_graphs_sha256=hashes(normal/'graphs'), failed_link=edge, fault_time_ns=4000,
        conditions={n: dict(route_update_delay_ns=d, local_failover=l) for n, (d, l) in CASES.items()},
        max_new_networks=5, budget=dict(monitored_seconds=1200, artifact_bytes=2*2**30, min_free_bytes=8*2**30)))
    print(json.dumps(dict(prepared=True, network_executed=False, failed_link=edge, requests=15)))


def checked(study):
    f = load(study/'frozen.json')
    assert sha(f['backend']) == f['backend_sha256']
    for group in ('compiled_sources_sha256', 'source_sha256'):
        for p, h in f[group].items():
            assert sha(ROOT/p) == h, p
    for group in ('routes_sha256', 'libraries_sha256'):
        for p, h in f[group].items():
            assert sha(p) == h, p
    for p, h in f['normal_sha256'].items():
        assert sha(Path(f['normal'])/p) == h, p
    assert hashes(Path(f['prepared'])) == f['prepared_sha256']
    return f


def run(a):
    study = a.study.resolve()
    f = checked(study)
    p = Path(f['prepared'])
    assert not (study/a.case).exists()
    assert len([n for n in CASES if (study/n).exists()]) < f['max_new_networks']
    public([sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', 'rdma-run',
            '--config', str(p/'service-fixed.json'), '--protocol', str(p/(a.case+'.json')),
            '--topology', str(p/'topology'), '--backend', f['backend'],
            '--protoc', str(a.protoc.resolve()), '--output', str(study/a.case)], study/(a.case+'-command.json'))
    print(json.dumps(dict(case=a.case, completed=True, result_sha256=sha(study/a.case/'result.json'))))


def summary(directory, edge):
    r = load(directory/'result.json')
    assert r['passed'] and r['drained'] and r['gpu_count'] == 128 and r['switch_count'] == 20
    assert load(directory/'verification.json')['passed']
    n = r['service_transport']['native']
    assert n['physical_drained'] and n['closed'] and n['inflight_packets'] == 0
    assert n['recovery']['complete'] and not n['recovery']['failed']
    assert r['service_transport']['message_buffers']['used_bytes'] == n['message_payload']['retained_bytes'] == 0
    assert not r['service']['inflight_helpers']['attempts']
    cap = r['packet_capture']
    assert cap['complete'] and cap['omitted_events'] == 0
    assert r['queue_observations'] == len(r['queue_events'])
    c = cap['columns']
    packets = [dict(zip(c, row)) for row in cap['events']]
    source_cols = n['source_packet_columns']
    high = defaultdict(int)
    total = repeats = 0
    for row in n['source_packets']:
        p = dict(zip(source_cols, row))
        start = p['sequence']
        end = start+p['payload_bytes']
        sid = p['stream']
        assert start <= high[sid]
        repeats += max(0, min(end, high[sid])-start)
        high[sid] = max(high[sid], end)
        total += p['payload_bytes']
    messages = r['service_transport']['messages']
    logical = sum(m['bytes'] for m in messages)
    assert total-logical == repeats and sum(high.values()) == logical
    assert all(m['ready_ns'] <= m['published_ns'] <= m['receive_ready_ns'] <= m['ack_ns'] for m in messages)
    streams = {s['id']: s for s in n['streams']}
    source_groups = {s['id']: s['src'] % 4 for s in n['streams']}
    data = cap['protocol_codes']['data']
    codes = cap['event_codes']
    loss_codes = {codes[k] for k in ('drop', 'local_drop') if k in codes}
    drops = [p for p in packets if p['event'] in loss_codes]
    by_group = Counter(source_groups[p['message_id']] for p in drops if p['message_id'] in source_groups)
    by_protocol = Counter(p['ip_protocol'] for p in drops)
    endpoints = {(edge['src'], edge['src_port']), (edge['dst'], edge['dst_port'])}
    link_packets = [p for p in packets if p['event'] == codes['tx'] and
                    (p['node'], p['port']) in endpoints]
    link_groups = Counter(source_groups[p['message_id']] for p in link_packets
                          if p['ip_protocol'] == data and p['message_id'] in source_groups)
    link_group_bytes = Counter()
    for p in link_packets:
        if p['ip_protocol'] == data:
            link_group_bytes[source_groups[p['message_id']]] += p['data_payload_bytes']
    timeouts = n['recovery']
    cols = timeouts['columns']
    retries = [dict(zip(cols, row)) for row in timeouts['events']
               if row[cols.index('event')] == timeouts['event_codes']['timeout_retransmit']]
    history = load(directory/'frontend.json')['history']
    groups = {str(i): max(h['finished_ns'] for h in history if h['group_index'] == i) for i in range(4)}
    gates = {g['graph_id']+'.'+g['id']: g['released_ns'] for g in r['service']['phase_completion']['gates']}
    ports = defaultdict(lambda: dict(data_model_bytes=0, data_payload_bytes=0, packets=0, groups=Counter()))
    for p in packets:
        if p['event'] != codes['tx'] or p['node'] < 128 or p['ip_protocol'] != data:
            continue
        v = ports[str(p['node'])+':'+str(p['port'])]
        v['data_model_bytes'] += p['model_bytes']
        v['data_payload_bytes'] += p['data_payload_bytes']
        v['packets'] += 1
        v['groups'][str(source_groups[p['message_id']])] += p['data_payload_bytes']
    queues = defaultdict(int)
    for row in r['queue_events']:
        x = dict(zip(r['queue_event_columns'], row))
        key = str(x['node'])+':'+str(x['port'])
        queues[key] = max(queues[key], x['bytes'])
    for key, v in ports.items():
        v['queue_peak_bytes'] = queues[key]
    fail = r.get('link_failures')
    if fail:
        assert fail['pending_events'] == 0 and fail['route_epoch'] == 1
        assert all(p['time_ns'] <= 4000 for p in link_packets), 'transmission started on failed port after shutdown'
    return dict(result_sha256=sha(directory/'result.json'), group_complete_ns=groups,
        max_group_complete_ns=max(groups.values()), drain_ns=r['finish_ns'], gates_ns=gates,
        messages=len(messages), logical_payload_bytes=logical, source_payload_bytes=total,
        retransmitted_payload_bytes=repeats, source_data_packets=len(n['source_packets']),
        loss_events=len(drops), drops_by_group=dict(sorted(by_group.items())),
        drops_by_protocol=dict(sorted(by_protocol.items())), drop_rows=drops,
        timeout_events=retries, link_failures=fail, selected_link_data_packets_by_group=dict(link_groups),
        selected_link_payload_bytes_by_group=dict(link_group_bytes), selected_link_tx=link_packets,
        physical_fault_drops=r['fault_drops'], queue_peak_bytes=max(queues.values()),
        buffer_peak_bytes=r['service_transport']['message_buffers']['peak_bytes'],
        ports=dict(ports), message_spans={str(m['id']): dict(src=m['src'], dst=m['dst'], stream=m['stream'],
            layer=m['layer'], phase=m['phase'], ready_ns=m['ready_ns'], receive_ready_ns=m['receive_ready_ns'],
            ack_ns=m['ack_ns'], bytes=m['bytes']) for m in messages},
        streams={str(i): dict(src=s['src'], dst=s['dst'], source_payload_bytes=s['source_payload_bytes'],
            qp_creations=s['qp_creations'], snd_una=s['snd_una'], published_bytes=s['published_bytes'])
            for i, s in streams.items()})


def analyze(a):
    study = a.study.resolve()
    f = checked(study)
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    rows = {}
    for name, directory in [('normal', Path(f['normal'])), *[(n, study/n) for n in CASES]]:
        assert hashes(directory/'graphs') == f['normal_graphs_sha256']
        assert route_identity(load(directory/'input.json'), directory) == route_identity(load(Path(f['prepared'])/'service-fixed.json'), ROOT)
        assert normalize(load(directory/'input.json')) == normalize(load(Path(f['prepared'])/'service-fixed.json'))
        rows[name] = summary(directory, f['failed_link'])
        gc.collect()
    effects = {}
    for delay in (20, 60):
        baseline, local = rows['global'+str(delay)], rows['local'+str(delay)]
        effects[str(delay)] = dict(group_saved_ns={str(i): baseline['group_complete_ns'][str(i)]-
            local['group_complete_ns'][str(i)] for i in range(4)},
            max_group_saved_ns=baseline['max_group_complete_ns']-local['max_group_complete_ns'],
            extra_source_payload_bytes=local['source_payload_bytes']-baseline['source_payload_bytes'],
            drain_saved_ns=baseline['drain_ns']-local['drain_ns'])
    save(out/'analysis.json', dict(frozen_sha256=sha(study/'frozen.json'), cases=rows, effects=effects,
        max_new_networks=5, reused_normal=True, failed_link=f['failed_link']))
    print(json.dumps(dict(passed=True, effects=effects, group_ns={n: v['group_complete_ns'] for n, v in rows.items()})))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    q = sub.add_parser('prepare')
    for name in ('source-manifest', 'source-base', 'build', 'normal', 'output'):
        q.add_argument('--'+name, type=Path, required=True)
    q = sub.add_parser('run')
    q.add_argument('--study', type=Path, required=True)
    q.add_argument('--case', choices=CASES, required=True)
    q.add_argument('--protoc', type=Path, required=True)
    q = sub.add_parser('analyze')
    q.add_argument('--study', type=Path, required=True)
    q.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    globals()[a.command](a)


if __name__ == '__main__':
    main()
