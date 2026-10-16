"""Parameterised packet-switch fabrics, with explicit NIC ports and native RDMA text exports.

This prepares physical input only. GPU nodes are endpoints, never transit routers.
No UB model, OCS scheduler, RDMA execution or GPU performance model is invoked.
"""
from collections import Counter, defaultdict, deque
import csv
import hashlib
import json
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError('Scale-Out topology: ' + message)


def fields(value, names, label):
    require(isinstance(value, dict) and set(value) == set(names.split()), 'invalid ' + label + ' fields')


def integer(value, label, minimum=1, maximum=4096):
    require(type(value) is int and minimum <= value <= maximum, 'invalid ' + label)


def validate(c):
    extended = c.get('schema_version') == 'moe-scaleout-topology/2'
    fields(c, 'schema_version deployment fabric nic links port_limits budget' + (' intra_host' if extended else ''), 'root')
    require(c['schema_version'] in ('moe-scaleout-topology/1','moe-scaleout-topology/2'), 'unsupported schema')
    if extended:
        local = c['intra_host']
        fields(local, 'kind enabled rate_bps delay_ns chunk_bytes', 'intra-host fabric')
        require(local['kind'] == 'nonblocking_chunked_crossbar' and type(local['enabled']) is bool,
                'invalid intra-host fabric model')
        integer(local['rate_bps'], 'intra-host per-direction GPU link rate', 1, 10**15)
        integer(local['delay_ns'], 'intra-host hop delay', 0, 1000000)
        integer(local['chunk_bytes'], 'intra-host chunk', 64, 1048576)
    h, f, nic = (c[k] for k in ('deployment', 'fabric', 'nic'))
    fields(h, 'pods segments_per_pod servers_per_segment gpus_per_server gpu_type', 'deployment')
    for key in ('pods', 'segments_per_pod', 'servers_per_segment', 'gpus_per_server'):
        integer(h[key], key)
    require(h['gpu_type'] in ('A100', 'A800', 'H100', 'H800', 'NONE'), 'GPU label not supported by locked SimAI reader')
    fields(f, 'rails planes tors_per_segment_per_rail upper_tier_scope aggs_per_pod_per_group '
           'cores_per_group tor_uplinks agg_uplinks core_wiring', 'fabric')
    for key in ('rails', 'planes', 'tors_per_segment_per_rail'):
        integer(f[key], key, maximum=64 if key != 'planes' else 16)
    for key in ('aggs_per_pod_per_group', 'cores_per_group', 'tor_uplinks', 'agg_uplinks'):
        integer(f[key], key, minimum=0)
    require(f['upper_tier_scope'] in ('shared', 'rail'), 'upper_tier_scope must be shared or rail')
    a, core, fanout = f['aggs_per_pod_per_group'], f['cores_per_group'], f['agg_uplinks']
    lower = a if a else h['segments_per_pod'] * f['tors_per_segment_per_rail'] * (f['rails'] if f['upper_tier_scope'] == 'shared' else 1)
    if not a:
        require(core > 0 and fanout == 0, 'without Agg require Core and agg_uplinks=0')
        fanout = f['tor_uplinks']
    else:
        require(0 < f['tor_uplinks'] <= a, 'tor_uplinks must be 1..Agg count')
    if core:
        require(0 < fanout <= core, 'core fanout must be 1..Core count')
        require(f['core_wiring'] in ('full_mesh', 'grouped', 'cyclic'), 'invalid core_wiring')
        if f['core_wiring'] == 'full_mesh':
            require(fanout == core, 'full_mesh fanout must equal Core count')
        if f['core_wiring'] == 'grouped':
            require(core == lower * fanout, 'grouped requires Core count = lower switches per pod/group × core fanout')
    else:
        require(a > 0 and fanout == 0 and f['core_wiring'] == 'none', 'without Core require Agg, agg_uplinks=0 and core_wiring=none')
    fields(nic, 'rails_by_gpu_slot total_rate_bps', 'NIC')
    integer(nic['total_rate_bps'], 'NIC total_rate_bps', maximum=10**15)
    mapping = nic['rails_by_gpu_slot']
    require(isinstance(mapping, list) and len(mapping) == h['gpus_per_server'], 'one NIC rail list per GPU slot required')
    used = set()
    for row in mapping:
        require(isinstance(row, list) and 0 < len(row) <= f['rails'], 'invalid NIC rail list')
        for rail in row:
            integer(rail, 'NIC rail', minimum=0, maximum=f['rails'] - 1)
        require(len(set(row)) == len(row), 'duplicate rail for one GPU; parallel edges unsupported by locked readers')
        used.update(row)
    require(used == set(range(f['rails'])), 'every rail must have at least one NIC per server')
    fields(c['links'], 'access tor_agg agg_core tor_core', 'links')
    for name, profile in c['links'].items():
        fields(profile, 'rate_bps delay_ns', name)
        integer(profile['rate_bps'], name + ' rate_bps', maximum=10**15)
        integer(profile['delay_ns'], name + ' delay_ns', minimum=0, maximum=10**12)
    require(f['planes'] * c['links']['access']['rate_bps'] <= nic['total_rate_bps'],
            'plane port rates exceed one NIC total_rate_bps; split the rate or explicitly increase NIC budget')
    fields(c['port_limits'], 'gpu tor agg core', 'port limits')
    for key, limit in c['port_limits'].items():
        integer(limit, key + ' logical device port limit', maximum=1024)
    fields(c['budget'], 'max_nodes max_links max_switches', 'budget')
    for key, limit in c['budget'].items():
        integer(limit, key, maximum=131072)
    servers = h['pods'] * h['segments_per_pod'] * h['servers_per_segment']
    gpus = servers * h['gpus_per_server']
    groups = f['rails'] if f['upper_tier_scope'] == 'rail' else 1
    tors = h['pods'] * h['segments_per_pod'] * f['rails'] * f['tors_per_segment_per_rail'] * f['planes']
    aggs = h['pods'] * groups * a * f['planes']
    cores = groups * core * f['planes']
    access = servers * sum(map(len, mapping)) * f['planes']
    edges = access + tors * f['tor_uplinks'] + aggs * f['agg_uplinks']
    require(gpus <= 4096 and gpus + tors + aggs + cores <= min(8192, c['budget']['max_nodes']), 'node budget exceeded')
    require(tors + aggs + cores <= c['budget']['max_switches'] and edges <= c['budget']['max_links'], 'switch/link budget exceeded')
    return dict(gpus=gpus, servers=servers, tor=tors, agg=aggs, core=cores, links=edges)


