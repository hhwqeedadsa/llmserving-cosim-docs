"""Offline audit and signed comparisons; no simulation or online policy input."""
import argparse
import hashlib
import html
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def packet_components(result, mid):
    """Follow the actual completing data packet, even if other bytes were retried."""
    capture = result['packet_capture']
    assert capture['complete']
    flow = next(m for m in result['messages'] if m['id'] == mid)
    events = [dict(zip(capture['columns'], row)) for row in capture['events']
              if row[5] == mid and row[6] == 17]
    delivered = [e for e in events if e['event'] == 1 and e['node'] == flow['dst']
                 and e['time_ns'] == flow['receive_ready_ns']]
    assert len(delivered) == 1
    packet = delivered[0]
    # UID is lineage, not a global transmission ID. This selected data lineage
    # must contain exactly one transmission per hop within this execution.
    lineage = [e for e in events if e['packet_uid'] == packet['packet_uid']]
    tx = [e for e in lineage if e['event'] == 0]
    rx = [e for e in lineage if e['event'] == 1]
    assert len(tx) == len(rx) and len({e['node'] for e in tx}) == len(tx)
    assert tx[0]['node'] == flow['src'] and rx[-1] == packet
    pending, residence = {}, []
    for e in lineage:
        key = e['node'], e['port']
        if e['event'] == 3:
            assert key not in pending
            pending[key] = e['time_ns']
        elif e['event'] == 4:
            start = pending.pop(key)
            residence.append(dict(node=e['node'], port=e['port'], enqueue_ns=start,
                                  dequeue_ns=e['time_ns'], residence_ns=e['time_ns'] - start))
    assert not pending and all(p['residence_ns'] >= 0 for p in residence)
    assert all(b['time_ns'] >= a['time_ns'] for a, b in zip(tx, rx))
    link = sum(b['time_ns'] - a['time_ns'] for a, b in zip(tx, rx))
    forward = sum(tx[i + 1]['time_ns'] - rx[i]['time_ns'] for i in range(len(tx) - 1))
    queued = sum(e['residence_ns'] for e in residence)
    span = tx[0]['time_ns'] - flow['first_tx_ns']
    other = forward - queued
    assert other >= 0 and span >= 0
    assert flow['receive_ready_ns'] - flow['first_tx_ns'] == span + link + queued + other
    return dict(id=mid, ready_ns=flow['ready_ns'], admission_ns=flow['admission_granted_ns'],
                first_tx_ns=flow['first_tx_ns'], data_ready_ns=flow['receive_ready_ns'], ack_ns=flow['ack_ns'],
                completing_packet_uid=packet['packet_uid'], sequence=packet['sequence'],
                source_emission_span_including_retries_ns=span,
                link_serialization_and_propagation_ns=link, switch_queue_residence_ns=queued,
                other_forwarding_ns=other, ack_tail_ns=flow['ack_ns'] - flow['receive_ready_ns'],
                hops=[dict(node=e['node'], port=e['port']) for e in tx], ports=residence)


