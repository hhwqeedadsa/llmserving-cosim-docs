"""Independent native-input, exhaustive path and byte-hash checks; no new network."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

from .model import hash_words, load, save, sha


def hash_bytes(raw, seed):
    h = seed
    for i in range(0,len(raw),4):
        k = int.from_bytes(raw[i:i+4],'little')
        k = k*3432918353 % 4294967296
        k = ((k << 15) | (k >> 17)) % 4294967296
        k = k*461845907 % 4294967296
        h ^= k
        h = ((h << 13) | (h >> 19)) % 4294967296
        h = (h*5+3864292196) % 4294967296
    h ^= len(raw)
    h ^= h >> 16
    h = h*2246822507 % 4294967296
    h ^= h >> 13
    h = h*3266489909 % 4294967296
    return h ^ (h >> 16)


def native_check(declaration, plan, initial, execution):
    connections = deepcopy(declaration['connections'])
    for c in connections:
        for k in ('source_rank','peer_rank','context_index'): del c[k]
    assert connections == initial['connections'], 'prospective connection identity/port mismatch'
    for c,b in zip(declaration['connections'],execution['bindings']):
        assert c['id'] == b['transaction_connection']
        assert all(c[k] == b[k] for k in ('source_rank','peer_rank','context_index'))
    kinds = {}
    for op in plan['operations']:
        if op['kind'] == 'barrier_signal': part = 'entry' if '.entry.' in op['id'] else 'exit'
        else: part = 'data' if op['kind'] == 'token_data' else 'counts'
        kinds[op['id']] = op['phase']+'.'+part
    expected, count = Counter(), 0
    for op in execution['native_operations']:
        if op['kind'] != 'remote' or 'self_service' in initial['connections'][op['connection']]: continue
        count += 1
        identity = execution['native_tasks'][str(op['id'])]['modern_operation']
        stage = kinds[identity]
        assert op['verb'] in ('put','fetch_add')
        size = 8 if op['verb'] == 'fetch_add' else (
            op['source']['bytes'] if 'source' in op else len(bytes.fromhex(op['hex'])))
        expected[stage,op['connection'],'request'] += 32+size
        if op['verb'] == 'fetch_add': expected[stage,op['connection'],'reply'] += 40
    actual = {(r['stage'],r['connection'],r['direction']):r['bytes'] for r in declaration['messages']}
    assert actual == expected, 'native request/reply demand mismatch'
    return len(connections), count


def all_paths(topology, src, snic, dst, dnic):
    gpu = {n['id'] for n in topology['nodes'] if n['kind'] == 'gpu'}
    adjacent = defaultdict(list)
    for e in topology['links']:
        adjacent[e['src']].append((e['dst'],e['src_port'],e['dst_port']))
        adjacent[e['dst']].append((e['src'],e['dst_port'],e['src_port']))
    first, = [p for p in adjacent[src] if p[1] == snic]
    last, = [p for p in adjacent[dst] if p[1] == dnic]
    answer = []
    def visit(node, seen, path):
        if node == last[0]:
            answer.append([*path,(node,last[2])]); return
        for peer,port,_ in adjacent[node]:
            if peer not in gpu and peer not in seen:
                visit(peer, seen | {peer}, [*path,(node,port)])
    visit(first[0], {first[0]}, [(src,snic)])
    assert answer
    shortest = min(map(len,answer))
    return [p for p in answer if len(p) == shortest]


def projection_check(topology, projection, declaration):
    rates = {}
    for e in topology['links']:
        rates[e['src'],e['src_port']] = e['rate_bps']
        rates[e['dst'],e['dst_port']] = e['rate_bps']
    fixed, split, hashes = Counter(), Counter(), 0
    assert len(projection['messages']) == len(declaration['messages'])
    for msg,row in zip(projection['messages'],declaration['messages']):
        assert all(msg[k] == v for k,v in row.items())
        choices = all_paths(topology,msg['src'],msg['source_nic'],msg['dst'],msg['destination_nic'])
        assert len(choices) == msg['path_count']
        raw = b''.join(v.to_bytes(4,'little') for v in (
            msg['source_ip'],msg['destination_ip'],msg['wire_port'] | (msg['wire_port'] << 16)))
        active, selected, order = choices, [choices[0][0]], []
        for position in range(1,len(choices[0])):
            node = active[0][position][0]
            candidates = list(dict.fromkeys(p[position][1] for p in active))
            order.append(dict(node=node,ports=candidates))
            value = hash_bytes(raw,11+node)
            assert value == hash_words((msg['source_ip'],msg['destination_ip'],
                msg['wire_port'] | (msg['wire_port'] << 16)),11+node)
            pick = candidates[value % len(candidates)]
            selected.append((node,pick))
            active = [p for p in active if p[position] == (node,pick)]
            hashes += 1
        assert order == msg['candidate_order'], 'native candidate order differs'
        assert [list(p) for p in selected] == msg['path'], 'hashed route differs'
        for node,port in selected: fixed[msg['stage'],node,port] += row['bytes']
        for path in choices:
            for node,port in path: split[msg['stage'],node,port] += Fraction(row['bytes'],len(choices))
    for key,counter in (('deterministic_load',fixed),('equal_path_load',split)):
        actual = {(r['stage'],r['node'],r['port']):Fraction(r['bytes_numerator'],r['bytes_denominator']) for r in projection[key]}
        assert actual == counter, 'projected byte demand differs'
    for s in projection['stages']:
        keys = [k for k in fixed.keys() | split.keys() if k[0] == s['stage']]
        a = max(Fraction(fixed[k]*8*10**9,rates[k[1:]]) for k in keys)
        b = max(Fraction(split[k]*8*10**9,rates[k[1:]]) for k in keys)
        assert s['exact_service_fraction'] == [a.numerator,a.denominator]
        assert s['split_service_fraction'] == [b.numerator,b.denominator]
        assert s['peak_service_ratio'] == float(a/b)
    return len(projection['messages']), hashes


def identity_check(observed, files):
    assert all(sha(observed/n) == h for n,h in files.items()), 'source identity mismatch'


def rejected(function, *args):
    try: function(*args)
    except AssertionError: return True
    raise AssertionError('corruption was accepted')


def execute(run, output):
    observed = run/'observed'
    identity = load(observed/'identity.json')['files']
    identity_check(observed,identity)
    prep = load(run/'preparation.json')
    assert all(sha(run/n) == h for n,h in prep['copied'].items())
    source = load(observed/'source.json')
    intake = load(observed/'intake.json')
    receipts = {row['name']:row for row in intake['files']}
    assert source['source']['confirmed_directory'] == 'cognitivecomputations/DeepSeek-R1-AWQ/mmlu/'
    assert not source['source']['newly_supplied_remote_identity_verified']
    for row in source['selected']:
        name = row['name']
        assert receipts[name]['eligible_offline'] and receipts[name]['origin_directory_user_confirmed']
        original = run/'truth/routes'/name
        assert sha(original) == row['sha256'] == receipts[name]['sha256']
        assert load(original)[1]['3'][0] == row['choices']
    totals = Counter()
    for batch in (4,8):
        declaration = load(run/f'predictions/batch{batch}-demand.json')
        plan = load(observed/f'batch{batch}/plan.json')
        initial = load(run/f'truth/batch{batch}/initial.json')
        execution = load(run/f'truth/batch{batch}/modern-execution.json')
        a,b = native_check(declaration,plan,initial,execution)
        totals['native_connections'] += a
        totals['native_remote_operations'] += b
        for cores in (1,2,4):
            topology = load(observed/f'cores{cores}/topology.json')
            projection = load(run/f'predictions/batch{batch}-cores{cores}.json')
            a,b = projection_check(topology,projection,declaration)
            totals['path_queries'] += a
            totals['hash_checks'] += b
            if cores == 1:
                assert projection['deterministic_load'] == projection['equal_path_load']
                assert all(s['peak_service_ratio'] == 1 for s in projection['stages'])
    observed_sources = load(observed/'sources.json')
    snippets = {
        'predictor': "count[target] == 1",
        'fabric': 'sw->SetEcmpSeed(config.at("seed")',
        'modern_binding': 'request_port=24000+2*identity',
        'streams': 'cnp_route',
        'cnp_projection': 'projected.ack.sport=fid',
        'native_switch': 'EcmpHash(buf.u8, 12, m_ecmpSeed) % nexthops.size()',
        'native_host': 'm_localAddressNic.find(qp->sip.Get())',
        'alibaba_switch': 'EcmpHash(buf.u8, 12, m_ecmpSeed) % nexthops.size()',
        'alibaba_host': "Mellanox's version of DCQCN",
    }
    locations = {}
    for name,needle in snippets.items():
        row = observed_sources[name]
        path = observed/row['path']
        assert sha(path) == row['sha256']
        matches = [i for i,line in enumerate(path.read_text().splitlines(),1) if needle in line]
        assert matches
        locations[name] = dict(**row,anchor_lines=matches)
    # Each corruption targets a separately checked object, not the success flag.
    bad_identity = identity.copy(); key = next(iter(bad_identity)); bad_identity[key] = '0'*64
    bad = deepcopy(declaration)
    next(c for c in bad['connections'] if 'request_port' in c)['request_port'] += 2
    port_rejected = rejected(native_check,bad,plan,initial,execution)
    bad = deepcopy(projection)
    candidates = next(o for m in bad['messages'] for o in m['candidate_order'] if len(o['ports']) > 1)
    candidates['ports'].reverse()
    order_rejected = rejected(projection_check,topology,bad,declaration)
    bad = deepcopy(projection); bad['deterministic_load'][0]['bytes_numerator'] += 1
    byte_rejected = rejected(projection_check,topology,bad,declaration)
    result = dict(passed=True,**totals,source_locations=locations,
        corruption_rejections=dict(source_hash=rejected(identity_check,observed,bad_identity),
            connection_port=port_rejected,candidate_order=order_rejected,demand_bytes=byte_rejected),
        observed_only_prediction=True,multipath_native_networks_executed=0,
        physical_timing_or_speedup_verified=False)
    save(output,result)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a = p.parse_args()
    execute(a.run.resolve(),a.output.resolve())
