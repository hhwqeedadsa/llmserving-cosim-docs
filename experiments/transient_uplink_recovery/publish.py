"""Publish bounded task results and deduplicated first-Dispatch receive curves."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import shutil


def load(p):
    return json.loads(p.read_bytes())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, value):
    with p.open('x') as s:
        json.dump(value, s, ensure_ascii=False, indent=2); s.write('\n')


def curve(directory):
    r = load(directory/'result.json')
    wave = load(directory/'graphs/g1.w0/wave.json')
    ids = {m['id'] for m in wave['messages'] if m['layer'] == 0 and m['phase'] == 'dispatch'}
    messages = [m for m in r['service_transport']['messages'] if m['id'] in ids]
    assert len(messages) == len(ids)
    regions = defaultdict(list)
    for m in messages:
        regions[m['stream']].append((m['begin'], m['end'], m['dst']))
    seen = defaultdict(list)
    data = defaultdict(int)
    increments = []
    cap = r['packet_capture']
    for row in cap['events']:
        x = dict(zip(cap['columns'], row))
        if x['event'] != cap['event_codes']['rx'] or x['ip_protocol'] != 17:
            continue
        sid = x['message_id']
        for begin, end, dst in regions.get(sid, []):
            if x['node'] != dst:
                continue
            left, right = max(begin, x['sequence']), min(end, x['sequence']+x['data_payload_bytes'])
            if right <= left:
                continue
            new = right-left-sum(max(0, min(right, b)-max(left, a)) for a, b in seen[sid])
            assert new >= 0
            merged = []
            for a, b in sorted([*seen[sid], (left, right)]):
                if merged and a <= merged[-1][1]:
                    merged[-1] = merged[-1][0], max(b, merged[-1][1])
                else:
                    merged.append((a, b))
            seen[sid] = merged
            if new:
                data[x['time_ns']] += new
                increments.append(dict(packet=x, new_payload_bytes=new))
    confirmed = defaultdict(int)
    for m in messages:
        confirmed[m['ack_ns']] += m['bytes']
    def cumulative(values):
        total = 0
        points = [[0, 0]]
        for now, amount in sorted(values.items()):
            total += amount
            points.append([now, total])
        return points
    received, acked = cumulative(data), cumulative(confirmed)
    total = sum(m['bytes'] for m in messages)
    assert received[-1][1] == acked[-1][1] == total
    assert received[-1][0] == max(m['receive_ready_ns'] for m in messages)
    gate = next(g for g in r['service']['phase_completion']['gates'] if g['graph_id'] == 'g1.w0' and g['id'] == 'L0.dispatch')
    assert acked[-1][0] == gate['released_ns']
    return dict(graph='g1.w0', phase='L0.dispatch', messages=messages, payload_bytes=total,
        unique_received_curve_ns_bytes=received, fully_confirmed_message_curve_ns_bytes=acked,
        actual_RX_increments=increments, dispatch_gate_ns=gate['released_ns'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('stage', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    stage = a.stage.resolve()
    study = stage/'study'
    analysis = load(stage/'analysis/analysis.json')
    proof = load(stage/'verification.json')
    assert proof['passed'] and proof['analysis_sha256'] == sha(stage/'analysis/analysis.json')
    mechanism = load(stage/'mechanism.json')
    assert mechanism['passed']
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    for src, name in ((stage/'analysis/analysis.json', 'analysis.json'), (stage/'verification.json', 'raw_verification.json'),
                      (stage/'mechanism.json', 'mechanism.json'), (study/'compatibility.json', 'compatibility.json')):
        shutil.copyfile(src, out/name)
    curves = {n: curve(study/n) for n in ('global-w60', 'local-w60', 'instant-w60')}
    assert len({c['payload_bytes'] for c in curves.values()}) == 1
    save(out/'arrival_curves.json', curves)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    cases = analysis['cases']
    names = list(cases)
    labels = ['Normal', 'Global W20', 'Local W20', 'Instant W20', 'Global W60', 'Local W60', 'Instant W60']
    fig, axes = plt.subplots(1, 2, figsize=(13.2, 4.7), layout='constrained')
    for group in range(4):
        axes[0].plot(labels, [cases[n]['group_complete_ns'][str(group)]/1000 for n in names], marker='o', label='EP group '+str(group))
    axes[0].set_ylabel('Bounded task completion (us)')
    axes[0].set_title('Clos128 / 15 real routing files / SR 17us')
    axes[0].legend(fontsize=8, ncols=2)
    axes[1].bar(labels, [cases[n]['retransmitted_payload_bytes']/1024 for n in names])
    axes[1].set_ylabel('Retransmitted source payload (KiB)')
    axes[1].set_title('Physical outage W20/W60; declared route notifications')
    for ax in axes:
        ax.tick_params(axis='x', labelrotation=33)
        ax.grid(axis='y', alpha=.2)
    fig.savefig(out/'comparison.png', dpi=160)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(10.6, 4.8), layout='constrained')
    colors = ['#b85c30', '#2465a7', '#2b8658']
    for (name, c), color in zip(curves.items(), colors):
        for field, style, label in (('unique_received_curve_ns_bytes', '-', 'unique DATA received'),
                                    ('fully_confirmed_message_curve_ns_bytes', '--', 'fully ACKed messages')):
            points = c[field]
            ax.step([x[0]/1000 for x in points], [x[1]/1024 for x in points], where='post',
                    color=color, linestyle=style, label=name+' / '+label)
    ax.axvline(64, color='gray', alpha=.6, label='Physical link up: 64us')
    ax.set(xlabel='Simulation time (us)', ylabel='Effective payload (KiB)',
           title='Same first Dispatch: actual RX bytes vs fully confirmed messages')
    ax.grid(alpha=.2)
    ax.legend(fontsize=8, ncols=2)
    fig.savefig(out/'arrival_curves.png', dpi=160)
    plt.close(fig)
    table, mdtable = [], []
    for name, c in cases.items():
        values = [name, f"{c['max_group_complete_ns']/1000:.3f}", f"{c['drain_ns']/1000:.3f}",
                  f"{c['retransmitted_payload_bytes']:,}", str(c['loss_events'])]
        table.append('<tr>'+''.join('<td>'+v+'</td>' for v in values)+'</tr>')
        mdtable.append('| '+' | '.join(values)+' |')
    gain = analysis['effects']
    local_curve = curves['local-w60']
    received_us = local_curve['unique_received_curve_ns_bytes'][-1][0]/1000
    acked_us = local_curve['fully_confirmed_message_curve_ns_bytes'][-1][0]/1000
    explanation = f"""# 短时共享上行故障：收益与剩余等待

