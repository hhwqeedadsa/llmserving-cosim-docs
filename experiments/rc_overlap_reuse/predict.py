"""Observed-only generic port/time noninterference certificate, not a new solver."""
import argparse
from collections import defaultdict, deque
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+'\n')


def route(topology, src, dst):
    adjacency = defaultdict(list)
    assert len(topology['links']) == len(topology['nodes'])-1
    for edge in topology['links']:
        for a, b, ap, bp in ((edge['src'],edge['dst'],edge['src_port'],edge['dst_port']),
                             (edge['dst'],edge['src'],edge['dst_port'],edge['src_port'])):
            adjacency[a].append(dict(node=a, peer=b, port=ap, peer_port=bp,
                rate=edge['rate_bps'], delay=edge['delay_ns']))
    seen = {src}
    pending = deque([(src, [])])
    while pending:
        node, path = pending.popleft()
        if node==dst:
            return path
        for edge in adjacency[node]:
            if edge['peer'] not in seen:
                seen.add(edge['peer'])
                pending.append((edge['peer'], path+[edge]))
    raise AssertionError('unreachable')


def tail(path, start, size, label):
    events = []
    for edge in path:
        serial = (size*8*10**9 + edge['rate']//2)//edge['rate']
        events.append(dict(kind='tx', node=edge['node'], port=edge['port'], time_ns=start,
                           bytes=size, serial_ns=serial, packet=label))
        start += serial+edge['delay']
        events.append(dict(kind='rx', node=edge['peer'], port=edge['peer_port'], time_ns=start,
                           bytes=size, packet=label))
    return events, start


def predict(observed, mode):
    folder = observed/mode
    normal, fault = read(folder/'normal.json'), read(folder/'loss.json')
    result = read(folder/'run/result.json')
    topology, protocol = read(observed/'topology/topology.json'), read(observed/'rdma.json')
    identity = read(observed/'identity.json')
    recorded = {Path(k).name:v for k,v in read(folder/'run/inputs.json')['hashes'].items()}
    assert recorded == {'topology.json':sha(observed/'topology/topology.json'),
        'rdma.json':sha(observed/'rdma.json'), 'normal.json':sha(folder/'normal.json'),
        'moe-rdma-rc-concurrent':identity['backend_sha256']}
    changed = deepcopy(fault)
    changed['loss'] = None
    assert changed==normal and normal['loss'] is None
    rc = result['rc_concurrent']
    assert result['passed'] and result['drained'] and rc['drained'] and rc['drops']==0
    assert all(q['timeouts']==0 for q in rc['connections'])
    assert result['packet_capture']['complete'] and not result['packet_capture']['omitted_events']
    assert not any(p['pfc_pause'] or p['pfc_resume'] or p['ecn_ce_seen'] for p in result['ports'])
    rule = fault['loss']
    q = next(q for q in fault['connections'] if q['id']==rule['connection'])
    assert rule['opcode']==18 and rule['node']==q['src']
    psn = 0
    operation = None
    for index, op in enumerate(q['operations']):
        n = (len(bytes.fromhex(op['data_hex']))+normal['mtu']-1)//normal['mtu'] if op['kind']=='write' else 1
        if psn+n-1==rule['psn']:
            assert op['kind']=='fetch_add'
            operation = index
        psn += n
    assert operation is not None
    shifts = deepcopy(rc['connections'])
    for state in shifts:
        if state['id']==q['id']:
            for done in state['operations']:
                if done['operation']>=operation:
                    done['completed_ns'] += normal['rto_ns']
    diagnostic = dict(connections=shifts,
        finish_ns=max(o['completed_ns'] for s in shifts for o in s['operations']),
        scope='ordinary unchanged-peer and RTO-shift diagnostic, not strongest queue baseline')
    if operation!=len(q['operations'])-1:
        return dict(admitted=False, reason='nonterminal_source_completion_has_successors',
                    ordinary_rto_shift=diagnostic)
    def matches(e, kind, opcode):
        body = bytes.fromhex(e['body_hex'])
        return e['connection']==q['id'] and e['kind']==kind and body[0]==opcode and \
            struct.unpack_from('!I', body, 8)[0]&0xffffff==rule['psn']
    sent = [e for e in rc['endpoint_events'] if matches(e,'request_sent',20)]
    replies = [e for e in rc['endpoint_events'] if matches(e,'response_received',18)]
    assert len(sent)==len(replies)==1
    sizes = []
    for e in (sent[0], replies[0]):
        wires = [p for p in rc['physical_packets'] if p['packet_uid']==e['packet_uid']]
        assert wires and len({len(p['packet_hex']) for p in wires})==1
        sizes.append(len(bytes.fromhex(wires[0]['packet_hex'])))
    retry = sent[0]['time_ns']+normal['rto_ns']
    fwd, rev = route(topology,q['src'],q['dst']), route(topology,q['dst'],q['src'])
    assert fwd[0]['port']==q['source_nic'] and fwd[-1]['peer_port']==q['destination_nic']
    request, arrival = tail(fwd,retry,sizes[0],'retry_request')
    response, completed = tail(rev,arrival,sizes[1],'cached_reply')
    events = request+response
    # An otherwise idle service slot does not alone prove noninterference:
    # transmitting a retry changes the source NIC's round-robin cursor. Bound
    # future competitors so that this cursor cannot change a later choice.
    future_owners = set()
    for packet in rc['physical_packets']:
        if packet['kind']=='tx' and packet['node']==q['src'] and packet['port']==q['source_nic'] and packet['time_ns']>=retry:
            body = bytes.fromhex(packet['packet_hex'])[30:]
            if body[0] not in (17,18):
                owner = (int.from_bytes(body[4:8],'big')-2)//2
                if owner!=q['id']:
                    future_owners.add(owner)
    if len(future_owners)>1:
        return dict(admitted=False,reason='unproved_source_round_robin_suffix',ordinary_rto_shift=diagnostic)
    links = {(p['node'],p['port']):p['rate_bps'] for p in result['ports']}
    conflicts = []
    for new in events:
        if new['kind']!='tx':
            continue
        for old in rc['physical_packets']:
            if old['kind']!='tx' or (old['node'],old['port'])!=(new['node'],new['port']):
                continue
            old_size = len(bytes.fromhex(old['packet_hex']))
            rate = links[old['node'],old['port']]
            end = old['time_ns']+(old_size*8*10**9+rate//2)//rate
            if not (end<new['time_ns'] or new['time_ns']+new['serial_ns']<old['time_ns']):
                conflicts.append(dict(node=new['node'],port=new['port'],new_start_ns=new['time_ns'],
                    old_start_ns=old['time_ns'],old_end_ns=end))
    details = dict(retry_ns=retry, baseline_finish_ns=result['finish_ns'], tail_finish_ns=completed,
        global_idle_gate=retry>result['finish_ns'], tail_events=events, ordinary_rto_shift=diagnostic)
    if conflicts:
        return dict(details, admitted=False, reason='port_service_overlap', conflicts=conflicts)
    queue_bound = sum(p['max_queue_at_hook_bytes'] for p in result['ports'])+sum(sizes)
    if protocol['switch_buffer_bytes']!=8388608 or any(queue_bound>=r['kmin_bytes'] for r in protocol['ecn_by_rate'].values()):
        return dict(details, admitted=False, reason='unproved_mmu_or_feedback_bound', queue_bound=queue_bound)
    states = deepcopy(rc['connections'])
    for state in states:
        if state['id']==q['id']:
            state['operations'][-1]['completed_ns'] = completed
            state['timeouts'] = 1
    return dict(details, admitted=True, reason='terminal_feedback_and_port_time_noninterference',
        queue_bound_bytes=queue_bound, connections=states, memories=rc['memories'],
        target_effects=rc['target_effects'], connection=q['id'], psn=rule['psn'],
        finish_ns=max(result['finish_ns'],completed),
        physical_tx=sum(p['kind']=='tx' for p in rc['physical_packets'])+len(fwd)+len(rev),
        physical_rx=sum(p['kind']=='rx' for p in rc['physical_packets'])-1+len(fwd)+len(rev),
        physical_drop=1, novel_solver=False, performance_claim=False)


def execute(stage, output):
    assert not output.exists()
    observed = stage/'observed'
    hashes = {p.relative_to(observed).as_posix():sha(p) for p in sorted(observed.rglob('*')) if p.is_file()}
    save(output, dict(passed=True, observed_hashes=hashes,
        cases={m:predict(observed,m) for m in ('terminal','gated')}, truth_read=False,
        method='ordinary port/time noninterference certificate', new_innovation_proven=False))


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    execute(args.stage.resolve(),args.output.resolve())
