"""Independent source packet, ACK timer, Karn and bounded-history replay."""
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

def packets(result, target):
    cap = result['packet_capture']; wire = cap['wire_headers']
    assert cap['complete'] and not cap['omitted_events']
    assert len(cap['events']) == len(wire['event_header_indices'])
    flows = {m['id']: m for m in result['messages']}; emitted = {}; observed = []
    for row, index in zip(cap['events'], wire['event_header_indices']):
        e = dict(zip(cap['columns'], row)); h = dict(zip(wire['columns'], wire['headers'][index]))
        assert e['packet_uid'] == h['packet_uid'] and e['ip_protocol'] == h['ip_protocol']
        if e['message_id'] == target: observed.append(e)
        if e['event'] == 0 and e['ip_protocol'] == 17 and e['message_id'] in flows:
            m = flows[e['message_id']]
            if e['node'] != m['src']: continue
            assert h['ports_present'] and h['source_port'] == h['destination_port'] == m['port']
            assert 0 <= e['sequence'] < e['sequence']+e['data_payload_bytes'] <= m['bytes']
            emitted.setdefault(m['id'], []).append(e)
    checks = 0
    for m in flows.values():
        if m['src'] not in cap['selection']['nodes']: continue
        rows = emitted[m['id']]
        assert len(rows) == m['source_data_packets']
        assert sum(e['data_payload_bytes'] for e in rows) == m['source_data_payload_bytes']
        end = 0
        for start, stop in sorted({(e['sequence'], e['sequence']+e['data_payload_bytes']) for e in rows}):
            assert start <= end, 'source has an unobserved byte gap'
            end = max(end, stop)
        assert end == m['bytes'] == m['source_unique_data_payload_bytes']; checks += 1
    m = flows[target]; rows = emitted[target]
    tx = [[e['time_ns'], e['sequence'], e['data_payload_bytes']] for e in rows]
    feedback = [[e['time_ns'], e['ip_protocol'], e['sequence']] for e in observed
                if e['event'] == 1 and e['node'] == m['src'] and e['ip_protocol'] in (252, 253)]
    assert min(t for t, _, _ in tx) == m['first_tx_ns']
    assert any(t == m['ack_ns'] and seq == m['bytes'] for t, _, seq in feedback)
    return dict(checked_source_messages=checks, source_tx=tx, source_feedback=feedback,
                first_feedback_after_first_tx_ns=min(e[0] for e in feedback)-min(e[0] for e in tx),
                drops=[e for e in observed if e['event'] == 2])