def completing_ack(result, mid):
    capture = result['packet_capture']
    flow = next(m for m in result['messages'] if m['id'] == mid)
    events = [dict(zip(capture['columns'], row)) for row in capture['events']
              if row[5] == mid and row[6] == 252]
    arrived = [e for e in events if e['event'] == 1 and e['node'] == flow['src']
               and e['time_ns'] == flow['ack_ns']]
    assert len(arrived) == 1
    lineage = [e for e in events if e['packet_uid'] == arrived[0]['packet_uid']]
    tx = [e for e in lineage if e['event'] == 0]
    rx = [e for e in lineage if e['event'] == 1]
    assert len(tx) == len(rx) and len({e['node'] for e in tx}) == len(tx)
    assert tx[0]['node'] == flow['dst'] and rx[-1] == arrived[0]
    pending, ports = {}, []
    for e in lineage:
        key = e['node'], e['port']
        if e['event'] == 3:
            assert key not in pending
            pending[key] = e['time_ns']
        elif e['event'] == 4:
            start = pending.pop(key)
            ports.append(dict(node=e['node'], port=e['port'], residence_ns=e['time_ns'] - start))
    assert not pending
    link = sum(b['time_ns'] - a['time_ns'] for a, b in zip(tx, rx))
    queued = sum(e['residence_ns'] for e in ports)
    forwarding = sum(tx[i + 1]['time_ns'] - rx[i]['time_ns'] for i in range(len(tx) - 1))
    source_wait = tx[0]['time_ns'] - flow['receive_ready_ns']
    other = forwarding - queued
    assert min(source_wait, other, queued) >= 0
    assert source_wait + link + queued + other == flow['ack_ns'] - flow['receive_ready_ns']
    return dict(id=mid, completing_ack_uid=arrived[0]['packet_uid'],
                receiver_to_ack_source_tx_ns=source_wait, link_ns=link,
                switch_residence_ns=queued, other_forwarding_ns=other, ports=ports)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    source, out = a.input.resolve(), a.output.resolve()
    out.mkdir(exist_ok=False)
    exp = read(source / 'experiment.json')
    assert len(exp['design']['cases']) == len(exp['cases']) == 24
    assert exp['design'] == read(Path(__file__).parent / 'design.json')
    root = Path(__file__).resolve().parents[2]
    for name, checksum in exp['source_hashes'].items():
        assert sha(root / name) == checksum, name
    cases, raw, paths, common_graphs = {}, {}, {}, {}
    for definition in exp['design']['cases']:
        name = definition['name']
        run = source / exp['cases'][name]['run']
        result = read(run / 'result.json')
        diagnosis = read(run.parent / 'diagnosis.json')
        frontend = read(run / 'frontend.json')
        service = read(run / 'input.json')
        protocol = read(run / 'rdma.json')
        expected_service = read(source / 'service.json')
        expected_service['workload']['batch_per_rank'] = definition['batch']
        assert service == expected_service and read(run.parent / 'topology.json') == read(source / 'topology.json')
        recovery = protocol['recovery']
        assert recovery['initial_rto_ns'] == definition['initial_rto_ns']
        assert recovery.get('history') == (exp['design']['history'] if definition['history'] else None)
        assert protocol['admission'].get('per_destination') == (exp['design']['per_destination'] if definition['shares'] else None)
        assert protocol['admission']['max_active_per_source'] == 3
        assert protocol['admission']['max_active_bytes_per_source'] == 16384
        expected_retries = 8 if definition['initial_rto_ns'] == 2000 else 2 if definition['fault'] == 'persistent' else 3
        assert recovery['max_retries'] == expected_retries
        assert recovery['max_rto_ns'] == (64000 if definition['initial_rto_ns'] == 2000 else 160000)
        rules = protocol.get('faults', [])
        if definition['fault'] == 'none':
            assert rules == []
        else:
            assert len(rules) == 1 and rules[0]['message_id'] == exp['target']['id']
            assert rules[0]['kind'] == 'ack' and rules[0]['sequence'] == exp['target']['bytes']
            assert rules[0]['count'] == (1 if definition['fault'] == 'single' else 10000)
        history = frontend['history']
        assert len(history) == (3 if definition['fault'] == 'persistent' else 4)
        complete = result['passed'] and all(h.get('finished_ns') is not None for h in history)
        finish = max(h['finished_ns'] for h in history) if complete else None
        states = result['recovery']['states']
        cache = result['recovery'].get('history', {})
        graphs = {path.parent.name: sha(path) for path in sorted((run / 'graphs').glob('*/wave.json'))}
        for graph, checksum in graphs.items():
            key = definition['batch'], graph
            assert common_graphs.setdefault(key, checksum) == checksum
        metrics = dict(name=name, expect='recovery_retry_exhausted' if definition['fault'] == 'persistent' else 'success',
                       comparison_group='batch' + str(definition['batch']), passed=result['passed'],
                       failure_reason=result.get('failure', {}).get('reason'), frontend_complete_ns=finish,
                       network_finish_or_stop_ns=result['finish_ns'],
                       network_drain_after_frontend_ns=result['finish_ns'] - finish if finish is not None else None,
                       submitted_messages=len(result['messages']),
                       completed_messages=sum(m['ack_callbacks'] == 1 for m in result['messages']),
                       submitted_logical_bytes=sum(m['bytes'] for m in result['messages']),
                       source_payload_bytes=sum(m['source_data_payload_bytes'] for m in result['messages']),
                       timeout_retransmissions=sum(s['timeout_retransmissions'] for s in states),
                       rtt_samples=sum(s.get('rtt', {}).get('samples', 0) for s in states),
                       history_hits=cache.get('hits'), history_misses=cache.get('misses'), history_evictions=cache.get('evictions'),
                       result_sha256=sha(run / 'result.json'), graph_hashes=graphs)
        assert metrics == exp['cases'][name]['metrics'], name
        assert diagnosis['passed'] == result['passed'] and diagnosis['recovery']['reconstructed']
        if definition['history']:
            assert diagnosis['recovery']['rtt']['history_reconstructed']
        if definition['fault'] == 'persistent':
            assert not result['passed'] and metrics['failure_reason'] == metrics['expect']
            target = next(m for m in result['messages'] if m['id'] == exp['target']['id'])
            assert target['receive_ready_ns'] is not None and target['ack_ns'] is None
            assert target['sender_resource_released_ns'] is None
            bound = next(b for b in diagnosis['recovery']['silence_bounds'] if b['id'] == target['id'])
            assert bound['failure_deadline_ns'] == result['finish_ns'] and bound['outcome'] == 'failed_at_bound'
            metrics['failure_certificate'] = bound
        else:
            assert result['passed'] and finish is not None
        metrics['condition'] = definition
        metrics['frontend_history'] = history
        cases[name], raw[name] = metrics, result
        paths[name] = {m['id']: [(h['node'], h['port']) for h in m['first_packet_path']]
                       for m in result['messages'] if m['first_tx_ns'] is not None}
    path_differences = {}
    for name, case in cases.items():
        baseline = 'b' + str(case['condition']['batch']) + '-normal-h0-s0'
        path_differences[name] = [mid for mid, path in paths[name].items() if path != paths[baseline][mid]]

    pairs = []
    for name, case in cases.items():
        c = case['condition']
        for dimension in ('history', 'shares'):
            if c[dimension]:
                continue
            modified = dict(c, **{dimension: True})
            peer = next((key for key, value in cases.items() if all(value['condition'][k] == v
                        for k, v in modified.items() if k != 'name')), None)
            assert peer is not None
            delta = {k: cases[peer][k] - case[k] if case[k] is not None else None
                     for k in ('frontend_complete_ns', 'network_finish_or_stop_ns', 'source_payload_bytes',
                               'timeout_retransmissions', 'rtt_samples')}
            pairs.append(dict(dimension=dimension, off=name, on=peer, delta_on_minus_off=delta,
                              success_comparison=case['passed'] and cases[peer]['passed']))

    # Single-QP fault: a cap also obstructs healthy work to the same GPU.
    protection = {}
    for name in ('b4-single-h0-s0', 'b4-single-h0-s1'):
        flows = {m['id']: m for m in raw[name]['messages']}
        target, healthy = flows[1], flows[13]
        protection[name] = dict(target=target, healthy_same_destination=healthy,
                                first_wave_finished_ns=cases[name]['frontend_history'][0]['finished_ns'])
        assert target['src'] == healthy['src'] and target['dst'] == healthy['dst']
    assert protection['b4-single-h0-s1']['healthy_same_destination']['admission_granted_ns'] == protection['b4-single-h0-s1']['target']['sender_resource_released_ns']

    observed = {}
    capture_root = source / 'captures'
    assert read(capture_root / 'capture.json')['passed']
    for name in read(capture_root / 'capture.json')['events']:
        run = capture_root / name / 'run'
        result = read(run / 'result.json')
        clean = dict(result)
        capture = clean.pop('packet_capture')
        assert clean == raw[name] and capture['complete']
        original = source / exp['cases'][name]['run']
        assert {q.parent.name: sha(q) for q in (run / 'graphs').glob('*/wave.json')} == {q.parent.name: sha(q) for q in (original / 'graphs').glob('*/wave.json')}
        mid = 86 if name.startswith('b4') else 65
        selected = [mid, 73, 40] if mid == 86 else [mid]
        tail = dict(zip(capture['columns'], capture['events'][-1]))
        assert tail['time_ns'] == result['finish_ns']
        # Offline diagnostic, not a policy observation: verify tail lineage and
        # receiver's actual duplicate response rather than assuming drain cause.
        late = [dict(zip(capture['columns'], e)) for e in capture['events']
                if e[4] == tail['packet_uid'] and e[5] == tail['message_id'] and e[6] == tail['ip_protocol']]
        origin = next(e for e in late if e['event'] == 0)
        duplicate = [dict(zip(result['recovery']['receiver_columns'], e))
                     for e in result['recovery']['receiver_events'] if e[0] == origin['time_ns']]
        if name.startswith('b1'):
            assert tail['ip_protocol'] == 252 and tail['event'] == 1 and duplicate
        observed[name] = dict(messages={str(i): packet_components(result, i) for i in selected},
                              completing_acks={str(i): completing_ack(result, i) for i in selected},
                              final_packet_event=tail, final_packet_origin=origin,
                              receiver_duplicate_evidence=duplicate)
    off = observed['b4-short-h0-s0']['messages']['86']
    on = observed['b4-short-h1-s0']['messages']['86']
    components = ['first_tx_ns', 'source_emission_span_including_retries_ns',
                  'link_serialization_and_propagation_ns', 'switch_queue_residence_ns', 'other_forwarding_ns', 'ack_tail_ns']
    decomposition = {k: on[k] - off[k] for k in components}
    assert sum(decomposition.values()) == cases['b4-short-h1-s0']['frontend_complete_ns'] - cases['b4-short-h0-s0']['frontend_complete_ns']
    upstream = {}
    for mid in ('40', '73'):
        before = observed['b4-short-h0-s0']
        after = observed['b4-short-h1-s0']
        data_delta = {k: after['messages'][mid][k] - before['messages'][mid][k] for k in components}
        ack_delta = {k: after['completing_acks'][mid][k] - before['completing_acks'][mid][k]
                     for k in ('receiver_to_ack_source_tx_ns', 'link_ns', 'switch_residence_ns', 'other_forwarding_ns')}
        assert sum(ack_delta.values()) == data_delta['ack_tail_ns']
        assert sum(data_delta.values()) == after['messages'][mid]['ack_ns'] - before['messages'][mid]['ack_ns']
        upstream[mid] = dict(data_delta=data_delta, ack_tail_delta=ack_delta)
    credit = {}
    for name in ('b4-short-h0-s0', 'b4-short-h1-s0'):
        flows = {m['id']: m for m in raw[name]['messages']}
        assert flows[86]['admission_granted_ns'] == flows[73]['sender_resource_released_ns']
        assert flows[86]['ack_ns'] == cases[name]['frontend_complete_ns']
        assert flows[40]['ack_ns'] == next(h['finished_ns'] for h in cases[name]['frontend_history'] if h['graph_id'] == 'g1.w0')
        credit[name] = {str(i): flows[i] for i in (72, 73, 74, 84, 85, 86)}

    summary = dict(platform_commit=exp['platform_commit'], analysis_sha256=sha(Path(__file__)),
                   cases=cases, signed_pairs=pairs, common_graphs_identical_within_batch=True,
                   path_difference_message_ids=path_differences, protection_obstruction=protection,
                   packet_evidence=observed, batch4_short_completion_delta_components_ns=decomposition,
                   upstream_feedback_chain_deltas_ns=upstream,
                   source_credit_chain=credit, input_sources_verified=len(exp['source_hashes']),
                   scope='24 finite synthetic decode conditions, 4 passive exact replays; no hardware or production claims')
    save(out / 'summary.json', summary)
    md = ['# 恢复历史与目的端份额：24组独立推理对照', '',
          '固定8GPU、两组交错EP4、1层2步已有KV的Decode，100G Clos。batch/rank为1或4。每源3消息/16384B；开启份额时每目的端1消息/8192B，未增加总资源。计算为声明值，专家选择为同一合成规则。', '',
          '历史让后续短连接参考近期恢复依据；份额防止一个目的端占满源资源。二者缺省关闭，均不保证推理更快。', '',
          '| 条件 | 业务完成 ns | 网络结束/失败 ns | 源payload B | 超时决策 | 有效RTT样本 |',
          '|---|---:|---:|---:|---:|---:|']
    for name, c in cases.items():
        md.append('| ' + ' | '.join(str(v) for v in (name, c['frontend_complete_ns'], c['network_finish_or_stop_ns'],
                  c['source_payload_bytes'], c['timeout_retransmissions'], c['rtt_samples'])) + ' |')
    md += ['', 'h1开启历史，s1开启目的端份额；normal初值40μs，short为2μs。single只丢目标消息的最终ACK一次，persistent持续丢同一最终ACK。None表示业务未完成。预期失败保留passed=false，停止更早或提交更少不构成优化。', '',
           '## 流量少了，为什么业务还晚313ns？', '',
           'batch4、短初值、无份额：历史使payload从1167360降至686080B，减少41.23%；超时决策184→72，有效RTT样本0→44。但业务63838→64151ns，晚313ns。首批无历史仍无法自动修好；超时退避被复用，并未伪造成RTT样本。', '',
           '最后业务完成都等消息86（1→7 Combine）。它的实际首发晚444ns；逐包拆开后：首发+444、最后交付包的源发出跨度+20、链路项0、交换机驻留−151、其他转发0、ACK尾部0，合计+313ns。总流量减少并不保证这个依赖分支最先结束。', '',
           '消息86的准入由消息73确认释放触发，分别50446与50890ns；消息86就绪时源上已有72/73/74三条消息，且逻辑字节占满16384B。随后84/85先消耗释放的资源。73晚确认444ns是真实反馈造成的额度等待，不能单看86当前路径就排除此前网络影响。', '',
           '继续向前追：g1第一步最后等待消息40，两条件数据到达均25906ns，但完成ACK的交换机驻留从0增到247ns，使下一步晚247ns启动。消息73随后首发也晚247ns，其完成数据包驻留少162ns，而ACK尾部多359ns（源端发ACK前多38ns，交换机驻留多321ns），合计晚444ns。再经86的+20−151ns，得到最终+313ns。这条实际反馈链说明局部队列可在总流量下降时变差；恒等分解不等于唯一可消除的反事实业务损失。', '',
           '| 条件/消息 | 首发 ns | 至最终交付包源TX跨度 ns | 链路项 ns | 交换机驻留 ns | ACK尾部 ns |',
           '|---|---:|---:|---:|---:|---:|']
    for name, evidence in observed.items():
        for mid, c in evidence['messages'].items():
            md.append('| ' + ' | '.join(str(v) for v in (name + '/' + mid, c['first_tx_ns'],
                      c['source_emission_span_including_retries_ns'], c['link_serialization_and_propagation_ns'],
                      c['switch_queue_residence_ns'], c['ack_tail_ns'])) + ' |')
    md += ['', '以上通过目的GPU在receive_ready_ns收到的真实完成包追溯同次运行的UID；允许其他字节已重传。跨度含发送等待/可能重传，不叫纯发送burst；队列驻留直接来自入/出队，包含该模型的转发驻留。', '',
           '## 业务提前，网络为何反而更晚排空？', '',
           'batch1、短初值、无份额：业务53667→53260ns，提前407ns；网络59655→61260ns，晚1605ns。历史开启时业务后的8000ns不是请求额外等待。最后事件确认为消息65的重复累计ACK回到源GPU2，序号4096；它对应接收端重复数据响应记录。原始包证据和源记录都保存在summary.json。残留流量能否影响后续持续到达请求，本轮未测。', '',
           '## 静态份额何时反而挡住健康消息？', '',
           '单最终ACK丢失时，目标1（0→4）在11649ns数据已齐，两策略均67400ns确认释放。健康消息13也发往GPU4：无份额17230ns获准、30272ns确认；有份额必须等目标1释放，67400ns才获准、80182ns确认。第一步从67400拖至80182ns，后续步受其依赖，业务98106→110962ns，慢12856ns。源payload和重试次数相同；这是同目的端静态串行化的代价，不是目标GPU整体故障。', '',
           '无故障、正常初值时，batch1份额使业务慢875ns，batch4早501ns；历史在这些条件不改变业务完成。两项策略都不能当作普遍加速开关。', '',
           '## 持续故障与交付边界', '',
           '四组持续丢失均280688ns明确失败，源资源保持占用；独立诊断逐条重建恢复历史，并核对无进展失败界。健康目的端获得准入不代表同一个请求成功，当前重试耗尽仍终止共享执行。没有请求级失败隔离。', '',
           '24组的共同实际任务图在相同batch内逐字节相同，首包路径按节点/端口比较，差异明细保存，不跨运行比较UID。4次被动补采除新增包记录外整个执行逐字段一致，图字节一致；它们是观测一致性证据，不是额外独立性能样本，也不是生产时延统计。', '',
           '建议：先校准初始超时和真实负载，再决定是否启用历史；静态目的端份额仅作为有明确保护要求的对照机制。后续优先研究可观察的单QP/目的端故障区分、请求级终止范围，以及真实专家路由下的吞吐和尾部时延。千卡、训练DP、真实热点和GPU硬件精度均未由本轮小实验验证。', '']
    (out / 'results.md').write_text('\n'.join(md))
    parts, table = [], False
    for line in md:
        if line.startswith('|'):
            if line.startswith('|---'):
                continue
            if not table:
                parts.append('<div class="scroll"><table>')
                table = True
            parts.append('<tr>' + ''.join('<td>' + html.escape(cell.strip()) + '</td>' for cell in line.strip('|').split('|')) + '</tr>')
            continue
        if table:
            parts.append('</table></div>')
            table = False
        if line.startswith('# '):
            parts.append('<h1>' + html.escape(line[2:]) + '</h1>')
        elif line.startswith('## '):
            parts.append('<h2>' + html.escape(line[3:]) + '</h2>')
        elif line:
            parts.append('<p>' + html.escape(line) + '</p>')
    if table:
        parts.append('</table></div>')
    (out / 'report.html').write_text('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>24组推理通信对照</title><style>body{font:17px/1.85 system-ui,sans-serif;max-width:1200px;margin:36px auto;padding:0 24px;color:#183047}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}td{padding:8px;border:1px solid #ccd8e3}tr:first-child{background:#e3edf5}h2{margin-top:40px}</style></head><body>' + ''.join(parts) + '<p><a href="summary.json">详细证据</a> · <a href="results.md">Markdown</a></p></body></html>')
    print(json.dumps(dict(passed=True, cases=len(cases), pairs=len(pairs), packet_replays=len(observed),
                          paths_changed=sum(bool(v) for v in path_differences.values()))))


if __name__ == '__main__':
    main()
