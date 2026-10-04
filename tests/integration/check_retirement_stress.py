"""Small real-UB repeated rounds measure bounded ownership and enforce disk failure."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.observation import backend_observation
from moe_ub_cosim.record_storage import iter_result

def read(path):return json.loads(path.read_text())
def save(path, value):path.write_text(json.dumps(value, indent=2)+'\n'); return path

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(); root = Path.cwd(); out = args.output_dir.resolve(); out.mkdir(parents=True, exist_ok=False)
    base = read(root/'fixtures/m10_full_model/service.json'); layers = read(root/'fixtures/m10_full_model/layers.json')
    base['groups'] = base['groups'][:1]; layers['groups'] = layers['groups'][:1]
    # The original short fixture intentionally has only a few KV blocks. Give
    # both synthetic stress lengths the same extra 64 KiB per rank so 32 steps
    # are a valid capacity input; the capacity formula/admission stay unchanged.
    for rank in base['groups'][0]['memory']['ranks']:rank['device_bytes'] += 65536
    base['requests'] = [r for r in base['requests'] if r['request_id'] in (201, 204)]
    ranks = layers['groups'][0]['layers'][0]['rank_ids']
    max_ns = 10000000
    obs = dict(clocks=[dict(device_type='rank', device_id=r, offset_ns=0, drift_ppb=0) for r in ranks]+
        [dict(device_type='switch', device_id=8, offset_ns=0, drift_ppb=0)],
        switches=[dict(switch_id=8, port_ids=[0, 4], start_local_ns=0, end_local_ns=max_ns, period_ns=10000)],
        record_provenance=True)
    observation = save(out/'observation.json', backend_observation(obs, ranks, max_ns))
    layer = save(out/'layers.json', layers)
    policy = dict(schema_version='moe-ub-record-storage/1', max_compressed_bytes=128*2**20,
                  max_buffer_bytes=2**20, max_record_bytes=2**18)
    policies = {False:save(out/'policy.json', policy), True:save(out/'tiny-policy.json', dict(policy, max_compressed_bytes=1))}
    commands = []; results = {}
    for steps, tiny in [(8, False), (32, False), (8, True)]:
        label = 'disk-limit' if tiny else f'rounds-{steps}'
        config = deepcopy(base)
        for request in config['requests']:
            routes = request['decode_routes']; request['decode_routes'] = [deepcopy(routes[i % len(routes)]) for i in range(steps)]
        input_path = save(out/(label+'.json'), config); run = out/label
        argv = [sys.executable, '-m', 'moe_ub_cosim.service', '--input', str(input_path), '--layer', str(layer),
            '--backend', str(args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'),
            '--system', str(root/'fixtures/m1_compute/system.json'), '--network', str(root/'fixtures/m3_shared_bottleneck/network'),
            '--output-dir', str(run), '--observation', str(observation), '--max-simulation-ns', str(max_ns),
            '--record-storage', str(policies[tiny])]
        start = datetime.now(timezone.utc).isoformat()
        with (out/(label+'.stdout.log')).open('w') as stdout, (out/(label+'.stderr.log')).open('w') as stderr:
            process = subprocess.run(argv, cwd=root, stdout=stdout, stderr=stderr, timeout=300)
        commands.append(dict(argv=argv, cwd=str(root), started_utc=start, ended_utc=datetime.now(timezone.utc).isoformat(), exit_code=process.returncode))
        save(out/'commands.json', commands)
        assert process.returncode == (1 if tiny else 0), (label, (out/(label+'.stderr.log')).read_text()[-2000:])
        if tiny:
            assert not (run/'execution/result.json').exists()
            failure = read(run/'execution/failure.json')
            assert 'disk budget exceeded' in failure['error']
            results[label] = dict(rejected=True, error=failure['error'], network=failure['network'])
            continue
        report = read(run/'execution/result.json'); front = read(run/'service_result.json')
        life = report['resource_lifecycle']; store = report['record_storage']
        assert len(front['waves']) == steps == life['retired_graphs']
        assert life['peak_resident_graphs'] == 1 and life['resident_graphs_at_exit'] == life['resident_transfers_at_exit'] == 0
        assert report['network_initializations'] == 1 and report['network_drain']['idle']
        assert all(row['same_network_objects'] for row in report['boundary_resources'])
        assert all(r[k] == 0 for r in report['rank_resources'] for k in ('compute_slots', 'send_slots', 'pending_events', 'pending_tasks', 'stale_callbacks'))
        assert all(not r['request_blocks'] and r['kv_used_bytes'] == 0 for r in front['memory_final'])
        assert life['provenance']['resident_packets'] == life['provenance']['resident_transfers'] == 0
        assert store['peak_buffered_payload_bytes'] <= policy['max_buffer_bytes']
        count = sum(1 for _ in iter_result(run/'execution', 'transfers', report))
        assert count == life['provenance']['retired_transfers']
        results[label] = dict(rounds=steps, writes=count, lifecycle=life, compressed_bytes=store['compressed_bytes'],
            service_finished_ns=report['service_finished_ns'])
    short, long = results['rounds-8']['lifecycle'], results['rounds-32']['lifecycle']
    assert short['peak_resident_transfers'] == long['peak_resident_transfers']
    assert long['provenance']['peak_resident_transfers'] <= short['provenance']['peak_resident_transfers']*2
    assert long['provenance']['peak_resident_packets'] <= short['provenance']['peak_resident_packets']*2
    assert long['transfer_identity_intervals'] == long['task_identity_intervals'] == long['tag_identity_intervals'] == 1
    save(out/'summary.json', dict(passed=True, cases=results, target_scale_executed=False,
                                 network_restarts=0, frontend_graph_archival_verified=False))
    print('PASS eight/32 rounds on one real UB network; bounded graph/transfer/provenance retention and disk failure')

if __name__ == '__main__':main()
