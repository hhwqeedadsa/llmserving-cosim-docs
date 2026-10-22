"""Fresh public workflow and independent wire/memory oracle; functional cases only."""
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
ROOT = Path(__file__).resolve().parents[2]


def audit(result, config, mtu):
    assert result['passed'] and result['native_network_executed'] and result['drained']
    tx = result['transactions']; net = tx['transport']
    assert not tx['roce_wire_equivalent'] and not tx['gpu_visibility_modeled'] and not tx['hardware_calibrated']
    assert net['physical_drained'] and net['closed'] and net['inflight_packets'] == 0
    connections = {c['id']: c for c in config['connections']}
    rows = {op['id']: op for op in tx['operations']}
    specs = {op['id']: op for op in config['operations']}
    assert rows.keys() == specs.keys()
    local_by_id = {e['operation']: e for e in tx['local_events']}
    assert len(local_by_id) == len(tx['local_events'])
    assert set(local_by_id) == {op['id'] for op in specs.values() if 'local_wait' in op}
    bindings = {(r['connection'], r['sequence']): r for r in rows.values()}
    source_bytes, frames = {}, {}
    packets = net['source_packets']; capture = net['source_payload_packets']
    assert len(packets) == len(capture)
    for p, observed in zip(packets, capture):
        stamp, stream, src, dst, nic, seq, size, uid = p
        c = connections[stream//2]; reverse = stream%2
        assert (src, dst, nic) == ((c['dst'], c['src'], c['destination_nic']) if reverse else
                                  (c['src'], c['dst'], c['source_nic']))
        body = bytes.fromhex(observed['hex'])
        assert (observed['time_ns'], observed['stream'], observed['sequence'], len(body)) == (stamp, stream, seq, size)
        data = source_bytes.setdefault(stream, bytearray())
        assert seq == len(data) and 0 < size <= mtu and uid >= 0
        data.extend(body)
    # Independent struct decoder. No imports of production frame/memory code.
    for stream, data in source_bytes.items():
        offset = 0
        while offset < len(data):
            magic, verb, seq, address, key, size = struct.unpack_from('>IIQQII', data, offset)
            assert magic == 0x4d525431 and verb in (1, 2, 3) and 0 < size <= 32768
            end = offset+32+size; body = bytes(data[offset+32:end]); assert len(body) == size
            assert (stream, seq) not in frames
            work = [w for w in net['work'] if w['stream'] == stream and w['begin'] == offset and w['end'] == end]
            assert len(work) == 1
            frames[stream, seq] = dict(verb=verb, address=address, key=key, body=body, work=work[0])
            observed_packets = [p for p in packets if p[1] == stream and offset <= p[5] < end]
            expected_packets = [(p, min(mtu, end-p)) for p in range(offset, end, mtu)]
            assert [(p[5], p[6]) for p in observed_packets] == expected_packets
            assert all(work[0]['published_ns'] <= p[0] <= work[0]['accepted_ns'] for p in observed_packets)
            offset = end
        assert offset == len(data)
    for row in rows.values():
        op = specs[row['id']]; f = frames[row['connection']*2, row['sequence']]
        expected_body = bytes.fromhex(op['hex']) if op['verb'] == 'put' else op['value'].to_bytes(8, 'big')
        assert (f['verb'], f['key'], f['address'], f['body']) == (1 if op['verb'] == 'put' else 2, op['key'], op['address'], expected_body)
        ready = max((rows[d['operation']][dict(applied='applied_ns', request_acked='request_acked_ns', completed='completed_ns')[d['event']]]
                     for d in op['after']), default=0)
        if 'local_wait' in op:
            assert row['ready_ns'] == local_by_id[row['id']]['time_ns'] >= ready
        else:
            assert row['ready_ns'] == ready
        assert row['ready_ns'] <= row['published_ns'] <= row['applied_ns'] < row['request_acked_ns']
        assert f['work']['ready_ns'] == row['published_ns'] == f['work']['published_ns']
        assert f['work']['accepted_ns'] == row['applied_ns'] and f['work']['acked_ns'] == row['request_acked_ns']
        if op['verb'] == 'put':
            assert row['response_ns'] == -1 and row['completed_ns'] >= row['request_acked_ns']
        else:
            reply = frames[row['connection']*2+1, row['sequence']]
            assert reply['verb'] == 3 and reply['key'] == reply['address'] == 0
            assert len(reply['body']) == 8 and int.from_bytes(reply['body'], 'big') == row['returned_value']
            assert reply['work']['ready_ns'] == row['applied_ns']
            assert reply['work']['accepted_ns'] == row['response_ns'] <= row['completed_ns']
            assert row['completed_ns'] >= max(row['request_acked_ns'], row['response_ns'])
        # In-order completion may additionally wait for earlier work.
        previous = [r['completed_ns'] for r in rows.values() if r['connection'] == row['connection'] and r['sequence'] < row['sequence']]
        assert row['completed_ns'] == max([row['request_acked_ns'], row['response_ns'], *previous])
    memory = {(w['owner'], w['key']): bytearray.fromhex(w['hex']) for w in config['windows']}
    windows = {(w['owner'], w['key']): w for w in config['windows']}
    assert len(tx['target_events']) == len(specs) and tx['duplicate_requests'] == 0
    events = sorted(tx['target_events']+tx['local_events'], key=lambda e: e['order'])
    assert [e['order'] for e in events] == list(range(len(events)))
    last_time = -1
    for event in events:
        if 'conditions' in event:
            assert event['time_ns'] >= last_time; last_time = event['time_ns']
            op = specs[event['operation']]; owner = connections[op['connection']]['src']
            assert event['owner'] == owner and len(event['conditions']) == len(op['local_wait'])
            # Evaluate all predicates before any reset, from independently
            # reconstructed actual request bytes, not the source operation table.
            for observed, condition in zip(event['conditions'], op['local_wait']):
                key = owner, condition['key']; offset = condition['address']-windows[key]['address']
                value = int.from_bytes(memory[key][offset:offset+8], 'little')
                expected = dict(condition, owner=owner, observed_value=value, matched=True)
                assert observed == expected
                assert value == condition['value'] if condition['compare'] == 'eq' else value >= condition['value']
            for condition in op['local_wait']:
                if 'reset_to' in condition:
                    key = owner, condition['key']; offset = condition['address']-windows[key]['address']
                    memory[key][offset:offset+8] = condition['reset_to'].to_bytes(8, 'little')
            continue
        assert event['time_ns'] >= last_time and not event['duplicate']; last_time = event['time_ns']
        c, seq = event['connection'], event['sequence']; row = bindings[c, seq]; f = frames[c*2, seq]
        assert (event['verb'], event['key'], event['address'], bytes.fromhex(event['body_hex'])) == (f['verb'], f['key'], f['address'], f['body'])
        assert event['time_ns'] == row['applied_ns']
        key = connections[c]['dst'], f['key']; offset = f['address']-windows[key]['address']
        if f['verb'] == 1: memory[key][offset:offset+len(f['body'])] = f['body']
        else:
            old = int.from_bytes(memory[key][offset:offset+8], 'little')
            assert old == event['old_value'] == row['returned_value']
            memory[key][offset:offset+8] = ((old+int.from_bytes(f['body'], 'big'))%(2**64)).to_bytes(8, 'little')
    assert {(w['owner'], w['key']): bytes.fromhex(w['hex']) for w in tx['windows']} == memory
    for c in connections.values():
        ordered = sorted((r for r in rows.values() if r['connection'] == c['id']), key=lambda r: r['sequence'])
        for index, row in enumerate(ordered):
            release = ordered[index-c['queue_depth']]['completed_ns'] if index >= c['queue_depth'] else 0
            assert row['published_ns'] == max(row['ready_ns'], release)
    for s in net['streams']:
        assert s['qp_creations'] == 1 and s['snd_nxt'] == s['snd_una'] == s['submitted_bytes']
    native = [p for p in result['loaded_libraries'] if Path(p).name.startswith('libns3')]
    assert native and len({str(Path(p).parent) for p in native}) == 1 and all('unified-bus' not in p for p in native)
    assert result['finish_ns'] >= max(w['acked_ns'] for w in net['work'])
    return dict(operations=len(specs), source_packets=len(packets), transported_payload_bytes=sum(map(len, source_bytes.values())),
                finish_ns=result['finish_ns'], final_atomic_value=int.from_bytes(memory[4, 7][:8], 'little'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); out = args.output.resolve(); backend = args.backend.resolve()
    assert not out.exists() and backend.is_file() and shutil.disk_usage(out.parent).free >= 8*2**30+32*2**20
    out.mkdir(); start = time.monotonic(); commands = []
    env = dict(os.environ, PYTHONPATH=str(ROOT/'python'), PYTHONDONTWRITEBYTECODE='1')
    for source, name in [('examples/scaleout_rdma/small-clos-100g.json', 'topology-input.json'),
                         ('examples/scaleout_rdma/rdma.json', 'protocol.json'),
                         ('examples/scaleout_rdma_transactions/work.json', 'work.json')]: shutil.copy2(ROOT/source, out/name)
    def cli(name, arguments, expected=0):
        command = [sys.executable, '-m', 'moe_ub_cosim', 'scaleout', *arguments]
        remaining = 290-(time.monotonic()-start); assert remaining > 0
        began = time.monotonic()
        with (out/(name+'.stdout')).open('w') as stdout, (out/(name+'.stderr')).open('w') as stderr:
            run = subprocess.run(command, cwd=out, env=env, stdout=stdout, stderr=stderr, timeout=min(65, remaining))
        commands.append(dict(argv=list(map(str, command)), cwd=str(out), exitcode=run.returncode,
                             expected_exitcode=expected, wall_seconds=time.monotonic()-began))
        (out/'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
        assert run.returncode == expected, name
    cli('topology', ['topology', '--config', 'topology-input.json', '--output', 'topology'])
    def run(name, config, protocol='protocol.json', expected=0):
        cli(name, ['rdma-transaction-probe', '--topology', 'topology', '--protocol', protocol, '--config', config,
                   '--backend', str(backend), '--output', name], expected)
        if not expected:
            usage = json.loads((out/name/'execution/probe.json').read_text())
            assert usage['status'] == 'completed' and usage['peak_sampled_group_rss_bytes'] <= 2**30
            return json.loads((out/name/'result.json').read_text())
    config = json.loads((out/'work.json').read_text()); protocol = json.loads((out/'protocol.json').read_text())
    normal = run('normal', 'work.json'); normal_audit = audit(normal, config, protocol['payload_bytes'])
    rows = {w['id']: w for w in normal['transactions']['operations']}
    assert rows[3]['ready_ns'] == rows[1]['request_acked_ns'] < rows[1]['response_ns'] == rows[3]['published_ns']
    deeper = deepcopy(config)
    for c in deeper['connections']: c['queue_depth'] = 2
    deeper['operations'][1]['after'] = []
    (out/'deep.json').write_text(json.dumps(deeper)); deep = run('deep', 'deep.json')
    deep_audit = audit(deep, deeper, protocol['payload_bytes'])
    assert all(w['published_ns'] == 0 for w in deep['transactions']['operations'] if w['id'] in (0, 1, 2))
    segmented = deepcopy(config); segmented['windows'][0]['hex'] = '00'*512
    segmented['operations'][0]['hex'] = bytes(range(145)).hex()
    small = deepcopy(protocol); small['payload_bytes'] = 64; small['chunk_bytes'] = 256
    (out/'segmented.json').write_text(json.dumps(segmented)); (out/'small-protocol.json').write_text(json.dumps(small))
    segmented_result = run('segmented', 'segmented.json', 'small-protocol.json')
    segmented_audit = audit(segmented_result, segmented, 64)
    assert segmented_audit['source_packets'] > normal_audit['source_packets']
    run('repeat', 'work.json'); assert (out/'normal/result.json').read_bytes() == (out/'repeat/result.json').read_bytes()
    renamed = deepcopy(config)
    for op in renamed['operations']:
        op['id'] += 100
        for dep in op['after']: dep['operation'] += 100
    (out/'renamed.json').write_text(json.dumps(renamed)); renamed_result = run('renamed', 'renamed.json')
    renamed_audit = audit(renamed_result, renamed, protocol['payload_bytes'])
    assert renamed_audit == normal_audit
    for key in ('source_packets', 'source_payload_packets'):
        assert normal['transactions']['transport'][key] == renamed_result['transactions']['transport'][key]
    bad = deepcopy(config); bad['operations'][0]['after'] = [dict(operation=5, event='completed')]
    (out/'future.json').write_text(json.dumps(bad)); run('future', 'future.json', expected=1)
    assert not (out/'future').exists()
    wrong = deepcopy(config); wrong['connections'][0]['source_nic'] = 99
    (out/'wrong-nic.json').write_text(json.dumps(wrong)); run('wrong-nic', 'wrong-nic.json', expected=1)
    assert json.loads((out/'wrong-nic/execution/probe.json').read_text())['status'] == 'child_failed'
    assert 'unique declared source and reverse NIC routes' in (out/'wrong-nic/execution/stderr.log').read_text()
    paths = ['backend/rdma/remote_memory.hh', 'backend/rdma/remote_transactions.hh', 'backend/rdma/remote_transactions.cc',
             'backend/rdma/persistent_streams.hh', 'backend/rdma/persistent_streams.cc', 'backend/rdma/transaction_probe.cc',
             'backend/rdma/targets.cmake', 'python/moe_ub_cosim/scaleout_transactions.py', 'python/moe_ub_cosim/cli.py',
             'python/moe_ub_cosim/scaleout_rdma.py', 'examples/scaleout_rdma_transactions/work.json',
             'tests/integration/check_remote_transaction_workflow.py']
    total = sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    assert total <= 32*2**20 and time.monotonic()-start < 300
    result = dict(passed=True, public_commands=len(commands), expected_rejections=2,
        normal=normal_audit, depth2=deep_audit, segmented=segmented_audit, repeat_byte_identical=True,
        application_id_renaming_wire_and_time_invariant=True,
        output_bytes_before_summary=total, wall_seconds=time.monotonic()-start,
        source_sha256={p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},
        scope='artificial functional tests; no modern application, RoCE timing, hardware calibration or research gain')
    (out/'verification.json').write_text(json.dumps(result, indent=2)+'\n'); print(json.dumps(result))


if __name__ == '__main__': main()
