"""Render scientific plots and a standalone Chinese report from checked outputs."""
import argparse
import html
import json
from pathlib import Path
import shutil


def load(p):
    return json.loads(Path(p).read_bytes())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    stage, out = a.stage.resolve(), a.output.resolve()
    analysis, proof, mechanism = (load(stage/f) for f in ('analysis.json', 'verification.json', 'mechanism.json'))
    assert analysis['passed'] and proof['passed'] and mechanism['passed']
    out.mkdir(parents=True, exist_ok=False)
    for f in ('analysis.json', 'verification.json', 'mechanism.json'):
        shutil.copyfile(stage/f, out/f)
    for name in ('normal', 'local-w60'):
        shutil.copyfile(stage/'study'/(name+'-compatible.json'), out/(name+'-compatible.json'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    cases = analysis['cases']
    names = ['normal', 'global-w20', 'local-w20', 'prefix-w20', 'instant-w20',
             'global-w60', 'local-w60', 'prefix-w60', 'instant-w60']
    fig, axes = plt.subplots(1, 2, figsize=(13.6, 5), layout='constrained')
    for i in range(4):
        axes[0].plot(names, [cases[n]['group_complete_ns'][str(i)]/1000 for n in names], marker='o', label='EP group '+str(i))
    axes[0].set(ylabel='Bounded task completion (us)', title='Same 15 real routing records / Clos128 / SR 17us')
    axes[0].legend(fontsize=8)
    axes[1].bar(names, [cases[n]['retransmitted_payload_bytes']/1024 for n in names])
    axes[1].set(ylabel='Retransmitted source payload (KiB)', title='Mature explicit-repair baseline; no novel protocol claim')
    for ax in axes:
        ax.tick_params(axis='x', labelrotation=45)
        ax.grid(axis='y', alpha=.2)
    fig.savefig(out/'comparison.png', dpi=160)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(11, 5.3), layout='constrained')
    for name, color in [('normal', '#727b86'), ('local-w60', '#b45a31'), ('prefix-w60', '#2269ab'), ('instant-w60', '#2e8a58')]:
        c = mechanism['cases'][name]['collective']
        for field, style, label in [('unique_received_curve_ns_bytes', '-', 'unique RX'),
                                    ('fully_confirmed_message_curve_ns_bytes', '--', 'fully ACKed messages')]:
            pts = c[field]
            ax.step([x[0]/1000 for x in pts], [x[1]/1024 for x in pts], where='post', color=color,
                    linestyle=style, label=name+' / '+label)
    ax.axvline(64, color='gray', alpha=.4, label='Physical restore 64us')
    ax.set(xlabel='Simulation time (us)', ylabel='Effective payload (KiB)',
           title='Identical g1.w0 first Dispatch: received DATA vs source-confirmed messages')
    ax.grid(alpha=.2)
    ax.legend(fontsize=8, ncols=2)
    fig.savefig(out/'arrival_curves.png', dpi=160)
    plt.close(fig)
    effects = analysis['effects']
    summary = []
    for window in ('20', '60'):
        e = effects[window]
        local, prefix = (cases[k+'-w'+window] for k in ('local', 'prefix'))
        summary.append(f"中断{window}µs时，最晚任务由{local['max_group_complete_ns']/1000:.3f}µs变为{prefix['max_group_complete_ns']/1000:.3f}µs，"
            f"减少{e['saved_ns']/1000:.3f}µs（{e['relative_local_percent']:.2f}%），收回原故障额外等待的{e['fault_penalty_recovered_percent']:.2f}%。"
            f"显式头部逐物理跳合计增加{prefix['encapsulation_wire_bytes']:,}B；相对local总线上字节变化{e['wire_delta_bytes']:+,}B。")
    normal = cases['normal']['max_group_complete_ns']
    c = mechanism['cases']['prefix-w60']
    witness = c['message']
    account = mechanism['accounting']['prefix-w60']
    rows = []
    for n in names:
        x = cases[n]
        rows.append([n, f"{x['max_group_complete_ns']/1000:.3f}", f"{x['drain_ns']/1000:.3f}",
            f"{x['retransmitted_payload_bytes']:,}", f"{x['encapsulation_wire_bytes']:,}", f"{x['queue_peak_bytes']:,}"])
    mdtable = '\n'.join('| '+' | '.join(row)+' |' for row in rows)
    group_rows = '\n'.join('| '+w+' | '+' | '.join(f"{effects[w]['group_saved_ns'][str(i)]/1000:+.3f}" for i in range(4))+' |' for w in ('20', '60'))
    text = f"""# 真实MoE路由：补足成熟绕行基线后，还剩多少等待

{chr(10).join(summary)}

这是已有绕行方法的收益，不能算新协议创新。正常参考为{normal/1000:.3f}µs。prefix-w60距正常仍{effects['60']['prefix_minus_normal_ns']/1000:.3f}µs，距零延迟全局参考为{effects['60']['prefix_minus_instant_ns']/1000:+.3f}µs；这些参考差距不是已证明可消除的收益。

## 实验究竟在做什么

128个仿真GPU端点、20台交换机的Clos，4个交错EP32组，15份不同DeepSeek-R1-AWQ/MMLU真实专家选择文件，每份作为一个请求，组内4/4/4/3请求。256专家/组、8专家/rank，层3/4、两个单token Decode步。原选择重建消息与依赖，所有组在同一个原生RDMA ns-3时钟及网络状态下执行。计算为声明成本、payload为功能标记；不是完整671B、现代DeepEP实现、GPU实测或128卡满载。

同一128–132交换机边在4µs关闭，分别在24/64µs恢复。normal不注入故障；global等待60µs全局通知；local额外立即剔除原ECMP坏出口；prefix在原候选全失效时携带显式两跳段132→129→133，到133拆封后沿旧安全后缀到目标。instant为零延迟全局改表参考。所有条件都有17µs初始RTO的选择性重传、参考DCQCN及原ACK/集合门限，不因数据先到就提前释放。

只有4个新网络：normal、local-w60、prefix-w20、prefix-w60。前两个完整原始输出除加载库路径外与旧同条件一致；另外5行只读复用旧结果。各新网络一次，没有重复成功网络，没有构建或改平台。

## 完成时间与代价

| 条件 | 最晚任务µs | 网络排空µs | 源重传payload B | 封装逐跳B | 交换机队列峰值B |
|---|---:|---:|---:|---:|---:|
{mdtable}

“封装逐跳B”仅统计实际发送中的新增头部；总线上字节还受重传、路径跳数和反馈数量影响。队列峰值来自原生交换机BEgressQueue完整钩子记录，不包括NIC ACK队列，不是排队积分。物理及本地丢弃事件也不是端到端唯一丢包率。

各组相对同窗口local提前量（正数提前，负数变慢）：

| 故障窗口µs | 组0µs | 组1µs | 组2µs | 组3µs |
|---|---:|---:|---:|---:|
{group_rows}

所有组都可能共享受损链路，不预设其他组完全健康。变化同时含避免丢弃、绕行竞争和后续相位变化，不能把组间差直接说成排队干扰的因果收益。

## 用真实ACK解释此前的等待

同一个消息3983（1→65，14,464B），旧local-w60的DATA在26.170µs已到，但ACK因原候选只剩坏出口而丢弃，最终133.846µs确认。prefix-w60中，该消息在{witness['receive_ready_ns']/1000:.3f}µs接收完成、{witness['ack_ns']/1000:.3f}µs获得真实源ACK。

同消息确认提前{account['same_message_ACK_saved_ns']/1000:.3f}µs；该组首Dispatch实际提前{account['first_dispatch_gate_saved_ns']/1000:.3f}µs，后续组内跨度再变化{account['later_group1_span_saved_ns']/1000:+.3f}µs，共使组1提前{account['group1_finish_saved_ns']/1000:.3f}µs。保留所有并列末前驱，不把不同最终组的全局最大差归因给一条消息。

到达曲线固定同一个g1.w0首Dispatch，共{len(c['collective']['messages'])}消息、{c['collective']['payload_bytes']:,}B。实线按目的端真实RX与字节区间去重；虚线在消息收到完整源ACK后计入整条消息字节，不是逐字节ACK。原始包、重试、实际ACK路径及各门限末前驱见mechanism.json。

## 对创新方向意味着什么

先把“已有方法能做到什么”补足，再评价新方案。封装和绕行不是本轮创新；任务完成差也不能直接推成生产收益。下一步应从剩余实际丢失、源重试退避以及替代路径竞争中寻找可解释、可干预的等待，再检验MoE任务进度能否指导有限恢复资源分配。若成熟基线已解释主要空间，就收敛或放弃原假设，不能削弱基线保留收益。

项目内IPv4协议250携带实际38B封装，经过真实队列和链路序列化；不是标准RoCE/MPLS/TI-LFA实现。没有硬件故障检测、处理、路径分发与MTU碎片校准。两种本地策略均假设立即检测。没有256/512新网络、完整模型或硬件性能结论。HTML完成静态检查后交付，不声称真实浏览器渲染；PNG单独检查。
"""
    (out/'README.md').write_text(text)
    table = ''.join('<tr>'+''.join('<td>'+html.escape(v)+'</td>' for v in row)+'</tr>' for row in rows)
    body = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>真实MoE：成熟绕行基线与剩余等待</title>
<style>body{{font:16px/1.8 system-ui,sans-serif;color:#173149;background:#f6f8fb;max-width:1150px;margin:32px auto;padding:0 24px}}img{{width:100%;height:auto}}table{{width:100%;border-collapse:collapse;background:white}}td,th{{padding:9px;text-align:right;border-bottom:1px solid #dbe2ea}}td:first-child,th:first-child{{text-align:left}}.box{{padding:18px;background:#e4eef9;border-left:5px solid #286aa9}}a{{color:#2269ab}}pre{{white-space:pre-wrap;font:inherit;background:white;padding:20px}}</style>
<h1>补足成熟绕行后，还剩多少等待？</h1><p>Clos128 · 15份真实专家选择 · 四组EP32 · 选择性重传始终开启</p>
<div class="box">{''.join('<p>'+html.escape(x)+'</p>' for x in summary)}<p>以上是成熟方法的收益，不能算新协议创新。</p></div>
<h2>所有组的完成时间与重传代价</h2><img src="comparison.png" alt="九条件四组完成与源重传"><table><tr><th>条件</th><th>最晚任务µs</th><th>排空µs</th><th>源重传B</th><th>封装逐跳B</th><th>队列峰值B</th></tr>{table}</table>
<h2>数据到了，为何还在等？</h2><img src="arrival_curves.png" alt="同一个Dispatch的有效接收与源确认曲线">
<p><a href="README.md">详细说明</a> · <a href="analysis.json">统计</a> · <a href="verification.json">逐包核验</a> · <a href="mechanism.json">机制与曲线原始见证</a> · <a href="normal-compatible.json">正常核对</a> · <a href="local-w60-compatible.json">故障核对</a></p>
<h2>方法、结果及局限</h2><pre>{html.escape(text)}</pre></html>"""
    (out/'report.html').write_text(body)
    print(json.dumps(dict(published=str(out), new_networks=4, reused_conditions=5)))


if __name__ == '__main__':
    main()