128端点Clos，15份不同R1-AWQ/MMLU真实专家选择，四组EP32，层3/4、两个单token Decode步。网络实际共享执行，GPU计算为声明成本、payload为功能标记。不是完整671B、现代DeepEP完整调用、生产硬件测量、大batch或128卡满载。

**20µs中断：local相对global使最晚任务提前{gain['20']['max_group_saved_ns']/1000:.3f}µs（{gain['20']['relative_global_percent']:.2f}%），收回故障额外等待的{gain['20']['fault_penalty_recovered_percent']:.2f}%。60µs中断：只提前{gain['60']['max_group_saved_ns']/1000:.3f}µs（{gain['60']['relative_global_percent']:.2f}%），收回{gain['60']['fault_penalty_recovered_percent']:.2f}%。** 同样的本地处理对两个窗口收益差异很大；这些是成熟方法的结果，不是新协议贡献。

| 条件 | 最晚任务µs | 网络排空µs | 重传payload B | 物理及本地丢弃事件 |
|---|---:|---:|---:|---:|
{chr(10).join(mdtable)}

normal无故障；w20/w60分别在4–24/4–64µs关闭同一共享上行。global/local的关闭和恢复通知都延迟60µs；local额外立即过滤两端旧等价出口。instant为零延迟全局改路。它们都有SR与参考DCQCN，任务图和完成条件相同。instant没有实际控制成本，不是生产可部署效果或数学下界。四组都可能共享受损链路，不预设任何组“健康”。

## 为什么数据到了，任务还没完成

事后追踪local-w60中组1首Dispatch的实际最后前驱：消息3983，1→65，14,464B。数据26.170µs已完整接收，ACK在Agg132于29.844/30.174/63.844/63.954µs被本地丢弃。原最短路回到GPU1只有经ToR128的出口；剔除后候选为空，不能生成另一条绕路。记录中的port5是ACK入端口，不是故障出口；失败边为Agg132 port1–ToR128 port17。

链路64µs恢复，原待发ACK不会复活。发送方在18.754/52.754/120.754µs超时重传；恢复后等待下一轮56.754µs，再经13.092µs收到真实ACK，合计69.846µs。最后一次ACK失败距上线只有46ns，是此固定样本的时序事实，不能推广为通用阈值。

instant同一消息31.995µs确认，但该组首Dispatch还等其他消息至35.565µs；所以同消息确认差101.851µs减去3.570µs余量，得到首Dispatch差98.281µs。后续执行跨度差−0.168µs，组1最终差98.113µs。全局最晚任务差95.823µs对应不同最终组，不能混用。逐包路径、源确认和门限见[机制记录](mechanism.json)。这是已有策略之间的实际执行差异，不是已经实现了消除这些等待的新方案。

## 集合通信的有效数据到达曲线

选择同一个g1.w0首Dispatch，29条消息共419,456B。实线逐包统计真实目的端RX，并按stream与字节区间去重；重传/重复包不增加有效数据。虚线只在消息收到完整源ACK时计入其字节，因此是“已完整确认消息的有效字节”，不是逐字节ACK进展。local-w60全体数据{received_us:.3f}µs已到，全部消息{acked_us:.3f}µs才确认；保留原Dispatch门限，没有用接收时间冒充可安全完成或释放。

