"""Input-only finite-connection ECMP demand projection; no network time model."""
from collections import Counter, defaultdict, deque
from fractions import Fraction
import hashlib
import json
from pathlib import Path

STAGES = ('dispatch.entry', 'dispatch.counts', 'dispatch.data', 'dispatch.exit',
          'combine.entry', 'combine.data', 'combine.exit')


def load(path): return json.loads(Path(path).read_bytes())
def save(path, value): Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stage_of(op):
    part = ('entry' if '.entry.' in op['id'] else 'exit') if op['kind'] == 'barrier_signal' else (
        'data' if op['kind'] == 'token_data' else 'counts')
    stage = op['phase']+'.'+part
    assert stage in STAGES
    return stage


def context(binding, slot, execution):
    sms, warps = execution['num_sms'], execution['combine_warps']
    width = (binding['owner_received_tokens']+sms*warps-1)//(sms*warps)
    channel = (binding['source_rank_prefix']+slot)//width
    sm, warp = channel % sms, channel//sms
    n = execution['call_contexts']
    if n == 1: return 0
    if sms <= n:
        count = n//sms+int(sm < n % sms)
        return sm+(warp % count)*sms
    return (sm*warps+warp) % n


def demand(cfg, plan):
    execution = cfg['modern']['execution']
    ops, groups = [], defaultdict(list)
    for op in plan['operations']:
        if op['path'] == 'local': continue
        if op['phase'] == 'combine' and op['kind'] == 'token_data':
            groups[op['call'], op['src'], op['dst']].append(op)
        else: ops.append((op, op['binding']['context_index']))
    for group in groups.values():
        op = group[0]
        common = lambda b: {k:v for k,v in b.items() if k != 'slot_variable'}
        assert len(group) == op['binding']['source_to_owner_count']
        assert all(o['operand_bytes'] == op['operand_bytes'] and common(o['binding']) == common(op['binding']) for o in group)
        ops.extend((op, context(op['binding'], slot, execution)) for slot in range(len(group)))
    identities = sorted({(op['src'], c, op['dst']) for op, c in ops})
    placement = {r['rank']:r for r in cfg['deployment']}
    connections = []
    for identity, (src, c, dst) in enumerate(identities):
        devices = []
        for rank in (src, dst):
            ids = execution['local_devices'][rank]
            logical = ids[(c % execution['connections']) % len(ids)]
            devices.append(next(d for d in placement[rank]['devices'] if d['device'] == logical))
        a, b = devices
        assert a['plane'] == b['plane']
        row = dict(id=identity, source_rank=src, peer_rank=dst, context_index=c,
            src=placement[src]['node'], dst=placement[dst]['node'], plane=a['plane'],
            source_nic=a['nic'], destination_nic=b['nic'], queue_depth=cfg['provider']['queue_depth'])
        if src == dst: row['self_service'] = cfg['provider']['self_service']
        else: row.update(request_port=24000+2*identity, response_port=24001+2*identity)
        connections.append(row)
    lookup = {key:i for i,key in enumerate(identities)}
    cells = Counter()
    for op, c in ops:
        if op['path'] != 'rdma': continue
        identity = lookup[op['src'], c, op['dst']]
        cells[stage_of(op), identity, 'request'] += 32+op['operand_bytes']
        if op['kind'] == 'barrier_signal': cells[stage_of(op), identity, 'reply'] += 40
    return dict(connections=connections, messages=[dict(stage=s, connection=c, direction=d, bytes=n)
        for (s,c,d),n in sorted(cells.items())])


def graph(topology):
    adjacency, edges = defaultdict(list), {}
    gpu = {n['id'] for n in topology['nodes'] if n['kind'] == 'gpu'}
    for link in topology['links']:
        for a,b,ap,bp in ((link['src'],link['dst'],link['src_port'],link['dst_port']),
                          (link['dst'],link['src'],link['dst_port'],link['src_port'])):
            adjacency[a].append((b, ap))
            edges[a,ap] = dict(src=a, dst=b, port=ap, destination_port=bp,
                rate_bps=link['rate_bps'], tier=link['tier'])
    return gpu, adjacency, edges


def paths(topology, src, snic, dst, dnic):
    gpu, adjacency, edges = graph(topology)
    first = edges[src,snic]
    reverse = edges[dst,dnic]
    target = reverse['dst']
    distance, pending = {target:0}, deque([target])
    while pending:
        node = pending.popleft()
        for peer,_ in adjacency[node]:
            if peer not in gpu and peer not in distance:
                distance[peer] = distance[node]+1
                pending.append(peer)
    assert first['dst'] in distance
    def walk(node):
        if node == target: return [[edges[target,reverse['destination_port']]]]
        result = []
        for peer,port in adjacency[node]:
            if peer in distance and distance[peer] == distance[node]-1:
                result.extend([[edges[node,port], *tail] for tail in walk(peer)])
        return result
    return [[first,*tail] for tail in walk(first['dst'])]


