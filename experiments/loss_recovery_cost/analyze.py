"""Offline independent packet/timer accounting and fixed-workload comparisons."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import html
import json
from pathlib import Path
import resource


def read(p): return json.loads(Path(p).read_text())
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p, v): Path(p).write_text(json.dumps(v, ensure_ascii=False, indent=2)+'\n')


def packet_check(result, target):
    capture = result['packet_capture']
    assert capture['complete'] and not capture['omitted_events']
    assert len(capture['events']) == len(capture['wire_headers']['event_header_indices'])
    flows = {m['id']: m for m in result['messages']}
    assert len(flows) == len(result['messages'])
    selected_sources = set(capture['selection']['nodes'])
    emitted = {}; observations = []
    for row, header_idx in zip(capture['events'], capture['wire_headers']['event_header_indices']):
        e = dict(zip(capture['columns'], row))
        h = dict(zip(capture['wire_headers']['columns'], capture['wire_headers']['headers'][header_idx]))
        assert h['packet_uid'] == e['packet_uid'] and h['ip_protocol'] == e['ip_protocol']
        if e['message_id'] == target:
            observations.append(e)
        if e['event'] == 0 and e['ip_protocol'] == 17 and e['message_id'] in flows:
            f = flows[e['message_id']]
            if e['node'] != f['src']: continue
            assert h['ports_present'] and h['source_port'] == f['port'] and h['destination_port'] == f['port']
            assert 0 <= e['sequence'] < f['bytes'] and e['data_payload_bytes'] > 0
            assert e['sequence']+e['data_payload_bytes'] <= f['bytes']
            emitted.setdefault(f['id'], []).append(e)
    checked = 0
    for mid, f in flows.items():
        if f['src'] not in selected_sources: continue
        events = emitted[mid]
        assert sum(e['data_payload_bytes'] for e in events) == f['source_data_payload_bytes']
        assert len(events) == f['source_data_packets']
        intervals = sorted({(e['sequence'], e['sequence']+e['data_payload_bytes']) for e in events})
        end = 0
        for start, stop in intervals:
            assert start == end, 'missing or overlapping source byte coverage'
            end = stop
        assert end == f['bytes'] == f['source_unique_data_payload_bytes']
        checked += 1
    f = flows[target]; events = emitted[target]
    counts = Counter((e['sequence'], e['data_payload_bytes']) for e in events)
    repeated = sum((count-1)*size for (seq, size), count in counts.items())
    assert repeated == f['source_data_payload_bytes']-f['bytes']
    target_tx = [[e['time_ns'], e['sequence'], e['data_payload_bytes']] for e in events]
    target_feedback = [[e['time_ns'], e['ip_protocol'], e['sequence']] for e in observations
                       if e['event'] == 1 and e['node'] == f['src'] and e['ip_protocol'] in (252, 253)]
    assert min(t for t, seq, size in target_tx) == f['first_tx_ns']
    assert any(t == f['ack_ns'] and seq == f['bytes'] for t, proto, seq in target_feedback)
    return dict(source_messages_checked=checked, target_source_tx=target_tx,
                target_received_feedback=target_feedback, target_repeated_payload_bytes=repeated,
                target_drop_events=[e for e in observations if e['event'] == 2])


def timers_check(result):
    if 'recovery' not in result: return dict(available=False, timeout_decisions=0)
    recovery = result['recovery']; c = recovery['config']
    assert recovery['complete'] and not recovery['omitted_events'] and not recovery['failed']
    previous = {}; retries = Counter(); complete = set(); clock = -1
    flows = {f['id']: f for f in result['messages']}
    for row in recovery['events']:
        t, event, source, mid, ack, high, count, rto, deadline = row
        assert clock <= t <= result['finish_ns'] and mid not in complete
        assert source == flows[mid]['src'] and 0 <= ack <= high <= flows[mid]['bytes']
        old = previous.get(mid)
        if old:
            assert ack >= old[0] and high >= old[1]
        if event == 0:
            assert (old is None or old[-1] == -1) and deadline == t+rto and high > ack
            assert rto == c['initial_rto_ns']
        elif event in (1, 3):
            assert old and ack > old[0] and rto == c['initial_rto_ns'] and count == 0
            assert deadline == (t+rto if high > ack else -1)
            if event == 3:
                assert ack == flows[mid]['bytes'] and t == flows[mid]['ack_ns']
                complete.add(mid)
        elif event == 2:
            assert old and t == old[-1] and ack == old[0] and count == old[2]+1
            assert rto == min(c['max_rto_ns'], 2*old[3]) and deadline == t+rto
            retries[mid] += 1
        elif event == 6:
            assert old and (ack, count, rto, deadline) == (old[0], old[2], old[3], old[4])
        else: raise AssertionError('unexpected event/failed run')
        previous[mid] = ack, high, count, rto, deadline
        clock = t
    assert complete == set(flows)
    for state in recovery['states']:
        assert state['state'] == 'complete' and state['deadline_ns'] == -1
        assert state['timeout_retransmissions'] == retries[state['id']]
    return dict(available=True, timeout_decisions=sum(retries.values()), source_states=len(complete))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (2*2**30, 2*2**30))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    assert not a.output.exists()
    root = a.input; exp = read(root/'experiment.json'); target = exp['target']['id']
    assert exp['state'] == 'completed' and len(exp['cases']) == 13
    records = []; evidence = {}; raw = {}; graph_checks = 0
    for case in exp['cases']:
        name = case['name']; directory = root/name; r = read(directory/'result.json')
        assert sha(directory/'result.json') == case['result_sha256']
        assert r['passed'] and r['drained'] and r['native_network_executed']
        history = read(directory/'frontend.json')['history']
        assert len(history) == 4 and all(h['finished_ns'] is not None for h in history)
        finish = max(h['finished_ns'] for h in history)
        assert finish <= r['finish_ns'] and r['service']['service_finished']
        graphs = {p.parent.name: sha(p) for p in (directory/'graphs').glob('*/wave.json')}
        assert graphs == case['graphs'] == exp['cases'][0]['graphs']
        graph_checks += len(graphs)
        for f in r['messages']:
            assert f['ack_callbacks'] == 1 and not f['sender_qp_present']
            assert f['receiver_contiguous_bytes'] == f['source_unique_data_payload_bytes'] == f['bytes']
            assert f['receive_ready_ns'] <= f['ack_ns'] == f['sender_resource_released_ns']
        f = next(f for f in r['messages'] if f['id'] == target)
        packets = packet_check(r, target); timers = timers_check(r)
        if case['fault'] != 'clean':
            assert r['fault_injection']['drop_counts'] == [1] and len(packets['target_drop_events']) == 1
            rule = read(root/(name+'.json'))['faults'][0]
            event = packets['target_drop_events'][0]
            assert event['sequence'] == rule['sequence'] and event['node'] == rule['node']
            assert event['ip_protocol'] == (252 if case['fault'] == 'ack' else 17)
        row = dict(name=name, rto_ns=case['rto_ns'], fault=case['fault'], frontend_complete_ns=finish,
                   network_finish_ns=r['finish_ns'], drain_after_frontend_ns=r['finish_ns']-finish,
                   logical_payload_bytes=sum(m['bytes'] for m in r['messages']),
                   actual_source_payload_bytes=sum(m['source_data_payload_bytes'] for m in r['messages']),
                   gpu_port_model_tx_bytes=sum(p['model_tx_bytes'] for p in r['ports'] if p['node'] < r['gpu_count']),
                   all_hop_model_tx_bytes=sum(p['model_tx_bytes'] for p in r['ports']),
                   timeout_decisions=timers['timeout_decisions'], messages=len(r['messages']),
                   target_receive_ready_ns=f['receive_ready_ns'], target_ack_ns=f['ack_ns'],
                   target_payload_bytes=f['source_data_payload_bytes'], target_logical_bytes=f['bytes'])
        row['repeated_source_payload_bytes'] = row['actual_source_payload_bytes']-row['logical_payload_bytes']
        row['source_payload_efficiency'] = row['logical_payload_bytes']/row['actual_source_payload_bytes']
        records.append(row); evidence[name] = dict(packets=packets, timers=timers,
            target_timer_events=[e for e in r.get('recovery', {}).get('events', []) if e[3] == target])
        raw[name] = r
    assert {r['logical_payload_bytes'] for r in records} == {389120}
    assert {r['messages'] for r in records} == {92}
    reference = {f['id']: f for f in raw['native-clean']['messages']}
    for r in raw.values():
        assert {f['id'] for f in r['messages']} == set(reference)
        for f in r['messages']:
            old = reference[f['id']]
            assert all(f[k] == old[k] for k in ('src', 'dst', 'bytes', 'plane', 'port'))
            assert [(x['node'], x['port']) for x in f['first_packet_path']] == [(x['node'], x['port']) for x in old['first_packet_path']]
    # A no-loss control must preserve all shared execution fields, not just latency.
    for name in ('r40-clean', 'r20-clean'):
        for key in ('messages', 'ports', 'service', 'queue_events', 'packet_capture'):
            assert raw[name][key] == raw['native-clean'][key], (name, key)
    for name, ev in evidence.items():
        feedback = ev['packets']['target_received_feedback']
        emitted = ev['packets']['target_source_tx']
        for t, event, source, mid, ack, high, count, rto, deadline in ev['target_timer_events']:
            if event in (1, 3): assert any(ft == t and seq == ack for ft, proto, seq in feedback)
            if event == 6: assert any(ft == t and seq <= ack for ft, proto, seq in feedback)
            if event == 0: assert any(tt == t for tt, seq, size in emitted)
        ev['target_first_feedback_after_first_tx_ns'] = min(t for t, proto, seq in feedback)-min(t for t, seq, size in emitted)
    by_name = {r['name']: r for r in records}; clean = by_name['r40-clean']['frontend_complete_ns']
    comparisons = []
    for row in records:
        own_clean = by_name[('native' if row['rto_ns'] is None else 'r'+str(row['rto_ns']//1000))+'-clean']
        row['fault_cost_vs_same_rto_clean_ns'] = row['frontend_complete_ns']-own_clean['frontend_complete_ns']
        row['signed_gap_vs_r40_clean_ns'] = row['frontend_complete_ns']-clean
        if row['rto_ns'] in (12000, 20000):
            baseline = by_name['r40-'+row['fault']]
            comparisons.append(dict(candidate=row['name'], baseline=baseline['name'],
                frontend_saved_ns=baseline['frontend_complete_ns']-row['frontend_complete_ns'],
                frontend_saved_fraction=(baseline['frontend_complete_ns']-row['frontend_complete_ns'])/baseline['frontend_complete_ns'],
                source_payload_change_bytes=row['actual_source_payload_bytes']-baseline['actual_source_payload_bytes'],
                network_finish_saved_ns=baseline['network_finish_ns']-row['network_finish_ns']))
    rejected = []
    changed = deepcopy(raw['r40-tail']); next(f for f in changed['messages'] if f['id'] == target)['source_data_payload_bytes'] += 1
    try: packet_check(changed, target)
    except AssertionError: rejected.append('inflated_source_payload')
    changed = deepcopy(raw['r40-tail'])
    next(e for e in changed['recovery']['events'] if e[3] == target and e[1] == 2)[0] -= 1
    try: timers_check(changed)
    except AssertionError: rejected.append('premature_timeout')
    changed = deepcopy(raw['r40-tail'])
    next(row for row in changed['packet_capture']['events'] if row[5] == target and row[3] == 0 and row[6] == 17)[-1] += 1
    try: packet_check(changed, target)
    except AssertionError: rejected.append('corrupted_packet_payload')
    assert len(rejected) == 3
    summary = dict(schema='moe-loss-recovery-cost/1', cases=records, comparisons=comparisons,
        scope=dict(fixed_tasks=True, graph_hashes_checked=graph_checks, independent_requests=0,
                   synthetic_routes=True, modern_GIN=False, gpu_execution=False, hardware_calibrated=False,
                   random_loss_probability_estimated=False, parameter_tuning_is_new_algorithm=False,
                   loss_free_gap_is_universal_lower_bound=False),
        verification=dict(packet_source_message_checks=sum(v['packets']['source_messages_checked'] for v in evidence.values()),
                          rejected_tampering=rejected, timer_source='source ACK progress, actual event deadlines; no receiver truth'),
        evidence=evidence)
    a.output.mkdir()
    save(a.output/'summary.json', summary)
    lines = ['# 固定MoE任务的丢包恢复成本', '',
        '固定8GPU、两个EP4副本、每rank batch4、100G Clos、每源3消息/8192B FIFO；同一任务与路由，声明计算成本。每条件92消息/389,120B，13条件任务图与首尝试路径一致。', '',
        '| 条件 | 业务完成 ns | 网络排空 ns | 重复payload B | 超时决策 | 目标ACK ns |',
        '|---|---:|---:|---:|---:|---:|']
    for r in records:
        lines.append(f"| {r['name']} | {r['frontend_complete_ns']} | {r['network_finish_ns']} | {r['repeated_source_payload_bytes']} | {r['timeout_decisions']} | {r['target_ack_ns']} |")
    lines += ['', '## 机制与优化空间', '',
        '中间包丢失由后继包触发真实NACK；本例重发从缺失偏移开始的后缀。尾包没有后继数据；最终ACK丢失时数据已经到达，二者由源ACK进展计时器恢复，完成仍来自实际反向ACK。', '',
        f"40μs条件：中间包丢失增加{by_name['r40-middle']['repeated_source_payload_bytes']:,}B源payload，业务完成相对无故障改变{by_name['r40-middle']['signed_gap_vs_r40_clean_ns']:,}ns。尾包仅多{by_name['r40-tail']['repeated_source_payload_bytes']}B，却使业务晚{by_name['r40-tail']['signed_gap_vs_r40_clean_ns']:,}ns；最终ACK同样多{by_name['r40-ack']['repeated_source_payload_bytes']}B，业务晚{by_name['r40-ack']['signed_gap_vs_r40_clean_ns']:,}ns。少量字节不代表小损失；单流确认变慢也不等于整体等量变慢。", '']
    for fault in ('tail', 'ack'):
        c = next(c for c in comparisons if c['candidate'] == 'r20-'+fault)
        lines.append(f"20μs相对40μs、同一{fault}故障：业务早{c['frontend_saved_ns']:,}ns（{c['frontend_saved_fraction']:.2%}），源payload变化{c['source_payload_change_bytes']}B；相对40μs无故障参考仍有{by_name['r20-'+fault]['signed_gap_vs_r40_clean_ns']}ns差距。")
    short = by_name['r12-clean']
    lines += ['', f"12μs无故障时也有{short['timeout_decisions']}次超时决策，重复payload为{short['repeated_source_payload_bytes']:,}B（比正常增加{short['repeated_source_payload_bytes']/short['logical_payload_bytes']:.2%}）；业务晚{short['signed_gap_vs_r40_clean_ns']}ns，网络排空晚{short['network_finish_ns']-by_name['r40-clean']['network_finish_ns']:,}ns。目标首个实际反馈往返为{evidence['r12-clean']['target_first_feedback_after_first_tx_ns']}ns，首次超时发生在其到达以前；短超时会把健康传输误判成丢失。", '',
        '12μs在故障条件的局部检测更快，整体仍略慢于20μs；不能将超时越短当成越好。', '',
        '20μs消除了本例40μs故障参考差距的大部分；这是当前任务/路径/带宽/准入条件下实测的参数空间，不是生产配置建议、新算法或任意方案的理论上界。无故障参考不能当成所有调度策略的普遍下界。没有减少模型工作、放宽完成安全性或增加网络资源。', '',
        '同样目标ACK时刻下，尾数据丢失与ACK丢失仍有不同业务完成时间：前者延迟接收消费者，后者主要延迟发送资源释放。下一步应比较更广的反馈时延与持久QP实现，不默认使用20μs；现代stream/事务当前不支持同样的故障恢复，本结果不等价真实GIN/RoCE。', '',
        '## 核验与复现', '',
        f"独立分析核对{graph_checks}份任务图、{summary['verification']['packet_source_message_checks']}个源消息的全部捕获发包覆盖，逐事件重放源计时器；三种篡改被拒绝。目标ACK进展事件另与实际收包时刻/累计序号匹配。源捕获只覆盖GPU0和4，不把该范围冒充全部节点包记录。", '',
        'summary.json含每条件指标、目标实包/反馈/超时证据与有符号对照。原始命令、输入hash、运行资源和后端/共享库身份保存在实验运行目录。不重放历史失败，不执行GPU、训练、千卡或生产故障率估计。', '']
    (a.output/'results.md').write_text('\n'.join(lines))
    parts = []; table = False
    for line in lines:
        if line.startswith('|'):
            if line.startswith('|---'): continue
            if not table: parts.append('<div class="table"><table>'); table = True
            parts.append('<tr>'+''.join('<td>'+html.escape(x.strip())+'</td>' for x in line.strip('|').split('|'))+'</tr>')
            continue
        if table: parts.append('</table></div>'); table = False
        if line.startswith('# '): parts.append('<h1>'+html.escape(line[2:])+'</h1>')
        elif line.startswith('## '): parts.append('<h2>'+html.escape(line[3:])+'</h2>')
        elif line: parts.append('<p>'+html.escape(line)+'</p>')
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>丢包恢复成本与优化空间</title><style>body{font:17px/1.8 system-ui,sans-serif;max-width:1150px;margin:32px auto;padding:0 24px;color:#182936}.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}td{border:1px solid #ccd5dd;padding:8px}tr:first-child{background:#e8f1f5}h1{font-size:29px}h2{font-size:23px;margin-top:32px}</style>'+''.join(parts)+'<p><a href="summary.json">详细数据与证据</a> · <a href="results.md">Markdown</a></p></html>'
    (a.output/'report.html').write_text(page)
    print(json.dumps(dict(passed=True, cases=len(records), comparisons=len(comparisons), verification=summary['verification'])))


if __name__ == '__main__': main()
