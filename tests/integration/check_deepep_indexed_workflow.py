"""New-directory public EP32 plan workflow, no historical run or backend input."""
from collections import Counter, deque
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def oracle(directory, config):
    """Independent source/token/expert enumeration and descendant propagation."""
    metadata = json.loads((directory/'plan.json').read_bytes())
    operations = [dict(zip(metadata['operation_columns'], json.loads(line)))
                  for line in (directory/'operations.jsonl').read_bytes().splitlines()]
    events = [json.loads(line) for line in (directory/'events.jsonl').read_bytes().splitlines()]
    ranks, experts = config['execution']['ranks'], config['model']['experts']
    local = experts//ranks
    expected = {}
    zero_count = zero_flag = remote = 0
    for call, spec in enumerate(config['calls']):
        for src, tokens in enumerate(spec['tokens']):
            counts = Counter(e for row in tokens for e in row)
            for token, choices in enumerate(tokens):
                for expert in choices:
                    owner = expert//local
                    expected[f'm{call}.d.data.s{src}.t{token}.e{expert}'] = (
                        call, 'dispatch', 'data', src, owner, expert, token, 14352, None, 1)
                    expected[f'm{call}.c.data.d{src}.t{token}.e{expert}'] = (
                        call, 'combine', 'data', owner, src, expert, token, 14336, None, 1)
            for expert in range(experts):
                owner = expert//local
                count = counts[expert]
                expected[f'm{call}.d.count.s{src}.e{expert}'] = (
                    call, 'dispatch', 'count', src, owner, expert, None, 4, -count-1, count)
                expected[f'm{call}.c.flag.d{src}.e{expert}'] = (
                    call, 'combine', 'flag', owner, src, expert, None, 4, 1, count)
                zero_count += count == 0
                zero_flag += count == 0
    assert len(operations) == len(expected) == 16448
    assert len(events) == 4*len(expected)+11*ranks*len(config['calls']) == 66144
    assert len({e[0] for e in events}) == len(events)
    assert len({o['id'] for o in operations}) == len(operations)
    fields = ('call', 'phase', 'kind', 'src', 'dst', 'expert', 'token', 'operand_bytes', 'value', 'contributions')
    for number, op in enumerate(operations):
        assert tuple(op[k] for k in fields) == expected.pop(op['id'])
        rdma = op['src'] != op['dst']
        remote += rdma
        assert op['path'] == ('rdma' if rdma else 'local')
        assert op['qp_key'] == ([op['src'], op['dst'], op['expert']%local] if rdma else None)
        for stage, event_index in zip(('issued', 'accepted', 'visible', 'sender_done'), op['events']):
            event = events[event_index]
            assert event[0] == op['id']+'.'+stage and event[1] == stage and event[4] == number
            assert event[2] == op['src' if stage in ('issued', 'sender_done') else 'dst']
    assert not expected
    children = [[] for _ in events]
    for child, event in enumerate(events):
        assert len(event[3]) == len(set(event[3]))
        for parent in event[3]:
            assert 0 <= parent < child
            children[parent].append(child)
    ids = {e[0]:i for i, e in enumerate(events)}

    def closure(held):
        blocked = {ids[name] for name in held}
        queue = deque(blocked)
        while queue:
            for child in children[queue.popleft()]:
                if child not in blocked:
                    blocked.add(child); queue.append(child)
        return dict(reachable_events=len(events)-len(blocked),
                    blocked_kernel_completions=[e[0] for i, e in enumerate(events)
                                                if e[1] == 'kernel_complete' and i in blocked])

    return closure, dict(operations=len(operations), events=len(events), remote_operations=remote,
                         zero_count=zero_count, zero_flag=zero_flag,
                         all_operation_attributes_checked=True, all_event_references_checked=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    shutil.copy2(ROOT/'examples/deepep_indexed/input.json', out/'input.json')
    env = dict(os.environ, PYTHONPATH=str(ROOT/'python'), PYTHONDONTWRITEBYTECODE='1')
    commands = []
    start = time.monotonic()

    def cli(label, arguments, expected=0):
        command = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', *arguments]
        remaining = 110-(time.monotonic()-start)
        if remaining <= 0:
            raise TimeoutError('Indexed workflow deadline exhausted')
        with (out/(label+'.stdout')).open('x') as stdout, (out/(label+'.stderr')).open('x') as stderr:
            before = time.monotonic()
            process = subprocess.run(command, cwd=out, env=env, stdout=stdout, stderr=stderr,
                                     timeout=min(30, remaining))
        commands.append(dict(label=label, argv=command, cwd=str(out), exit_code=process.returncode,
                             expected_exit_code=expected, seconds=time.monotonic()-before))
        (out/'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
        assert process.returncode == expected, label
        if expected == 0:
            return json.loads((out/(label+'.stdout')).read_bytes())

    generated = cli('generate', ['deepep-plan', '--format', 'indexed-jsonl', '--config', 'input.json', '--output', 'plan'])
    assert generated['operation_count'] == 16448 and generated['event_count'] == 66144
    config = json.loads((out/'input.json').read_bytes())
    closure, independent = oracle(out/'plan', config)
    probes = dict(normal=[], zero_count=['m0.d.count.s0.e8.visible'],
                  flag=['m0.c.flag.d0.e8.visible'], sender_done=['m0.d.data.s0.t0.e0.sender_done'],
                  combined=['m0.d.count.s0.e8.visible', 'm0.c.flag.d0.e8.visible'])
    for label, held in probes.items():
        arguments = ['deepep-check', '--plan', 'plan', '--output', label+'.json']
        for event in held:
            arguments += ['--withhold', event]
        result = cli(label, arguments)
        expected = closure(held)
        assert result['reachable_events'] == expected['reachable_events']
        assert result['blocked_kernel_completions'] == expected['blocked_kernel_completions']
        assert not result['simulated_packets'] and not result['latency_predicted']
    cli('unknown', ['deepep-check', '--plan', 'plan', '--output', 'unknown.json', '--withhold', 'bad'], 1)
    assert not (out/'unknown.json').exists()
    cli('repeat', ['deepep-plan', '--format', 'indexed-jsonl', '--config', 'input.json', '--output', 'repeat'])
    for name in ('input.json', 'plan.json', 'manifest.json', 'operations.jsonl', 'events.jsonl'):
        assert (out/'plan'/name).read_bytes() == (out/'repeat'/name).read_bytes()
    (out/'repeat').rename(out/'relocated')
    portable = cli('portable', ['deepep-check', '--plan', 'relocated', '--output', 'portable.json'])
    assert portable == json.loads((out/'normal.json').read_bytes())
    # Rehash a changed operation and all metadata. Compiler comparison must
    # reject it even though the updated checksum chain is internally coherent.
    target = out/'relocated/operations.jsonl'
    lines = target.read_bytes().splitlines(keepends=True)
    row = json.loads(lines[0]); row[8] += 1
    lines[0] = (json.dumps(row, ensure_ascii=False, separators=(',', ':'))+'\n').encode()
    target.write_bytes(b''.join(lines))
    plan_path = out/'relocated/plan.json'
    plan = json.loads(plan_path.read_bytes())
    info = plan['files']['operations.jsonl']
    info.update(bytes=target.stat().st_size, sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    plan_raw = (json.dumps(plan, ensure_ascii=False, separators=(',', ':'))+'\n').encode()
    plan_path.write_bytes(plan_raw)
    manifest_path = out/'relocated/manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest.update(files=plan['files'], plan_bytes=len(plan_raw), plan_sha256=hashlib.sha256(plan_raw).hexdigest())
    manifest_path.write_text(json.dumps(manifest)+'\n')
    cli('rehashed-tamper', ['deepep-check', '--plan', 'relocated', '--output', 'tampered.json'], 1)
    assert not (out/'tampered.json').exists()
    # Old flat limits are unchanged and still reject this full input.
    flat = dict(config, schema_version='moe-deepep-ll-plan-input/1', limits=dict(
        max_events=65536, max_operations=16384, max_input_bytes=65536, max_output_bytes=16*2**20))
    (out/'flat.json').write_text(json.dumps(flat)+'\n')
    cli('old-flat-reject', ['deepep-plan', '--config', 'flat.json', '--output', 'flat-plan'], 1)
    assert not (out/'flat-plan').exists()
    assert 'event budget exceeded' in (out/'old-flat-reject.stderr').read_text()
    report = dict(passed=True, public_commands=len(commands), expected_rejections=3,
        independent=independent, probes=probes, total_output_bytes=generated['total_output_bytes'],
        peak_accounted_index_bytes=generated['peak_accounted_index_bytes'],
        repeated_files_identical=5, portable_check_identical=True,
        simulated_packets=False, hardware_calibrated=False, real_route_input=False,
        scope='artificial CPU plan representation, not a 32-endpoint network execution')
    (out/'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