def core_targets(index, count, fanout, pattern):
    if pattern == 'full_mesh':
        return range(count)
    return [(index * fanout + j) % count for j in range(fanout)]


def generate(c):
    counts = validate(c)
    h, f = c['deployment'], c['fabric']
    nodes, links, nic_ports = [], [], []
    switches, pairs = {}, set()
    degree = Counter()
    groups = list(range(f['rails'])) if f['upper_tier_scope'] == 'rail' else [-1]

    def node(kind, **location):
        identity = len(nodes)
        row = dict(id=identity, kind=kind, **location)
        nodes.append(row)
        return identity

    for pod in range(h['pods']):
        for segment in range(h['segments_per_pod']):
            for local_server in range(h['servers_per_segment']):
                server = (pod * h['segments_per_pod'] + segment) * h['servers_per_segment'] + local_server
                for gpu_slot in range(h['gpus_per_server']):
                    node('gpu', pod=pod, segment=segment, server=server, server_in_segment=local_server, gpu_slot=gpu_slot)
    # IDs 0..GPU-1 preserve the server-contiguous convention of the SimAI reader.
    for plane in range(f['planes']):
        for pod in range(h['pods']):
            for segment in range(h['segments_per_pod']):
                for rail in range(f['rails']):
                    for index in range(f['tors_per_segment_per_rail']):
                        switches['tor', plane, pod, segment, rail, index] = node('tor', plane=plane, pod=pod, segment=segment, rail=rail, index=index)
            for group in groups:
                for index in range(f['aggs_per_pod_per_group']):
                    switches['agg', plane, pod, group, index] = node('agg', plane=plane, pod=pod, rail=group, index=index)
        for group in groups:
            for index in range(f['cores_per_group']):
                switches['core', plane, group, index] = node('core', plane=plane, rail=group, index=index)

    def edge(src, dst, tier):
        pair = tuple(sorted((src, dst)))
        require(src != dst and pair not in pairs, 'duplicate/self link')
        pairs.add(pair)
        degree[src] += 1
        degree[dst] += 1
        for endpoint in (src, dst):
            require(degree[endpoint] <= c['port_limits'][nodes[endpoint]['kind']],
                    f'node {endpoint} ({nodes[endpoint]["kind"]}) exceeds logical port limit')
        link = dict(id=len(links), src=src, dst=dst, src_port=degree[src], dst_port=degree[dst], tier=tier, **c['links'][tier])
        links.append(link)
        return link

    for gpu in nodes[:counts['gpus']]:
        tor_index = gpu['server_in_segment'] % f['tors_per_segment_per_rail']
        for nic, rail in enumerate(c['nic']['rails_by_gpu_slot'][gpu['gpu_slot']]):
            for plane in range(f['planes']):
                dst = switches['tor', plane, gpu['pod'], gpu['segment'], rail, tor_index]
                link = edge(gpu['id'], dst, 'access')
                nic_ports.append(dict(gpu=gpu['id'], server=gpu['server'], gpu_slot=gpu['gpu_slot'], nic=nic,
                                      rail=rail, plane=plane, gpu_port=link['src_port'], tor=dst,
                                      tor_port=link['dst_port'], rate_bps=link['rate_bps']))
    for sw in nodes[counts['gpus']:]:
        if sw['kind'] == 'tor':
            group = sw['rail'] if f['upper_tier_scope'] == 'rail' else -1
            index = sw['segment'] * f['tors_per_segment_per_rail'] + sw['index']
            if group == -1:
                index = (sw['segment'] * f['rails'] + sw['rail']) * f['tors_per_segment_per_rail'] + sw['index']
            if f['aggs_per_pod_per_group']:
                for a in core_targets(index, f['aggs_per_pod_per_group'], f['tor_uplinks'], 'cyclic'):
                    edge(sw['id'], switches['agg', sw['plane'], sw['pod'], group, a], 'tor_agg')
            else:
                for core in core_targets(index, f['cores_per_group'], f['tor_uplinks'], f['core_wiring']):
                    edge(sw['id'], switches['core', sw['plane'], group, core], 'tor_core')
        elif sw['kind'] == 'agg' and f['cores_per_group']:
            for core in core_targets(sw['index'], f['cores_per_group'], f['agg_uplinks'], f['core_wiring']):
                edge(sw['id'], switches['core', sw['plane'], sw['rail'], core], 'agg_core')
    require(len(links) == counts['links'], 'internal link count mismatch')
    topology = dict(schema_version='moe-scaleout-physical-topology/1', nodes=nodes, links=links, nic_ports=nic_ports)
    report = audit(c, nodes, links, counts)
    if 'intra_host' in c:
        topology['schema_version'] = 'moe-scaleout-physical-topology/2'
        topology['intra_host'] = dict(c['intra_host'])
        report['intra_host'] = dict(**c['intra_host'], servers=counts['servers'],
            interpretation='One ingress and one egress link per GPU; nonblocking host crossbar. '
                           'Chunked bandwidth model, not NVLink packet/credit protocol.')
        report['native_text_scope'] = 'External RDMA fabric only; local fabric is in topology.json'
    return topology, report