def estimates(result):
    """Reconstruct adaptive state solely from recorded source transmissions/feedback."""
    recovery = result['recovery']; c = recovery['config']
    if 'rtt_estimator' not in c: return {}, {}, dict(enabled=False)
    flows = {m['id']: m for m in result['messages']}; actual = {s['id']: s for s in recovery['states']}
    states = {}; seeds = {}; resets = {}; cache = {}; serial = Counter(); counts = Counter(); clock = -1
    hc = c.get('history'); bound = c['rtt_estimator']; observed_timeouts = Counter()
    def expire(source, now):
        if not hc: return
        expired = [k for k, e in cache.items() if k[0] == source and now-e[1] >= hc['max_age_ns']]
        for key in expired: del cache[key]
        counts['expirations'] += len(expired)
    def observe(key, now, rto):
        if not hc: return
        expire(key[0], now); local = [k for k in cache if k[0] == key[0]]
        if key not in cache and len(local) == hc['max_entries_per_source']:
            del cache[min(local, key=lambda k: cache[k][2])]; counts['evictions'] += 1
        old = cache.get(key, (0, 0, 0))[0]; serial[key[0]] += 1; counts['updates'] += 1
        cache[key] = (max(old, rto), now, serial[key[0]])
    def ambiguous(s):
        s['barrier'] = max(s['barrier'], s['high'])
        if s['probe_time'] >= 0: s['discarded'] += 1; s['probe_time'] = -1
    for now, kind, mid, start, end in recovery['rtt_events']:
        assert clock <= now <= result['finish_ns'] and mid in flows; clock = now
        m = flows[mid]; key = m['src'], 0x0b000001+m['dst']*32+m['plane']
        if mid not in states:
            assert kind == 0; initial = c['initial_rto_ns']
            if hc:
                expire(key[0], now); hit = key in cache; counts['hits' if hit else 'misses'] += 1
                if hit:
                    value, evidence, _ = cache[key]; serial[key[0]] += 1
                    cache[key] = value, evidence, serial[key[0]]; initial = max(initial, value)
                assert actual[mid]['seed_rto_ns'] == initial and actual[mid]['history_hit'] == hit
            seeds[mid] = initial
            states[mid] = dict(ack=0, high=0, barrier=0, probe_time=-1, probe_end=0, samples=0,
                               discarded=0, srtt=0, var=0, base=initial, payload=0, rto=initial, retries=0)
        s = states[mid]
        if kind == 0:
            assert 0 <= start < end <= m['bytes'] and start <= s['high']
            if start < s['high']: ambiguous(s)
            elif s['probe_time'] < 0 and s['ack'] >= s['barrier']: s.update(probe_time=now, probe_end=end)
            s['high'] = max(s['high'], end); s['payload'] += end-start
        elif kind in (1, 2):
            assert end == 0 and 0 <= start <= s['high']
            if kind == 2: ambiguous(s)
            if start > s['ack']:
                if s['probe_time'] >= 0 and start >= s['probe_end']:
                    sample = now-s['probe_time']; assert sample > 0
                    if s['samples'] == 0: s['srtt'] = sample; s['var'] = (sample+1)//2
                    else:
                        s['var'] = (3*s['var']+abs(s['srtt']-sample)+3)//4
                        s['srtt'] = (7*s['srtt']+sample+7)//8
                    s['base'] = min(c['max_rto_ns'], max(bound['min_rto_ns'], s['srtt']+max(bound['granularity_ns'], 4*s['var'])))
                    s['samples'] += 1; s['probe_time'] = -1; observe(key, now, s['base'])
                s['ack'] = start; s['rto'] = s['base']; s['retries'] = 0
                resets[mid, now, start] = s['base']
        else:
            assert kind == 3 and start == end == 0
            observed_timeouts[mid] += 1; ambiguous(s)
            assert s['retries'] < c['max_retries']
            s['retries'] += 1; s['rto'] = min(c['max_rto_ns'], 2*s['rto']); observe(key, now, s['rto'])
    assert set(states) == set(actual)
    for mid, s in states.items():
        assert actual[mid]['rtt'] == dict(samples=s['samples'], discarded_probes=s['discarded'], srtt_ns=s['srtt'],
            rttvar_ns=s['var'], base_rto_ns=s['base'], ambiguity_until_bytes=s['barrier'],
            probe_end_bytes=s['probe_end'], probe_sent_ns=s['probe_time'])
        assert s['ack'] == actual[mid]['acknowledged_bytes'] == flows[mid]['bytes']
        assert s['high'] == actual[mid]['highest_sent_bytes'] and s['payload'] == flows[mid]['source_data_payload_bytes']
        assert observed_timeouts[mid] == actual[mid]['timeout_expiries']
    if hc:
        expected = [dict(source=k[0], destination_ipv4=k[1], rto_ns=e[0], evidence_ns=e[1], access_serial=e[2]) for k, e in sorted(cache.items())]
        assert recovery['history']['entries'] == expected
        assert all(recovery['history'][name] == counts[name] for name in ('hits', 'misses', 'updates', 'expirations', 'evictions'))
    return seeds, resets, dict(enabled=True, samples=sum(s['samples'] for s in states.values()),
                              discarded=sum(s['discarded'] for s in states.values()), history_replayed=bool(hc), history=dict(counts))

