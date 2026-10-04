"""Two-tier independent-plane fabric, including idle physical endpoints.

Only connectivity/numbering and explicit route candidates are generated here.
UB selects TPs and routes at runtime; no operation provenance enters this module.
"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path

from .plan import integer, require

HEADERS = {
    'node.csv': 'nodeId,nodeType,portNum,allocationDelay,forwardDelay',
    'topology.csv': 'nodeId1,portId1,nodeId2,portId2,bandwidth,delay',
    'routing_table.csv': 'nodeId,dstNodeId,dstPortId,outPorts,metrics',
    'transport_channel.csv': 'nodeId1,portId1,tpn1,nodeId2,portId2,tpn2,priority,metric',
}


def fields(value, names, label):
    require(isinstance(value, dict) and set(value) == set(names.split()), 'Invalid fabric ' + label)


def physical(rank, node, shape):
    d = shape['chips_per_server'] * shape['dies_per_chip']
    return dict(rank_id=rank, die_id=f'die-{node}',
                chip_id=f"chip-{node // shape['dies_per_chip']}", server_id=f'server-{node // d}')


def validate(spec):
    fields(spec, 'schema_version shape groups budget', 'fields')
    require(spec['schema_version'] == 'moe-ub-fabric/1', 'Unsupported fabric schema')
    s = spec['shape']; fields(s, 'servers chips_per_server dies_per_chip planes l2_per_plane', 'shape')
    for k, v in s.items(): integer(v, k, 1, 128)
    servers, planes, spines = s['servers'], s['planes'], s['l2_per_plane']
    d = s['chips_per_server'] * s['dies_per_chip']; devices = servers * d
    require(d + spines <= 128, 'Fabric L1 port limit is 128')
    b = spec['budget']; fields(b, 'max_nodes max_links max_route_rows max_channels', 'budget')
    for k, v in b.items(): integer(v, k, 1, 2**32-1)
    nodes = devices + servers * planes + planes * spines
    links = devices * planes + servers * planes * spines
    require(nodes <= min(4096, b['max_nodes']) and links <= b['max_links'], 'Fabric topology budget exceeded')
    require(isinstance(spec['groups'], list) and spec['groups'], 'Fabric needs explicit active groups')
    ranks, endpoints, names = set(), set(), set(); channels = 0
    for g in spec['groups']:
        fields(g, 'group_id rank_ids rank_to_ub_node', 'group')
        require(isinstance(g['group_id'], str) and g['group_id'] and g['group_id'] not in names, 'Duplicate fabric group')
        names.add(g['group_id'])
        require(isinstance(g['rank_ids'], list) and isinstance(g['rank_to_ub_node'], list) and
                0 < len(g['rank_ids']) == len(g['rank_to_ub_node']), 'Invalid fabric mapping')
        chips = Counter()
        for r, n in zip(g['rank_ids'], g['rank_to_ub_node']):
            integer(r, 'rank', 0, 2**32-1); integer(n, 'device', 0, devices-1)
            require(r not in ranks and n not in endpoints, 'Duplicate fabric rank/device')
            ranks.add(r); endpoints.add(n); chips[n // s['dies_per_chip']] += 1
        count = len(g['rank_ids'])
        channels += (count * (count-1) - sum(v*(v-1) for v in chips.values())) // 2 * planes
    # Count compressed route rows without constructing the tables.
    route_rows = 0
    for n in range(devices):
        lo, hi = n // d * d, (n // d + 1) * d - 1
        route_rows += planes * sum(a <= z for a,z in ((0,lo-1),(lo,n-1),(n+1,hi),(hi+1,devices-1)))
    route_rows += servers * planes * (d + servers-1) + planes * spines * servers
    require(channels <= b['max_channels'] and route_rows <= b['max_route_rows'], 'Fabric routing/channel budget exceeded')
    return dict(devices=devices, l1_switches=servers*planes, l2_switches=planes*spines,
                nodes=nodes, links=links, ports=2*links, planes=planes, active_endpoints=len(endpoints),
                inactive_endpoints=devices-len(endpoints), route_rows=route_rows, channels=channels)


def check_groups(spec, groups, layers):
    expected = {g['group_id']: g for g in spec['groups']}
    require(set(expected) == {g['group_id'] for g in groups}, 'Service/fabric group mismatch')
    for group in groups:
        g = expected[group['group_id']]
        require(all(group[k] == g[k] for k in ('rank_ids', 'rank_to_ub_node')), 'Service/fabric rank mapping mismatch')
    for group in layers['groups']:
        g = expected[group['group_id']]
        placement = [physical(r,n,spec['shape']) for r,n in zip(g['rank_ids'],g['rank_to_ub_node'])]
        for layer in group['layers']:
            require(layer.get('deployment', {}).get('ranks') == placement, 'Layer/fabric physical placement mismatch')
            require(layer['rank_ids'] == g['rank_ids'] and layer['rank_to_ub_node'] == g['rank_to_ub_node'],
                    'Layer/fabric rank mapping mismatch')


def generate(spec, defaults):
    counts = validate(spec)
    fields(defaults, 'rate_bps delay_ns allocation_ns forward_ns', 'defaults')
    for k,v in defaults.items(): integer(v,k,1 if k=='rate_bps' else 0,2**63-1)
    s=spec['shape']; d=s['chips_per_server']*s['dies_per_chip']; ndev=counts['devices']
    p=s['planes']; h=s['l2_per_plane']; servers=s['servers']
    def l1(server,plane):return ndev+server*p+plane
    def l2(plane,spine):return ndev+servers*p+plane*h+spine
    nodes=[];links=[];routes=[];channels=[]
    def node(n,ports,kind):
        nodes.append(dict(node_id=n,ports=ports,kind=kind,allocation_ns=defaults['allocation_ns'],forward_ns=defaults['forward_ns']))
    def link(a,ap,b,bp):
        links.append(dict(src=a,src_port=ap,dst=b,dst_port=bp,rate_bps=defaults['rate_bps'],delay_ns=defaults['delay_ns']))
    def route(n,lo,hi,plane,ports,metric):
        if lo>hi:return
        routes.append(dict(nodeId=str(n),dstNodeId=str(lo) if lo==hi else f'{lo}..{hi}',dstPortId=str(plane),
                           outPorts=' '.join(map(str,ports)),metrics=' '.join([str(metric)]*len(ports))))
    for n in range(ndev):
        node(n,p,'DEVICE'); server=n//d;lo=server*d;hi=lo+d-1
        for plane in range(p):
            link(n,plane,l1(server,plane),n%d)
            for a,b,metric in ((0,lo-1,4),(lo,n-1,2),(n+1,hi,2),(hi+1,ndev-1,4)):
                route(n,a,b,plane,[plane],metric)
    for server in range(servers):
        for plane in range(p):
            n=l1(server,plane);node(n,d+h,'SWITCH')
            for local in range(d):route(n,server*d+local,server*d+local,plane,[local],1)
            for remote in range(servers):
                if remote!=server:route(n,remote*d,remote*d+d-1,plane,list(range(d,d+h)),3)
            for spine in range(h):link(n,d+spine,l2(plane,spine),server)
    for plane in range(p):
        for spine in range(h):
            n=l2(plane,spine);node(n,servers,'SWITCH')
            for server in range(servers):route(n,server*d,server*d+d-1,plane,[server],2)
    tp=Counter()
    for group in spec['groups']:
        members=sorted(group['rank_to_ub_node'])
        for i,a in enumerate(members):
            for b in members[i+1:]:
                if a//s['dies_per_chip']==b//s['dies_per_chip']:continue
                for plane in range(p):
                    # TPNs are endpoint-local, deterministic and independent of traffic.
                    channels.append(dict(zip(HEADERS['transport_channel.csv'].split(','),map(str,
                        (a,plane,tp[a],b,plane,tp[b],7,2 if a//d==b//d else 4)))))
                    tp[a]+=1;tp[b]+=1
    links.sort(key=lambda x:(x['src'],x['src_port'],x['dst'],x['dst_port']))
    require(len(routes)==counts['route_rows'] and len(channels)==counts['channels'], 'Fabric count mismatch')
    audit=dict(schema_version='moe-ub-fabric-audit/1',counts=counts,shape=s,groups=spec['groups'],
               physical_ranks=[physical(r,n,s) | dict(ub_node_id=n) for g in spec['groups']
                               for r,n in zip(g['rank_ids'],g['rank_to_ub_node'])],
               numbering=dict(device='server * chips_per_server * dies_per_chip + local_die',
                  l1='devices + server * planes + plane',l2='devices + servers * planes + plane * l2_per_plane + spine',
                  device_port='plane',l1_down_port='local_die',l1_up_port='dies_per_server + spine',l2_port='server'),
               routing=dict(plane_selection='native random single TP per GetTpns call, EnableMultiPath=false',
                  switch_selection='native HASH over equal shortest candidates',packet_spray=False,
                  inactive_devices_have_routes=True,same_chip_channels=False),
               calibration_status='unvalidated_reference',target_scale_executed=False)
    return dict(nodes=nodes,links=links),routes,channels,audit


def tables(topology,routes,channels):
    return {'node.csv': [[n['node_id'],n['kind'],n['ports'],f"{n['allocation_ns']}ns",f"{n['forward_ns']}ns"] for n in topology['nodes']],
            'topology.csv': [[l['src'],l['src_port'],l['dst'],l['dst_port'],f"{l['rate_bps']}bps",f"{l['delay_ns']}ns"] for l in topology['links']],
            'routing_table.csv': [list(r.values()) for r in routes],
            'transport_channel.csv': [list(r.values()) for r in channels]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--defaults',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--audit-existing',action='store_true')
    args=parser.parse_args();spec=json.loads(args.input.read_text());defaults=json.loads(args.defaults.read_text())
    topology,routes,channels,audit=generate(spec,defaults)
    if not args.audit_existing:args.output_dir.mkdir(parents=True,exist_ok=False)
    for name,rows in tables(topology,routes,channels).items():
        expected=[HEADERS[name].split(',')]+[[str(v) for v in r] for r in rows]
        if args.audit_existing:
            with (args.output_dir/name).open(newline='') as stream:
                require(list(csv.reader(stream))==expected,'Fabric CSV differs: '+name)
        else:
            with (args.output_dir/name).open('w',newline='') as stream:csv.writer(stream,lineterminator='\n').writerows(expected)
    if not args.audit_existing:
        (args.output_dir/'fabric.json').write_text(json.dumps(spec,indent=2)+'\n')
        (args.output_dir/'fabric-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
    print(json.dumps(audit['counts'],sort_keys=True))


if __name__=='__main__':main()
