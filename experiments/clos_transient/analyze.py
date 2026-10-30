"""Audit originals, packet counters and shared exits; report both contrasts."""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import html
import json
import os
from pathlib import Path

from moe_ub_cosim.scaleout_arrivals import reconstruct
from moe_ub_cosim.scaleout_data_loss import audit as loss_audit
from moe_ub_cosim.service_terminal import service_terminal


def load(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def need(value, reason):
    if not value:
        raise ValueError(reason)


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def csv_file(path, rows):
    need(bool(rows), 'empty CSV')
    with path.open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def original_matrix(directory, config, waves):
    """Independent original-JSON calculation; never calls Replay/make_wave."""
    manifest = load(directory / config['route_replay']['manifest']['path'])
    originals, hashes = {}, set()
    for source in manifest['files']:
        path = directory / source['file']['path']
        digest = sha(path)
        need(digest == source['file']['sha256'] and digest not in hashes, 'changed or cloned original')
        hashes.add(digest)
        originals[source['file_id']] = load(path)
    need(len(originals) == 15, 'wrong original count')
    messages = logical = 0
    for wave in waves:
        group = wave['rank_ids']
        need(len(group) == 64, 'unexpected EP size')
        expected = Counter()
        for request in config['route_replay']['requests']:
            src = request['rank_id']
            if src not in group:
                continue
            raw = originals[request['file_id']]
            entry = request['first_decode_entry'] + wave['decode_step']
            for layer, lid in enumerate(config['route_replay']['layer_ids']):
                for expert in raw[entry][str(lid)][0]:
                    dst = group[expert // 4]
                    if dst != src:
                        expected[layer, 'dispatch', src, dst] += 14336
                        expected[layer, 'combine', dst, src] += 14336
        actual = {(m['layer'], m['phase'], m['src'], m['dst']): m['bytes'] for m in wave['messages']}
        need(actual == dict(expected) and len(actual) == len(wave['messages']), 'raw contribution matrix differs')
        messages += len(expected)
        logical += sum(expected.values())
    return dict(originals_checked=len(hashes), messages=messages, logical_bytes=logical)


def analyze_case(directory, prepared, output, case):
    result, front = load(directory / 'result.json'), load(directory / 'frontend.json')
    timing = service_terminal(result, front, require_complete=True)
    config = load(directory / 'input.json')
    need(result['gpu_count'] == 128 and config['phase_completion'] == dict(mode='ep_transport_flush/1'),
         'wrong endpoint count or completion semantics')
    waves = [load(p) for p in sorted((directory / 'graphs').glob('*/wave.json'))]
    need({w['graph_id'] for w in waves} == {'g0.w0', 'g0.w1', 'g1.w0', 'g1.w1'}, 'missing graph')
    for wave in waves:
        need(wave == load(prepared / 'planned' / (wave['graph_id'] + '.json')), 'actual graph changed')
    independent = original_matrix(directory, config, waves)
    summary, rows, endpoints, _, _ = reconstruct(result, waves, 'g0.w0', 0, 'dispatch')
    need(summary['generated_graph_logical_bytes'] == independent['logical_bytes'] == summary['accepted_bytes'],
         'original and receiver bytes differ')
    csv_file(output / (case + '-messages.csv'), rows)
    csv_file(output / (case + '-endpoints.csv'), endpoints)
    curves, slices = {}, {}
    for group in (0, 1):
        for name, step, layer, phase in (('first-dispatch', 0, 0, 'dispatch'), ('last-combine', 1, 1, 'combine')):
            report, _, _, _, curve = reconstruct(result, waves, f'g{group}.w{step}', layer, phase)
            key = f'g{group}-{name}'
            curves[key], slices[key] = curve, report['slice']
            csv_file(output / (case + '-' + key + '.csv'), curve)
    native = result['service_transport']['native']
    streams = {s['id']: s for s in native['streams']}
    groups = config['parallel']['groups']
    stream_group = {sid: next(gi for gi, group in enumerate(groups) if s['src'] in group)
                    for sid, s in streams.items()}
    need(all(s['dst'] in groups[stream_group[sid]] for sid, s in streams.items()), 'cross-group application stream')
    source_packets, source_bytes, dropped = Counter(), Counter(), Counter()
    shared = defaultdict(Counter)
    marked_packets = Counter()
    for values in result['packet_capture']['events']:
        p = dict(zip(result['packet_capture']['columns'], values))
        sid = p['message_id']
        if sid not in streams or p['ip_protocol'] != 17:
            continue
        group = stream_group[sid]
        if p['event'] == 0:
            if p['node'] == streams[sid]['src']:
                source_packets[sid] += 1
                source_bytes[sid] += p['data_payload_bytes']
            if p['node'] >= 128:
                shared[p['node'], p['port']][group] += p['model_bytes']
        if p['event'] == 2:
            dropped[group] += 1
        if p['event'] == 1 and p['node'] == streams[sid]['dst'] and p['ecn'] == 3:
            marked_packets[group] += 1
    for sid, stream in streams.items():
        need(source_packets[sid] == stream['source_data_packets'] and
             source_bytes[sid] == stream['source_payload_bytes'], 'independent source counters differ')
    need(not dropped[1], 'healthy group directly lost data')
    common = [dict(node=n, port=p, group0_data_model_bytes=c[0], group1_data_model_bytes=c[1])
              for (n, p), c in sorted(shared.items()) if c[0] and c[1]]
    need(bool(common), 'two groups did not use common directed switch exits')
    csv_file(output / (case + '-shared-exits.csv'), common)
    by_group = []
    for gi, group in enumerate(groups):
        chosen = [r for r in rows if r['group_index'] == gi]
        history = [h for h in front['history'] if h['group_index'] == gi]
        by_group.append(dict(group=gi, rank_count=len(group),
            finished_ns=max(h['finished_ns'] for h in history),
            messages=len(chosen), logical_bytes=sum(r['logical_bytes'] for r in chosen),
            source_payload_bytes=sum(r['tx_payload_bytes'] for r in chosen),
            repeated_payload_bytes=sum(r['tx_duplicate_payload_bytes'] for r in chosen),
            accepted_bytes=sum(r['accepted_bytes'] for r in chosen), direct_drop_events=dropped[gi],
            destination_ecn_ce_packets=marked_packets[gi],
            graphs=[dict(graph_id=h['graph_id'], start_ns=h['start_ns'], finished_ns=h['finished_ns'],
                         elapsed_ns=h['finished_ns']-h['start_ns']) for h in history]))
    protocol = load(directory / 'rdma.json')
    if case == 'local':
        expected = dict(kind='periodic_destination_data/1', numerator=1, denominator=1,
            offset=0, start_ns=0, end_ns=100000, nodes=[load(prepared / 'preparation.json')['hotspot_rank']])
        need(protocol.get('data_loss') == expected, 'fault configuration differs')
        need(loss_audit(result).get('dropped_packets', 0) > 0, 'fault did not hit')
    else:
        need('data_loss' not in protocol and not result['fault_drops'], 'unexpected normal loss')
    drops = result['switch_drop_capture']
    need(drops['complete'] and not drops['omitted_events'], 'truncated natural drop observations')
    recovery = native['recovery']
    event_counts = Counter(dict(zip(recovery['columns'], row))['event'] for row in recovery['events'])
    summary = dict(timing=timing, groups=by_group, slices=slices, independent_verification=independent,
        actual_source_data_packets=sum(source_packets.values()),
        actual_source_payload_bytes=sum(source_bytes.values()),
        packet_events=len(result['packet_capture']['events']),
        shared_directed_exit_count=len(common), shared_directed_exits=common,
        switch_reason_counts=drops['all_switch_reason_counts'],
        pfc_pause=sum(p['pfc_pause'] for p in result['ports']),
        max_queue_at_hook_bytes=max(p['max_queue_at_hook_bytes'] for p in result['ports']),
        queue_area_byte_ns=sum(p.get('queue_metrics', {}).get('area_byte_ns', 0) for p in result['ports']),
        data_loss=loss_audit(result),
        recovery_event_counts={name: event_counts[code] for name, code in recovery['event_codes'].items()},
        recovery_count_scope='timer retransmit events are recovery requests, not actual source packet count',
        result_sha256=sha(directory / 'result.json'), frontend_sha256=sha(directory / 'frontend.json'))
    return summary, curves


def plot_curves(output, curves):
    os.environ.setdefault('MPLCONFIGDIR', str(output / '.matplotlib'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 2, figsize=(12, 7), constrained_layout=True)
    for gi in (0, 1):
        for col, phase in enumerate(('first-dispatch', 'last-combine')):
            ax = axes[gi, col]
            for case, color in (('normal', '#2463b7'), ('local', '#d36b22')):
                curve = curves[case][f'g{gi}-{phase}']
                for field, style, label in (('accepted_bytes', '-', 'accepted'),
                                            ('source_confirmed_bytes', '--', 'source confirmed')):
                    ax.step([r['time_ns']/1000 for r in curve], [r[field]/2**20 for r in curve],
                            where='post', color=color, linestyle=style, label=f'{case} {label}')
            ax.set(title=f'Group {gi}: {phase}', xlabel='Simulated time (us)', ylabel='Cumulative payload (MiB)')
            ax.grid(alpha=.2)
            ax.legend(fontsize=8)
    fig.savefig(output / 'arrival-curves.png', dpi=170)
    fig.savefig(output / 'arrival-curves.svg')
    plt.close(fig)


def analyze(root, output):
    output.mkdir(parents=True, exist_ok=False)
    prepared = root / 'prepared'
    preparation = load(prepared / 'preparation.json')
    for name, digest in preparation['input_hashes'].items():
        need(sha(prepared / name) == digest, 'frozen input hash changed: ' + name)
    cases, curves = {}, {}
    for case in ('normal', 'local'):
        need(load(root / 'attempts' / (case + '.json'))['status'] == 'verified', 'unverified actual run')
        cases[case], curves[case] = analyze_case(root / case, prepared, output, case)
    clean, fault = cases['normal'], cases['local']
    delta = dict(application_extra_ns=fault['timing']['application_finished_ns']-clean['timing']['application_finished_ns'],
        extra_source_payload_bytes=fault['actual_source_payload_bytes']-clean['actual_source_payload_bytes'],
        group_extra_ns=[fault['groups'][i]['finished_ns']-clean['groups'][i]['finished_ns'] for i in (0, 1)])
    delta['application_extra_percent'] = 100*delta['application_extra_ns']/clean['timing']['application_finished_ns']
    delta['healthy_group_extra_percent'] = 100*delta['group_extra_ns'][1]/clean['groups'][1]['finished_ns']
    graph_deltas = []
    for gi in (0, 1):
        for normal, local in zip(clean['groups'][gi]['graphs'], fault['groups'][gi]['graphs']):
            need(normal['graph_id'] == local['graph_id'], 'mismatched graph contrast')
            graph_deltas.append(dict(graph_id=normal['graph_id'],
                start_extra_ns=local['start_ns']-normal['start_ns'],
                execution_extra_ns=local['elapsed_ns']-normal['elapsed_ns'],
                finish_extra_ns=local['finished_ns']-normal['finished_ns']))
    delta['graphs'] = graph_deltas
    report = dict(schema_version='moe-clos-transient-analysis/1', passed=True,
        gpu_endpoints=128, ep_sizes=[64, 64], independent_requests=15,
        preparation_sha256=sha(prepared / 'preparation.json'), cases=cases, contrast=delta,
        measured_novel_algorithm_gain=False, complete_DeepEP=False, hardware_calibrated=False,
        interpretation='fault costs are measured; neither the loss cost nor healthy-group delay is proven eliminable gain')
    save(output / 'analysis.json', report)
    plot_curves(output, curves)
    rows = []
    for name, c in cases.items():
        values = [name, *[f'{x/1000:.3f}' for x in (c['timing']['application_finished_ns'],
            c['groups'][0]['finished_ns'], c['groups'][1]['finished_ns'], c['timing']['network_drained_ns'])],
            str(sum(g['repeated_payload_bytes'] for g in c['groups'])), str(c['data_loss'].get('dropped_packets', 0))]
        rows.append('<tr>' + ''.join('<td>' + html.escape(v) + '</td>' for v in values) + '</tr>')
    heads = ['条件', '任务完成 µs', '故障组完成 µs', '健康组完成 µs', '网络排空 µs', '重复载荷 B', '注入丢包数']
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>128卡Clos局部故障</title>'
    page += '<style>body{max-width:1150px;margin:40px auto;padding:0 20px;font:17px/1.8 sans-serif;color:#18263b}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd5df;padding:9px}th{background:#edf3f8}img{max-width:100%}a{color:#2463b7}</style>'
    page += '<h1>128卡Clos：局部丢包对两个MoE任务组的影响</h1><p>15份不同R1-AWQ/MMLU专家选择，两个EP64组，各8/7个请求。单层每rank4专家，两层两步Decode。所有组共用一次网络执行；H100只作标签，计算、到达及部署为声明假设。</p>'
    page += '<table><tr>' + ''.join('<th>' + h + '</th>' for h in heads) + '</tr>' + ''.join(rows) + '</table>'
    page += f'<p>故障条件：仅组0的GPU {preparation["hotspot_rank"]} 在前100微秒丢弃目的数据，控制包不丢；不是全网丢包或物理上行失效。SR与参考DCQCN在两个条件下完全相同。</p>'
    page += f'<p>故障使整个有限任务窗口增加 {delta["application_extra_ns"]/1000:.3f} 微秒（{delta["application_extra_percent"]:+.2f}%）；未直接丢包的组1变化 {delta["group_extra_ns"][1]/1000:+.3f} 微秒（{delta["healthy_group_extra_percent"]:+.2f}%）。负值表示更早完成。</p>'
    page += '<h2>累计有效到达与源确认</h2><p>实线表示协议已连续接受的有效字节，虚线表示源端已确认字节。蓝色为正常、橙色为局部故障；上排故障组、下排健康组，左侧首Dispatch、右侧末Combine。曲线来自同一次执行中的完整包记录。</p><img src="arrival-curves.png" alt="Cumulative accepted and source-confirmed byte curves">'
    page += '<h2>结果能说明什么</h2><p>实际数据包确认了两组共用交换机有向出口；还需区分排队、源端节奏、阶段屏障及下一步提交时间。共用路径和时间变化不直接证明可消除收益。本轮采用必要传输完成门限，但未实现完整DeepEP控制信号、按目的rank去重或真实GPU可见性；不能当作完整现代库性能。</p>'
    page += '<p><a href="analysis.json">完整指标与核验</a> · <a href="arrival-curves.svg">矢量图</a> · <a href="normal-messages.csv">正常消息统计</a> · <a href="local-messages.csv">故障消息统计</a> · <a href="normal-endpoints.csv">正常端点统计</a> · <a href="local-endpoints.csv">故障端点统计</a></p></html>'
    (output / 'report.html').write_text(page)
    print(json.dumps(dict(passed=True, contrast=delta, report=str(output / 'report.html'))))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    analyze(args.root.resolve(), args.output.resolve())
