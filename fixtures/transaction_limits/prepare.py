"""Deterministic functional inputs; no training or hardware-performance claim."""
import argparse
from copy import deepcopy
import json
from pathlib import Path


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def expanded():
    ranks = [0, 1, 4, 5]
    w = dict(schema_version='moe-rdma-transactions/5',
        execution_limits=dict(max_connections=12, max_remote_operations=96, max_local_operations=20),
        local_model=dict(mode='serial_per_gpu/1', duration_source='declared_ns'),
        source_model=dict(capture='registered_window_at_publish/1', release='transaction_completed/1'),
        local_memory_model=dict(mode='guarded_copy/1', acquire='admission/1', capture='start/1', commit='completion/1'),
        connections=[], windows=[], operations=[])
    for rank in ranks:
        w['windows'].append(dict(owner=rank, key=7, address=4096, allow_atomic=True,
            bytes=65536, initialization='zero_fill/1',
            segments=[dict(offset=0, hex=bytes((rank*32+i)%256 for i in range(32)).hex())]))
    incoming = {}
    for src in ranks:
        for dst in ranks:
            if src == dst:
                continue
            c = len(w['connections'])
            w['connections'].append(dict(id=c, src=src, dst=dst, plane=0,
                request_port=24000+2*c, response_port=24001+2*c,
                source_nic=1, destination_nic=1, queue_depth=4))
            for j in range(8):
                i = len(w['operations'])
                address = 8192+src*1024+j*32
                w['operations'].append(dict(id=i, kind='remote', connection=c, verb='put',
                    key=7, address=address, source=dict(key=7, address=4096, bytes=32), after=[]))
                incoming.setdefault(dst, []).append((i, address))
    for rank in ranks:
        for j, (parent, address) in enumerate(incoming[rank][:5]):
            w['operations'].append(dict(id=len(w['operations']), kind='local', rank=rank,
                duration_ns=10, copy=dict(source=dict(key=7, address=address, bytes=32),
                    target=dict(key=7, address=40960+j*32)),
                after=[dict(operation=parent, event='completed')]))
    return w


def long_sequence():
    w = expanded()
    w['execution_limits'] = dict(max_connections=1, max_remote_operations=64, max_local_operations=0)
    w['transport_recovery'] = dict(mode='go_back_n/1', initial_rto_ns=80000,
        max_rto_ns=640000, max_retries=8, max_events=10000)
    w['connections'] = w['connections'][:1]
    w['windows'] = [dict(owner=1, key=7, address=4096, allow_atomic=True, bytes=4096,
                         initialization='zero_fill/1', segments=[])]
    w['operations'] = [dict(id=i, kind='remote', connection=0, verb='fetch_add', key=7,
                            address=4096, value=i+1, after=[]) for i in range(64)]
    return w


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repository', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); a.output.mkdir(exist_ok=False)
    save(a.output/'expanded.json', expanded()); save(a.output/'long.json', long_sequence())
    topology = json.loads((a.repository/'examples/scaleout_rdma/small-clos-100g.json').read_text())
    topology['deployment']['gpu_type'] = 'NONE'
    save(a.output/'topology.json', topology)
    protocol = json.loads((a.repository/'examples/scaleout_link_failure/stream-rdma.json').read_text())
    del protocol['link_failures']; del protocol['ecn_by_rate']['25000000000']
    protocol['limits']['queue_capture'] = 'events'
    protocol.pop('mmu_snapshot', None)
    save(a.output/'protocol.json', protocol)
    fault = deepcopy(protocol)
    fault['faults'] = [dict(node=1, port=0, message_id=0, kind='data', sequence=1280,
                           start_ns=0, end_ns=5000000, count=1)]
    save(a.output/'fault.json', fault)


if __name__ == '__main__':
    main()
