"""Post-run explanation and visualization, separate from frozen execution code."""
import argparse
from collections import Counter
import hashlib
import html
import json
import math
from pathlib import Path
import shutil


def load(p):
    return json.loads(Path(p).read_bytes())


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(p, v):
    with p.open('x') as s:
        json.dump(v, s, ensure_ascii=False, indent=2)
        s.write('\n')


def path(rows, uid, stream, protocol):
    values = [x for x in rows if x['packet_uid'] == uid and x['message_id'] == stream and
              x['ip_protocol'] == protocol and x['event'] in (0, 1, 2)]
    assert values[0]['event'] == 0 and values[-1]['event'] == 1
    switches = {}
    for x in values:
        if x['node'] < 128:
            continue
        p = switches.setdefault(str(x['node']), {})
        if x['event'] == 1:
            assert 'rx_ns' not in p
            p['rx_ns'] = x['time_ns']
        if x['event'] == 0:
            assert 'tx_ns' not in p
            p.update(tx_ns=x['time_ns'], port=x['port'])
    for s in switches.values():
        s['residence_ns'] = s['tx_ns']-s['rx_ns']
    return dict(events=values, switches=switches, source_tx_ns=values[0]['time_ns'],
                destination_rx_ns=values[-1]['time_ns'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--study', type=Path, required=True)
    p.add_argument('--analysis', type=Path, required=True)
    p.add_argument('--verification', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    analysis = load(a.analysis)
    verification = load(a.verification)
    assert verification['passed'] and verification['analysis_sha256'] == sha(a.analysis)
    out = a.output
    out.mkdir(parents=True, exist_ok=False)
    mechanism = {}
    # Selected after results: group2 is the last group in both60us conditions;
    # message27313 is local60's final predecessor, not a preselected victim.
    for name in ('global60', 'local60'):
        r = load(a.study/name/'result.json')
        rows = [dict(zip(r['packet_capture']['columns'], row)) for row in r['packet_capture']['events']]
        m = next(m for m in r['service_transport']['messages'] if m['id'] == 27313)
        gate = next(g for g in r['service']['phase_completion']['gates']
                    if g['graph_id'] == 'g2.w1' and g['id'] == 'L1.combine')
        if name == 'local60':
            assert gate['released_ns'] == m['ack_ns']
            last = [x for x in gate['members'] if x['arrived_ns'] == gate['released_ns']]
            assert len(last) == 1 and last[0]['rank'] == m['src'] == 70
            wave = load(a.study/name/'graphs/g2.w1/wave.json')
            nodes = {n['id']: n for n in wave['rank_nodes']['70']}
            flush = nodes[last[0]['node']]
            assert flush['kind'] == 'GATE'
            assert any(nodes[d]['kind'] == 'SEND' and nodes[d]['message']['tag'] == m['id'] for d in flush['deps'])
        native = r['service_transport']['native']
        source = [dict(zip(native['source_packet_columns'], row)) for row in native['source_packets']]
        last = [x for x in source if x['stream'] == m['stream'] and
                x['sequence']+x['payload_bytes'] == m['end'] and x['time_ns'] <= m['receive_ready_ns']]
        assert len(last) == 1
        data = path(rows, last[0]['packet_uid'], m['stream'], 17)
        assert data['destination_rx_ns'] == m['receive_ready_ns']
        ack = [x for x in rows if x['event'] == 1 and x['node'] == m['src'] and x['ip_protocol'] == 252
               and x['message_id'] == m['stream'] and x['time_ns'] == m['ack_ns'] and x['sequence'] >= m['end']]
        assert len(ack) == 1
        feedback = path(rows, ack[0]['packet_uid'], m['stream'], 252)
        assert feedback['source_tx_ns'] == m['receive_ready_ns']
        streams = {s['id']: s for s in native['streams']}
        topology = load(a.study/name/'topology.json')
        occupied = {}
        for node in ('136', '141'):
            q = data['switches'][node]
            edge = next(e for e in topology['links'] if (e['src'], e['src_port']) == (int(node), q['port'])
                        or (e['dst'], e['dst_port']) == (int(node), q['port']))
            wire = []
            groups = Counter()
            for row in rows:
                if row['event'] != 0 or (row['node'], row['port']) != (int(node), q['port']):
                    continue
                duration = math.ceil(row['model_bytes']*8*10**9/edge['rate_bps'])
                overlap = max(0, min(q['tx_ns'], row['time_ns']+duration)-max(q['rx_ns'], row['time_ns']))
                if overlap:
                    st = streams.get(row['message_id'])
                    group = str(st['src'] % 4) if st else 'unmapped'
                    groups[group] += overlap
                    wire.append(dict(packet=row, serialization_ns=duration, occupied_ns=overlap, group=group))
            assert sum(groups.values()) == q['residence_ns']
            occupied[node] = dict(by_source_group_ns=dict(groups), wire=wire)
        mechanism[name] = dict(message=m, source_last_packet=last[0], data_path=data, ack_path=feedback,
                               final_combine_gate_ns=gate['released_ns'], occupiers=occupied)
    base, local = mechanism['global60'], mechanism['local60']
    assert set(base['data_path']['switches']) == set(local['data_path']['switches'])
    deltas = {n: local['data_path']['switches'][n]['residence_ns']-q['residence_ns']
              for n, q in base['data_path']['switches'].items()}
    ready = local['message']['ready_ns']-base['message']['ready_ns']
    source_wait = (local['data_path']['source_tx_ns']-local['message']['ready_ns'])-(
        base['data_path']['source_tx_ns']-base['message']['ready_ns'])
    ack = (local['message']['ack_ns']-local['message']['receive_ready_ns'])-(
        base['message']['ack_ns']-base['message']['receive_ready_ns'])
    slack = base['final_combine_gate_ns']-base['message']['ack_ns']
    final = local['final_combine_gate_ns']-base['final_combine_gate_ns']
    assert ready+source_wait+sum(deltas.values())+ack-slack == final == 2831
    assert ready == 1249 and source_wait == ack == 0 and sum(deltas.values()) == 1765 and slack == 183
    mechanism['accounting'] = dict(selection='post_run_largest_completion_group_and_actual_last_predecessor',
        ready_delta_ns=ready, source_wait_delta_ns=source_wait, switch_residence_delta_ns=deltas,
        ack_path_delta_ns=ack, baseline_critical_slack_ns=slack, final_group_delay_ns=final,
        same_message_ack_delta_ns=local['message']['ack_ns']-base['message']['ack_ns'],
        interpretation='1249+0+1765+0-183=2831ns; later normal DATA shares ports, not necessarily retransmission packets')
    save(out/'mechanism.json', mechanism)
    shutil.copyfile(a.analysis, out/'analysis.json')
    shutil.copyfile(a.verification, out/'raw_verification.json')
    names = list(analysis['cases'])
    cases = analysis['cases']
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(12.8, 4.5), layout='constrained')
    for group in range(4):
        axes[0].plot(names, [cases[n]['group_complete_ns'][str(group)]/1000 for n in names],
                     marker='o', label='EP group '+str(group))
    axes[0].set_ylabel('Bounded task completion (us)')
    axes[0].set_title('Same15 requests / Clos128 / SR17us')
    axes[0].legend(ncols=2, fontsize=8)
    axes[1].bar(names, [cases[n]['retransmitted_payload_bytes']/1024 for n in names])
    axes[1].set_ylabel('Retransmitted source payload (KiB)')
    axes[1].set_title('Fewer retry bytes need not mean faster tasks')
    for ax in axes:
        ax.tick_params(axis='x', labelrotation=30)
        ax.grid(axis='y', alpha=.2)
    fig.savefig(out/'comparison.png', dpi=160)
    fig.savefig(out/'comparison.svg')
    plt.close(fig)
    svg = out/'comparison.svg'
    svg.write_text('\n'.join(x.rstrip() for x in svg.read_text().splitlines())+'\n')
    normal = cases['normal']['max_group_complete_ns']
    gain = analysis['effects']['20']['max_group_saved_ns']
    penalty = cases['global20']['max_group_complete_ns']-normal
    percent = gain/penalty*100
    total_link_bytes = sum(cases['normal']['selected_link_payload_bytes_by_group'].values())
    records = []
    table = []
    for n in names:
        c = cases[n]
        table.append(f"<tr><td>{n}</td><td>{c['max_group_complete_ns']/1000:.3f}</td>"
            f"<td>{c['drain_ns']/1000:.3f}</td><td>{c['retransmitted_payload_bytes']:,}</td>"
            f"<td>{c['loss_events']}</td><td>{c['drops_by_protocol'].get('17',0)}</td>"
            f"<td>{c['drops_by_protocol'].get('252',0)}</td></tr>")
        records.append(f"| {n} | {c['max_group_complete_ns']/1000:.3f} | {c['drain_ns']/1000:.3f} | {c['retransmitted_payload_bytes']:,} | {c['loss_events']} |")
    explanation = f"""# 共享上行故障：收益来自哪里，边界在哪里

15份不同R1真实专家选择、128端点Clos、四EP32组，各源rank的batch=1；两层两个Decode步。GPU计算为声明值、网络由固定原生RDMA ns-3实际推进、marker payload验证字节buffer；不是完整R1服务或硬件测量。五个新故障网络各一次，只读复用已验收正常运行。

**20µs路由收敛延迟下，本地剔除坏出口收回{gain/1000:.3f}µs，约占该故障相对正常额外等待的{percent:.2f}%。这是成熟方法的收益。** 最晚任务185.464→148.750µs，下降{gain/cases['global20']['max_group_complete_ns']*100:.2f}%；残余12.977µs仅是与正常条件的参考差距，包含断链丢包及容量/路径改变，不能都算成可优化空间。

| 条件 | 本片段最晚任务µs | 物理排空µs | 重传payload B | 物理+本地丢失事件 |
|---|---:|---:|---:|---:|
{chr(10).join(records)}

`normal`无注入；`global20/60`在4µs断链，24/64µs全局改路；`local20/60`额外在4µs本地剔除坏出口；`global0`在4µs立即全局改路。链路始终断开，没有恢复上线。local即时获知自己的端口状态，不包含检测/编程成本；global0改变全网路径及哈希候选，不是业务时延的数学最优界。

## 网络发现

正常时选定128–132链路实际承载四组的{total_link_bytes:,}B DATA payload，190次物理DATA发送（双向合计）。它是共享上行，不是单GPU黑洞。ToR128连接16张400G网卡和4条400G上联；断掉其中一条只表示该ToR上联容量减少25%，不能称全网带宽减少25%。

ToR128能够剔除坏端口并保留其他上联；Agg132对下方16个GPU原本只有该下跳，本地剔除后16个目的暂时没有候选，仍需真实丢弃并等待全局收敛。local不能凭空产生可用绕路。原始记录还显示ACK/CNP真实丢失，不能沿用此前“只丢DATA、控制包始终保留”的解释。

60µs延迟下，local较global少重传237,208B，却让最晚任务晚2.831µs、物理排空晚28.584µs。收益不能只用重传次数/字节评价。global0也不保证每组都快于local20：不同时间改表改变后续流量相位和队列。

## 2.831µs退化的逐包解释

事后选择两条件共同最晚的group2，并追踪local60最后的真实前驱：70→10、message27313、stream824，第二步第二层Combine。该消息源ready晚1,249ns；末包源等待差0；五个交换机驻留合计多1,765ns；ACK返回跨度差0。它在global60原本比真正最后前驱早183ns，因此最终组变化为**1,249+1,765−183=2,831ns**。关键前驱从rank78变为rank70，不能只比较两条不同消息就归因。

ToR136出口18的等待72→652ns，Agg141出口5为805→2,134ns。local60中group1数据分别占据该末包等待的362ns和1,046ns，其余为同组数据；保存实际TX区间并核对全部等待。这里是故障改变流量相位后，后续普通数据在共享端口重新竞争，不将这些包全部叫作重传流量，也不把端口等待直接相加当端到端收益。

## 创新方向与尚未证明的部分

本轮排除了“已有重传/本地ECMP本身就是创新”。值得继续验证的候选是：在既有可靠传输和更完整故障绕路基线之上，利用MoE阶段的实际未完成贡献，分配有限恢复发送资源，减少关键阶段等待及其他任务的排队干扰；不丢弃原ACK、不伪造门限完成、不提前释放buffer。

但当前未实现或证明该候选有增益。下一步先核对能覆盖旧候选为空场景的既有快速重路由，以及物理链路恢复所需的原生状态、路由代次和QP唤醒；再在独立预算/留出请求中比较应用感知恢复。少重传、单组提前和零开销理想控制均不足以证明论文贡献。现有15请求也不能证明生产p99、满载收益、完整671B或经济回报。

原始复核覆盖4,668消息、46,067次物理DATA跳、150,396队列行；各条件264份原图一致，QP/源ACK/buffer均排空。[原始验收](raw_verification.json)、[分析](analysis.json)、[逐包机理](mechanism.json)、[对比图](comparison.png)。PNG人工查看与网页静态检查另行记录，不冒充浏览器渲染。
"""
    (out/'README.md').write_text(explanation)
    sections = f"""<h1>共享上行故障：恢复收益与剩余空间</h1>
<p>128端点Clos · 15份不同真实R1路由 · 四EP32组 · 两层两个Decode步 · 各rank batch=1 · SR初始超时17µs</p>
<div class="callout"><b>20µs收敛延迟：</b>成熟本地改路让最晚任务185.464→148.750µs，收回故障额外等待约{percent:.2f}%。<br><b>60µs收敛延迟：</b>少重传237,208B，最晚任务反而晚2.831µs。</div>
<p>这是共享交换机上行的真实断链仿真；4µs断开128–132链路后不再上线。不是前一轮GPU32短时DATA黑洞，也不是物理硬件实验。local即时本地响应和global0即时全局响应都是声明的理想参考。</p>
<img src="comparison.png" alt="四组任务完成时间与源重传字节对比">
<h2>同一输入下的结果</h2><table><tr><th>条件</th><th>最晚任务µs</th><th>物理排空µs</th><th>重传B</th><th>丢失事件</th><th>DATA</th><th>ACK</th></tr>{''.join(table)}</table>
<p>global20/60：断链后20/60µs才全局改路；local20/60：额外立即剔除两端自己的坏出口；global0：断链时全局改路。所有条件可靠重传均已开启。最晚任务与网络排空分别记录。</p>
<h2>为什么本地改路还不够？</h2><p>ToR128有其他可用上联；Agg132对16个GPU原本只有一个下跳，剔除后暂时无候选，仍会丢弃DATA、ACK及其他控制包。本地剔除不生成新绕路。{total_link_bytes:,}B正常DATA实际经过这条链路，涉及四组。</p>
<h2>少重传，为什么更慢？</h2><p>追踪local60最后的70→10 Combine消息：ready晚1,249ns，交换机驻留增加1,765ns，ACK跨度不变；减去它在global60原有183ns余量，恰好得到最终2,831ns退化。后续正常数据在共享端口重新竞争，包含group1对group2的排队占用。</p>
<h2>研究空间如何判断？</h2><p>20µs条件已有方法解决约{percent:.2f}%额外等待，不能把全部提升算成新方法。与正常相比剩余12.977µs是参考差距，未证明都可消除。60µs条件说明控制路径、阶段依赖和竞争时序必须一起看。下一步先补足更完整的已有绕路基线，再验证利用MoE实际未完成贡献分配恢复资源的候选；当前没有新协议收益结论。</p>
<p><a href="README.md">详细解释</a> · <a href="analysis.json">全部结果</a> · <a href="mechanism.json">逐包证据</a> · <a href="raw_verification.json">独立原始核验</a></p>
<p>限制：声明计算、功能标记数据、固定片段；未校准硬件，未验证完整R1、Prefill、满载或生产p99。五新网络各一次，正常样本只读复用。HTML仅作静态资源核对。</p>"""
    css = 'body{font:16px/1.7 system-ui,sans-serif;max-width:1120px;margin:36px auto;padding:0 24px;color:#173047;background:#f6f8fb}h1,h2{color:#133356}table{border-collapse:collapse;width:100%;background:white}td,th{padding:9px;border-bottom:1px solid #d9e1eb;text-align:right}td:first-child,th:first-child{text-align:left}img{width:100%;background:white;border-radius:12px}.callout{background:#e1edf9;padding:18px;border-left:5px solid #276fc2}a{color:#1e61a9}'
    (out/'report.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>共享上行恢复对照</title><style>'+css+'</style><main>'+sections+'</main></html>')
    save(out/'publication.json', dict(analysis_sha256=sha(a.analysis), verification_sha256=sha(a.verification),
        publisher_sha256=sha(Path(__file__)), mechanism_sha256=sha(out/'mechanism.json'),
        permanent_link_failure=True, physical_link_restoration_supported=False,
        recovered_penalty_percent=percent, max_group_saved_ns=gain, graph_rendered_in_browser=False))
    print(json.dumps(dict(passed=True, output=str(out), recovered_penalty_percent=percent, mechanism_delay_ns=final)))


if __name__ == '__main__':
    main()