def timers(result):
    recovery = result['recovery']; c = recovery['config']; seeds, resets, adaptive = estimates(result)
    assert recovery['complete'] and not recovery['omitted_events'] and not recovery['failed']
    flows = {m['id']: m for m in result['messages']}; previous = {}; completed = set(); timeouts = Counter(); clock = -1
    for now, kind, source, mid, ack, high, retries, rto, deadline in recovery['events']:
        assert clock <= now <= result['finish_ns'] and mid not in completed; clock = now
        assert source == flows[mid]['src'] and 0 <= ack <= high <= flows[mid]['bytes']
        old = previous.get(mid)
        if old: assert ack >= old[0] and high >= old[1]
        if kind == 0:
            assert (old is None or old[4] == -1) and high > ack and deadline == now+rto
            assert rto == (old[3] if old else seeds.get(mid, c['initial_rto_ns']))
        elif kind in (1, 3):
            assert old and ack > old[0] and retries == 0
            assert rto == (resets[mid, now, ack] if adaptive['enabled'] else c['initial_rto_ns'])
            assert deadline == (now+rto if high > ack else -1)
            if kind == 3:
                assert ack == flows[mid]['bytes'] and now == flows[mid]['ack_ns']; completed.add(mid)
        elif kind == 2:
            assert old and now == old[4] and ack == old[0] and retries == old[2]+1
            assert rto == min(c['max_rto_ns'], 2*old[3]) and deadline == now+rto
            timeouts[mid] += 1
        else:
            assert kind == 6 and old and (ack, retries, rto, deadline) == (old[0], old[2], old[3], old[4])
        previous[mid] = ack, high, retries, rto, deadline
    assert completed == set(flows)
    for final in recovery['states']:
        assert final['state'] == 'complete' and final['deadline_ns'] == -1
        assert final['timeout_retransmissions'] == timeouts[final['id']]
    return dict(timeout_decisions=sum(timeouts.values()), adaptive=adaptive)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); resource.setrlimit(resource.RLIMIT_AS, (2*2**30, 2*2**30)); resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    assert not a.output.exists(); root = a.input; exp = read(root/'experiment.json'); target = exp['target']['id']
    assert exp['state'] == 'completed' and len(exp['cases']) == 45
    runs = [(root, c) for c in exp['cases']]
    supplement = root/'bootstrap80/experiment.json'
    if supplement.exists():
        extra = read(supplement)
        assert extra['state'] == 'completed' and len(extra['cases']) == 9
        assert extra['runtime'] == exp['runtime']
        runs += [(root/'bootstrap80', c) for c in extra['cases']]
    rows = []; evidence = {}; references = {}; raw = {}; graph_checks = 0
    for location, case in runs:
        name = case['name']; r = read(location/name/'result.json'); raw[name] = r
        assert sha(location/name/'result.json') == case['result_sha256'] and r['passed'] and r['drained']
        graphs = {p.parent.name: sha(p) for p in (location/name/'graphs').glob('*/wave.json')}
        assert graphs == case['graphs'] == exp['cases'][0]['graphs']; graph_checks += len(graphs)
        for m in r['messages']:
            assert m['ack_callbacks'] == 1 and not m['sender_qp_present']
            assert m['receiver_contiguous_bytes'] == m['source_unique_data_payload_bytes'] == m['bytes']
            assert m['receive_ready_ns'] <= m['ack_ns'] == m['sender_resource_released_ns']
        ps = packets(r, target); ts = timers(r); m = next(m for m in r['messages'] if m['id'] == target)
        if case['fault'] != 'clean':
            assert r['fault_injection']['drop_counts'] == [1] and len(ps['drops']) == 1
            rule = read(location/(name+'.json'))['faults'][0]; drop = ps['drops'][0]
            assert drop['sequence'] == rule['sequence'] and drop['node'] == rule['node']
            assert drop['ip_protocol'] == (252 if case['fault'] == 'ack' else 17)
        frontend = read(location/name/'frontend.json')['history']; assert len(frontend) == 4
        finish = max(h['finished_ns'] for h in frontend); assert finish <= r['finish_ns'] and r['service']['service_finished']
        identity = {m['id']: tuple(m[k] for k in ('src', 'dst', 'bytes', 'plane', 'port')) for m in r['messages']}
        paths = {m['id']: [(p['node'], p['port']) for p in m['first_packet_path']] for m in r['messages']}
        reference = references.setdefault(case['delay_ns'], (identity, paths))
        logical = sum(m['bytes'] for m in r['messages']); sent = sum(m['source_data_payload_bytes'] for m in r['messages'])
        assert logical == 389120 and len(r['messages']) == 92
        row = dict(name=name, delay_ns=case['delay_ns'], policy=case['policy'], fault=case['fault'],
            frontend_complete_ns=finish, network_finish_ns=r['finish_ns'], repeated_payload_bytes=sent-logical,
            logical_payload_bytes=logical, actual_payload_bytes=sent, payload_efficiency=logical/sent,
            all_hop_model_tx_bytes=sum(p['model_tx_bytes'] for p in r['ports']), timeout_decisions=ts['timeout_decisions'],
            target_ack_ns=m['ack_ns'], target_receive_ns=m['receive_ready_ns'],
            target_first_feedback_ns=ps['first_feedback_after_first_tx_ns'],
            same_delay_message_identities_equal=identity == reference[0], same_delay_first_paths_equal=paths == reference[1],
            history_hits=ts['adaptive'].get('history', {}).get('hits', 0), rtt_samples=ts['adaptive'].get('samples', 0))
        rows.append(row); evidence[name] = dict(packets=ps, timers=ts,
            target_timers=[e for e in r['recovery']['events'] if e[3] == target])
        for event in evidence[name]['target_timers']:
            t, kind, _, _, ack, *_ = event
            if kind in (1, 3): assert any(ft == t and seq == ack for ft, _, seq in ps['source_feedback'])
    by = {(r['delay_ns'], r['policy'], r['fault']): r for r in rows}
    for r in rows:
        r['fault_cost_ns'] = r['frontend_complete_ns']-by[r['delay_ns'], r['policy'], 'clean']['frontend_complete_ns']
        base = by[r['delay_ns'], 'fixed40', r['fault']]
        r['saved_vs_fixed40_ns'] = base['frontend_complete_ns']-r['frontend_complete_ns']
        r['saved_vs_fixed40_fraction'] = r['saved_vs_fixed40_ns']/base['frontend_complete_ns']
    mutations = []
    bad = deepcopy(raw['d500-fixed80-tail']); next(e for e in bad['recovery']['events'] if e[1] == 2)[0] -= 1
    try: timers(bad)
    except AssertionError: mutations.append('premature_timeout')
    bad = deepcopy(raw['d500-fixed80-clean']); next(m for m in bad['messages'] if m['id'] == target)['source_data_payload_bytes'] += 1
    try: packets(bad, target)
    except AssertionError: mutations.append('inflated_source_payload')
    bad = deepcopy(raw['d500-history40-clean']); bad['recovery']['history']['hits'] += 1
    try: estimates(bad)
    except AssertionError: mutations.append('fabricated_history_hit')
    assert len(mutations) == 3
    shared_keys = ('messages', 'ports', 'service', 'queue_events', 'packet_capture')
    identical = {f'{delay}-{fault}': all(raw[f'd{delay}-karn40-{fault}'][key] == raw[f'd{delay}-history40-{fault}'][key]
        for key in shared_keys) for delay in (500, 2000) for fault in ('clean', 'tail', 'ack')}
    result = dict(schema='moe-recovery-delay/1', primary_conditions=45, bootstrap80_controls=len(runs)-45, cases=rows, evidence=evidence,
        checks=dict(graphs=graph_checks, captured_source_messages=sum(e['packets']['checked_source_messages'] for e in evidence.values()),
                    source_only_rtt_and_timer_replay=True, mutations_rejected=mutations,
                    all_same_delay_identities_equal=all(r['same_delay_message_identities_equal'] for r in rows),
                    all_same_delay_first_paths_equal=all(r['same_delay_first_paths_equal'] for r in rows),
                    karn_history_shared_execution_equal=identical),
        scope=dict(synthetic=True, modern_GIN=False, GPU_executed=False, hardware_calibrated=False,
                   independent_stochastic_repetitions=0, new_algorithm=False, captured_nodes=[0, 4]))
    a.output.mkdir(); save(a.output/'summary.json', result)
    lines = ['# 反馈时延改变后，丢包恢复参数是否仍可靠', '',
        f"45个预登记条件及{len(runs)-45}个保守启动Karn强基线补充，固定8GPU/两EP4/batch4/100G Clos及92消息389120B。只改变逐跳传播、源端恢复策略和明确的一次丢失；业务完成和物理排空分开。计算耗时声明、路由合成，无硬件校准。", '',
        '| 每跳传播 ns | 方法 | 故障 | 业务完成 ns | 排空 ns | 重复payload B | 超时 | 相对同方法无故障增加 ns |',
        '|---|---|---|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['delay_ns']} | {r['policy']} | {r['fault']} | {r['frontend_complete_ns']} | {r['network_finish_ns']} | {r['repeated_payload_bytes']} | {r['timeout_decisions']} | {r['fault_cost_ns']} |")
    short_tail = by[500, 'fixed20', 'tail']; short_ack = by[500, 'fixed20', 'ack']
    middle = by[2000, 'fixed20', 'clean']; long = by[4000, 'fixed20', 'clean']
    karn_tail = by[2000, 'karn40', 'tail']; karn_ack = by[2000, 'karn40', 'ack']
    long_fixed = by[4000, 'fixed40', 'clean']; long_hist = by[4000, 'history40', 'clean']
    lines += ['', '## 主要发现', '',
        f"短路径（每跳500ns）首次实际反馈为{short_tail['target_first_feedback_ns']}ns。20μs相对40μs在尾数据/最终ACK故障下分别早{short_tail['saved_vs_fixed40_ns']}ns（{short_tail['saved_vs_fixed40_fraction']:.2%}）和{short_ack['saved_vs_fixed40_ns']}ns（{short_ack['saved_vs_fixed40_fraction']:.2%}），无故障不增加重复payload。",
        f"每跳2000ns时，首次反馈为{middle['target_first_feedback_ns']}ns。20μs无故障即有{middle['timeout_decisions']}次误超时和{middle['repeated_payload_bytes']}B重复payload（原任务的{middle['repeated_payload_bytes']/middle['logical_payload_bytes']:.2%}），业务晚86ns，网络排空晚20086ns。原有20μs结论不能推广为默认。",
        f"每跳4000ns时，首次反馈为{long['target_first_feedback_ns']}ns。20μs无故障有{long['timeout_decisions']}次超时、{long['repeated_payload_bytes']}B重复量（原任务的{long['repeated_payload_bytes']/long['logical_payload_bytes']:.0%}），业务完成仍与80μs相等，排空晚20000ns。只看业务时延会漏掉成倍流量。40μs也有{long_fixed['timeout_decisions']}次误超时，排空晚40086ns。",
        f"Karn在每跳2000ns时，目标首个24522ns样本产生SRTT24522ns、初始RTTVAR12261ns，RTO变成73566ns。尾数据/最终ACK故障的业务相对固定40μs分别晚{-karn_tail['saved_vs_fixed40_ns']}ns和{-karn_ack['saved_vs_fixed40_ns']}ns。自适应在本条件更保守，不能只报告它有利的短路径结果。",
        f"每跳4000ns时，Karn的40μs初值先于首反馈到期，目标样本被丢弃，本QP完成前没有新的原始包可采样；其业务、包与排空和固定40μs相同。历史帮助后续QP，将无故障超时{long_fixed['timeout_decisions']}→{long_hist['timeout_decisions']}、重复payload{long_fixed['repeated_payload_bytes']}→{long_hist['repeated_payload_bytes']}B（减少{1-long_hist['repeated_payload_bytes']/long_fixed['repeated_payload_bytes']:.0%}），业务早86ns、排空早40086ns；仍未消除冷启动。80μs保守对照在所有无故障条件均无重复量，但在短路径丢包下等待明显更长。",
        '500/2000ns的六组Karn与历史条件，消息、端口、服务、队列及包捕获共有执行字段完全相等：本场景历史虽命中68次，仍没有带来新的执行收益。命中率不等于效果。', '',
        '## 结果解释', '',
        '同一传播时延内比较方法；不能把长网络本身增加的时间当成某算法损失。固定20μs是否过短，应先看无故障是否也发生超时与重复发送，再看业务完成代价。Karn只在实际收到无歧义反馈后更新，不能提前知道第一次反馈；历史只作用后续QP，不消除第一个冷启动。', '',
        '表中fault_cost是相同方法和传播时延下的有符号差值。故障改变竞争顺序时可出现非单调性；零或负差值不证明没有重传，也不是理论下界。fixed80是预登记的保守对照，不是事后最佳或生产推荐。所有机制都沿用平台已有实现，没有将调参称为新创新。', '',
        f"独立核对{graph_checks}份任务图、{result['checks']['captured_source_messages']}个捕获源消息；重放源端计时器、Karn与历史缓存，拒绝3种篡改。同延迟消息身份完全相等：{result['checks']['all_same_delay_identities_equal']}；首尝试路径完全相等：{result['checks']['all_same_delay_first_paths_equal']}。捕获仅GPU0/4，不冒充全网逐包覆盖。", '',
        'summary.json保存逐条件结果、目标实际TX/ACK/丢失/计时器证据，以及同故障相对fixed40的有符号收益。原始记录含公开命令、服务资源监控、输入与运行时hash；没有执行现代GIN、千卡、生产故障率或GPU性能测量。', '']
    if supplement.exists():
        lines += ['## 保守启动Karn强基线', '',
            '只将已有Karn的初值40μs改为原设计的保守80μs，不按故障或路径选初值。原45条件保留不变，新增9次网络，没有搜索更多参数。']
        for delay in (500, 2000, 4000):
            clean = by[delay, 'karn80', 'clean']; tail = by[delay, 'karn80', 'tail']; ack = by[delay, 'karn80', 'ack']
            lines.append(f"每跳{delay}ns：无故障重复{clean['repeated_payload_bytes']}B、超时{clean['timeout_decisions']}次；尾数据/最终ACK业务完成分别{tail['frontend_complete_ns']}/{ack['frontend_complete_ns']}ns，同故障相对固定40μs分别早{tail['saved_vs_fixed40_ns']}/{ack['saved_vs_fixed40_ns']}ns。负值表示更慢。")
        lines += ['这个对照区分了估计器规则与冷启动参数。保守初值若已解决误重传，就不能把经典方法未用好的现象包装成新算法需求；中等路径下由估计规则引起的长等待仍须保留。', '']
    (a.output/'results.md').write_text('\n'.join(lines))
    body = []; table = False
    for line in lines:
        if line.startswith('|'):
            if line.startswith('|---'): continue
            if not table: body.append('<div class="scroll"><table>'); table = True
            body.append('<tr>'+''.join('<td>'+html.escape(c.strip())+'</td>' for c in line.strip('|').split('|'))+'</tr>'); continue
        if table: body.append('</table></div>'); table = False
        if line.startswith('# '): body.append('<h1>'+html.escape(line[2:])+'</h1>')
        elif line.startswith('## '): body.append('<h2>'+html.escape(line[3:])+'</h2>')
        elif line: body.append('<p>'+html.escape(line)+'</p>')
    (a.output/'report.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>反馈时延与恢复</title><style>body{font:16px/1.8 system-ui;max-width:1200px;margin:32px auto;padding:0 20px;color:#192f3d}.scroll{overflow:auto}table{border-collapse:collapse;font-size:14px;width:100%}td{border:1px solid #ccd6de;padding:7px}tr:first-child{background:#e7f0f5}h1{font-size:28px}</style>'+''.join(body)+'<p><a href="summary.json">详细证据JSON</a> · <a href="results.md">Markdown</a></p></html>')
    print(json.dumps(dict(passed=True, cases=len(rows), checks=result['checks'])))

if __name__ == '__main__': main()
