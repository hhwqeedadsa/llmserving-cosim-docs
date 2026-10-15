"""Prepare a bounded, single-plane hierarchical UB network for Scale-Out studies.

This is connectivity and input preparation, not a RoCE/NIC implementation or a
network execution. All traffic still executes in the existing joint backend.
"""
from collections import Counter
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path

from .fabric import HEADERS, physical
from .plan import MAX_RANK_ID, integer, require

DOMAINS = ('device_access', 'server_uplink', 'rack_uplink')
SCENARIO_FIELDS = ('schema_version scenario_id basis service layer system backend execution '
                   'observation max_simulation_ns')


def fields(value, names, label):
    require(isinstance(value, dict) and set(value) == set(names.split()), 'Invalid Scale-Out ' + label)


def validate(config):
    fields(config, 'schema_version scenario shape groups link_profiles node_defaults budget', 'fields')
    require(config['schema_version'] == 'moe-ub-scaleout/1', 'Unsupported Scale-Out schema')
    s = config['shape']
    fields(s, 'racks servers_per_rack chips_per_server dies_per_chip', 'shape')
    for key, value in s.items():
        integer(value, key, 1, 128)
    servers = s['racks'] * s['servers_per_rack']
    per_server = s['chips_per_server'] * s['dies_per_chip']
    devices = servers * per_server
    require(per_server + 1 <= 128 and s['servers_per_rack'] + 1 <= 128, 'Scale-Out switch port limit is 128')
    counts = dict(devices=devices, servers=servers, racks=s['racks'],
                  nodes=devices + servers + s['racks'] + 1, links=devices + servers + s['racks'],
                  route_rows=devices * (devices - 1 + servers + s['racks'] + 1), channels=0)
    b = config['budget']
    fields(b, 'max_nodes max_links max_route_rows max_channels', 'budget')
    for key, value in b.items():
        integer(value, key, 1, 2**32 - 1)
    require(counts['nodes'] <= min(4096, b['max_nodes']) and counts['links'] <= b['max_links'] and
            counts['route_rows'] <= min(1000000, b['max_route_rows']), 'Scale-Out topology/routing budget exceeded')
    profiles = config['link_profiles']
    fields(profiles, ' '.join(DOMAINS), 'link profiles')
    for name, profile in profiles.items():
        fields(profile, 'rate_bps delay_ns', name)
        integer(profile['rate_bps'], name + ' rate_bps', 1)
        integer(profile['delay_ns'], name + ' delay_ns')
    fields(config['node_defaults'], 'allocation_ns forward_ns', 'node defaults')
    for key, value in config['node_defaults'].items():
        integer(value, key)
    require(isinstance(config['groups'], list) and config['groups'], 'Scale-Out needs explicit groups')
    names, ranks, endpoints = set(), set(), set()
    for g in config['groups']:
        fields(g, 'group_id rank_ids rank_to_ub_node', 'group')
        name = g['group_id']
        require(isinstance(name, str) and name and name not in names, 'Duplicate/empty Scale-Out group')
        names.add(name)
        rs, ns = g['rank_ids'], g['rank_to_ub_node']
        require(isinstance(rs, list) and isinstance(ns, list) and 0 < len(rs) == len(ns), 'Invalid Scale-Out mapping')
        for rank, node in zip(rs, ns):
            integer(rank, 'rank_id', 0, MAX_RANK_ID)
            integer(node, 'device', 0, devices - 1)
            require(rank not in ranks and node not in endpoints, 'Duplicate Scale-Out rank/device')
            ranks.add(rank); endpoints.add(node)
        require(rs == sorted(rs), 'Scale-Out rank_ids must be sorted')
        # The existing single-plane resolver requires complete precreated pairs,
        # including unused same-chip pairs. Local deliveries do not use them.
        counts['channels'] += len(rs) * (len(rs) - 1) // 2
    require(counts['channels'] <= min(65536, b['max_channels']), 'Scale-Out channel budget exceeded')
    counts['active_endpoints'] = len(endpoints)
    return counts


