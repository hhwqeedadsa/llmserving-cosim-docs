"""Independent stdlib packet, receiver, memory, transcript and baseline audit.

Does not import preparation, controller, platform analyzers, tests or fixtures.
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct

SIZE, STRIDE, RECV, CANONICAL, SOURCE = 14336, 16384, 4096, 262144, 1048576


def read(path):
    return json.loads(Path(path).read_text())


def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def fail_unless(ok, why):
    if not ok:raise AssertionError(why)


def expected(request, owner, experts):
    seed = json.dumps([request, owner, sorted(experts)], separators=(',', ':')).encode()
    return hashlib.shake_256(b'MoE-functional-owner-output/1:'+seed).digest(SIZE)


def sparse_bytes(window):
    result = bytearray(window['bytes']); used = set()
    for page in window['pages']:
        offset, body = page['offset'], bytes.fromhex(page['hex'])
        fail_unless(offset not in used and offset % 4096 == 0, 'invalid/repeated sparse page')
        used.add(offset)
        fail_unless(len(body) == min(4096, len(result)-offset), 'sparse page length')
        result[offset:offset+len(body)] = body
    fail_unless(window['resident_bytes'] == len(used)*4096, 'resident accounting')
    return bytes(result)


def audit_case(stage, condition, tamper=None):
    manifest = read(stage/'manifest.json')
    request = next(r for r in manifest['requests'] if r['request'] == condition['request'])
    original_raw = Path(request['file'])
    # Prefer bundled originals, so relocated audits cannot silently depend on
    # source-machine absolute paths remaining present.
    raw = stage/'raw'/original_raw.name
    if not raw.is_file():raw = original_raw
    fail_unless(hashlib.sha256(raw.read_bytes()).hexdigest() == request['sha256'], 'raw routing hash')
    experts = read(raw)[1]['4'][0]
    fail_unless(len(experts) == len(set(experts)) == 8, 'Top8 identity')
    owner_experts = {o: sorted(e for e in experts if e//64 == o) for o in range(4)
                     if any(e//64 == o for e in experts)}
    owners = sorted(owner_experts); bad = min(owners)
    fail_unless(owners == request['owners'], 'owner derivation')
    root = stage/'cases'/condition['name']
    r, initial = read(root/'result.json'), read(root/'initial.json')
    observed = rows(root/'observed/decisions.jsonl')
    control = rows(root/'control.jsonl')
    if tamper == 'missing_target_effect':r['transactions']['target_events'].pop(0)
    if tamper == 'wrong_canonical':
        page = next(w for w in r['transactions']['windows'] if w['owner'] == 4 and w['key'] == 9)['pages'][0]
        body = bytearray.fromhex(page['hex']); body[0] ^= 1; page['hex'] = body.hex()
    if tamper == 'forged_progress':
        event = next(e for row in observed for e in row['view']['events'] if e['event'] == 'applied')
        event['time_ns'] -= 1
    fail_unless(not r['passed'] and not r['session']['finished'] and r['finish_ns'] == 5000000
                and 'execution_error' not in r, 'native partial result/cutoff')
    t, transport, cap = r['transactions'], r['transactions']['transport'], r['packet_capture']
    fail_unless({p for p in r['loaded_libraries'] if Path(p).name.startswith('libns3')} ==
                {row['path'] for row in manifest['libraries']}, 'actual loaded native library paths')
    fail_unless(cap['complete'] and cap['omitted_events'] == 0, 'capture incomplete')
    fail_unless(transport['recovery']['complete'] and transport['source_isolation']['terminal_feedback_complete'], 'recovery log incomplete')
    failures = transport['recovery']['failures']
    fail_unless(len(failures) == 1 and failures[0]['stream'] == 2*bad, 'failure identity')
    fail_unless(transport['source_isolation']['wire_idle'] and not transport['closed']
                and not transport['source_isolation']['failed_resources_released'], 'failed resource lifetime')
    fail_unless(transport['source_isolation']['healthy_registered_work_drained'], 'healthy unfinished work')
    fail_unless(len(control) == len(observed)*2, 'one observed view per decision')
    remote, copies, local = {}, {}, {}
    seen_applied, chosen = {}, None
    replay, recovery_at, coverage_at, ready_at, generic_plan = None, None, None, None, None
    timer = False; native_events = []
    for n, obs in enumerate(observed):
        native, commit = control[2*n], control[2*n+1]
        fail_unless(native['direction'] == 'native' and commit['direction'] == 'frontend', 'transcript alternation')
        v, c = native['message'], commit['message']; native_events += v['events']
        allowed_remote = set(owners) | {100+o for o in owners}
        allowed_local = {50} | {200+o for o in owners}
        filtered = dict(decision_id=n, now_ns=v['now_ns'], events=[e for e in v['events'] if
                        (e['operation'] in allowed_remote and e['event'] == 'applied') or e['operation'] in allowed_local])
        fail_unless(obs['view'] == filtered, 'forged or unfiltered receiver progress')
        fail_unless(c['decision_id'] == n and c['now_ns'] == v['now_ns'] and not c['finish'], 'commit identity/forbidden Close')
        fail_unless(obs['submissions'] == c['submissions'], 'observed action differs from actual commit')
        for e in filtered['events']:
            if e['event'] == 'applied':seen_applied[e['operation']] = e['time_ns']
            if e['operation'] == 50 and e['event'] == 'completed':timer = True
        expected_replay = []
        if n == 0:
            fail_unless(v['now_ns'] == 0 and not v['events'], 'initial callback')
        elif chosen is None:
            missing = [o for o in owners if o not in seen_applied]
            if not missing:
                chosen = {o: o for o in owners}; recovery_at = v['now_ns']; replay = []
                generic_plan = []
            elif timer:
                generic_plan = list(missing)
                expected_replay = owners if condition['strategy'] == 'whole_token' else missing
                chosen = {o: 100+o if o in expected_replay else o for o in owners}
                recovery_at, replay = v['now_ns'], expected_replay
                if condition['strategy'] == 'owner_retention':
                    fail_unless(expected_replay == missing, 'operation-level plan differs from retention')
        submitted_replay = []
        for op in c['submissions']:
            opid = op['id']
            if op['kind'] == 'remote':
                fail_unless(opid not in remote and op['verb'] == 'put', 'repeated/invalid remote op')
                owner = opid % 100; retry = opid >= 100
                fail_unless(owner in owners and opid == owner+(100 if retry else 0), 'remote op identity')
                fail_unless(op['connection'] == owner+(4 if retry else 0) and op['key'] == 7
                            and op['address'] == RECV+(owner+(4 if retry else 0))*STRIDE, 'remote bank/connection')
                fail_unless(op['source'] == dict(key=8, address=SOURCE, bytes=SIZE), 'source identity')
                if retry:
                    fail_unless(timer and v['now_ns'] >= 100000, 'premature retry')
                    fail_unless(op['after'] == [dict(operation=50, event='completed')], 'retry dependency')
                    submitted_replay.append(owner)
                else:fail_unless(n == 0 and op['after'] == [], 'late original put')
                remote[opid] = op
            elif opid == 50:
                fail_unless(n == 0 and op == dict(id=50, kind='local', rank=4, duration_ns=100000, after=[]), 'timer semantics')
                local[opid] = op
            else:
                owner = opid-200
                fail_unless(chosen is not None and owner in owners and owner not in copies, 'invalid/repeated output contribution')
                fail_unless(all(i in seen_applied for i in chosen.values()), 'copy before selected full coverage')
                sourceop = chosen[owner]; bank = 4 if sourceop >= 100 else 0
                fail_unless(op['copy'] == dict(source=dict(key=7, address=RECV+(owner+bank)*STRIDE, bytes=SIZE),
                                              target=dict(key=9, address=CANONICAL+owner*STRIDE)), 'copy bank mismatch')
                fail_unless(op['after'] == [dict(operation=sourceop, event='applied')] and op['duration_ns'] == 0 and op['rank'] == 4,
                            'copy dependency')
                copies[owner] = op
        fail_unless(submitted_replay == expected_replay, 'actual recovery plan mismatch')
    fail_unless(native_events == r['session']['events'], 'session history differs from transcript')
    fail_unless(chosen is not None and sorted(copies) == owners, 'incomplete chosen contributions')
    fail_unless(set(remote) == set(owners) | {100+o for o in replay}, 'initial/retry operation coverage')
    # Actual source payload packets and source device TX must match one-to-one.
    packets = [dict(zip(transport['source_packet_columns'], row)) for row in transport['source_packets']]
    payloads = transport['source_payload_packets']
    fail_unless(len(packets) == len(payloads), 'source byte capture mismatch')
    by_uid = {}
    for packet, bodyrow in zip(packets, payloads):
        fail_unless(all(packet[k] == bodyrow[k] for k in ('time_ns', 'stream', 'sequence')), 'source payload order')
        body = bytes.fromhex(bodyrow['hex'])
        fail_unless(len(body) == packet['payload_bytes'] and packet['packet_uid'] not in by_uid, 'source segment identity')
        conn = packet['stream']//2
        fail_unless(packet['stream'] % 2 == 0 and conn in {op['connection'] for op in remote.values()}, 'unregistered packet')
        if conn >= 4:fail_unless(packet['time_ns'] >= 100000, 'spare packet before timer')
        by_uid[packet['packet_uid']] = (packet, body)
    # Also decode the complete source frame of the failed original, whose final
    # segment may never reach the receiver. Retransmits must preserve bytes.
    segments = defaultdict(dict)
    for packet, bodyrow in zip(packets, payloads):
        body = bytes.fromhex(bodyrow['hex']); parts = segments[packet['stream']]
        fail_unless(packet['sequence'] not in parts or parts[packet['sequence']] == body,
                    'retransmission changed source bytes')
        parts[packet['sequence']] = body
    for stream, parts in segments.items():
        raw = bytearray()
        for sequence, body in sorted(parts.items()):
            fail_unless(sequence == len(raw), 'source frame segmentation gap')
            raw.extend(body)
        magic,verb,sequence,address,key,size = struct.unpack('>IIQQII',raw[:32])
        operation = next(op for op in remote.values() if op['connection'] == stream//2)
        owner = operation['connection']%4
        fail_unless((magic,verb,sequence,address,key,size) ==
                    (0x4d525431,1,0,operation['address'],7,SIZE), 'source MRT1 addressing')
        fail_unless(bytes(raw[32:]) == expected(request['request'],owner,owner_experts[owner]),
                    'source frame does not carry selected expert output identity')
    capture = [dict(zip(cap['columns'], row)) for row in cap['events']]
    gpu_ids = set(range(8)); source_tx = [e for e in capture if e['event'] == 0 and e['node'] in gpu_ids and e['ip_protocol'] == 17]
    fail_unless(len(source_tx) == len(packets), 'source TX count')
    for e in source_tx:
        p, body = by_uid[e['packet_uid']]
        fail_unless((e['time_ns'],e['node'],e['sequence'],e['message_id'],e['data_payload_bytes']) ==
                    (p['time_ns'],p['src'],p['sequence'],p['stream'],len(body)), 'actual source TX mismatch')
    # Replay receiver continuous-prefix acceptance; physical out-of-order RX is not accepted.
    prefixes = defaultdict(bytearray); accepted = {}; duplicate_rx = 0; out_of_order = 0
    tx = Counter(); data_tx = Counter(); control_tx = Counter(); paths = defaultdict(list)
    for e in capture:
        if e['event'] == 0:
            tx[e['node'],e['port']] += e['model_bytes']
            (data_tx if e['ip_protocol'] == 17 else control_tx)[e['node'],e['port']] += e['model_bytes']
            if e['ip_protocol'] == 17:paths[e['packet_uid']].append([e['node'],e['port']])
        if not(e['event'] == 1 and e['node'] == 4 and e['ip_protocol'] == 17):continue
        p, body = by_uid[e['packet_uid']]; stream = p['stream']; buf = prefixes[stream]
        if p['sequence'] < len(buf):duplicate_rx += 1; continue
        if p['sequence'] > len(buf):out_of_order += 1; continue
        buf.extend(body)
        if len(buf) >= 32:
            magic, verb, sequence, address, key, size = struct.unpack('>IIQQII', buf[:32])
            fail_unless((magic,verb,sequence,key,size) == (0x4d525431,1,0,7,SIZE), 'invalid MRT1 frame')
            if len(buf) == 32+size:
                fail_unless(stream not in accepted, 'duplicate logical target effect')
                accepted[stream] = dict(connection=stream//2, sequence=sequence, verb=verb,
                                        address=address, key=key, body_hex=bytes(buf[32:]).hex(),
                                        time_ns=e['time_ns'], duplicate=False)
            fail_unless(len(buf) <= 32+size, 'unexpected extra frame')
    target = t['target_events']
    fail_unless(len(target) == len(accepted), 'missing/extra target effect')
    for event in target:
        expected_event = accepted[2*event['connection']]
        fail_unless(all(event[k] == value for k,value in expected_event.items()), 'target effect not backed by accepted wire bytes')
    port_services = []
    for port in r['ports']:
        k = port['node'],port['port']
        fail_unless((tx[k],data_tx[k],control_tx[k]) == (port['model_tx_bytes'],port['data_tx_bytes'],port['control_tx_bytes']), 'port service mismatch')
        if tx[k]:port_services.append(dict(node=k[0],port=k[1],tier=port['tier'],bytes=tx[k],
                                         service_ns=tx[k]*8*10**9/port['rate_bps']))
    original_missing = [o for o in owners if 2*o not in accepted]
    fail_unless(original_missing == ([bad] if condition['fault'] == 'data_tail' else []), 'fault delivery semantics')
    # Reconstruct real receiver bytes; canonical copy must consume those bytes.
    recv, canonical = bytearray(STRIDE*8), bytearray(STRIDE*4)
    for event in accepted.values():
        owner = event['connection']%4; body = bytes.fromhex(event['body_hex'])
        fail_unless(body == expected(request['request'],owner,owner_experts[owner]), 'expert/content identity')
        offset = event['address']-RECV
        fail_unless(0 <= offset and offset+SIZE <= len(recv), 'wire target range')
        recv[offset:offset+SIZE] = body
    local_times = {row['id']:row for row in r['local_work']}
    fail_unless(local_times[50]['completed_ns'] == 100000, 'timer completion')
    for owner, opid in chosen.items():
        effect = accepted[2*remote[opid]['connection']]
        op_progress = next(p for p in t['operations'] if p['id'] == opid)
        fail_unless(op_progress['applied_ns'] == effect['time_ns'] == seen_applied[opid], 'target callback evidence')
        body = bytes.fromhex(effect['body_hex']); offset = owner*STRIDE
        canonical[offset:offset+SIZE] = body
        phases = [e for e in t['local_memory_events'] if e['operation'] == owner+200]
        fail_unless([e['kind'] for e in phases] == ['acquired','captured','committed','released'], 'local copy phases')
        fail_unless(all(bytes.fromhex(e['hex']) == body for e in phases if 'hex' in e), 'copy not derived from receive bytes')
        fail_unless(local_times[owner+200]['start_ns'] >= max(100000,effect['time_ns'])
                    and local_times[owner+200]['completed_ns'] >= local_times[owner+200]['start_ns'], 'serial timer/copy ordering')
    windows = {(w['owner'],w['key']):w for w in t['windows']}
    fail_unless(sparse_bytes(windows[4,7]) == bytes(recv), 'receive window differs from packet replay')
    fail_unless(sparse_bytes(windows[4,9]) == bytes(canonical), 'wrong canonical output')
    for owner in owners:
        fail_unless(sparse_bytes(windows[owner,8]) == expected(request['request'],owner,owner_experts[owner]), 'source mutated')
    expected_lease = [dict(operation=bad,owner=bad,key=8,address=SOURCE,bytes=SIZE)]
    fail_unless(t['active_source_leases'] == expected_lease and not t['active_local_memory_leases'], 'retained/released leases')
    released = {e['operation'] for e in t['source_events'] if e['kind'] == 'released'}
    fail_unless(released == set(remote)-{bad}, 'healthy source release mismatch')
    original_progress = {op['id']:op for op in t['operations'] if op['id'] in owners}
    fail_unless(original_progress[bad]['request_acked_ns'] == -1 and original_progress[bad]['completed_ns'] == -1, 'failed source falsely complete')
    coverage_at = max(accepted[2*remote[op]['connection']]['time_ns'] for op in chosen.values())
    ready_at = max(local_times[200+o]['completed_ns'] for o in owners)
    summary = read(root/'observed/summary.json')
    fail_unless(summary['coverage_ns'] == coverage_at and summary['fragment_ready_ns'] == ready_at
                and summary['selected'] == {str(k):v for k,v in chosen.items()}, 'controller completion mismatch')
    fail_unless(summary['recovery']['replay_owners'] == replay and summary['recovery']['time_ns'] == recovery_at, 'reported retry plan mismatch')
    fail_unless(summary['recovery']['generic_operation_plan'] == generic_plan and
                summary['recovery']['missing_original_operations'] == generic_plan,
                'strong baseline not derived from same receiver observations')
    code_classification_error = summary['terminal'] is None
    if code_classification_error:
        err = (stage/'monitors'/condition['name']/'stderr.log').read_text()
        fail_unless(condition == manifest['conditions'][0] and 'Scale-Out topology: native transaction session failed; inspect partial result' in err,
                    'unexpected controller terminal error')
    else:fail_unless(summary['terminal'] == 'native_partial_exit_1' and summary['native_exit_code'] == 1, 'terminal classification')
    headers = cap['wire_headers']; hs = [dict(zip(headers['columns'],row)) for row in headers['headers']]
    routes = defaultdict(set)
    topology = read(stage/'inputs/topology/topology.json')
    peers = {}
    for edge in topology['links']:
        peers[edge['src'],edge['src_port']] = edge['dst']
        peers[edge['dst'],edge['dst_port']] = edge['src']
    for p in packets:
        path = paths[p['packet_uid']]
        fail_unless(path and path[0][0] == p['src'], 'missing source route')
        route = [p['src']]
        for node,port in path:
            fail_unless(node == route[-1], 'disconnected route')
            route.append(peers[node,port])
        fail_unless(route[-1] == 4, 'route does not reach receiver/drop point')
        routes[p['stream']].add(tuple(route))
    for e,index in zip(capture,headers['event_header_indices']):
        if e['ip_protocol'] != 17:continue
        h=hs[index]; fail_unless(h['source_port'] == 24000+e['message_id'] and h['destination_port'] == h['source_port'], 'port/ECMP identity')
    spare = [p for p in packets if p['stream'] >= 8]
    counts = Counter((p['stream'],p['sequence']) for p in packets)
    retransmit_payload = sum(p['payload_bytes'] for p in packets)-sum(
        next(p['payload_bytes'] for p in packets if (p['stream'],p['sequence']) == key) for key in counts)
    last_ack = max([e['time_ns'] for e in capture if e['event'] == 1 and e['ip_protocol'] == 252] or [-1])
    return dict(name=condition['name'],request=condition['request'],fault=condition['fault'],strategy=condition['strategy'],
                owners=owners,experts_by_owner=owner_experts,failed_owner=bad,
                replay_owners=replay,generic_missing_originals=generic_plan,
                owner_plan_equivalent_to_operation_replay=(replay == generic_plan),
                coverage_ns=coverage_at,fragment_ready_ns=ready_at,source_failure_ns=failures[0]['time_ns'],
                original_failed_owner_applied_ns=original_progress[bad]['applied_ns'],
                failed_source_still_unacked=True,retained_source_lease_bytes=SIZE,
                source_packets=len(packets),source_payload_tx_bytes=sum(p['payload_bytes'] for p in packets),
                source_model_tx_bytes=sum(e['model_bytes'] for e in source_tx),
                retransmit_payload_bytes=retransmit_payload,recovery_source_packets=len(spare),
                recovery_payload_tx_bytes=sum(p['payload_bytes'] for p in spare),
                last_received_ack_ns=last_ack,last_captured_wire_event_ns=max(e['time_ns'] for e in capture),
                fragment_precedes_global_failure_cutoff_ns=r['finish_ns']-ready_at,
                accepted_target_effects=len(target),physical_duplicate_rx=duplicate_rx,physical_out_of_order_rx=out_of_order,
                fault_drops=r['fault_drops'],controller_exit_classification_error=code_classification_error,
                ecn_ce_seen_total=sum(p['ecn_ce_seen'] for p in r['ports']),
                pfc_pause_total=sum(p['pfc_pause'] for p in r['ports']),
                max_queue_at_hook_bytes=max(p['max_queue_at_hook_bytes'] for p in r['ports']),
                paths={str(k):[list(v) for v in sorted(values)] for k,values in routes.items()},
                port_services=port_services,hardware_calibrated=False,full_inference=False)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--first-only',action='store_true')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();m=read(a.stage/'manifest.json')
    cases=m['conditions'][:1] if a.first_only else m['conditions']
    results=[audit_case(a.stage,c) for c in cases]
    tamper=[]
    for kind in ('missing_target_effect','wrong_canonical','forged_progress'):
        try:audit_case(a.stage,cases[0],kind)
        except AssertionError as error:tamper.append(dict(kind=kind,rejected=True,reason=str(error)))
        else:raise AssertionError('tampering accepted: '+kind)
    output=dict(schema_version='receiver-recovery-independent-audit/1',cases=results,tampering=tamper,
                independent_stdlib=True,complete_matrix=not a.first_only)
    a.output.write_text(json.dumps(output,indent=2,sort_keys=True)+'\n')
    print(json.dumps(dict(cases=len(results),tamper_rejections=len(tamper))))


if __name__=='__main__':main()
