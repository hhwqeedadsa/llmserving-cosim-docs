"""Independent route microbenchmark; public CLI execution, never fixture drivers."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

CASES = ('terminal_normal', 'gated_normal', 'terminal_loss', 'gated_loss')


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def prepare(repo, runtime, stage):
    from moe_ub_cosim.route_evidence import inspect_raw, validate_manifest
    from moe_ub_cosim.scaleout_topology import execute
    assert not stage.exists()
    manifest = read(repo / 'data/moe-trace-small/manifest.json')
    validate_manifest(manifest)
    source = next(r for r in manifest['files'] if r['file_id'] == 'local-1808')
    raw = repo / source['file']['path']
    assert sha(raw) == source['file']['sha256']
    inspect_raw(raw.read_bytes(), manifest['layers'])
    experts = read(raw)[0]['3'][0]
    destinations = sorted({e // 64 for e in experts} - {0})
    assert destinations == [1, 2, 3]
    identity = read(runtime / 'backend.json')
    binary = Path(identity['backend'])
    assert sha(binary) == identity['backend_sha256']
    assert sha(repo / 'backend/rdma/rc_concurrent_probe.cc') == identity['driver_sha256']
    stage.mkdir()
    observed = stage / 'observed'
    observed.mkdir()
    (stage / 'truth').mkdir()
    copied = stage / 'runtime'
    (copied / 'runtime/lib').mkdir(parents=True)
    shutil.copy2(binary, copied / binary.name)
    for name, digest in identity['library_hashes'].items():
        original = runtime / 'runtime/lib' / name
        assert sha(original) == digest
        shutil.copyfile(original, copied / 'runtime/lib' / name)
    shutil.copyfile(raw, observed / 'source-1808.json')
    save(observed / 'source-manifest.json', manifest)
    save(observed / 'identity.json', dict(identity, backend_sha256=sha(copied / binary.name),
         platform_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()))
    save(observed / 'selection.json', dict(expert_ids=experts, destinations=destinations,
         raw_sha256=sha(raw), original_path=source['original_path'], entry=0, layer=3, token=0,
         EP=4, E=256, top_k=8, H=7168, activation_dtype='declared BF16',
         source_rank=0, hidden_bytes=14336, write_bytes=43008, fault_connection=2,
         scope='constructed segmented/gated RC fragment; not actual DeepEP, full CCL or GPU'))
    topology = dict(schema_version='moe-scaleout-topology/1',
        deployment=dict(pods=2, segments_per_pod=1, servers_per_segment=2, gpus_per_server=1, gpu_type='NONE'),
        fabric=dict(rails=1, planes=1, tors_per_segment_per_rail=1, upper_tier_scope='shared',
                    aggs_per_pod_per_group=1, cores_per_group=1, tor_uplinks=1, agg_uplinks=1, core_wiring='grouped'),
        nic=dict(rails_by_gpu_slot=[[0]], total_rate_bps=100000000000),
        links={k: dict(rate_bps=100000000000, delay_ns=1000) for k in ('access','tor_agg','agg_core','tor_core')},
        port_limits=dict(gpu=16, tor=128, agg=128, core=128),
        budget=dict(max_nodes=32, max_links=64, max_switches=16))
    save(observed / 'topology-config.json', topology)
    execute(observed / 'topology-config.json', observed / 'topology')
    protocol = dict(schema_version='moe-scaleout-rdma/2', cc_mode=1, payload_bytes=256, chunk_bytes=256,
        ack_interval_bytes=1, base_rtt_ns=14000, switch_buffer_bytes=8388608, switch_counter_storage='port_dense',
        ecn_by_rate={'100000000000': dict(kmin_bytes=32768, kmax_bytes=131072, pmax=1.0)}, seed=19,
        packet_capture=dict(nodes=[], start_ns=0, end_ns=5000000, max_events=80000, wire_headers=True),
        queue_metrics=True, limits=dict(max_gpus=4, max_switches=16, max_links=64, max_messages=64,
            max_queue_events=80000, queue_capture='events', max_sim_ns=5000000, wall_seconds=60,
            rss_bytes=1073741824, address_bytes=2147483648, artifact_bytes=67108864, result_json_bytes=33554432))
    save(observed / 'rdma.json', protocol)
    for mode in ('terminal', 'gated'):
        directory = observed / mode
        directory.mkdir()
        work = dict(schema_version='moe-rc-concurrent-input/1', mtu=256, rto_ns=14000,
            memories=[dict(gpu=g, data_hex=bytes(16384).hex()) for g in range(4)], connections=[], loss=None)
        for cid, dst in enumerate(destinations):
            data = bytes((i + cid + 31) % 251 for i in range(14336))
            final = dict(kind='fetch_add', address=14336, addend=1)
            if cid == 2:
                operations = [dict(kind='write', address=0, data_hex=data.hex()), final]
                if mode == 'gated':
                    operations.insert(0, dict(kind='fetch_add', address=14344, addend=1))
            else:
                operations = [dict(kind='write', address=0, data_hex=data[:7168].hex()),
                    dict(kind='fetch_add', address=14344, addend=1),
                    dict(kind='write', address=7168, data_hex=data[7168:].hex()), final]
            work['connections'].append(dict(id=cid, src=0, dst=dst, source_nic=1,
                destination_nic=1, plane=0, operations=operations))
        save(directory / 'normal.json', work)
        fault = deepcopy(work)
        fault['loss'] = dict(connection=2, node=0, opcode=18, psn=56 if mode=='terminal' else 0)
        save(directory / 'loss.json', fault)
    save(stage / 'execution.json', dict(cases={c:'pending' for c in CASES}, networks=0, closed=False))


def run(stage, case):
    ledger = read(stage / 'execution.json')
    assert not ledger['closed'] and ledger['cases'][case] == 'pending'
    index = CASES.index(case)
    for prior in CASES[:index]:
        assert ledger['cases'][prior] == 'completed'
        assert read(stage / (prior + '-checked.json'))['passed']
    mode, condition = case.split('_')
    if condition == 'loss':
        seal = read(stage / 'prediction-seal.json')
        assert sha(stage / 'prediction.json') == seal['sha256']
    output = stage / ('observed/' + mode + '/run' if condition=='normal' else 'truth/' + mode)
    command = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', 'rc-fabric-probe',
        '--topology', str(stage / 'observed/topology'), '--protocol', str(stage / 'observed/rdma.json'),
        '--config', str(stage / 'observed' / mode / (condition + '.json')),
        '--backend', str(stage / 'runtime/moe-rdma-rc-concurrent'), '--output', str(output)]
    ledger['cases'][case] = 'started'
    ledger['networks'] += 1
    save(stage / 'execution.json', ledger)
    result = subprocess.run(command, capture_output=True, timeout=90)
    (stage / (case + '.stdout')).write_bytes(result.stdout)
    (stage / (case + '.stderr')).write_bytes(result.stderr)
    ledger['cases'][case] = 'completed' if result.returncode==0 else 'failed'
    ledger['closed'] = result.returncode!=0 or ledger['networks']==4
    save(stage / 'execution.json', ledger)
    assert result.returncode==0, result.stderr.decode()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', *CASES))
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--repository', type=Path)
    parser.add_argument('--runtime', type=Path)
    args = parser.parse_args()
    if args.action=='prepare':
        prepare(args.repository.resolve(), args.runtime.resolve(), args.stage.resolve())
    else:
        run(args.stage.resolve(), args.action)