def generate(config):
    """Return topology, exact single-path routes, channels and structural audits."""
    counts = validate(config)
    s = config['shape']; per_server = s['chips_per_server'] * s['dies_per_chip']
    ndev, servers, racks = (counts[k] for k in ('devices', 'servers', 'racks'))
    spr = s['servers_per_rack']
    spine = ndev + servers + racks
    host = lambda server: ndev + server
    leaf = lambda rack: ndev + servers + rack
    nodes, links, port_rows = [], [], []
    for node in range(counts['nodes']):
        ports = 1 if node < ndev else per_server + 1 if node < ndev + servers else spr + 1 if node < spine else racks
        nodes.append(dict(node_id=node, ports=ports, kind='DEVICE' if node < ndev else 'SWITCH', **config['node_defaults']))

    def link(a, ap, b, bp, domain):
        profile = config['link_profiles'][domain]
        links.append(dict(src=a, src_port=ap, dst=b, dst_port=bp, **profile))
        for src, sport, dst, dport, direction in ((a, ap, b, bp, 'up'), (b, bp, a, ap, 'down')):
            port_rows.append(dict(node_id=src, port_id=sport, peer_node=dst, peer_port=dport,
                                  domain=domain, direction=direction, **profile))

    for n in range(ndev):
        link(n, 0, host(n // per_server), n % per_server, 'device_access')
    for server in range(servers):
        link(host(server), per_server, leaf(server // spr), server % spr, 'server_uplink')
    for rack in range(racks):
        link(leaf(rack), spr, spine, rack, 'rack_uplink')
    routes, lookup = [], {}
    for n in range(counts['nodes']):
        for dst in range(ndev):
            if n == dst:
                continue
            ds, dr = dst // per_server, dst // per_server // spr
            if n < ndev:
                port = 0
                metric = 2 if n // per_server == ds else 4 if n // per_server // spr == dr else 6
            elif n < ndev + servers:
                server = n - ndev
                port = dst % per_server if server == ds else per_server
                metric = 1 if server == ds else 3 if server // spr == dr else 5
            elif n < spine:
                rack = n - ndev - servers
                port = ds % spr if rack == dr else spr
                metric = 2 if rack == dr else 4
            else:
                port, metric = dr, 3
            row = dict(nodeId=str(n), dstNodeId=str(dst), dstPortId='0', outPorts=str(port), metrics=str(metric))
            routes.append(row); lookup[n, dst] = port
    channels = []; tp = Counter()
    for group in config['groups']:
        members = sorted(group['rank_to_ub_node'])
        for i, a in enumerate(members):
            for b in members[i+1:]:
                metric = 2 if a // per_server == b // per_server else 4 if a // per_server // spr == b // per_server // spr else 6
                channels.append(dict(zip(HEADERS['transport_channel.csv'].split(','), map(str,
                    (a, 0, tp[a], b, 0, tp[b], 7, metric)))))
                tp[a] += 1; tp[b] += 1
    adjacency = {(p['node_id'], p['port_id']): p for p in port_rows}
    paths = []
    for group in config['groups']:
        members = list(zip(group['rank_ids'], group['rank_to_ub_node']))
        for rank, src in members:
            for peer, dst in members:
                if src == dst:
                    continue
                same_chip = src // s['dies_per_chip'] == dst // s['dies_per_chip']
                scope = ('same_chip' if same_chip else 'same_server' if src // per_server == dst // per_server else
                         'same_rack' if src // per_server // spr == dst // per_server // spr else 'cross_rack')
                current, route = src, []
                if not same_chip:
                    while current != dst:
                        require(len(route) < 6, 'Scale-Out route loop or excessive hops')
                        p = adjacency[current, lookup[current, dst]]
                        route.append(p); current = p['peer_node']
                paths.append(dict(group_id=group['group_id'], src_rank=rank, dst_rank=peer, src_node=src, dst_node=dst,
                    scope=scope, delivery='local_delivery' if same_chip else 'UB', hop_count=len(route),
                    bottleneck_bps=min((p['rate_bps'] for p in route), default=0),
                    propagation_ns=sum(p['delay_ns'] for p in route),
                    egress_ports=' '.join(f"{p['node_id']}:{p['port_id']}" for p in route)))
    profiles = config['link_profiles']
    cuts = [dict(level='server', identity=server, node_id=host(server), egress_port=per_server,
                 downstream_bps=per_server * profiles['device_access']['rate_bps'],
                 uplink_bps=profiles['server_uplink']['rate_bps']) for server in range(servers)]
    cuts += [dict(level='rack', identity=rack, node_id=leaf(rack), egress_port=spr,
                  downstream_bps=spr * profiles['server_uplink']['rate_bps'],
                  uplink_bps=profiles['rack_uplink']['rate_bps']) for rack in range(racks)]
    require(len(routes) == counts['route_rows'] and len(channels) == counts['channels'], 'Scale-Out inventory mismatch')
    audit = dict(schema_version='moe-ub-scaleout-audit/1', state='prepared_not_executed',
        calibration_status='unvalidated_reference', transport_model='UB; not Ethernet/RoCE',
        scope='single_plane_single_spine_tree', counts=counts,
        rank_placement=[physical(r, n, s) | dict(ub_node_id=n, rack_id=f'rack-{n // per_server // spr}')
                        for g in config['groups'] for r, n in zip(g['rank_ids'], g['rank_to_ub_node'])],
        path_scope_counts=dict(Counter(p['scope'] for p in paths)), cuts=cuts,
        cut_definition='One direction, physical rates; downstream_bps/uplink_bps is structural oversubscription, not achieved throughput',
        path_definition='Configured paths; same-chip uses existing local delivery. No measured queueing or completion times',
        limits=['No NIC DMA/PCIe model', 'No RoCE ECN/PFC/DCQCN', 'No ECMP or packet-spray path alternatives',
                'No automatic inter-node forwarding/aggregation from a real MoE communication library'])
    return dict(nodes=nodes, links=links), routes, channels, port_rows, paths, audit


def remap_layers(service, layers, config):
    """Change physical placement only; retain rank/expert/request/cost semantics."""
    require(service.get('schema_version') in ('moe-ub-configured-service/4', 'moe-ub-configured-service/5'),
            'Scale-Out preparation requires public inline full-model/finite-segment service')
    require(layers.get('schema_version') == 'moe-ub-layer-segments/1', 'Scale-Out requires layer segments')
    expected = {g['group_id']: g for g in config['groups']}
    for collection in (service['groups'], layers['groups']):
        names = [g['group_id'] for g in collection]
        require(len(names) == len(set(names)) and set(names) == set(expected), 'Scale-Out service/layer group mismatch')
    result = deepcopy(layers)
    for group in result['groups']:
        g = expected[group['group_id']]
        require(group['layers'], 'Scale-Out group needs layers')
        for layer in group['layers']:
            require(layer['rank_ids'] == g['rank_ids'], 'Scale-Out cannot change logical rank IDs')
            layer['rank_to_ub_node'] = list(g['rank_to_ub_node'])
            require(layer.get('deployment', {}).get('network_scope') == 'cross_chip', 'Scale-Out requires explicit physical deployment')
            layer['deployment']['ranks'] = [physical(r, n, config['shape'])
                                           for r, n in zip(g['rank_ids'], g['rank_to_ub_node'])]
    return result


def prepare(input_path, output):
    """Write portable input copies and a scenario accepted by existing resolve."""
    import yaml
    from . import fabric, plan, scenario as scenario_module
    from .scenario import read_yaml
    input_path, output = Path(input_path).resolve(), Path(output).resolve()
    config = json.loads(input_path.read_text())
    topology, routes, channels, ports, paths, audit = generate(config)
    scenario = deepcopy(config['scenario'])
    fields(scenario, SCENARIO_FIELDS, 'scenario')
    require(scenario['schema_version'] == 'moe-ub-scenario-run/1' and scenario['basis'] == 'selected_reference',
            'Scale-Out uses explicit-route scenario-run/1 with selected_reference basis')
    require(scenario['observation'] is None, 'Prepare observations after topology selection; old port IDs must not be reused')
    root = input_path.parent
    sources = {k: (root / scenario[k]).resolve() for k in ('service', 'layer', 'system')}
    require(isinstance(scenario['backend'], dict) and 'reference_file' in scenario['backend'], 'Missing Scale-Out backend reference')
    sources['reference'] = (root / scenario['backend']['reference_file']).resolve()
    service, layers, system = (json.loads(sources[k].read_text()) for k in ('service', 'layer', 'system'))
    layers = remap_layers(service, layers, config)
    reference = read_yaml(sources['reference'])
    require(reference.get('schema_version') == 'moe-ub-scenario-reference/0.1', 'Unsupported Scale-Out backend reference')
    require(not output.exists(), 'Scale-Out output already exists: ' + str(output))
    # CSV values alone are NOT authoritative: resolve_topology uses defaults
    # and explicit overrides. Carry every tier rate/delay into those overrides.
    defaults = dict(**config['link_profiles']['device_access'], **config['node_defaults'])
    scenario['network'] = dict(case_path='network', plane_id='scaleout-plane-0', defaults=defaults,
                               nodes=[], links=topology['links'])
    scenario.update(service='service.json', layer='layers.json', system='system.json')
    scenario['backend']['reference_file'] = 'backend-reference.yaml'
    code_sources = [Path(module.__file__) for module in (fabric, plan, scenario_module)]
    lock = Path(__file__).resolve().parents[2] / 'deps.lock.json'
    audit['sources'] = [dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                        for p in (input_path, *sources.values(), Path(__file__), *code_sources, lock)]
    output.mkdir(parents=True, exist_ok=False); (output / 'network').mkdir()
    def save(name, value):
        (output / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    def table(name, rows, header):
        with (output / name).open('x', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=header.split(','), lineterminator='\n')
            writer.writeheader(); writer.writerows(rows)
    table('network/node.csv', [dict(zip(HEADERS['node.csv'].split(','),
        (n['node_id'], n['kind'], n['ports'], f"{n['allocation_ns']}ns", f"{n['forward_ns']}ns"))) for n in topology['nodes']], HEADERS['node.csv'])
    table('network/topology.csv', [dict(zip(HEADERS['topology.csv'].split(','),
        (l['src'], l['src_port'], l['dst'], l['dst_port'], f"{l['rate_bps']}bps", f"{l['delay_ns']}ns"))) for l in topology['links']], HEADERS['topology.csv'])
    table('network/routing_table.csv', routes, HEADERS['routing_table.csv'])
    table('network/transport_channel.csv', channels, HEADERS['transport_channel.csv'])
    table('ports.csv', ports, 'node_id,port_id,peer_node,peer_port,domain,direction,rate_bps,delay_ns')
    table('paths.csv', paths, 'group_id,src_rank,dst_rank,src_node,dst_node,scope,delivery,hop_count,bottleneck_bps,propagation_ns,egress_ports')
    save('scaleout.json', config); save('service.json', service); save('layers.json', layers); save('system.json', system)
    (output / 'backend-reference.yaml').write_bytes(sources['reference'].read_bytes())
    (output / 'scenario.yaml').write_text(yaml.safe_dump(scenario, sort_keys=False))
    audit['artifacts'] = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(output.rglob('*')) if p.is_file()}
    save('scaleout-audit.json', audit)
    return dict(passed=True, state=audit['state'], output=str(output), counts=audit['counts'],
                next_step='moe-ub resolve --config <output>/scenario.yaml --backend <locked-backend> --output <new-inputs>')
