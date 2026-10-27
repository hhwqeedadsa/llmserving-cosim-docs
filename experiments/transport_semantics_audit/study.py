"""Read-only audit of saved MRT1 packet traces; never launches a simulator."""
import argparse
from collections import Counter, defaultdict
import hashlib
import html
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile

ARCHIVE_SHA = '25ce43a9dc4d72c402df935529d1083dd2801d236b85131ad5646762fc758e40'
CONDITIONS = ('single', 'quad')


def load(path):
    return json.loads(path.read_bytes())


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(repo, root):
    assert not (root / 'inputs.json').exists()
    old = repo / 'runs/ecmp_network_choice_20261027'
    archive = old / 'checkpoint.tar.gz'
    assert sha(archive) == ARCHIVE_SHA
    members = ['stage/observed/batch4/input.json', 'stage/observed/batch4/plan.json']
    for name in CONDITIONS:
        members += [f'stage/truth/batch4/{name}/modern/{file}' for file in
                    ('result.json', 'initial.json', 'modern-execution.json', 'protocol.json', 'topology.json')]
    inputs = []
    with tarfile.open(archive) as source:
        manifest = json.load(source.extractfile('manifest.json'))['files']
        for name in members:
            original = old / name
            expected = manifest['run/' + name]
            assert original.stat().st_size == expected['bytes'] <= 12*2**20 and sha(original) == expected['sha256']
            target = root / name.removeprefix('stage/')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
            inputs.append(dict(path=target.relative_to(root).as_posix(), **expected))
    assert sum(r['bytes'] for r in inputs) < 32*2**20
    sources = []
    paths = ['backend/rdma/remote_memory.hh', 'backend/rdma/remote_transactions.cc', 'backend/rdma/persistent_streams.cc']
    native = 'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3'
    paths += [native + '/src/point-to-point/model/rdma-hw.cc']
    for name in paths:
        original = repo / name
        local = name.removeprefix(native + '/') if name.startswith(native) else name
        owner = repo / native if name.startswith(native) else repo
        blob = subprocess.check_output(['git', 'rev-parse', 'HEAD:' + local], cwd=owner, text=True).strip()
        raw = original.read_bytes()
        assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == blob
        target = root / 'observed/sources' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        sources.append(dict(path=target.relative_to(root).as_posix(), sha256=sha(target), git_blob=blob,
                            revision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=owner, text=True).strip()))
    linux = repo / 'data/research/rc_atomic_20261022'
    recorded = load(linux / 'sources.json')
    for row in recorded['sources']:
        original = linux / Path(row['path']).name
        assert sha(original) == row['sha256'] and original.stat().st_size == row['bytes']
        target = root / 'observed/rxe' / original.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        sources.append(dict(path=target.relative_to(root).as_posix(), sha256=sha(target),
                            ref='Linux v6.12; saved SHA256 only, no independently pinned tree', url=row['url']))
    shutil.copyfile(linux / 'sources.json', root / 'observed/rxe/sources.json')
    save(root / 'inputs.json', dict(source_archive_sha256=ARCHIVE_SHA, files=inputs, sources=sources,
                                   new_networks=0, downloads=0))