def audit(c, nodes, links, counts):
    switch_ids = {n['id'] for n in nodes if n['kind'] != 'gpu'}
    adjacency = {i: set() for i in switch_ids}
    attached = defaultdict(set)
    down, up, degree = Counter(), Counter(), Counter()
    for link in links:
        a, b, rate = link['src'], link['dst'], link['rate_bps']
        degree[a] += 1; degree[b] += 1
        down[b] += rate
        up[a] += rate
        if a in switch_ids:
            adjacency[a].add(b); adjacency[b].add(a)
        else:
            attached[b].add(a)
    domains, membership = [], defaultdict(set)
    pending = set(switch_ids)
    while pending:
        start = min(pending)
        queue, component = deque([start]), {start}
        pending.remove(start)
        while queue:
            for peer in adjacency[queue.popleft()]:
                if peer in pending:
                    pending.remove(peer); component.add(peer); queue.append(peer)
        endpoints = set().union(*(attached[i] for i in component))
        identity = len(domains)
        for gpu in endpoints:
            membership[gpu].add(identity)
        domains.append(dict(id=identity, planes=sorted({nodes[i]['plane'] for i in component}),
                            rails=sorted({nodes[i]['rail'] for i in component if nodes[i]['rail'] >= 0}),
                            switch_count=len(component), gpu_ids=sorted(endpoints)))
    signatures = Counter(tuple(sorted(membership[i])) for i in range(counts['gpus']))
    signatures = [(set(s), count) for s, count in signatures.items()]
    unreachable = sum(n * m for i, (a, n) in enumerate(signatures) for b, m in signatures[i+1:] if not a & b)
    unreachable += sum(n * (n - 1) // 2 for a, n in signatures if not a)
    rows = []
    for sw in nodes[counts['gpus']:]:
        rows.append(dict(node_id=sw['id'], kind=sw['kind'], plane=sw['plane'], rail=sw['rail'],
                         pod=sw.get('pod'), segment=sw.get('segment'), ports_used=degree[sw['id']],
                         port_limit=c['port_limits'][sw['kind']], downstream_bps=down[sw['id']], upstream_bps=up[sw['id']],
                         down_to_up_ratio=down[sw['id']] / up[sw['id']] if up[sw['id']] else None))
    warnings = []
    if unreachable:
        warnings.append('Some GPU pairs share no packet-switch domain. Full EP All-to-All requires explicit GPU relay/intra-host transfer or additional NICs; endpoints are not routers.')
    if any(not degree[i] for i in switch_ids):
        warnings.append('Some configured switches have no links; unused capacity is retained, not silently rewired.')
    if any(r['downstream_bps'] == 0 and r['upstream_bps'] > 0 for r in rows):
        warnings.append('Some switches have no downstream attachment; inspect counts and fanout before using the topology.')
    return dict(schema_version='moe-scaleout-topology-audit/1', state='generated_not_executed', network_executed=False,
                counts=counts, link_tiers=dict(Counter(l['tier'] for l in links)),
                per_nic=dict(ports=c['fabric']['planes'], port_rate_bps=c['links']['access']['rate_bps'],
                             total_port_rate_bps=c['fabric']['planes'] * c['links']['access']['rate_bps'],
                             declared_total_rate_bps=c['nic']['total_rate_bps']),
                switch_ports=rows, packet_switch_domains=domains,
                all_gpu_pairs_have_packet_switch_path=unreachable == 0,
                unreachable_unordered_gpu_pairs=unreachable,
                fixed_switch_counter_array_bytes=len(switch_ids) * 1025 * 1025 * 8 * 4,
                warnings=warnings,
                limits=['Packet-switch connectivity only; no OCS circuits or reconfiguration.',
                        'Ports are logical link/device ports, not physical cage/breakout validation.',
                        'Ratios are per-switch capacity sums, not measured throughput or bisection bandwidth.',
                        'No NIC/PCIe shared service model, intra-host fabric, GPU relay, adaptive routing or traffic execution.',
                        'Native readers compute routing; rail/plane selection and application rank placement remain separate.'])


def export_text(topology, config, simai=False):
    nodes, links = topology['nodes'], topology['links']
    switch_ids = [n['id'] for n in nodes if n['kind'] != 'gpu']
    if simai:
        header = f"{len(nodes)} {config['deployment']['gpus_per_server']} 0 {len(switch_ids)} {len(links)} {config['deployment']['gpu_type']}"
    else:
        header = f'{len(nodes)} {len(switch_ids)} {len(links)}'
    lines = [header, ' '.join(map(str, switch_ids))]
    lines.extend(f"{l['src']} {l['dst']} {l['rate_bps']}bps {l['delay_ns']}ns 0" for l in links)
    return '\n'.join(lines) + '\n'


def execute(config, output):
    config, output = Path(config).resolve(), Path(output).resolve()
    require(config.stat().st_size <= 65536, 'config exceeds 64KiB')
    raw = config.read_bytes()
    require(len(raw) <= 65536, 'config exceeds 64KiB')
    c = json.loads(raw)
    topology, report = generate(c)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'config.json').write_bytes(raw)
    for name, value in [('topology.json', topology), ('audit.json', report)]:
        (output / name).write_text(json.dumps(value, indent=2) + '\n')
    (output / 'topology.astra.txt').write_text(export_text(topology, c))
    (output / 'topology.simai.txt').write_text(export_text(topology, c, simai=True))
    with (output / 'gpu_nic_ports.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(topology['nic_ports'][0]))
        writer.writeheader(); writer.writerows(topology['nic_ports'])
    manifest = dict(schema_version='moe-scaleout-topology-manifest/1', state=report['state'],
                    network_executed=False, input_sha256=hashlib.sha256(raw).hexdigest(),
                    generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir())})
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return dict(passed=True, state=report['state'], output=str(output), counts=report['counts'],
                all_gpu_pairs_have_packet_switch_path=report['all_gpu_pairs_have_packet_switch_path'], warnings=report['warnings'])
