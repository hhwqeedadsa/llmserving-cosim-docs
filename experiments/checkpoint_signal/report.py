"""Publish checkpoint evidence with a stronger source-ACK timeout reference."""
import argparse
import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

def load(path):
    return json.loads(path.read_bytes())

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    a, network, watchdog = [load(args.analysis/name) for name in ('analysis.json', 'network.json', 'watchdog.json')]
    assert a['passed'] and network['passed'] and watchdog['passed']
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    assert not (out/'report.html').exists()
    for directory in ('figures', 'plans', 'minimal'):
        (out/directory).mkdir()
    for name in ('analysis.json', 'network.json', 'watchdog.json', 'decisions.csv'):
        shutil.copyfile(args.analysis/name, out/name)
    frozen = load(args.prepared/'frozen.json')
    if (out/'frozen.json').exists():
        assert load(out/'frozen.json') == frozen
    else:
        shutil.copyfile(args.prepared/'frozen.json', out/'frozen.json')
    names = ['first_dispatch', 'first_decode', 'complete_segment']
    chinese = dict(first_dispatch='首个Dispatch', first_decode='第一步Decode', complete_segment='完整两步')
    for name in names:
        shutil.copyfile(args.prepared/(name+'.json'), out/'plans'/(name+'.json'))
        for condition in ('normal', 'local'):
            shutil.copyfile(args.prepared/'observed'/f'{name}-{condition}-minimal.jsonl', out/'minimal'/f'{name}-{condition}.jsonl')
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'svg.hashsalt': 'checkpoint-signal-v1'})
    fig, (ax, reference) = plt.subplots(1, 2, figsize=(13, 4.3), gridspec_kw={'width_ratios': [1.5, 1]})
    for i, name in enumerate(names):
        item = a['checkpoints'][name]
        normal = item['conditions']['normal']['actual_complete_ns']/1000
        local = item['conditions']['local']['actual_complete_ns']/1000
        ax.plot([normal, local], [i, i], color='#c7d0d7', linewidth=3)
        ax.scatter(normal, i, color='#158876', s=65, label='Normal completion' if i == 0 else None)
        ax.scatter(local, i, color='#c26038', s=65, label='Local-fault completion' if i == 0 else None)
        ax.scatter(item['deadline_ns']/1000, i, color='#273c61', marker='|', s=350, linewidth=2,
                   label='Configured check time' if i == 0 else None)
    ax.set_yticks(np.arange(3), ['First Dispatch', 'First Decode', 'Whole segment'])
    ax.invert_yaxis()
    ax.set_xlabel('Actual original-control timeline (us)')
    ax.set_title('Earlier observations; no new recovery execution')
    ax.grid(axis='x', alpha=.2)
    ax.legend(fontsize=8, loc='upper center', bbox_to_anchor=(.5, -.18), ncol=2)
    values = [a['checkpoints']['first_dispatch']['deadline_ns']/1000,
        watchdog['references']['normal_calibrated']['conditions']['local']['first_nominal_deadline_ns']/1000,
        watchdog['references']['documented_example']['conditions']['local']['first_nominal_deadline_ns']/1000]
    reference.scatter(values, [0, 1, 2], color=['#273c61', '#158876', '#c26038'], s=65)
    for value, y in zip(values, [0, 1, 2]):
        reference.text(value+.04, y, f'{value:.3f} us', va='center', fontsize=9)
    reference.set_yticks([0, 1, 2], ['Stage checkpoint', 'Normal-fit source ACK', '20-us source ACK'])
    reference.invert_yaxis()
    reference.set_xlim(18.8, 21.8)
    reference.set_xlabel('Nominal eligibility time (us), not speedup')
    reference.set_title('A simple ACK timeout is only 30 ns later')
    reference.grid(axis='x', alpha=.2)
    fig.tight_layout()
    fig.savefig(out/'figures/signals.png', dpi=150, metadata={'Software': 'Matplotlib'})
    fig.savefig(out/'figures/signals.svg', metadata={'Date': None})
    svg = out/'figures/signals.svg'
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines())+'\n')
    plt.close(fig)
    rows = ''
    for name in names:
        item = a['checkpoints'][name]
        normal, local = (item['conditions'][condition] for condition in ('normal', 'local'))
        rows += f'<tr><td>{chinese[name]}</td><td>{normal["actual_complete_ns"]/1000:.3f}</td><td>{local["actual_complete_ns"]/1000:.3f}</td><td>{item["deadline_ns"]/1000:.3f}</td><td>已完成，暂不备用</td><td>未完成，可考虑备用</td></tr>'
    net_rows = ''.join(f'<tr><td>{"正常" if condition == "normal" else "局部故障"}</td><td>{n["completed_pack_nodes"]}/{n["initial_pack_nodes"]}</td><td>{n["posted_messages"]}</td><td>{n["receive_completed_messages"]}</td><td>{n["source_confirmed_messages"]}</td><td>{n["pending_source_confirmations"]}</td></tr>'
                       for condition, n in network['conditions'].items())
    pending = network['conditions']['local']['pending_messages']
    assert len(pending) == 3
    fit = watchdog['references']['normal_calibrated']['conditions']
    assert fit['normal']['count'] == 0 and fit['local']['count'] == 3
    advance = fit['local']['delay_from_phase_check_ns']
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>阶段进度有用，但已有消息超时也能早发现</title><style>body{{margin:0;background:#f1f5f8;color:#203342;font:16px/1.8 system-ui,sans-serif}}main{{max-width:1120px;margin:auto;padding:32px 20px 60px}}h1{{font-size:32px;line-height:1.4}}h2{{font-size:23px}}section{{background:white;padding:22px 26px;border-radius:12px;margin:20px 0}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:10px;border-bottom:1px solid #dae2e8;text-align:right;white-space:nowrap}}td:first-child,th:first-child{{text-align:left}}.scroll{{overflow:auto}}.note{{background:#fff2d8;border-left:4px solid #d89b34;padding:14px}}img{{width:100%;height:auto}}a{{color:#126985}}code{{background:#edf2f5;padding:2px 5px}}small{{color:#526678}}</style><main>
<small>2026-10-30 · 128端点Clos · 原网络只读分析 · 零新增网络、构建或GPU执行</small>
<h1>阶段进度有用，但已有消息超时也能早发现</h1>
<p>上一轮按完整任务设置127微秒门槛，备用来不及追上原任务。本轮发现：19微秒时已有通信阶段信号可区分这两条历史轨迹；但普通源端确认超时经过相同正常样本校准，在19.030微秒也能出现同样的等待信号，只晚{advance}纳秒。因此没有证据说明需要新的复杂检测机制。</p>
<section><h2>实际读了什么，没做什么</h2><p>仅复核已有正常、局部短故障两份无备用控制：128模拟端点Clos，四组EP32，目标4份/健康11份不同专家路由原件，原层3/4、连续两步Decode，原SR和参考DCQCN。局部仍是GPU28前20微秒DATA丢弃，原网络未修改或重跑。</p><p>准备器导出严格早于检查时刻的<code>observed/</code>；18次独立判断进程只获得初始计划和过去事件，不读取未来结果、故障或包路径；评价器之后再读取完整记录核对。本轮只判断“是否值得考虑备用”，不提交备用，不产生实际通知或加速结果。</p></section>
<section><h2>更早看到异常，具体早在哪里</h2><div class="scroll"><table><thead><tr><th>检查点</th><th>正常完成 µs</th><th>故障完成 µs</th><th>检查时刻 µs</th><th>正常判断</th><th>故障判断</th></tr></thead><tbody>{rows}</tbody></table></div>
<img src="figures/signals.png" alt="实际检查点完成时间，以及阶段检查与已有源端确认超时参考的时刻对比">
<p>门槛只从正常样本严格向上取整到下一个1微秒网格。首个Dispatch检查比完整任务检查提前108微秒，第一步检查提前61微秒。这些是<strong>判断时刻差，不能当推理加速</strong>。若在19或66微秒仍只看完整结果，正常任务也尚未完成，会被纳入备用候选；已有阶段完成标志能避免这两次正常候选。</p></section>
<section><h2>网络侧：不是还没开始计算，而是3条通信在等确认</h2><p>19微秒以前的回调显示，32个rank的首层pack均已完成，26条Dispatch消息均已实际post：</p><div class="scroll"><table><thead><tr><th>条件</th><th>已完成pack</th><th>已post消息</th><th>接收完成</th><th>源确认完成</th><th>尚未确认</th></tr></thead><tbody>{net_rows}</tbody></table></div>
<p>故障中未确认的是消息37/68/99，分别从rank4/8/12发往GPU28，各14,464字节，实际post均在1.030微秒；19微秒时接收与源确认都还未完成。这说明当前等待已经落在已提交通信上，但仅凭确认未到仍不能区别丢包、拥塞或确认路径排队，不能将超时直接当成丢包诊断。</p></section>
<section><h2>为什么更强的已有基线改变了研究判断</h2><p>仓库已有源端确认watchdog：按实际SEND post加声明期限，确认到达就取消。正常首层消息的最大post到确认跨度为{watchdog['normal_max_post_to_ack_ns']/1000:.3f}微秒，按相同严格取整规则得到18微秒期限；加1.030微秒post时刻，就是19.030微秒。正常26条均不满足超时条件，故障3条满足；与19微秒阶段检查只差{advance}纳秒。</p>
<p>现有文档中的20微秒<strong>示例参数</strong>得到21.030微秒，也只有故障3条满足条件。20微秒不是默认值，18微秒也不是p99或生产最优值。这里仅在旧回调上核对已有触发条件，原控制没有启用watchdog，所以不能写成“本轮实际发出3次通知”。当前完整备用模式也禁止直接并用watchdog，真实联动需要独立平台实现与验收。</p>
<p class="note">相对完整任务超时的108微秒提前量，不能全部归给新的阶段或网络语义。简单消息超时已经给出相近信号。继续研究必须回答如何在这些成熟信号之上减少无效备用、控制共享网络代价，或者在实际完成约束下选择更合适的恢复单位。</p></section>
<section><h2>一个完成标志为何已足够</h2><p>完整过去事件分别有1,782～9,422条；针对一个固定检查点，最小视图只需“已收到该完成回调”这一事实，正常记录1条、故障记录0条，六种检查均与全成员判断一致。当前全EP成员到齐后，以零协调时延同步完成所有GATE，所以一个固定rank的GATE完成即可代表这个已定义阶段。这是<strong>模型语义</strong>，不是新遥测压缩或生产监测成本节省。</p>
<p>缺少标志只在观测完整、时钟与身份可靠的前提下表示尚未完成；生产遥测丢失或传递延迟可能改变判断。原生OBSERVE是被动观察，不是可提交任务的DECIDE；管道等待不推进仿真时间，也没有模拟分布式监控协调成本。</p></section>
<section><h2>系统产出与验收</h2><p>交付了可重复运行的计划准备、严格过去前缀导出、独立判断、完整历史评价、网络回调解释和已有watchdog参考脚本。没有改平台或重传协议。两份原始网络公开rdma-check通过、全部文件SHA保持；18次判断和42项未来/同刻、重复、缺序、时钟倒退及身份篡改拒绝通过。判断输出和真假评价分开保存，示例最小输入可脱离路由数据做CPU演示。</p>
<p>仅一个确定性正常样本与一个已知局部故障，不是盲测、准确率或故障概率估计。真实的是专家选择，计算与BF16消息布局是声明，阶段协调是下界；没有完整671B、满载128卡、硬件校准、吞吐或新算法收益。图像与静态资源核查见最终验收，未做浏览器渲染。</p></section>
<section><h2>下一步的实际门槛</h2><p>先把普通源确认超时或已有进度标志与完整备用调度接起来，明确同一ns-3时刻的决策、一次触发、后续回退门槛、资源准入和真实KV释放；原图门限、ACK及迟到输家继续保留。以同一正常与故障输入，和只改固定启动时间的简单对照比较，再测目标、健康任务、实际额外字节和收尾。若简单已有基线足够，就不再为检测信号开发复杂方案。</p>
<p>早期检查通过只表示当前阶段已完成，不能永久禁用后续故障保护。较小恢复单位还须有实际通信消费、一次归并及迟到隔离依据。没有这些实现和实际网络对照，就不能报恢复收益或创新。</p></section>
<section><h2>证据和复现</h2><p><a href="analysis.json">检查点分析</a> · <a href="network.json">严格过去网络回调</a> · <a href="watchdog.json">已有超时参考</a> · <a href="decisions.csv">18次判断</a> · <a href="frozen.json">冻结输入</a> · <a href="receipt.json">执行收据</a> · <a href="resources.json">资源</a> · <a href="final-audit.json">最终核查</a> · <a href="README.md">结论</a> · <a href="../../README.md">复现入口</a> · <a href="../../protocol.md">事前方法</a> · <a href="../../watchdog_protocol.md">超时参考补充</a></p></section></main></html>'''
    (out/'report.html').write_text(page)
    print(json.dumps(dict(passed=True, scientific_figures=1, portable_minimal_examples=6, new_network_executions=0)))

if __name__ == '__main__':
    main()