见[到达曲线](arrival_curves.png)、[逐包增量](arrival_curves.json)和[逐组对照](comparison.png)。固定选择是事后机理诊断，不用于按结果修改故障或负载。

## 收益空间怎样理解

local-w60距normal仍109.033µs，距instant仍95.823µs；这些只是参考差距，不能全部承诺为可优化收益。当前大部分差异已能由成熟全局改路绕开ACK损失解释；下一步先补足候选为空时的现成局部绕路/ACK返回路径基线，随后才判断MoE进度感知恢复是否有额外价值。不能把重新实现绕路、缩短RTO或恢复触发重试写成论文创新。

七个新网络各一次，无重跑；独立检查108,864次物理发送、5,446条消息的实际源ACK、所有原图/路由和buffer排空。正常原始结果与旧件仅加载库路径不同。独立构建ELF不逐字节相同，固定原生版本、串行overlay和规范化后的442条编译命令相同；见[构建核查](compatibility.json)。完整输入/源码及预算留在运行目录；原路由未提交。接收到达状态模型没有帧中间CRC/光模块训练、分布式控制报文和硬件校准，未新增256/512实际执行证据。总体研究目标仍active。
"""
    (out/'README.md').write_text(explanation)
    body = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>短时共享上行故障：收益与边界</title>
<style>body{{font:16px/1.8 system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 24px;background:#f6f8fb;color:#183047}}img{{width:100%;height:auto}}table{{border-collapse:collapse;width:100%;background:white}}th,td{{padding:9px;border-bottom:1px solid #ddd;text-align:right}}td:first-child,th:first-child{{text-align:left}}.box{{background:#e4edf8;padding:18px;border-left:5px solid #2868a9}}a{{color:#2465a7}}</style>
<h1>短时故障下，少重传不等于等比例提速</h1><p>128端点Clos · 15份真实R1专家路由 · 四组EP32 · 两层两个Decode步 · SR始终开启</p>
<div class="box">20µs中断：本地避障使最晚任务提前33.699µs，降低18.58%，收回故障额外等待73.85%。<br>60µs中断：少重传237,208B，但最晚任务只提前3.556µs，降低1.43%，收回3.16%。本轮是成熟方法对照，尚无新协议收益。</div>
<h2>同一输入的七个实际运行</h2><p>w20/w60表示中断持续时间；global/local均延迟60µs通知，local立即过滤旧等价出口；instant立即全局更新，是声明的理想参考。</p>
<table><tr><th>条件</th><th>最晚任务µs</th><th>排空µs</th><th>重传payload B</th><th>丢弃事件</th></tr>{''.join(table)}</table><img src="comparison.png" alt="四组任务完成时间与重传字节对照">
<h2>逐包看到：数据已到，ACK还没回来</h2><p>同一首Dispatch的29条消息共419,456B。实线统计接收端逐包到达并去重；虚线统计已经完整源确认的消息字节。local-w60全体数据{received_us:.3f}µs到达，全部消息{acked_us:.3f}µs才确认。</p><img src="arrival_curves.png" alt="集合通信累计有效DATA到达与完整消息确认曲线">
<p>其中1→65消息26.170µs已到，但ACK在聚合交换机多次丢失：本地剔除后没有原有等价出口。链路64µs上线，发送方120.754µs才再次重传，133.846µs收到确认。恢复后56.754µs等重试＋13.092µs通信＝69.846µs。该确认卡住组1首Dispatch，原门限和缓冲要求均保留。</p>
<h2>研究价值与下一步</h2><p>60µs条件本地策略距正常109.033µs、距即时全局参考95.823µs，均为参考差距，不能当成保证收益。先完善现成的局部绕路与ACK返回路径基线，再检验MoE进度信息是否能提供额外收益；重传、缩短超时或普通绕路本身不是创新。</p>
<p>边界：真实的是专家选择；计算为声明值、内容为功能标记。不是完整671B、现代DeepEP完整执行、硬件校准或128卡满载。七次网络各一次，逐跳108,864次物理发送及5,446条消息源ACK已独立核查；网页仅静态检查，不声明浏览器渲染验收。</p>
<p><a href="README.md">详细解释</a> · <a href="analysis.json">全部指标</a> · <a href="arrival_curves.json">逐包到达增量</a> · <a href="mechanism.json">ACK机理</a> · <a href="raw_verification.json">原始核验</a> · <a href="compatibility.json">构建核查</a></p></html>"""
    (out/'report.html').write_text(body+'\n')
    print(json.dumps(dict(published=True, output=str(out),
        collective_payload_bytes=419456, local_all_received_us=received_us, local_all_ACKed_us=acked_us)))


if __name__ == '__main__':
    main()
