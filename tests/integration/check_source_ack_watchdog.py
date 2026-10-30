"""Independent saved-run comparison, raw-prefix checks and rejection workflow."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def load(path):return json.loads(path.read_bytes())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path, value):
    with path.open('x') as stream:json.dump(value, stream, indent=2);stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('normal', 'local', 'reference', 'backend', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve();output.mkdir(parents=True, exist_ok=False)
    hashes = {};cases = {}
    for mode in ('normal', 'local'):
        run = getattr(args, mode).resolve();ref = args.reference.resolve()/mode
        result = load(run/'result.json');reference = load(ref/'result.json')
        assert result['passed'] and result['gpu_count'] == 128
        native = result['service'].pop('source_ack_watchdog')
        assert result == reference, mode
        matched = []
        for name in ('execution_events.jsonl', 'contribution_calls.jsonl', 'contribution_summary.json'):
            assert (run/name).read_bytes() == (ref/name).read_bytes(), (mode, name)
            matched.append(name)
        files = sorted(path.relative_to(run/'graphs') for path in (run/'graphs').rglob('*') if path.is_file())
        assert files == sorted(path.relative_to(ref/'graphs') for path in (ref/'graphs').rglob('*') if path.is_file())
        for name in files:assert (run/'graphs'/name).read_bytes() == (ref/'graphs'/name).read_bytes()
        callbacks = {row['id']: row for row in result['service']['message_callbacks']}
        messages = {row['id']: row for row in result['service_transport']['messages']}
        raw = [json.loads(line[len('MOE_RDMA_EVENT '):]) for line in (run/'native.stdout.log').read_text().splitlines()
               if line.startswith('MOE_RDMA_EVENT ')]
        actual = [row for row in raw if row['type'] == 'SOURCE_ACK_TIMEOUT']
        assert actual == [json.loads(line) for line in (run/'source_ack_timeouts.jsonl').read_text().splitlines()]
        assert len(actual) == (1 if mode == 'local' else 0)
        # Independent per-message source callback intervals, without the platform observer.
        observed = 0;prefix = {};verified = []
        for frame in raw:
            if frame['type'] == 'OBSERVE':
                observed += 1
                if frame['kind'] in ('message_send_post', 'message_send_complete'):
                    mid = frame['message'];prefix.setdefault(mid, {})[frame['kind']] = frame['time_ns']
            elif frame['type'] == 'SOURCE_ACK_TIMEOUT':
                mid = frame['message'];cb = callbacks[mid]
                assert frame['observed_events'] == observed and 'message_send_post' in prefix[mid]
                assert 'message_send_complete' not in prefix[mid]
                assert frame['send_post_ns'] == cb['send_post_ns']
                assert frame['deadline_ns'] == frame['time_ns'] == cb['send_post_ns']+20000
                assert frame['time_ns'] < cb['send_callback_ns']
                assert frame['rank'] == messages[mid]['src']
                verified.append(dict(message=mid, source=messages[mid]['src'], destination=messages[mid]['dst'],
                    send_post_ns=cb['send_post_ns'], notification_ns=frame['time_ns'],
                    receiver_callback_ns=cb['recv_callback_ns'], sender_callback_ns=cb['send_callback_ns'],
                    observed_events=observed))
        expected = {mid for mid, cb in callbacks.items() if cb['send_callback_ns']-cb['send_post_ns'] > 20000}
        assert {row['message'] for row in actual} == expected
        assert native == load(run/'source_ack_watchdog.json')
        assert native['messages'] == native['source_completions'] == 62
        assert native['pending_timers'] == native['unconfirmed_messages'] == 0
        assert native['notifications'] == len(actual) and native['cancelled_timers']+len(actual) == 62
        if mode == 'local':
            assert verified == [dict(message=2, source=0, destination=6, send_post_ns=1030,
                notification_ns=21030, receiver_callback_ns=83058, sender_callback_ns=85060, observed_events=1142)]
        cases[mode] = dict(native_result_except_watchdog_equal=True, byte_identical=matched,
            graph_files_equal=len(files), application_and_drain_ns=result['finish_ns'],
            packet_events=len(result['packet_capture']['events']), watchdog=native, notices=verified)
        for name in ('result.json', 'input.json', 'rdma.json', 'native.stdout.log', 'execution_events.jsonl',
                     'contribution_calls.jsonl', 'source_ack_timeouts.jsonl', 'source_ack_watchdog.json'):
            hashes[str(run/name)] = sha(run/name)
    commands = []
    def command(argv, expected):
        result = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=30)
        commands.append(dict(argv=list(map(str, argv)), exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr))
        assert result.returncode == expected, result.stderr
        return result
    def cli(run, expected):
        return command([sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', 'rdma-check', '--run', run], expected)
    cli(args.reference.resolve()/'normal', 0)  # default-disabled old saved service
    relocated = output/'relocated';shutil.copytree(args.local, relocated)
    cli(relocated, 0)
    names = ('source_ack_timeouts.jsonl', 'native.stdout.log', 'source_ack_watchdog.json', 'result.json')
    originals = {name:(relocated/name).read_bytes() for name in names}
    notice = json.loads(originals['source_ack_timeouts.jsonl'])
    rejected = {}
    for mutation in ('wrong-message', 'early-notice', 'wrong-prefix', 'duplicate', 'missing', 'wrong-summary', 'truth-field'):
        for name, raw in originals.items():(relocated/name).write_bytes(raw)
        row = deepcopy(notice)
        if mutation == 'wrong-message':row['message'] = 999999
        elif mutation == 'early-notice':row['time_ns'] -= 1;row['deadline_ns'] -= 1
        elif mutation == 'wrong-prefix':row['observed_events'] -= 1
        elif mutation == 'truth-field':row['fault_end_ns'] = 20000
        encoded = json.dumps(row, separators=(',', ':'))+'\n'
        rows = originals['native.stdout.log'].decode().splitlines(keepends=True)
        index = next(i for i, line in enumerate(rows) if '"type":"SOURCE_ACK_TIMEOUT"' in line)
        if mutation == 'missing':encoded = '';rows.pop(index)
        elif mutation == 'duplicate':encoded *= 2;rows[index:index+1] = ['MOE_RDMA_EVENT '+json.dumps(row)+'\n']*2
        elif mutation == 'wrong-summary':
            summary = load(relocated/'source_ack_watchdog.json');summary['notifications'] = 0
            (relocated/'source_ack_watchdog.json').write_text(json.dumps(summary))
            native = load(relocated/'result.json');native['service']['source_ack_watchdog'] = summary
            (relocated/'result.json').write_text(json.dumps(native))
        else:rows[index] = 'MOE_RDMA_EVENT '+encoded
        (relocated/'source_ack_timeouts.jsonl').write_text(encoded)
        (relocated/'native.stdout.log').write_text(''.join(rows))
        failure = cli(relocated, 1)
        assert any(term in failure.stderr+failure.stdout for term in ('watchdog', 'source ACK')), mutation
        rejected[mutation] = True
    for name, raw in originals.items():(relocated/name).write_bytes(raw)
    # Native malformed options must fail before topology/Fabric initialization.
    native_rejected = {}
    valid = load(args.local/'input.json')['source_ack_watchdog']
    for mutation in ('bool-timeout', 'zero-budget', 'extra-field', 'missing-events'):
        row = dict(valid)
        if mutation == 'bool-timeout':row['timeout_ns'] = True
        elif mutation == 'zero-budget':row['max_messages'] = 0
        elif mutation == 'extra-field':row['extra'] = 1
        cfg = output/(mutation+'.json');save(cfg, row)
        result_path = output/(mutation+'-must-not-exist.json')
        argv = [args.backend.resolve(), '/missing-topology', '/missing-rdma', '/missing-system', '/missing-bootstrap',
                result_path, '--transport', (args.local/'service-transport.json').resolve(), '--source-ack-watchdog', cfg]
        if mutation != 'missing-events':argv += ['--execution-events', '32768']
        failure = command(argv, 1)
        assert 'watchdog' in failure.stderr and not result_path.exists()
        native_rejected[mutation] = True
    assert all(sha(Path(name)) == value for name, value in hashes.items())
    save(output/'commands.json', commands)
    save(output/'verification.json', dict(passed=True, execution_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        cases=cases, negative_checks=rejected, native_pre_fabric_rejections=native_rejected,
        relocation_passed=True, legacy_default_disabled_check_passed=True, original_hashes=hashes,
        raw_callbacks_and_deadlines_independently_checked=True,
        new_network_executions=0, scope='online source ACK deadline correctness; no recovery policy or gain'))
    print(json.dumps(dict(passed=True, negative_checks=len(rejected), native_pre_fabric_rejections=len(native_rejected),
                         normal_notices=0, local_notices=1, original_network_results_unchanged=True)))


if __name__ == '__main__':main()
