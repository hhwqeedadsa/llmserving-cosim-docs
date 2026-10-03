"""Resolved native parameters, real WRITE/RTP effects and passive observation."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import yaml


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    binary = args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    out = args.output_dir.resolve(); out.mkdir(parents=True, exist_ok=False)
    fixture = root/'fixtures/m6_scenario_backend/reference.yaml'
    original = yaml.safe_load(fixture.read_text())
    for key in ('service', 'layer', 'system'):
        original[key] = str((fixture.parent/original[key]).resolve())
    original['backend']['reference_file'] = str((fixture.parent/original['backend']['reference_file']).resolve())
    original['network']['case_path'] = str((fixture.parent/original['network']['case_path']).resolve())
    commands, cases, failures = [], {}, {}

    def save(path, value):
        path.write_text(json.dumps(value, indent=2)+'\n'); return path

    def invoke(label, argv, expected=0, stdin=None):
        argv = [str(a) for a in argv]; start = datetime.now(timezone.utc).isoformat()
        proc = subprocess.run(argv, cwd=root, input=stdin, capture_output=True, text=True, timeout=90)
        for field in ('stdout', 'stderr'):
            (out/f'{label}.{field}.log').write_text(getattr(proc, field))
        if stdin is not None: (out/f'{label}.stdin.log').write_text(stdin)
        commands.append(dict(label=label, argv=argv, cwd=str(root), started_utc=start,
            ended_utc=datetime.now(timezone.utc).isoformat(), exit_code=proc.returncode))
        save(out/'commands.json', commands)
        assert proc.returncode == expected, (label, proc.returncode, proc.stderr)
        return proc

    def command(label, config, action='run'):
        path = out/f'{label}.yaml'; path.write_text(yaml.safe_dump(config, sort_keys=False))
        return [sys.executable, '-m', 'moe_ub_cosim.scenario', action, '--input', path,
                '--backend', binary, '--output-dir', out/label]

    def run(label, config, business=1152, writes=6, local=18):
        invoke(label, command(label, config))
        path = out/label; execution = path/'run/execution'
        resolution, effective = read(path/'resolution.json'), read(execution/'effective_backend.json')
        front, result = read(path/'run/service_result.json'), read(execution/'result.json')
        assert resolution['state'] == 'executed' and resolution['normal_exit'] and result['normal_exit']
        assert resolution['calibration_status'] == 'unvalidated_reference'
        assert not resolution['target_replication_claimed'] and not resolution['reference_model_and_target_topology_imported']
        assert not resolution['base_case_attribute_file_used']
        requested = read(path/'network/backend.json')
        assert effective['catalog'] == requested['catalog']
        assert effective['topology'] == requested['topology'] and effective['effective_globals'] == requested['globals']
        assert effective['random'] == {k: config['execution'][k] for k in ('seed', 'run')}
        assert result['random'] == effective['random']
        assert len(result['transfers']) == writes and sum(t['bytes'] for t in result['transfers']) == business
        assert len(result['local_deliveries']) == local
        assert len(result['wqe_starts']) == len(result['wqe_completions']) == writes
        assert result['network_initializations'] == 1 and result['network_drain']['idle']
        assert result['network_drain']['started_packets'] == result['network_drain']['arrived_packets']
        assert all(r[k] == 0 for r in result['rank_resources'] for k in
                   ('compute_slots', 'send_slots', 'pending_events', 'pending_tasks', 'stale_callbacks'))
        assert all(r['kv_used_bytes'] == 0 and not r['request_blocks'] for r in front['memory_final'])
        ta = int(effective['attributes']['ns3::UbJetty::TaSegmentBytes'])
        inflight = int(effective['attributes']['ns3::UbJetty::UbJettyInflightMax'])
        assert all(w['ta_segment_bytes'] == ta and w['jetty_inflight_max'] == inflight for w in result['wqe_starts'])
        assert max(result['peak_wqes'], default=0) <= config['execution']['send_slots_per_rank']
        commits = {}
        for event in result['target_writes']: commits.setdefault(event['task'], []).append(event)
        fabric = {t['transfer_id']: t for t in result['transfers']}
        for transfer in result['transfers']:
            parts = commits[transfer['ub_task_id']]
            assert sum(p['bytes'] for p in parts) == transfer['bytes'] == transfer['committed_bytes']
            assert len(parts) == transfer['committed_segments'] == (transfer['bytes']+ta-1)//ta
            assert max(p['time_ns'] for p in parts) == transfer['data_ready_ns'] < transfer['transaction_done_ns']
            assert transfer['send_done_ns'] == transfer['transaction_done_ns']
            assert transfer['receive_done_ns'] == max(transfer['receive_start_ns'], transfer['data_ready_ns'])
            assert transfer['send_callbacks'] == transfer['receive_callbacks'] == transfer['submitted_tasks'] == 1
        for wave in front['waves']:
            plan = read(path/f"run/graphs/{wave['graph_id']}/plan.json")
            assert plan['input']['chunk_bytes'] == config['execution']['chunk_bytes']
            assert plan['input']['send_slots_per_rank'] == config['execution']['send_slots_per_rank']
            events = {(e['rank_id'], e['node_id']): e for e in result['node_execution'] if e['graph_id'] == wave['graph_id']}
            for graph in plan['rank_graphs']:
                rank = graph['rank_id']; intervals = []
                for node in graph['nodes']:
                    event = events[rank, node['node_id']]
                    assert all(event['start_ns'] >= events[rank, d]['end_ns'] for d in node['deps'])
                    if node['kind'] == 'COMP':
                        assert event['end_ns']-event['start_ns'] == node['duration_ns']
                        intervals.append((event['start_ns'], event['end_ns']))
                intervals.sort(); assert all(a[1] <= b[0] for a,b in zip(intervals,intervals[1:]))
            for transfer in plan['transfers']:
                if transfer['transport_profile_id'] == 'same-chip-local-reference':
                    assert transfer['transfer_id'] not in fabric and 'ub_task_id' not in transfer
                else:
                    actual = fabric[transfer['transfer_id']]
                    assert transfer['payload_bytes'] == actual['bytes'] <= config['execution']['chunk_bytes']
                    assert events[transfer['src_rank'], transfer['send_node_id']]['end_ns'] == actual['transaction_done_ns']
                    assert events[transfer['dst_rank'], transfer['recv_node_id']]['end_ns'] == actual['receive_done_ns']
        assert all(b['submitted_ns'] == a['finished_ns'] for a,b in zip(front['waves'], front['waves'][1:]))
        for local_result in result['local_deliveries']:
            assert local_result['transfer_id'] not in fabric
            assert local_result['send_callbacks'] == local_result['recv_callbacks'] == 1
            assert not local_result['active'] and not local_result['pending_events']
        observation = config['observation']
        packets = []
        if observation and observation['record_provenance']:
            truth = rows(execution/'truth/transfer_registry.jsonl')
            assert {t['transfer_id'] for t in truth} == set(fabric)
            assert sum(t['business_payload_bytes'] for t in truth) == business
            packets = rows(execution/'truth/packet_ranges.jsonl')
            assert all(0 < p['task_byte_range'][1]-p['task_byte_range'][0] <= 4096 for p in packets)
            for observed, truth in zip(rows(execution/'observed/port_counters.jsonl'), rows(execution/'truth/port_byte_truth.jsonl')):
                assert observed['counted_packet_bytes'] == sum(truth[k] for k in
                    ('business_payload_bytes','application_metadata_bytes','data_protocol_overhead_bytes','control_packet_bytes','unattributed_bytes'))
                assert truth['unattributed_bytes'] == 0
        cases[label] = dict(finished_ns=front['finished_ns'], writes=writes, business_bytes=business,
            local_deliveries=local, target_segments=len(result['target_writes']),
            observed_payload_packets=len(packets), peak_wqes=result['peak_wqes'])
        return front, result, effective

    base = run('reference', original)
    assert base[2]['attributes']['ns3::UbSwitchAllocator::AllocationTime'] == '+10ns'
    assert all(n['allocation_ns'] == 1 for n in base[2]['topology']['nodes'])
    assert 'ns3::UbEgressQueue::MaxEgressBytes' in base[2]['catalog']['attributes']
    assert 'UB_FAULT_ENABLE' in base[2]['catalog']['globals']
    for label, observation in [('source-off', dict(original['observation'], record_provenance=False)), ('no-observation', None)]:
        config = deepcopy(original); config['observation'] = observation
        result = run(label, config)
        assert result[:2] == base[:2]
        assert not (out/label/'run/execution/truth').exists()
    for p in (out/'reference/run/execution/observed').iterdir():
        assert p.read_bytes() == (out/'source-off/run/execution/observed'/p.name).read_bytes()
    for label, key, value in [('slow-link', 'rate_bps', 1000000000), ('long-link', 'delay_ns', 2000)]:
        config = deepcopy(original); config['network']['defaults'][key] = value
        front, result, _ = run(label, config)
        assert front['finished_ns'] > base[0]['finished_ns']
        assert result['transfers'][0]['data_ready_ns'] > base[1]['transfers'][0]['data_ready_ns']
        baseline_compute = [e for e in base[1]['node_execution'] if e['graph_id']=='g0' and e['rank_id']==12 and e['kind']=='COMP']
        changed_compute = [e for e in result['node_execution'] if e['graph_id']=='g0' and e['rank_id']==12 and e['kind']=='COMP']
        assert changed_compute[0]['start_ns'] > baseline_compute[0]['start_ns']
        assert front['waves'][1]['submitted_ns'] > base[0]['waves'][1]['submitted_ns']
    config = deepcopy(original); config['backend']['attributes']['ns3::UbJetty::TaSegmentBytes'] = 64
    run('ta64', config)
    assert cases['ta64']['target_segments'] == 18 and cases['reference']['target_segments'] == 6
    assert cases['ta64']['observed_payload_packets'] > cases['reference']['observed_payload_packets']
    config = deepcopy(original); config['execution']['chunk_bytes'] = 64
    run('chunk64', config, writes=18)
    config['execution']['send_slots_per_rank'] = 1
    run('chunk64-slot1', config, writes=18)
    assert max(cases['chunk64']['peak_wqes']) == 2 and max(cases['chunk64-slot1']['peak_wqes']) == 1
    config = deepcopy(original)
    config['network']['nodes'] = [dict(node_id=4, allocation_ns=200, forward_ns=30)]
    config['network']['links'] = [dict(src=4, src_port=2, dst=2, dst_port=0, rate_bps=12000000000, delay_ns=500)]
    config['backend']['attributes'].update({'ns3::UbEgressQueue::MaxEgressBytes':262144,
        'ns3::UbJetty::UbJettyInflightMax':2, 'ns3::UbTransportChannel::DefaultMaxInflightPacketSize':4,
        'ns3::UbTransportChannel::DefaultMaxWqeSegNum':2})
    config['execution'].update(seed=7, run=3)
    _, _, effective = run('overrides', config)
    assert effective['topology']['nodes'][4]['allocation_ns'] == 200
    assert effective['topology']['links'][2]['delay_ns'] == 500
    assert all(o['attributes']['ns3::UbEgressQueue::MaxEgressBytes']=='262144' for o in effective['objects'] if o['role'].startswith('egress:'))
    service = read(Path(original['service'])); service['requests'] = service['requests'][:1]
    config = deepcopy(original); config['service'] = str(save(out/'local-service.json', service))
    _, local_result, _ = run('all-local', config, business=0, writes=0, local=8)
    assert local_result['network_drain']['started_packets'] == 0
    # Exercise the compiled 4096-byte TP limit with chunks larger than a packet.
    service = read(Path(original['service'])); layer = read(Path(original['layer']))
    service['model'] = service['model_spec']['name'] = 'synthetic/large-payload-gqa'
    service['model_spec']['hidden_size'] = layer['hidden_dim'] = 4096
    service['model_spec']['attention'] = dict(num_heads=64, num_kv_heads=8, head_dim=64)
    service['memory']['max_kv_blocks_per_rank'] = 65536
    for rank in service['memory']['ranks']:
        rank.update(device_bytes=1000000000, communication_buffer_bytes=1048576)
    config = deepcopy(original); config['execution']['chunk_bytes'] = 8192
    config['service'] = str(save(out/'large-service.json', service)); config['layer'] = str(save(out/'large-layer.json', layer))
    run('large-packets', config, business=73728, writes=9)
    packets = rows(out/'large-packets/run/execution/truth/packet_ranges.jsonl')
    assert packets and all(p['task_byte_range'][1]-p['task_byte_range'][0]==4096 for p in packets)
    # An inactive candidate may be incomplete; it is not silently selected.
    reference = yaml.safe_load(Path(original['backend']['reference_file']).read_text())
    reference['ns3_backend']['transaction_profiles']['unused-unimplemented'] = None
    refpath = out/'inactive-reference.yaml'; refpath.write_text(yaml.safe_dump(reference))
    config = deepcopy(original); config['backend']['reference_file'] = str(refpath)
    invoke('resolve-only', command('resolve-only', config, 'resolve'))
    assert read(out/'resolve-only/resolution.json')['state'] == 'resolved_not_executed'
    assert not (out/'resolve-only/run').exists()

    rejected = {
        'read': (lambda c:c['backend']['profile_by_role'].__setitem__('dispatch','urma_read_rtp_candidate'), 'Active profile'),
        'ctp': (lambda c:c['backend']['attributes'].__setitem__('ns3::UbApp::TransportMode','CTP'), 'Unsupported joint'),
        'native-ldst': (lambda c:c['backend']['profile_by_role'].__setitem__('combine','native_ldst_candidates'), 'Active profile'),
        'multipath': (lambda c:c['backend']['attributes'].__setitem__('ns3::UbApp::EnableMultiPath',True), 'Unsupported joint'),
        'spray': (lambda c:c['backend']['attributes'].__setitem__('ns3::UbTransportChannel::UsePacketSpray',True), 'Unsupported joint'),
        'retrans': (lambda c:c['backend']['attributes'].__setitem__('ns3::UbTransportChannel::EnableRetrans',True), 'Unsupported joint'),
        'cc': (lambda c:c['backend']['globals'].__setitem__('UB_CC_ENABLED',True), 'Unsupported joint'),
        'fault': (lambda c:c['backend']['globals'].__setitem__('UB_FAULT_ENABLE',True), 'Unsupported joint'),
        'unknown': (lambda c:c['backend']['attributes'].__setitem__('ns3::UbApp::MadeUp',1), 'Unknown backend'),
        'tp-payload': (lambda c:c['backend'].__setitem__('tp_max_data_payload_bytes',2048), 'compile-time constant'),
        'on-demand': (lambda c:c['backend'].__setitem__('transport_channel_setup','on_demand'), 'precreated channels'),
        'zero-ta': (lambda c:c['backend']['attributes'].__setitem__('ns3::UbJetty::TaSegmentBytes',0), 'nonpositive active resource'),
        'negative-credit': (lambda c:c['backend']['attributes'].__setitem__('ns3::UbPort::CbfcInitCreditCell',-1), 'nonpositive active resource'),
        'negative-ta': (lambda c:c['backend']['attributes'].__setitem__('ns3::UbJetty::TaSegmentBytes',-1), 'invalid unsigned'),
        'invalid-time-unit': (lambda c:c['backend']['attributes'].__setitem__('ns3::UbPort::UbInterframeGap','10bananas'), 'explicit unit'),
        'negative-time': (lambda c:c['backend']['attributes'].__setitem__('ns3::UbPort::UbInterframeGap','-1ns'), 'nonnegative'),
        'time-overflow': (lambda c:c['backend']['attributes'].__setitem__('ns3::UbPort::UbInterframeGap','1e100s'), 'execution range'),
        'invalid-rate': (lambda c:c['network']['defaults'].__setitem__('rate_bps',0), 'Invalid rate_bps'),
        'oversize-buffer': (lambda c:c['execution'].__setitem__('chunk_bytes',8192), 'Communication buffer'),
    }
    for label, (mutate, message) in rejected.items():
        config = deepcopy(original); mutate(config)
        proc = invoke('reject-'+label, command('reject-'+label, config), 1)
        assert message in proc.stderr and not (out/('reject-'+label)/'run').exists()
        failures[label] = message
    duplicate = out/'duplicate.yaml'; duplicate.write_text(fixture.read_text()+'scenario_id: duplicate\n')
    proc = invoke('reject-duplicate', [sys.executable,'-m','moe_ub_cosim.scenario','resolve','--input',duplicate,
                                      '--backend',binary,'--output-dir',out/'reject-duplicate'],1)
    assert 'duplicate YAML key' in proc.stderr; failures['duplicate'] = 'duplicate YAML key'
    # Bypass the Python resolver: the backend still rejects stale catalogs and profiles.
    resolved = read(out/'reference/network/backend.json')
    for label, mutate, message in [
        ('stale-catalog', lambda c:c['catalog'].__setitem__('ub_revision','wrong'), 'catalog does not match'),
        ('read-profile', lambda c:c['profiles']['dispatch'].__setitem__('operation','READ'), 'unimplemented active'),
        ('changed-slots', lambda c:c['execution'].__setitem__('send_slots_per_rank',3), 'invalid send slots')]:
        config = deepcopy(resolved); mutate(config)
        proc = invoke('reject-'+label, [binary,'--check-backend',save(out/f'{label}.json',config)],1)
        assert message in proc.stderr; failures[label] = message
    # A CSV edit after resolution must fail before any graph activation.
    net = out/'tampered-network'; shutil.copytree(out/'reference/network', net)
    p = net/'topology.csv'; p.write_text(p.read_text().replace('224000000000bps','1000000000bps'))
    proc = invoke('reject-live-mismatch', [binary,'m6-locality',out/'reference/run',original['system'],net,'-',
        out/'tampered-execution',10000000], 1)
    assert 'live topology differs' in proc.stderr and not (out/'tampered-execution/result.json').exists()
    failures['live-mismatch'] = 'topology rejected before business activation'
    sources = [p for folder in ('backend','python/moe_ub_cosim','fixtures/m6_scenario_backend')
               for p in (root/folder).glob('*') if p.is_file()]
    save(out/'summary.json', dict(passed=True, cases=cases, failures=failures,
        source_hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}))
    print('PASS: resolved native catalog, live objects, packetization, causal feedback, passive observation and rejection')


if __name__ == '__main__':
    main()