def analyze_condition(root, name):
    base = root / f'truth/batch4/{name}/modern'
    result, frontend = load(base / 'result.json'), load(base / 'modern-execution.json')
    assert result['passed'] and result['packet_capture']['complete'] and result['packet_capture']['omitted_events'] == 0
    transactions = result['transactions']
    assert not transactions['roce_wire_equivalent']
    transport = transactions['transport']
    assert transport['physical_drained'] and not result['fault_drops']
    mtu = load(base / 'protocol.json')['payload_bytes']
    source_rows = transport['source_packets']
    payloads = {(p['stream'], p['sequence']): p for p in transport['source_payload_packets']}
    assert len(payloads) == len(source_rows)
    streams = {s['id']: s for s in transport['streams']}
    native_ops = {(o['connection'], o['sequence']): o for o in transactions['operations'] if o.get('delivery') != 'declared_self_service/1'}
    logical = {o['id']: o for o in load(root / 'observed/batch4/plan.json')['operations']}
    work_packets = defaultdict(list)
    for p in source_rows:
        time, stream, src, dst, nic, sequence, length, uid = p
        payload = payloads[stream, sequence]
        assert payload['time_ns'] == time and len(bytes.fromhex(payload['hex'])) == length
        owners = [w for w in transport['work'] if w['stream'] == stream and w['begin'] <= sequence < w['end']]
        assert len(owners) == 1 and sequence + length <= owners[0]['end'], 'source packet crossed a work boundary'
        work_packets[owners[0]['id']].append((sequence, bytes.fromhex(payload['hex']), uid))
    frames = []
    totals = Counter()
    app_finish = max(row['time_ns'] for row in frontend['events'] if row['kind'] == 'application_stage_complete')
    cap = result['packet_capture']
    physical_by_uid = defaultdict(list)
    for event in cap['events']:
        physical_by_uid[event[4]].append(event)
    for w in transport['work']:
        packets = sorted(work_packets[w['id']])
        raw = b''.join(p[1] for p in packets)
        assert len(raw) == w['bytes'] == w['end'] - w['begin']
        assert [p[0] for p in packets] == list(range(w['begin'], w['end'], mtu))
        magic, verb, sequence, address, key, length = struct.unpack('!IIQQII', raw[:32])
        assert magic == 0x4d525431 and length + 32 == len(raw) and verb in (1, 2, 3)
        stream = w['stream']
        assert (stream % 2 == 1) == (verb == 3)
        op = native_ops[stream // 2, sequence]
        operation = frontend['native_tasks'][str(op['id'])]['modern_operation']
        description = logical[operation]
        category = 'atomic_reply' if verb == 3 else 'atomic_request' if verb == 2 else 'token_data' if description['kind'] == 'token_data' else 'put_control'
        assert length == (8 if verb in (2, 3) else description['operand_bytes'])
        modeled_packets = (len(raw) + mtu - 1) // mtu
        body_only_packets = (length + mtu - 1) // mtu
        assert modeled_packets == len(packets)
        tx = [e for _, _, uid in packets for e in physical_by_uid[uid] if e[3] == 0]
        acks = [e for e in cap['events'] if e[3] == 0 and e[6] == 252 and e[5] == stream and w['begin'] < e[7] <= w['end']]
        ack_uids = sorted({e[4] for e in acks})
        assert len(ack_uids) == len(packets), 'expected ACK-each-packet with no loss'
        frame = dict(work=w['id'], stream=stream, operation=operation, category=category,
                     verb=verb, sequence=sequence, body_bytes=length, framed_bytes=len(raw),
                     source_packets=len(packets), body_only_packet_count=body_only_packets,
                     source_packet_uids=[p[2] for p in packets], ACK_packet_uids=ack_uids,
                     published_ns=w['published_ns'], accepted_ns=w['accepted_ns'], acked_ns=w['acked_ns'],
                     physical_data_TX=len(tx), physical_data_TX_bytes=sum(e[8] for e in tx),
                     physical_ACK_TX=len(acks), physical_ACK_TX_bytes=sum(e[8] for e in acks),
                     native_operation_completed_ns=op['completed_ns'])
        frames.append(frame)
        for k, value in dict(frames=1, body_bytes=length, framed_bytes=len(raw), function_header_bytes=32,
                             source_packets=len(packets), body_only_packet_count=body_only_packets).items():
            totals[k] += value
    category_rows = {}
    for category in sorted({f['category'] for f in frames}):
        chosen = [f for f in frames if f['category'] == category]
        category_rows[category] = {k: sum(f[k] for f in chosen) for k in
            ('body_bytes', 'framed_bytes', 'source_packets', 'physical_data_TX', 'physical_data_TX_bytes', 'physical_ACK_TX', 'physical_ACK_TX_bytes')}
        category_rows[category]['frames'] = len(chosen)
    reply = [f for f in frames if f['category'] == 'atomic_reply']
    stats = dict(**totals, categories=category_rows, actual_payload_MTU=mtu,
                 application_stage_complete_ns=app_finish, physical_drained_ns=result['finish_ns'],
                 all_operations_complete_ns=max(o['completed_ns'] for o in transactions['operations']),
                 function_header_fraction_of_framed_bytes=totals['function_header_bytes']/totals['framed_bytes'],
                 atomic_origin_data_and_ACK_packets=2*sum(f['source_packets'] for f in frames if f['verb'] in (2, 3)),
                 all_origin_data_and_ACK_packets=2*totals['source_packets'],
                 reply_ACK_after_application=sum(f['acked_ns'] > app_finish for f in reply),
                 latest_reply_ACK_ns=max(f['acked_ns'] for f in reply),
                 reply_ACK_after_own_operation=sum(f['acked_ns'] > f['native_operation_completed_ns'] for f in reply),
                 packets_crossing_work_boundaries=0, framing_only_source_packet_reduction=totals['source_packets']-totals['body_only_packet_count'],
                 physical_gpu_count=result['gpu_count'], active_network_GPU_ids=sorted({r[2] for r in source_rows} | {r[3] for r in source_rows}),
                 CNP_physical_TX=sum(e[3] == 0 and e[6] == 255 for e in cap['events']))
    return dict(summary=stats, frames=frames)


def analyze(root, output):
    record = load(root / 'inputs.json')
    for row in record['files'] + record['sources']:
        assert sha(root / row['path']) == row['sha256']
    data = dict(schema='transport-semantics-audit/1', conditions={name: analyze_condition(root, name) for name in CONDITIONS},
                new_networks=0, RoCE_performance_predicted=False, new_research_benefit_established=False)
    save(output / 'analysis.json', data)
    save(output / 'summary.json', dict(schema='transport-semantics-audit-summary/1',
        conditions={name:row['summary'] for name,row in data['conditions'].items()},
        detailed_frames='Archived analysis/analysis.json; not included in this summary',
        new_networks=0,RoCE_performance_predicted=False,new_research_benefit_established=False))
    lines = ['# 功能事务载体对网络结论的影响', '', '本轮仅分析已封存的两个条件，不重新执行网络。MRT1不是RoCE，以下数字不作为真实网卡收益。', '']
    lines += ['| 条件 | 功能帧 | 源数据包 | 功能帧头B | 仅删帧头少几包 | Reply确认晚于应用完成 | 应用/全部操作/排空ns |', '|---|---:|---:|---:|---:|---:|---|']
    for name, row in data['conditions'].items():
        s = row['summary']
        lines.append(f"| {name} | {s['frames']} | {s['source_packets']} | {s['function_header_bytes']} | {s['framing_only_source_packet_reduction']} | {s['reply_ACK_after_application']} | {s['application_stage_complete_ns']} / {s['all_operations_complete_ns']} / {s['physical_drained_ns']} |")
    lines += ['', '| 条件 | 原子请求/Reply帧 | 原子相关源数据包与ACK | 全部源数据包与ACK | 功能帧头占帧字节 | 全操作完成后的Reply确认尾部ns |', '|---|---:|---:|---:|---:|---:|']
    for name, row in data['conditions'].items():
        s=row['summary'];c=s['categories']
        lines.append(f"| {name} | {c['atomic_request']['frames']} / {c['atomic_reply']['frames']} | {s['atomic_origin_data_and_ACK_packets']} | {s['all_origin_data_and_ACK_packets']} | {100*s['function_header_fraction_of_framed_bytes']:.3f}% | {s['latest_reply_ACK_ns']-s['all_operations_complete_ns']} |")
    lines += ['', '逐帧/源包边界、实际ACK和类别字节见analysis.json。', '', (Path(__file__).parent / 'interpretation.md').read_text()]
    markdown = '\n'.join(lines).rstrip() + '\n'
    (output / 'report.md').write_text(markdown)
    blocks = []
    for part in markdown.split('\n\n'):
        if part.startswith('|'):
            rows = part.splitlines()
            table = '<div class="table"><table>'
            for number, row in enumerate(rows):
                if number == 1:
                    continue
                cell = 'th' if number == 0 else 'td'
                table += '<tr>' + ''.join(f'<{cell}>{html.escape(s.strip())}</{cell}>' for s in row.strip('|').split('|')) + '</tr>'
            blocks.append(table + '</table></div>')
        elif part.startswith('## '):
            blocks.append('<h2>' + html.escape(part[3:]) + '</h2>')
        elif part.startswith('# '):
            blocks.append('<h1>' + html.escape(part[2:]) + '</h1>')
        else:
            blocks.append('<p>' + html.escape(part) + '</p>')
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>事务载体边界</title><style>body{max-width:1050px;margin:40px auto;padding:20px;font:17px/1.8 system-ui;color:#203044}p{white-space:pre-wrap;overflow-wrap:anywhere}.table{overflow:auto}table{border-collapse:collapse;font-size:14px}td,th{border:1px solid #bac8d4;padding:10px}th{background:#edf2f6}</style><main>' + '\n'.join(blocks) + '</main></html>\n'
    (output / 'report.html').write_text(page)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('prepare', 'analyze'))
    p.add_argument('--repository', type=Path)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    if a.action == 'prepare':
        prepare(a.repository.resolve(), a.run.resolve())
    else:
        analyze(a.run.resolve(), a.output.resolve())
