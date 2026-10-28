"""Independent offline recomputation for the heavy-loss inference conditions.

Reads only the saved run directory produced by run.py; does not import platform
internals. Recomputes per-condition completion, target-message state, source-side
duplicate payload bytes, and group progress from raw captures.
"""
import argparse
from collections import Counter
import json
from pathlib import Path


def read(p):
    return json.loads(p.read_text())


def save(p, v):
    p.write_text(json.dumps(v, indent=2, ensure_ascii=False) + '\n')


def condition(stage, label, target_id, src, dst, size):
    d = stage / label
    result = read(d / 'result.json')
    front = read(d / 'frontend.json')
    row = dict(label=label, passed=result['passed'], finish_ns=result.get('finish_ns'),
               failure=result.get('failure'))
    columns = result['packet_capture']['columns']
    idx = {c: i for i, c in enumerate(columns)}
    tx = Counter()
    rx_contiguous_max = 0
    for e in result['packet_capture']['events']:
        if e[idx['message_id']] != target_id:
            continue
        if e[idx['ip_protocol']] == 17 and e[idx['event']] == 0 and e[idx['node']] == src:
            tx[e[idx['sequence']]] += 1
    flow = next((m for m in result['messages'] if m['id'] == target_id), None)
    if flow:
        row['target'] = dict(receive_ready_ns=flow.get('receive_ready_ns'), ack_ns=flow.get('ack_ns'),
                             receiver_contiguous_bytes=flow.get('receiver_contiguous_bytes'),
                             source_data_payload_bytes=flow.get('source_data_payload_bytes'))
    row['source_transmissions'] = {str(k): v for k, v in sorted(tx.items())}
    payload_sent = sum(v * min(1000, size - k) for k, v in tx.items())
    row['source_payload_bytes_sent'] = payload_sent
    row['duplicate_payload_bytes'] = payload_sent - size if payload_sent >= size else None
    row['drop_counts'] = result.get('fault_injection', {}).get('drop_counts', [])
    row['capture_complete'] = result['packet_capture']['complete']
    groups = {}
    for w in front['history']:
        g = groups.setdefault(w['group_index'], [0, 0])
        g[1] += 1
        g[0] += 1 if 'finished_ns' in w else 0
    row['waves_finished_by_group'] = {str(k): dict(finished=v[0], total=v[1]) for k, v in groups.items()}
    row['requests_finished'] = sum(q['state'].lower() == 'finished' for q in front['requests'])
    row['requests_total'] = len(front['requests'])
    return row


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    stage = a.input.resolve()
    out = a.output.resolve()
    if out.exists():
        raise RuntimeError('analysis output must not exist')
    out.mkdir(parents=True)
    conditions = read(stage / 'conditions.json')
    target = conditions['target']
    labels = ['baseline'] + [c['label'] for c in conditions['cases']]
    rows = [condition(stage, l, target['id'], target['src'], target['dst'], target['bytes']) for l in labels]
    base = rows[0]
    for r in rows[1:]:
        if base['finish_ns'] and r['finish_ns']:
            r['finish_delta_ns'] = r['finish_ns'] - base['finish_ns']
        if base.get('target', {}).get('ack_ns') and r.get('target', {}).get('ack_ns'):
            r['target_ack_delta_ns'] = r['target']['ack_ns'] - base['target']['ack_ns']
    save(out / 'analysis.json', dict(target=target, conditions=rows))

    lines = ['# 重度丢包对照：离线复核', '',
             '| 条件 | 通过 | 服务完成(ns) | 相对基线(ns) | 目标确认(ns) | 目标确认差(ns) | 重复载荷(B) | 丢弃命中 | 组0波次 | 组1波次 | 完成请求 |',
             '|---|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        t = r.get('target') or {}
        g = r['waves_finished_by_group']
        lines.append('| {} | {} | {} | {} | {} | {} | {} | {} | {}/{} | {}/{} | {}/{} |'.format(
            r['label'], r['passed'], r['finish_ns'], r.get('finish_delta_ns', 0),
            t.get('ack_ns'), r.get('target_ack_delta_ns', 0), r.get('duplicate_payload_bytes'),
            r['drop_counts'], g.get('0', {}).get('finished', 0), g.get('0', {}).get('total', 0),
            g.get('1', {}).get('finished', 0), g.get('1', {}).get('total', 0),
            r['requests_finished'], r['requests_total']))
    lines += ['', '失败条件：' + ', '.join(r['label'] + '=' + json.dumps(r['failure'], ensure_ascii=False)
                                    for r in rows if not r['passed'])]
    (out / 'report.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps(dict(conditions=len(rows), output=str(out))))


if __name__ == '__main__':
    main()
