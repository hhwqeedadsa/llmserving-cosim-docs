"""Actual receive buffers forwarded on persistent native paths, with read guards.

Functional MRT1 snapshots/ACK leases, not GPU DMA, GIN timing or MoE execution.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time

from check_remote_transaction_workflow import audit, audit_memory
from check_transaction_local_stage_workflow import endpoint_audit, local_audit

ROOT = Path(__file__).resolve().parents[2]


def read(path): return json.loads(path.read_text())
def save(path, value): path.write_text(json.dumps(value, indent=2)+'\n')


def prefix_audit(result, config, mtu):
    """Decode actual wire prefixes, then independently replay writes and readers."""
    tx = result['transactions']; net = tx['transport']
    specs = {op['id']: op for op in config['operations']}
    rows = {r['id']: r for r in tx['operations']}
    bindings = {(r['connection'], r['sequence']): r for r in rows.values()}
    connections = {c['id']: c for c in config['connections']}
    captured = {e['operation']: e for e in tx['source_events'] if e['kind'] == 'captured'}
    target = {(e['connection'], e['sequence']): e for e in tx['target_events']}
    expected = {}; frames = {}; observed = {}
    for stream in net['streams']:
        identity = stream['id']; data = bytearray()
        for work in sorted((w for w in net['work'] if w['stream'] == identity), key=lambda w: w['begin']):
            assert work['begin'] == len(data)
            row = next(r for r in rows.values() if r['connection'] == identity//2 and
                       r['published_ns'] >= 0 and
                       (r['sequence'] == sum(w['stream'] == identity and w['begin'] < work['begin']
                                             for w in net['work']) if identity%2 == 0 else
                        # Replies skip ordinary puts: match the actual frame's
                        # work-ready time and target apply, with unique fixture times.
                        r['verb'] == 'fetch_add' and r['applied_ns'] == work['ready_ns']))
            op = specs[row['id']]
            if identity%2:
                verb, address, key = 3, 0, 0
                body = target[identity//2, row['sequence']]['old_value'].to_bytes(8, 'big')
            else:
                verb = 1 if op['verb'] == 'put' else 2; address, key = op['address'], op['key']
                body = (bytes.fromhex(captured[row['id']]['hex']) if 'source' in op else
                        bytes.fromhex(op['hex']) if verb == 1 else op['value'].to_bytes(8, 'big'))
            frame = struct.pack('>IIQQII', 0x4d525431, verb, row['sequence'], address, key, len(body))+body
            assert work['end'] == work['begin']+len(frame)
            data.extend(frame)
        expected[identity] = bytes(data)
    assert len(net['source_packets']) == len(net['source_payload_packets'])
    for p, payload in zip(net['source_packets'], net['source_payload_packets']):
        stamp, stream, src, dst, nic, seq, size, uid = p
        c = connections[stream//2]
        assert (src, dst, nic) == ((c['dst'], c['src'], c['destination_nic']) if stream%2 else
                                  (c['src'], c['dst'], c['source_nic']))
        body = bytes.fromhex(payload['hex'])
        assert (payload['time_ns'], payload['stream'], payload['sequence'], len(body)) == (stamp, stream, seq, size)
        data = observed.setdefault(stream, bytearray()); assert seq == len(data) and 0 < size <= mtu and uid >= 0
        work, = [w for w in net['work'] if w['stream'] == stream and w['begin'] <= seq < w['end']]
        assert size == min(mtu, work['end']-seq) and stamp >= work['published_ns']
        data.extend(body)
    partial = 0
    for stream, data in observed.items():
        assert bytes(data) == expected[stream][:len(data)] and len(data) <= len(expected[stream])
        offset = 0
        while offset+32 <= len(data):
            magic, verb, seq, address, key, length = struct.unpack_from('>IIQQII', data, offset)
            assert magic == 0x4d525431 and verb in (1, 2, 3) and 0 < length <= 32768
            if offset+32+length > len(data): break
            frames[stream, seq] = dict(verb=verb, address=address, key=key, body=bytes(data[offset+32:offset+32+length]))
            offset += 32+length
        partial += offset < len(data)
    memory = audit_memory(tx, config, frames, rows)
    # All physically submitted source puts acquire one guard; a still-queued
    # one has no capture or packet. No completion is invented at the cutoff.
    for identity, row in rows.items():
        if 'source' not in specs[identity]: continue
        events = [e for e in tx['source_events'] if e['operation'] == identity]
        assert [e['kind'] for e in events] == ['acquired']+(['captured'] if row['published_ns'] >= 0 else [])+(['released'] if row['completed_ns'] >= 0 else [])
    return dict(source_puts=sum('source' in specs[i] for i in rows), captured_source_puts=len(captured),
                active_source_leases=len(tx['active_source_leases']), applied=len(tx['target_events']),
                source_packets=len(net['source_packets']), payload_bytes=sum(map(len, observed.values())),
                incomplete_wire_streams=partial, memory_windows=len(memory))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--reference-backend', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    a = parser.parse_args(); backend, reference, out = (p.resolve() for p in (a.backend, a.reference_backend, a.output))
    assert backend.is_file() and reference.is_file() and not out.exists()
    assert shutil.disk_usage(out.parent).free >= 8*2**30+64*2**20
    out.mkdir(); started = time.monotonic(); commands = []
    env = dict(os.environ, PYTHONPATH=str(ROOT/'python'), PYTHONDONTWRITEBYTECODE='1')

    def invoke(name, argv, expected=0):
        before = time.monotonic(); remaining = 55-(before-started); assert remaining > 0
        with (out/(name+'.stdout')).open('w') as stdout, (out/(name+'.stderr')).open('w') as stderr:
            p = subprocess.run(list(map(str, argv)), cwd=out, env=env, stdout=stdout, stderr=stderr, timeout=remaining)
        commands.append(dict(argv=list(map(str, argv)), cwd=str(out), exitcode=p.returncode,
                             expected_exitcode=expected, wall_seconds=time.monotonic()-before))
        save(out/'commands.json', commands); assert p.returncode == expected, name

    def cli(name, argv, expected=0): invoke(name, [sys.executable, '-m', 'moe_ub_cosim', 'scaleout', *argv], expected)

    def run(name, work, binary=backend, expected=0, topology='topology', protocol='protocol.json'):
        save(out/(name+'-input.json'), work)
        cli(name, ['rdma-transaction-probe', '--topology', topology, '--protocol', protocol,
                   '--config', name+'-input.json', '--backend', binary, '--output', name], expected)
        if (out/name/'result.json').exists(): return read(out/name/'result.json')

    topology = read(ROOT/'examples/scaleout_rdma/small-clos-100g.json')
    protocol = read(ROOT/'examples/scaleout_rdma/rdma.json'); protocol.update(payload_bytes=64, chunk_bytes=256)
    protocol['packet_capture'] = dict(nodes=[0, 1, 4], start_ns=0, end_ns=5000000, max_events=10000)
    save(out/'topology-input.json', topology); save(out/'protocol.json', protocol)
    cli('generate', ['topology', '--config', 'topology-input.json', '--output', 'topology'])
    config = read(ROOT/'examples/scaleout_rdma_transactions/source-windows.json')
    normal = run('normal', config); normal_prefix = prefix_audit(normal, config, 64)
    full = audit(normal, config, 64); local_audit(normal, config); endpoint_audit(normal, config)
    assert not normal['transactions']['active_source_leases'] and normal['completed_operations'] == 6
    rows = {r['id']: r for r in normal['transactions']['operations']}
    assert rows[2]['ready_ns'] == rows[3]['ready_ns'] < rows[2]['completed_ns'] == rows[3]['published_ns']
    assert rows[6]['published_ns'] >= rows[3]['completed_ns']
    events = normal['transactions']['source_events']
    assert [e['operation'] for e in events if e['kind'] == 'captured'] == [0, 2, 3, 4]
    original = bytes.fromhex(config['windows'][0]['hex'])[:257]
    for owner, key, offset in [(1, 9, 0), (1, 9, 384), (0, 7, 512)]:
        w, = [w for w in normal['transactions']['windows'] if (w['owner'], w['key']) == (owner, key)]
        assert bytes.fromhex(w['hex'])[offset:offset+257] == original
    run('repeat', config)
    assert (out/'normal/result.json').read_bytes() == (out/'repeat/result.json').read_bytes()
    changed = deepcopy(config); initial = bytearray.fromhex(changed['windows'][0]['hex'])
    initial[8:257] = bytes(x^0x55 for x in initial[8:257]); changed['windows'][0]['hex'] = initial.hex()
    assert changed['operations'] == config['operations']  # no downstream constant changed
    mutated = run('changed-input', changed); prefix_audit(mutated, changed, 64); audit(mutated, changed, 64)
    assert all(bytes.fromhex(e['hex']) == bytes(initial[:257]) for e in mutated['transactions']['source_events'] if e['kind'] == 'captured')
    # The declared local stage itself does not read/lease numerical inputs.
    # An earlier legal overwrite therefore changes the later publish snapshot;
    # this proves both live memory capture and the remaining application gap.
    early = deepcopy(config)
    next(op for op in early['operations'] if op['id'] == 6)['after'] = [dict(operation=10, event='completed')]
    early_result = run('overwrite-before-submit', early); prefix_audit(early_result, early, 64); audit(early_result, early, 64)
    replacement = bytes.fromhex(next(op for op in early['operations'] if op['id'] == 6)['hex'])
    assert all(bytes.fromhex(e['hex']) == (original if e['operation'] == 0 else replacement)
               for e in early_result['transactions']['source_events'] if e['kind'] == 'captured')
    renamed = deepcopy(config)
    for op in renamed['operations']:
        op['id'] += 100
        for d in op['after']: d['operation'] += 100
    rename = run('renamed', renamed); prefix_audit(rename, renamed, 64); audit(rename, renamed, 64)
    for k in ('source_packets', 'source_payload_packets'):
        assert normal['transactions']['transport'][k] == rename['transactions']['transport'][k]

    missing = deepcopy(config); missing['operations'][1]['value'] = 0
    blocked = run('missing-count', missing, expected=1); missing_audit = prefix_audit(blocked, missing, 64)
    assert not blocked['passed'] and blocked['completed_operations'] == 2 and len(blocked['pending_operations']) == 4
    assert {r['id'] for r in blocked['transactions']['operations']} == {0, 10}

    failures = {}
    for name in ('overwrite-put', 'overwrite-atomic', 'local-reset'):
        case = deepcopy(config)
        if name == 'local-reset':
            case['operations'].append(dict(id=8, kind='local', rank=4, duration_ns=0,
                after=[dict(operation=1, event='completed')], local_wait=[
                    dict(key=8, address=8960, compare='eq', value=0, reset_to=1),
                    dict(key=8, address=8192, compare='eq', value=int.from_bytes(original[:8], 'little'), reset_to=0)]))
        else:
            op = next(op for op in case['operations'] if op['id'] == 6)
            op['after'] = [dict(operation=1, event='completed')]
            if name == 'overwrite-atomic': op.update(verb='fetch_add', value=1); del op['hex']
        result = run(name, case, expected=1)
        assert not result['passed'] and result['execution_error'] == 'write overlaps active source read lease'
        failures[name] = prefix_audit(result, case, 64)
        assert {r['operation'] for r in result['transactions']['active_source_leases']} == {2, 3}
        w, = [w for w in result['transactions']['windows'] if w['owner'] == 4]
        assert bytes.fromhex(w['hex'])[:257] == original and bytes.fromhex(w['hex'])[768:776] == b'\0'*8
        if name == 'local-reset':
            p, = [p for p in result['pending_local_work'] if p['id'] == 8]
            assert not p['admitted'] and all(e['operation'] != 8 for e in result['transactions']['local_events'])
        else:
            r, = [r for r in result['transactions']['operations'] if r['id'] == 6]
            assert r['applied_ns'] == -1

    slow = deepcopy(topology)
    for link in slow['links'].values(): link['rate_bps'] = 1000000
    slow_protocol = deepcopy(protocol); slow_protocol['ecn_by_rate']['1000000'] = dict(kmin_bytes=32768, kmax_bytes=131072, pmax=1.0)
    save(out/'slow-topology-input.json', slow); save(out/'slow-protocol.json', slow_protocol)
    cli('generate-slow', ['topology', '--config', 'slow-topology-input.json', '--output', 'slow-topology'])
    cutoff = run('cutoff', config, expected=1, topology='slow-topology', protocol='slow-protocol.json')
    cutoff_audit = prefix_audit(cutoff, config, 64)
    assert not cutoff['passed'] and cutoff['finish_ns'] == 5000000 and 'execution_error' not in cutoff
    assert {r['operation'] for r in cutoff['transactions']['active_source_leases']} == {0}
    assert cutoff['completed_operations'] == 0 and not any(e['kind'] == 'released' for e in cutoff['transactions']['source_events'])

    legacy = []
    for name in ('work', 'local-waits', 'queue-fences', 'local-stages'):
        case = read(ROOT/f'examples/scaleout_rdma_transactions/{name}.json')
        run(name+'-old', case, reference); run(name+'-new', case)
        assert (out/(name+'-old')/'result.json').read_bytes() == (out/(name+'-new')/'result.json').read_bytes()
        legacy.append(name)
    literal = read(ROOT/'examples/scaleout_rdma_transactions/local-stages.json')
    literal.update(schema_version='moe-rdma-transactions/3', source_model=deepcopy(config['source_model']))
    converted = run('literal-v3', literal)
    for key in ('source_model', 'source_events', 'active_source_leases'): converted['transactions'].pop(key)
    assert converted == read(out/'local-stages-old/result.json')

    invalid = []
    for name in ('wrong-model', 'foreign-source', 'bool-length', 'long-source', 'extra-owner', 'double-payload', 'atomic-source', 'own-reset'):
        case = deepcopy(config); op = case['operations'][0]
        if name == 'wrong-model': case['source_model']['release'] = 'accepted'
        elif name == 'foreign-source': op['source']['key'] = 8
        elif name == 'bool-length': op['source']['bytes'] = True
        elif name == 'long-source': op['source']['bytes'] = 32769
        elif name == 'extra-owner': op['source']['owner'] = 0
        elif name == 'double-payload': op['hex'] = '00'
        elif name == 'atomic-source': case['operations'][1]['source'] = deepcopy(op['source'])
        else: op['local_wait'] = [dict(key=7, address=4096, compare='eq', value=0, reset_to=0)]
        assert run(name, case, expected=1) is None and not (out/name).exists()
        invoke(name+'-native', [backend, out/'topology/topology.json', out/'protocol.json',
                               out/(name+'-input.json'), out/(name+'-native.json')], 1)
        assert not (out/(name+'-native.json')).exists(); invalid.append(name)
    total = sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    assert total < 64*2**20 and time.monotonic()-started < 60
    result = dict(passed=True, normal=normal_prefix, complete_audit=full, source_events=events,
        changed_only_initial_window_changes_all_forwarded_bytes=True, source_readers_overlap=True,
        pre_submit_overwrite_changes_publish_snapshot=True, local_stage_input_lifetime_modeled=False,
        repeat_byte_identical=True, id_renaming_wire_invariant=True, missing_count=missing_audit,
        source_conflicts=failures, cutoff=cutoff_audit, legacy_byte_identical=legacy,
        literal_v3_equal=True, invalid_cli_and_native=invalid, commands=len(commands),
        public_commands=sum(c['argv'][1] == '-m' for c in commands), native_commands=sum(c['argv'][1] != '-m' for c in commands),
        output_bytes=total, wall_seconds=time.monotonic()-started,
        backend_sha256=hashlib.sha256(backend.read_bytes()).hexdigest(), reference_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
        scope='functional memory snapshot at publication and ACK guards; no GPU/GIN calibration or MoE execution')
    save(out/'verification.json', result); print(json.dumps(result))


if __name__ == '__main__': main()
