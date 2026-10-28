"""Sustained heavy loss (>=50% of packets) on one Dispatch message of the fixed two-group EP4 Decode service.

Only public CLI and saved run artifacts; never tests or private drivers. Conditions and
expectations are frozen in protocol.md before any condition runs.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def read(p):
    return json.loads(p.read_text())


def save(p, v):
    p.write_text(json.dumps(v, indent=2, ensure_ascii=False) + '\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend', type=Path, required=True)
    p.add_argument('--protoc', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--recovery', action='store_true',
                   help='Batch B: same conditions with the platform go_back_n recovery '
                        '(initial 40us, max 160us, 3 retries); registered after Batch A evidence')
    a = p.parse_args()
    out = a.output.resolve()
    if out.exists():
        raise RuntimeError('output directory must not exist')
    out.mkdir(parents=True)
    commands = []

    def cli(label, argv, success=True):
        if sum(f.stat().st_size for f in out.rglob('*') if f.is_file()) > 128 * 1024**2 \
                or shutil.disk_usage(out).free < 8 * 1024**3:
            raise RuntimeError('experiment artifact/free-space budget exceeded; no expansion')
        cmd = [sys.executable, '-m', 'moe_ub_cosim', 'scaleout', *map(str, argv)]
        with (out / (label + '.stdout')).open('w') as stdout, (out / (label + '.stderr')).open('w') as stderr:
            r = subprocess.run(cmd, cwd=ROOT, stdout=stdout, stderr=stderr, timeout=90)
        commands.append(dict(label=label, argv=cmd, cwd=str(ROOT), exit_code=r.returncode, expected_success=success))
        save(out / 'commands.json', commands)
        if (r.returncode == 0) != success:
            raise RuntimeError(label + ': unexpected exit ' + str(r.returncode) + '; inspect logs, no retry')

    config = read(ROOT / 'examples/scaleout_rdma/service.json')
    config['parallel']['groups'] = [[0, 2, 4, 6], [1, 3, 5, 7]]
    config['model']['layers'] = 1
    config['workload']['decode_steps'] = 2
    save(out / 'service.json', config)
    cli('topology', ['topology', '--config', ROOT / 'examples/scaleout_rdma/small-clos-100g.json', '--output', out / 'topology'])
    base_protocol = read(ROOT / 'examples/scaleout_rdma/rdma.json')
    base_protocol['packet_capture'] = dict(nodes=[], start_ns=0, end_ns=5000000, max_events=100000)
    if a.recovery:
        base_protocol['recovery'] = dict(mode='go_back_n', initial_rto_ns=40000,
                                         max_rto_ns=160000, max_retries=3, max_events=100000)

    save(out / 'baseline.json', base_protocol)
    cli('baseline', ['rdma-run', '--config', out / 'service.json', '--topology', out / 'topology',
                     '--protocol', out / 'baseline.json', '--backend', a.backend, '--protoc', a.protoc,
                     '--output', out / 'baseline'])
    result = read(out / 'baseline/result.json')
    if not result['packet_capture']['complete']:
        raise RuntimeError('baseline capture truncated; stop before fault conditions')

    wave = read(out / 'baseline/graphs/g0.w0/wave.json')
    target = next(m for m in wave['messages'] if m['phase'] == 'dispatch' and m['src'] == 0 and m['dst'] == 4)
    save(out / 'selected-message.json', target)
    size = target['bytes']
    payload = base_protocol['payload_bytes']
    data_seqs = list(range(0, size, payload))
    columns = result['packet_capture']['columns']
    idx = {c: i for i, c in enumerate(columns)}
    ack_seqs = sorted({row[idx['sequence']] for row in result['packet_capture']['events']
                       if row[idx['message_id']] == target['id'] and row[idx['ip_protocol']] == 252
                       and row[idx['event']] == 0 and row[idx['node']] == target['dst']})
    if not ack_seqs or ack_seqs[-1] != size or len(data_seqs) < 7:
        raise RuntimeError('baseline data/ACK streams differ from protocol definitions; stop')

    def rule(node, kind, seq, count):
        return dict(node=node, port=0, message_id=target['id'], kind=kind, sequence=seq,
                    start_ns=0, end_ns=5000000, count=count)

    nonfinal_acks = ack_seqs[:-1]
    ack_pick = nonfinal_acks[::2]
    if nonfinal_acks[-1] not in ack_pick:
        ack_pick = ack_pick + [nonfinal_acks[-1]]
    cases = [
        ('data-57-transient', [rule(target['dst'], 'data', s, 2) for s in data_seqs[::2]], True),
        ('data-57-persistent', [rule(target['dst'], 'data', s, 10000) for s in data_seqs[::2]], False),
        ('data-86-transient', [rule(target['dst'], 'data', s, 2) for s in data_seqs[:-1]], True),
        ('ack-57-transient', [rule(target['src'], 'ack', s, 2) for s in ack_pick], True),
    ]
    save(out / 'conditions.json', dict(target=target, data_sequences=data_seqs, ack_sequences=ack_seqs,
                                       cases=[dict(label=l, rules=r, expected_success=s) for l, r, s in cases]))

    summaries = {'baseline': None}
    for label, rules, success in cases:
        protocol = deepcopy(base_protocol)
        protocol['faults'] = rules
        save(out / (label + '.json'), protocol)
        cli(label, ['rdma-run', '--config', out / 'service.json', '--topology', out / 'topology',
                    '--protocol', out / (label + '.json'), '--backend', a.backend, '--protoc', a.protoc,
                    '--output', out / label], success)
        cli(label + '-diagnose', ['rdma-diagnose', '--run', out / label, '--output', out / (label + '-diagnosis.json')])

    case_rules = {label: rules for label, rules, _ in cases}
    for label in ['baseline'] + [c[0] for c in cases]:
        result = read(out / label / 'result.json')
        front = read(out / label / 'frontend.json')
        diag_path = out / (label + '-diagnosis.json')
        diag = read(diag_path) if diag_path.exists() else None
        if a.recovery and label == 'data-57-persistent':
            if result.get('failure', {}).get('reason') != 'recovery_retry_exhausted':
                raise RuntimeError('persistent loss must end as recovery_retry_exhausted, not silent stall')
        flow = next((m for m in result['messages'] if m['id'] == target['id']), None)
        callbacks = next((m for m in result.get('service', {}).get('message_callbacks', [])
                          if m['id'] == target['id']), None)
        drops = result.get('fault_injection', {}).get('drop_counts', [])
        case_rule_list = case_rules.get(label, [])
        data_expected = [r['count'] for r in case_rule_list if r['kind'] == 'data' and r['count'] <= 2]
        ack_rules = [r for r in case_rule_list if r['kind'] == 'ack']
        if data_expected and drops[:len(data_expected)] != data_expected:
            raise RuntimeError(label + ': transient data rules did not hit exactly as declared: ' + str(drops))
        if ack_rules and (len(drops) != len(case_rule_list) or any(d < 1 for d in drops)):
            raise RuntimeError(label + ': some declared drops never happened: ' + str(drops))
        idx = {c: i for i, c in enumerate(result['packet_capture']['columns'])}
        transmissions = {}
        for row in result['packet_capture']['events']:
            if row[idx['message_id']] == target['id'] and row[idx['ip_protocol']] == 17 \
                    and row[idx['event']] == 0 and row[idx['node']] == target['src']:
                transmissions[row[idx['sequence']]] = transmissions.get(row[idx['sequence']], 0) + 1
        summaries[label] = dict(
            passed=result['passed'], finish_ns=result.get('finish_ns'),
            failure=result.get('failure'),
            target=dict(flow, source_transmissions=transmissions) if flow else None,
            callbacks=callbacks, drop_counts=drops,
            capture_complete=result['packet_capture']['complete'],
            packet_events=len(result['packet_capture']['events']),
            waves_by_group={},
            request_states={state: sum(q['state'] == state for q in front['requests'])
                            for state in sorted({q['state'] for q in front['requests']})})
        for w in front['history']:
            g = summaries[label]['waves_by_group'].setdefault(w['group_index'], dict(finished=0, total=0))
            g['total'] += 1
            g['finished'] += 1 if 'finished_ns' in w else 0
        for path in (out / label / 'graphs').glob('*/wave.json'):
            base = out / 'baseline/graphs' / path.parent.name / 'wave.json'
            if base.exists() and base.read_bytes() != path.read_bytes():
                raise RuntimeError(label + ': common submitted graph changed')

    sources = [ROOT / 'backend/rdma/fabric.cc', ROOT / 'backend/rdma/fabric.hh',
               ROOT / 'python/moe_ub_cosim/scaleout_diagnostics.py', Path(__file__).resolve()]
    save(out / 'summary.json', dict(
        passed=True,
        scope='8 endpoints, two interleaved EP4 groups, one layer, two ready-KV Decode steps; declared compute; native RDMA recovery only',
        target=target, data_sequences=data_seqs, ack_sequences=ack_seqs, cases=summaries,
        source_hashes={str(s.relative_to(ROOT)): hashlib.sha256(s.read_bytes()).hexdigest() for s in sources},
        limitations=[
            'Deterministic per-sequence drops on one message, not a network-wide random loss rate or calibrated hardware fault rate.',
            'Persistent-loss failure means incomplete within the 5ms simulation window; it does not establish production behavior.',
            'Compute is declared; routes are one synthetic selection; no GPU or RTO hardware calibration.']))
    print('heavy loss conditions completed, including the preserved expected failure')


if __name__ == '__main__':
    main()
