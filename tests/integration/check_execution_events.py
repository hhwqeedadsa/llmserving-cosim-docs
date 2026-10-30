"""Audit two existing 128-endpoint executions; no network replay or build."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from moe_ub_cosim.execution_events import audit
from moe_ub_cosim.scaleout_verify import node_events


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def load(path):return json.loads(path.read_bytes())
def save(path, value):
    with path.open('x') as stream:json.dump(value, stream, indent=2);stream.write('\n')


def check(off, on, output):
    output.mkdir(parents=True, exist_ok=False)
    clean, observed = load(off/'result.json'), load(on/'result.json')
    assert clean['passed'] and observed['passed'] and clean['gpu_count'] == observed['gpu_count'] == 128
    config = load(off/'input.json');on_config = load(on/'input.json')
    option = on_config.pop('execution_events')
    assert on_config == config
    assert config['parallel']['groups'] == [list(range(0, 128, 2)), list(range(1, 128, 2))]
    assert load(off/'topology.json') == load(on/'topology.json')
    native = deepcopy(observed);native['service'].pop('execution_events')
    assert clean == native  # Every packet observation, queue, callback and timestamp.
    capture = clean['packet_capture']
    assert capture['complete'] and capture['omitted_events'] == 0 and len(capture['events']) == capture['observations']
    nodes = node_events(off)
    assert nodes == node_events(on)
    assert len(list((off/'graphs').glob('*/wave.json'))) == 4
    for path in (off/'graphs').rglob('*'):
        if path.is_file():assert path.read_bytes() == (on/'graphs'/path.relative_to(off/'graphs')).read_bytes()
    summary = audit(on, observed, load(on/'frontend.json'))
    assert summary['events'] == 2*summary['nodes']+4*summary['messages']+summary['registered_graphs']+1
    assert not (off/'execution_events.jsonl').exists()
    assert audit(off, clean, load(off/'frontend.json')) is None
    source_hashes = {str(path):sha(path) for path in (on/'execution_events.jsonl', on/'result.json', on/'execution_observation.json')}
    events = [json.loads(line) for line in (on/'execution_events.jsonl').read_text().splitlines()]
    commands = []
    # A relocated complete user workflow, with no old run path needed by checks.
    relocated = output/'relocated'
    shutil.copytree(on, relocated)
    command = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', 'rdma-check', '--run', str(relocated)]
    run = subprocess.run(command, capture_output=True, text=True, timeout=30)
    commands.append(dict(argv=command, exit_code=run.returncode, stdout=run.stdout, stderr=run.stderr))
    assert run.returncode == 0
    original_events = (relocated/'execution_events.jsonl').read_bytes()
    failures = {}
    for name in ('missing-drain', 'wrong-rank', 'different-log-time', 'duplicate', 'truncated'):
        rows = deepcopy(events)
        if name == 'missing-drain':rows.pop()
        elif name == 'wrong-rank':
            target = next(e for e in rows if e['kind'] == 'message_recv_complete')
            target['rank'] = (target['rank']+1)%128
        elif name == 'different-log-time':
            target = next(e for e in rows if e['kind'] == 'message_recv_complete')
            target['time_ns'] += 1
        elif name == 'duplicate':rows.insert(0, deepcopy(rows[0]))
        raw = ''.join(json.dumps(row, separators=(',', ':'))+'\n' for row in rows).encode()
        if name == 'truncated':raw = raw[:-1]
        (relocated/'execution_events.jsonl').write_bytes(raw)
        run = subprocess.run(command, capture_output=True, text=True, timeout=30)
        commands.append(dict(argv=command, case=name, exit_code=run.returncode, stdout=run.stdout, stderr=run.stderr))
        assert run.returncode != 0, name
        failures[name] = dict(rejected=True, exit_code=run.returncode)
    (relocated/'execution_events.jsonl').write_bytes(original_events)
    assert all(sha(Path(path)) == digest for path, digest in source_hashes.items())
    callbacks = observed['service']['message_callbacks']
    different = [row for row in callbacks if row['recv_callback_ns'] < row['send_callback_ns']]
    assert different
    first = different[0]
    spec = next(row for row in observed['service_transport']['messages'] if row['id'] == first['id'])
    live_prefix = next(e['sequence'] for e in events if e['kind'] == 'message_recv_complete' and e['message'] == first['id'])
    assert not any(e['kind'] == 'message_send_complete' and e['message'] == first['id'] for e in events[:live_prefix+1])
    row = dict(passed=True, source_execution_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        gpu_endpoints=128, independent_synthetic_sources=4, ep_sizes=[64, 64], graphs=4,
        same_graph_bytes=True, same_complete_packet_queue_callback_results=True,
        packet_events=len(capture['events']), live_observation=summary,
        messages_receiver_before_sender=len(different), real_callback_example=dict(first, src=spec['src'], dst=spec['dst']),
        fault_injected=False, new_network_executions=0, input_recorded_not_tensor_execution=True,
        negative_checks=failures, original_evidence_unchanged=True, source_hashes=source_hashes,
        scope='128 Clos passive observation correctness and noninterference; no recovery or algorithm gain')
    save(output/'verification.json', row)
    save(output/'commands.json', commands)
    print(json.dumps(row, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('off', 'on', 'output'):parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    check(args.off.resolve(), args.on.resolve(), args.output.resolve())