def hash_words(words, seed):
    mask = (1 << 32)-1
    rot = lambda x,n: ((x << n) | (x >> (32-n))) & mask
    h = seed
    for word in words:
        k = rot(word*0xcc9e2d51 & mask, 15)*0x1b873593 & mask
        h = (rot(h ^ k, 13)*5+0xe6546b64) & mask
    h ^= 12
    h ^= h >> 16
    h = h*0x85ebca6b & mask
    h ^= h >> 13
    h = h*0xc2b2ae35 & mask
    return h ^ (h >> 16)


def select(alternatives, sip, dip, port, seed):
    candidates = alternatives
    position, answer, orders = 1, [alternatives[0][0]], []
    while position < len(candidates[0]):
        node = candidates[0][position]['src']
        ports = list(dict.fromkeys(path[position]['port'] for path in candidates))
        orders.append(dict(node=node,ports=ports))
        chosen = ports[hash_words((sip,dip,port | port << 16),seed+node) % len(ports)]
        answer.append(next(path[position] for path in candidates if path[position]['port'] == chosen))
        candidates = [path for path in candidates if path[position]['port'] == chosen]
        position += 1
    assert len(candidates) == 1
    return answer, orders


def project(topology, declaration, seed):
    fixed, split = Counter(), Counter()
    _,_,edges = graph(topology)
    maximum_paths, witnesses = 0, []
    for row in declaration['messages']:
        c = declaration['connections'][row['connection']]
        if row['direction'] == 'request':
            src,snic,dst,dnic,port = c['src'],c['source_nic'],c['dst'],c['destination_nic'],c['request_port']
        else:
            src,snic,dst,dnic,port = c['dst'],c['destination_nic'],c['src'],c['source_nic'],c['response_port']
        alternatives = paths(topology,src,snic,dst,dnic)
        maximum_paths = max(maximum_paths,len(alternatives))
        sip, dip = 0x0c000000+(src << 11)+snic, 0x0c000000+(dst << 11)+dnic
        chosen, orders = select(alternatives,sip,dip,port,seed)
        for edge in chosen: fixed[row['stage'],edge['src'],edge['port']] += row['bytes']
        for path in alternatives:
            for edge in path: split[row['stage'],edge['src'],edge['port']] += Fraction(row['bytes'],len(alternatives))
        witnesses.append(dict(**row,src=src,dst=dst,source_nic=snic,destination_nic=dnic,
            source_ip=sip,destination_ip=dip,wire_port=port,path_count=len(alternatives),
            path=[[e['src'],e['port']] for e in chosen],candidate_order=orders))
    stages = []
    for stage in STAGES:
        keys = sorted(key for key in fixed.keys() | split.keys() if key[0] == stage)
        exact = max(Fraction(fixed[k]*8*10**9,edges[k[1:]]['rate_bps']) for k in keys)
        equal = max(Fraction(split[k]*8*10**9,edges[k[1:]]['rate_bps']) for k in keys)
        fixed_core = sum(fixed[k] for k in keys if edges[k[1:]]['tier'] == 'agg_core')
        split_core = sum(split[k] for k in keys if edges[k[1:]]['tier'] == 'agg_core')
        assert fixed_core == split_core
        stages.append(dict(stage=stage, deterministic_peak_service_ns=float(exact),
            equal_path_peak_service_ns=float(equal), peak_service_ratio=float(exact/equal),
            exact_service_fraction=[exact.numerator,exact.denominator],
            split_service_fraction=[equal.numerator,equal.denominator],
            agg_core_hop_bytes=fixed_core))
    def table(counter): return [dict(stage=s,node=n,port=p,bytes_numerator=v.numerator,bytes_denominator=v.denominator)
        for (s,n,p),v in sorted((k,Fraction(v)) for k,v in counter.items())]
    return dict(maximum_shortest_paths=maximum_paths,stages=stages,
        deterministic_load=table(fixed),equal_path_load=table(split),messages=witnesses)


def execute(observed, output):
    assert not output.exists()
    output.mkdir()
    receipt = load(observed/'identity.json')
    assert all(sha(observed/n) == h for n,h in receipt['files'].items())
    summary = dict(schema_version='ecmp-baseline-audit/1',cases={},new_networks=0,
        hardware_calibrated=False,latency_measured=False,new_algorithm_benefit_proven=False,
        equal_path_reference_is_hash_expectation=False)
    for batch in (4,8):
        declaration = demand(load(observed/f'batch{batch}/input.json'),load(observed/f'batch{batch}/plan.json'))
        save(output/f'batch{batch}-demand.json',declaration)
        for cores in (1,2,4):
            projection = project(load(observed/f'cores{cores}/topology.json'),declaration,11)
            save(output/f'batch{batch}-cores{cores}.json',projection)
            summary['cases'][f'batch{batch}-cores{cores}'] = {k:v for k,v in projection.items()
                if k in ('maximum_shortest_paths','stages')}
    save(output/'summary.json',summary)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--observed',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    execute(args.observed.resolve(),args.output.resolve())
