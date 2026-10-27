"""Independent port/queue counts and evidence rejection; zero network runs."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import experiments.interaction_scope.analyze as implementation


def load(path):
    return json.loads(path.read_bytes())


def graph_sets(resources):
    vertices = sorted(resources)
    adjacent = {v:set() for v in vertices}
    for index,a in enumerate(vertices):
        for b in vertices[index+1:]:
            if resources[a] & resources[b]:
                adjacent[a].add(b)
                adjacent[b].add(a)
    pending = set(vertices)
    groups = []
    while pending:
        reached = set()
        stack = [min(pending)]
        while stack:
            v = stack.pop()
            if v not in reached:
                reached.add(v)
                stack.extend(adjacent[v]-reached)
        pending -= reached
        groups.append(sorted(reached))
    return sorted(groups),sum(map(len,adjacent.values()))//2


def independent(directory, output, expected):
    raw = load(directory/'result.json')
    initial = load(directory/'initial.json')
    cap = raw['packet_capture']
    witness = load(output)
    reconstructed_tx,reconstructed_rx = Counter(),Counter()
    port_bytes = Counter()
    all_ports,requests = defaultdict(set),defaultdict(set)
    uid_owner = {}
    for p in witness['paths']:
        assert p['packet_uid'] not in uid_owner
        uid_owner[p['packet_uid']] = p
        for hop in p['hops']:
            reconstructed_tx[p['packet_uid'],hop['node'],hop['port'],hop['tx_ns'],hop['model_bytes']] += 1
            reconstructed_rx[p['packet_uid'],hop['peer'],hop['peer_port'],hop['rx_ns'],hop['model_bytes']] += 1
            port_bytes[hop['node'],hop['port']] += hop['model_bytes']
            all_ports[p['connection']].add((hop['node'],hop['port']))
            if p['protocol']=='udp' and p['direction']=='request':
                requests[p['connection']].add((hop['node'],hop['port']))
    actual_tx = Counter((r[4],r[1],r[2],r[0],r[8]) for r in cap['events'] if r[3]==0)
    actual_rx = Counter((r[4],r[1],r[2],r[0],r[8]) for r in cap['events'] if r[3]==1)
    assert reconstructed_tx==actual_tx and reconstructed_rx==actual_rx
    connections = {c['id']:c for c in initial['connections']}
    for p in witness['paths']:
        c = connections[p['connection']]
        src,dst = (c['src'],c['dst']) if p['direction']=='request' else (c['dst'],c['src'])
        if p['protocol']=='ack':
            src,dst = dst,src
        assert (src,dst)==(p['source'],p['destination'])
    # An independent endpoint/port attribution from each captured wire header.
    for row,index in zip(cap['events'],cap['wire_headers']['event_header_indices']):
        p = uid_owner[row[4]]
        header = cap['wire_headers']['headers'][index]
        c = connections[p['connection']]
        port = c['request_port'] if p['direction']=='request' else c['response_port']
        assert header[4]==header[5]==port
        assert header[1]==0x0b000001+32*p['source']+c['plane']
        assert header[2]==0x0b000001+32*p['destination']+c['plane']
    pending = {}
    integrals,waits = Counter(),[]
    for row in cap['events']:
        key = (row[1],row[2],row[4],row[6],row[7],row[8])
        if row[3]==3:
            assert key not in pending
            pending[key] = row[0]
        elif row[3]==4:
            start = pending.pop(key)
            wait = row[0]-start
            integrals[row[1],row[2]] += wait*row[8]
            waits.append(wait)
    assert not pending
    for port in raw['ports']:
        key = port['node'],port['port']
        assert port_bytes[key]==port['model_tx_bytes']
        if 'queue_metrics' in port:
            assert integrals[key]==port['queue_metrics']['area_byte_ns']
    positive = witness['positive_waits']
    assert len(positive)==sum(w>0 for w in waits)==expected['positive_wait_packets']
    assert sum(waits)==expected['per_packet_queue_wait']['wait_ns']
    assert sum(w['wait_ns'] for w in positive)==sum(waits)
    assert all(sum(b['overlap_ns'] for b in w['blockers'])==w['wait_ns'] for w in positive)
    assert sum(b['overlap_ns'] for w in positive for b in w['blockers'] if b['connection']!=w['connection'])==expected['per_packet_queue_wait']['other_connection_ns']
    assert sum(b['overlap_ns'] for w in positive for b in w['blockers'] if b['protocol']=='ack')==expected['per_packet_queue_wait']['ack_serialization_ns']
    full_groups,full_edges = graph_sets(all_ports)
    request_groups,request_edges = graph_sets(requests)
    assert full_groups==expected['full_direction_port_sharing']['components'] and full_edges==expected['full_direction_port_sharing']['direct_connection_pairs']
    assert request_groups==expected['request_only_port_sharing']['components'] and request_edges==expected['request_only_port_sharing']['direct_connection_pairs']
    by_plane = defaultdict(list)
    for identity in all_ports:
        by_plane[connections[identity]['plane']].append(identity)
    assert full_groups==sorted(sorted(group) for group in by_plane.values())
    return dict(source_udp_packets=len(raw['transactions']['transport']['source_packets']),
                packet_paths=len(witness['paths']),physical_transmissions=sum(actual_tx.values()),
                queue_wait_ns=sum(waits),queue_residences=len(waits),
                full_direction_pairs=full_edges,request_only_pairs=request_edges)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage',type=Path,required=True)
    parser.add_argument('--repository',type=Path,required=True)
    args = parser.parse_args()
    source = load(args.stage/'source.json')
    for name,digest in source['files'].items():
        assert hashlib.sha256((args.stage/name).read_bytes()).hexdigest()==digest
    for name,digest in source['source_hashes'].items():
        assert hashlib.sha256((args.repository/name).read_bytes()).hexdigest()==digest
    summaries = load(args.stage/'full-analysis/summary.json')
    counts = {name:independent(args.stage/name,args.stage/'full-analysis'/(name+'-witnesses.json'),summary)
              for name,summary in summaries.items()}
    for name in ('baseline','bandwidth'):
        again,_ = implementation.analyze(args.stage/name)
        assert again==summaries[name]
    documents = {name:load(args.stage/'baseline'/name) for name in ('topology.json','protocol.json','initial.json','result.json','modern-plan.json')}
    rejected = []
    def reject(label,change):
        modified = deepcopy(documents)
        change(modified)
        original = implementation.load
        implementation.load = lambda path:modified[path.name]
        try:
            implementation.analyze(Path('virtual-records'))
        except (ValueError,KeyError):
            rejected.append(label)
        else:
            raise AssertionError('modified evidence accepted: '+label)
        finally:
            implementation.load = original
    reject('incomplete_capture',lambda d:d['result.json']['packet_capture'].__setitem__('complete',False))
    reject('unmapped_wire_tuple',lambda d:d['result.json']['packet_capture']['wire_headers']['headers'][0].__setitem__(4,1))
    def change_event(d,code,column):
        row = next(r for r in d['result.json']['packet_capture']['events'] if r[3]==code)
        row[column] += 1
    reject('physical_receive_time_changed',lambda d:change_event(d,1,0))
    reject('queue_occupancy_changed',lambda d:change_event(d,3,10))
    def duplicate(d):
        c = d['result.json']['packet_capture']
        index = next(i for i,r in enumerate(c['events']) if r[3]==0)
        c['events'].append(deepcopy(c['events'][index]))
        c['wire_headers']['event_header_indices'].append(c['wire_headers']['event_header_indices'][index])
        c['observations'] += 1
    reject('ambiguous_duplicate_transmission',duplicate)
    def omit_ack(d):
        c = d['result.json']['packet_capture']
        kept = [(r,i) for r,i in zip(c['events'],c['wire_headers']['event_header_indices']) if r[6]!=252]
        c['events'] = [r for r,_ in kept]
        c['wire_headers']['event_header_indices'] = [i for _,i in kept]
        c['observations'] = len(kept)
    reject('all_ACKs_silently_omitted',omit_ack)
    report = dict(passed=True,independent_counts=counts,rejected=rejected,source_hashes_verified=True,
                  deterministic_reanalysis=True,new_networks=0)
    (args.stage/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
