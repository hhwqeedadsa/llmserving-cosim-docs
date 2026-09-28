"""Verify M1 port windows, exact payload provenance and observer non-interference."""
import argparse
import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    build, output = args.build_dir.resolve(), args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary = build / 'output/cosim/ns3.44-cosim-write'
    fixture = read(root / 'fixtures/m1_transfer/single_write.json')
    config = read(root / 'fixtures/m1_transfer/observation.json')
    system = root / 'fixtures/m1_compute/system.json'
    case = root / 'third_party/ns-3-ub/scratch/2nodes_single-tp'
    commands, summaries = [], {}

    def invoke(label, source, observation, network=case):
        run_dir = output / label
        command = ['timeout', '--kill-after=5s', '30s', str(binary), str(source), str(system),
                   str(network), str(run_dir)]
        if observation is not None:
            path = output / (label + '-observation.json')
            path.write_text(json.dumps(observation, indent=2) + '\n')
            command.append(str(path))
        started = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
        (output / (label + '.stdout.log')).write_text(result.stdout)
        (output / (label + '.stderr.log')).write_text(result.stderr)
        commands.append(dict(label=label, argv=command, cwd=str(root), started_utc=started,
                             exit_code=result.returncode,
                             finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))
        (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        return result, run_dir

    def evidence(run):
        # Native logs contain simulated time. ASTRA's wall-clock log prefix is
        # deliberately removed, while every actual execution line is retained.
        trace = {str(p.relative_to(run)): sha(p) for p in (run / 'network/runlog').rglob('*')
                 if p.is_file()}
        astra = re.sub(r'^\[\d{4}-\d{2}-\d{2} [^]]+\] ', '',
                       (run / 'log/log.log').read_text(), flags=re.MULTILINE)
        assert not (run / 'log/err.log').read_text()
        return dict(report=read(run / 'result.json'), native=trace, astra=astra,
                    graphs=[sha(run / f'write.{rank}.et') for rank in (0, 1)])

    def validate(run, data, cfg):
        observed = rows(run / 'observed/port_counters.jsonl')
        endpoints = rows(run / 'observed/endpoint_ops.jsonl')
        audit, report = read(run / 'observation-audit.json'), read(run / 'result.json')
        native = (run / 'network/runlog' /
                  f"PortTrace_node_{cfg['switch_id']}_port_{cfg['port_id']}.tr").read_text()
        starts = [(int(Decimal(t) * 1000), int(size)) for t, size in re.findall(
            r'\[([\d.]+)us\] Port Tx, port ID: \d+ PacketSize: (\d+)', native)]
        assert sum(size for _, size in starts) == audit['native_tx_bytes_delta'] == audit['all_successful_start_bytes']
        assert len(starts) == audit['all_successful_start_packets']
        bounds = range(cfg['window_start_ns'], cfg['window_end_ns'], cfg['window_period_ns'])
        assert len(observed) == len(bounds)
        for row, start in zip(observed, bounds):
            end = min(start + cfg['window_period_ns'], cfg['window_end_ns'])
            sizes = [size for t, size in starts if start <= t < end]
            assert row == dict(switch_id=cfg['switch_id'], port_id=cfg['port_id'], direction='egress',
                               local_window_start_ns=start, local_window_end_ns=end,
                               counted_packets=len(sizes), counted_packet_bytes=sum(sizes))
        # Strict field equality prevents hidden global ids and simulator times
        # from entering endpoint observations.
        assert endpoints == [
            dict(rank_id=0, local_record_id=1, kind='write_send', start_local_ns=data['start_ns'],
                 end_local_ns=report['send_done_ns']),
            dict(rank_id=1, local_record_id=1, kind='write_receive', start_local_ns=data['receive_start_ns'],
                 end_local_ns=report['receive_done_ns'])]
        actual = evidence(run)['astra']
        for rank, node, record in zip((0, 1), (1, 2), endpoints):
            for event, field in [('issue', 'start_local_ns'), ('callback', 'end_local_ns')]:
                times = re.findall(fr'{event},sys->id={rank}, tick=(\d+), node->id={node},', actual)
                assert times == [str(record[field])]
        public = read(run / 'observed/public_config.json')
        assert set(public) == {'schema_version', 'counter_location', 'byte_semantics', 'switch_id',
                               'port_id', 'direction', 'observation_start_local_ns', 'observation_end_local_ns',
                               'window_period_ns', 'clock_resolution_ns'}
        assert public['counter_location'] == 'egress_packet_start'
        if not cfg['record_provenance']:
            assert not (run / 'truth').exists()
            return audit
        operation = rows(run / 'truth/operation_registry.jsonl')
        clocks = read(run / 'truth/clock_models.json')
        assert clocks['offset_ns'] == clocks['drift'] == 0
        transfer = rows(run / 'truth/transfer_registry.jsonl')
        ranges = rows(run / 'truth/packet_ranges.jsonl')
        truth = rows(run / 'truth/port_byte_truth.jsonl')
        assert len(operation) == len(transfer) == 1
        assert operation[0]['operation_id'] == transfer[0]['operation_id'] == 'm1-write/write'
        assert transfer[0]['transfer_id'] == data['transfer_id'] > 2**32
        assert transfer[0]['ub_task_id'] == data['task_id']
        assert transfer[0]['task_byte_range'] == [0, data['bytes']]
        assert transfer[0]['payload_bytes'] == transfer[0]['business_payload_bytes'] == data['bytes']
        assert transfer[0]['application_metadata_bytes'] == 0
        assert len(truth) == len(observed)
        for o, t in zip(observed, truth):
            matching = [p for p in ranges if t['window_start_sim_ns'] <= p['sim_time_ns'] < t['window_end_sim_ns']]
            assert o['counted_packet_bytes'] == sum(t[k] for k in (
                'business_payload_bytes', 'application_metadata_bytes', 'data_protocol_overhead_bytes',
                'control_packet_bytes', 'unattributed_bytes'))
            assert t['business_payload_bytes'] == sum(p['task_byte_range'][1] - p['task_byte_range'][0]
                                                      for p in matching)
            assert t['data_protocol_overhead_bytes'] == sum(p['packet_byte_range'][0] for p in matching)
            assert t['application_metadata_bytes'] == t['unattributed_bytes'] == 0
        for p in ranges:
            assert p['operation_id'] == operation[0]['operation_id']
            assert p['transfer_id'] == data['transfer_id'] and p['ub_task_id'] == data['task_id']
            a, b = p['task_byte_range']; c, d = p['packet_byte_range']
            assert 0 <= a < b <= data['bytes'] and 0 < c < d == p['counted_packet_bytes']
            assert b - a == d - c
        if cfg['switch_id'] == 3 and cfg['window_start_ns'] == 0:
            cursor = 0
            for p in sorted(ranges, key=lambda p: p['task_byte_range'][0]):
                a, b = p['task_byte_range']
                assert a == cursor, (run, cursor, a)
                cursor = b
            assert cursor == data['bytes'] == report['committed_bytes']
            assert sum(t['control_packet_bytes'] for t in truth) > 0
        if cfg['switch_id'] == 2:
            assert ranges == [] and sum(t['business_payload_bytes'] for t in truth) == 0
            assert sum(t['control_packet_bytes'] for t in truth) == sum(o['counted_packet_bytes'] for o in observed) > 0
        return audit

    variants = [('normal', {}, {}), ('one-byte', {'bytes': 1}, {}),
                ('ta-tail', {'bytes': 65537}, {}),
                ('late-reclaim', {'receive_start_ns': 20000, 'reclaim_tps': True}, {}),
                ('control-port', {}, {'switch_id': 2}),
                ('clipped-window', {}, {'window_start_ns': 1000, 'window_end_ns': 8500, 'window_period_ns': 999}),
                ('custom-segment', {'bytes': 65537}, {})]
    normal = None
    for label, updates, obs_updates in variants:
        data, cfg = dict(fixture, **updates), dict(config, **obs_updates)
        source = output / (label + '-input.json')
        source.write_text(json.dumps(data, indent=2) + '\n')
        network = case
        if label == 'custom-segment':
            network = output / 'custom-network'
            network.mkdir()
            for name in ('network_attribute.txt', 'node.csv', 'topology.csv', 'routing_table.csv', 'transport_channel.csv'):
                shutil.copyfile(case / name, network / name)
            with (network / 'network_attribute.txt').open('a') as stream:
                stream.write('\ndefault ns3::UbJetty::TaSegmentBytes "5000"\n')
        results, audits, observed_hashes = [], [], []
        for mode in ('on', 'off', 'absent'):
            option = None if mode == 'absent' else dict(cfg, record_provenance=mode == 'on')
            result, run = invoke(label + '-' + mode, source, option, network)
            assert result.returncode == 0 and not result.stderr.strip(), (label, mode, result.stdout, result.stderr)
            results.append(evidence(run))
            if option is not None:
                audits.append(validate(run, data, option))
                observed_hashes.append({p.name: sha(p) for p in (run / 'observed').iterdir()})
            else:
                assert not (run / 'observed').exists() and not (run / 'truth').exists()
        assert results[0] == results[1] == results[2], label
        assert audits[0] == audits[1] and observed_hashes[0] == observed_hashes[1], label
        if label == 'normal':
            normal = results[0]
            assert any(r['counted_packet_bytes'] == 0 for r in rows(output / 'normal-on/observed/port_counters.jsonl'))
            assert normal['report']['data_ready_ns'] == 8534 and normal['report']['send_done_ns'] == 8622
        if label in ('control-port', 'clipped-window'):
            assert results[0] == normal, 'changing observation location/window changed execution'
        if label == 'custom-segment':
            assert results[0]['report']['committed_segments'] == 14
        summaries[label] = dict(audit=audits[0], native_files=len(results[0]['native']),
                                data_ready_ns=results[0]['report']['data_ready_ns'],
                                send_done_ns=results[0]['report']['send_done_ns'])
        print(f'PASS {label}: source provenance on/off/absent; exact windows, ranges and native traces')

    bad = [('zero-period', {'window_period_ns': 0}, 'interval or period'),
           ('empty-window', {'window_end_ns': 0}, 'interval or period'),
           ('too-many-windows', {'window_period_ns': 1}, '4096 windows'),
           ('past-stop', {'window_end_ns': 1000011}, 'unsigned field'),
           ('fractional-period', {'window_period_ns': 0.5}, 'unsigned field'),
           ('bad-switch', {'switch_id': 999}, 'does not exist'),
           ('endpoint-not-switch', {'switch_id': 0}, 'switch port'),
           ('bad-port', {'port_id': 999}, 'switch port'),
           ('bad-schema', {'schema_version': 'unknown'}, 'schema or fields'),
           ('hidden-key', {'operation_id': 'secret'}, 'schema or fields')]
    for label, updates, error in bad:
        result, run = invoke('invalid-' + label, output / 'normal-input.json', dict(config, **updates))
        assert result.returncode == 1 and error in result.stderr, (label, result.stderr)
        assert not (run / 'result.json').exists()
    (output / 'summary.json').write_text(json.dumps(dict(passed=True, variants=summaries,
        simulation_runs=len(variants) * 3, invalid_configs=len(bad)), indent=2) + '\n')
    print('PASS: 21 successful runs, 10 invalid configurations; no observer-induced execution changes')


if __name__ == '__main__':
    main()
