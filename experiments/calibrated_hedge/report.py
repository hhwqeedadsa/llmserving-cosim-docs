"""Build a portable static report and scientific figures from saved evidence."""
import argparse
import html
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
    parser.add_argument('--frozen', type=Path, required=True)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    analysis = load(args.analysis/'analysis.json')
    mechanism = load(args.analysis/'mechanism.json')
    assert analysis['passed'] and mechanism['passed']
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    assert not (out/'report.html').exists()
    if not (out/'frozen.json').exists():
        shutil.copyfile(args.frozen, out/'frozen.json')
    if not (out/'calibration.json').exists():
        (out/'calibration.json').write_text(json.dumps(load(args.frozen)['calibration'], indent=2)+'\n')
    assert load(out/'frozen.json') == load(args.frozen)
    (out/'figures').mkdir()
    (out/'metrics').mkdir()
    for name in ('analysis.json', 'mechanism.json'):
        shutil.copyfile(args.analysis/name, out/name)
    for path in args.analysis.glob('*.csv'):
        shutil.copyfile(path, out/'metrics'/path.name)
    cases = analysis['cases']
    order = [policy+'-'+condition for condition in ('normal', 'local')
             for policy in ('control', 'early', 'calibrated')]
    labels = [c.replace('control', 'No backup').replace('early', '70 us').replace('calibrated', '127 us') for c in order]
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'svg.hashsalt': 'normal-calibrated-hedge-v1'})
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), gridspec_kw={'width_ratios': [1.65, 1]})
    positions = np.arange(6)
    for shift, key, label, color in [(-.23, 'target', 'Target result', '#126985'),
                                   (0, 'healthy', 'Healthy result', '#19a285'),
                                   (.23, 'drain', 'Network drained', '#dc8d27')]:
        values = [(cases[c]['network_drained_ns'] if key == 'drain' else cases[c]['result_ready_ns'][key])/1000 for c in order]
        axes[0].barh(positions+shift, values, height=.21, label=label, color=color)
    axes[0].set_yticks(positions, labels)
    axes[0].invert_yaxis()
    axes[0].set_xlabel('Time from simulation start (us)')
    axes[0].set_title('Same 128-endpoint Clos, same 15 source routes')
    axes[0].legend(loc='upper center', bbox_to_anchor=(.5, -.14), fontsize=8, ncol=3)
    axes[0].grid(axis='x', alpha=.18)
    for shift, metric, label, color in [(-.16, 'unique_payload_bytes', 'Effective payload', '#126985'),
                                     (.16, 'declared_compute_work_ns', 'Declared compute', '#19a285')]:
        values = [(cases[c][metric]/cases['control-'+c.split('-')[1]][metric]-1)*100 for c in order]
        axes[1].barh(positions+shift, values, height=.30, label=label, color=color)
    axes[1].set_yticks(positions, labels)
    axes[1].invert_yaxis()
    axes[1].set_xlabel('Increase over matched no-backup case (%)')
    axes[1].set_title('Cost is not business-time savings')
    axes[1].legend(loc='upper center', bbox_to_anchor=(.5, -.14), fontsize=8)
    axes[1].grid(axis='x', alpha=.18)
    fig.tight_layout()
    save_figure(fig, out/'figures/comparison')
    fig, ax = plt.subplots(figsize=(12, 3.8))
    race = mechanism['actual_race']
    groups = [('Original', race['primary_waves']), ('Backup', race['replica_waves'])]
    for y, (name, waves) in enumerate(groups):
        for i, wave in enumerate(waves):
            start, stop = wave['submitted_ns']/1000, wave['finished_ns']/1000
            ax.barh(y, stop-start, left=start, height=.45, color=('#126985', '#19a285')[i])
            ax.text((start+stop)/2, y, f'Decode {i+1}', ha='center', va='center', color='white', fontsize=9)
    for value, label, color in [(race['backup_start_ns']/1000, 'Backup starts', '#7d649d'),
                                (race['primary_finished_ns']/1000, 'Result ready', '#c26038')]:
        ax.axvline(value, color=color, linestyle='--', linewidth=1.3, label=label)
    ax.set_yticks([0, 1], [name for name, _ in groups])
    ax.invert_yaxis()
    ax.set_xlim(0, max(race['backup_finished_ns']/1000+10, 260))
    ax.set_xlabel('Local fault case: actual simulation time (us)')
    ax.set_title('Backup restarts both steps; the already-running original wins')
    ax.legend(loc='lower left', bbox_to_anchor=(0, 1.01), ncol=2, fontsize=8)
    ax.grid(axis='x', alpha=.18)
    fig.tight_layout()
    save_figure(fig, out/'figures/race')
    rows = ''
    policy_names = {'control': '不启动备用', 'early': '旧门槛70µs', 'calibrated': '校准门槛127µs'}
    for name in order:
        policy, condition = name.split('-')
        c = cases[name]
        rows += f'<tr><td>{"正常" if condition == "normal" else "局部故障"}</td><td>{policy_names[policy]}</td><td>{c["result_ready_ns"]["target"]/1000:.3f}</td><td>{c["result_ready_ns"]["healthy"]/1000:.3f}</td><td>{c["network_drained_ns"]/1000:.3f}</td><td>{c["unique_payload_bytes"]:,}</td><td>{"原任务" if c["call_winners"]["target"] == "primary" else "备用"}</td></tr>'
    ack_rows = ''.join(f'<tr><td>{m["message"]}</td><td>{m["receive_change_ns"]}</td><td>{m["ack_change_ns"]}</td><td>{m["baseline_phase_slack_ns"]}</td><td>{m["phase_release_change_ns"]}</td></tr>'
                       for m in mechanism['changed_healthy_messages'])
    links = ''.join(f'<li><a href="metrics/{html.escape(p.name)}">{html.escape(p.name)}</a></li>' for p in sorted((out/'metrics').glob('*.csv')))
    effect = analysis['effects']['calibrated-local']
    pct = effect['extra_effective_bytes']/cases['control-local']['unique_payload_bytes']*100
    compute = effect['extra_declared_compute_ns']/cases['control-local']['declared_compute_work_ns']*100
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>先校准备用门槛，再谈优化收益</title><style>
body{{margin:0;background:#f2f5f8;color:#1b2d3b;font:16px/1.75 system-ui,sans-serif}}main{{max-width:1120px;margin:auto;padding:32px 20px 64px}}h1{{font-size:32px;line-height:1.3}}h2{{font-size:23px}}section{{background:white;padding:22px 26px;border-radius:12px;margin:20px 0}}.note{{background:#fff2d8;padding:14px;border-left:4px solid #dc8d27}}.lead{{font-size:19px}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:10px;border-bottom:1px solid #dce3e9;text-align:right;white-space:nowrap}}td:first-child,th:first-child{{text-align:left}}img{{width:100%;height:auto}}a{{color:#126985}}code{{background:#edf2f5;padding:2px 5px}}small{{color:#526678}}</style>
<main><small>2026-10-30 · 128模拟端点Clos · 正常样本校准 · 两个新增网络，各执行一次</small>
<h1>先校准备用门槛，再谈优化收益</h1>
<p class="lead">把备用从70微秒改为正常样本校准的127微秒后，正常误触发消失；本次局部故障中，完整备用没有缩短目标等待，却增加通信与收尾时间。这是成熟机制的基线检查，没有证明新算法收益。</p>
<section><h2>这次只改变什么</h2><p>原来正常的两步目标就需要126.626微秒，70微秒启动备用过早。本轮只读取这份正常样本，按1微秒向上取整，固定为127微秒，留374纳秒余量，再执行正常与故障对照。规则在新执行前提交，未扫描门槛或依据故障结果改参。故障旧结果此前已知，不能称盲测或独立留出集。</p>
<p>正常normal不额外注入故障；local仅在前20微秒丢弃到达GPU28的DATA，控制包保留，实际仍丢6包。两者均使用选择性重传及参考DCQCN。control/treatment是策略，normal/local是网络条件。</p>
<p class="note">只有一个确定性正常样本，127微秒不是p99或生产最优门槛，374纳秒余量不代表实际部署稳健性。取整是实验规则，不是创新，也不是硬件时钟精度。</p></section>
<section><h2>收益和代价放在一起看</h2><div class="scroll"><table><thead><tr><th>网络</th><th>策略</th><th>目标 µs</th><th>健康 µs</th><th>排空 µs</th><th>有效字节</th><th>目标赢家</th></tr></thead><tbody>{rows}</tbody></table></div>
<img src="figures/comparison.png" alt="六条件结果时延与相对通信计算开销">
<p>校准后的正常条件与control的完整逐包和原始队列记录完全相同；备用未开始，目标、健康和网络收尾时间也完全相同。已预留的备用KV仍真实保留到跳过时释放，不能称零备用内存成本。</p>
<p>局部故障下目标收益为<strong>{effect['target_saved_ns']/1000:.3f}微秒</strong>，健康完成不变；总有效载荷增加{effect['extra_effective_bytes']:,}字节（{pct:.2f}%），声明计算增加{compute:.2f}%，网络排空晚{effect['network_drain_change_ns']/1000:.3f}微秒。本报告三种局部故障策略各实际丢6包，重传43,392字节。有效载荷与重传分列，不能把已有重传当新策略工作。</p>
<p>旧70微秒条件只读复用：局部目标少等1.820微秒，但正常也启动、健康受拖累。校准后未保留这点目标收益，因此不能以旧门槛的结果证明新恢复机制有效。</p></section>
<section><h2>为什么备用追不上</h2><img src="figures/race.png" alt="故障原任务与127微秒启动的完整两步备用实际执行时间线">
<p>备用127微秒开始。事后记录显示，原任务还需{race['original_remaining_until_result_ns']/1000:.3f}微秒完成；备用从头做两步，实际用了{race['backup_execution_ns']/1000:.3f}微秒，直到{race['backup_finished_ns']/1000:.3f}微秒才结束。原任务先交付结果，已开始的输家继续执行、确认和清理，不能直接删掉尾部流量。原任务8个阶段的释放时刻均与control一致。</p>
<p>这是实际竞争结果的回溯解释。原任务剩余时间、未来ACK和排队路径均不进入在线策略；这些数据不能冒充策略在127微秒已知的信息，也不证明所有更晚门槛或其他负载都无效。</p></section>
<section><h2>有包级干扰，为什么健康任务没有变慢</h2><p>4,264次健康DATA包的交换机经过全部配对，无缺失。5处驻留发生变化，总变化仅410纳秒，其中包含一处减少37纳秒。这个累计包指标不能直接当作业务时延。</p>
<p>另外3条健康消息的数据接收时刻没有改变，但源端ACK晚到。按实际ACK包的发送、逐跳接收与转发记录核对，延迟分别全部来自ACK路径驻留增加，ACK起点发送时刻相同：</p>
<div class="scroll"><table><thead><tr><th>消息</th><th>数据接收增量 ns</th><th>ACK增量 ns</th><th>距阶段结束原余量 ns</th><th>阶段结束增量 ns</th></tr></thead><tbody>{ack_rows}</tbody></table></div>
<p>这些确认原本就不是决定阶段结束的最晚依赖，延迟没有耗尽余量。各rank局部到达时刻有变化，健康任务全部8个阶段的释放时刻及最终完成时间仍相同。因此“网络中有排队”不能直接推出“能优化同等数量的推理等待”。</p></section>
<section><h2>下一步怎样决定做什么</h2><p>本轮排除了“只把完整备用门槛调好，就能在该短故障片段中显著获益”的乐观预期。当前对照下，直接依靠既有选择性重传即可得到相同目标和健康完成时间，同时省去整份额外备用工作；不能把这个选择包装成新算法。</p>
<p>继续研究前，应先用已有记录评估在线可见进度能否识别无效重执行，并与简单固定延迟、资源限额、进度感知等已有方法比较；如果只是修正规则或已完成阶段信息就能解释收益，应照实报告。若转向较小恢复单位，须先有真实通信消费/一次归并/迟到隔离的接口依据，不能绕过原collective门限制造加速。新请求持续到达、代表性故障分布和GPU校准仍是待补项。</p></section>
<section><h2>系统、来源和证据边界</h2><p>本轮使用已实现的多步完整备用平台，没有新增后端算法、编译或依赖改动。两次公开rdma-run均成功；旧四份网络逐文件SHA保持，独立核对原件路由矩阵、计算、门限、包、ACK、原生定时/决策与KV。首次离线解释脚本把“阶段释放相同”误写成“每rank到达也相同”而失败，已修正为保留实际到达、核对最大释放时刻；原失败日志保留，未重跑网络。</p>
<p>15份原件中4份有固定远端修订，新增11份仅目录用户确认及本机SHA，远端内容版本未知。128端点、四组EP32、每rank每层8专家；只使用原层3/4、两个Decode入口。专家选择是真实数据，消息是前端投影和ns-3模拟包，计算、BF16布局与现成专家/KV副本均为声明，不能称完整671B实测、满载128卡、p99、吞吐或经济收益。</p>
<p>两幅科学图使用已有Matplotlib生成并另存SVG/PNG；网页无外部脚本，可本地打开。浏览器渲染状态与资源、哈希核查见最终验收。</p></section>
<section><h2>可复查材料</h2><p><a href="analysis.json">六条件独立分析</a> · <a href="mechanism.json">ACK与任务竞争机制</a> · <a href="calibration.json">门槛来源</a> · <a href="frozen.json">事前固定输入</a> · <a href="receipt.json">执行收据</a> · <a href="resources.json">资源核算</a> · <a href="final-audit.json">最终核查</a> · <a href="README.md">结论</a> · <a href="../../README.md">复现入口</a> · <a href="../../protocol.md">事前方法</a></p><details><summary>逐包和汇总CSV</summary><ul>{links}</ul></details></section></main></html>'''
    (out/'report.html').write_text(page)
    print(json.dumps(dict(passed=True, plots=2, csv_files=len(list((out/'metrics').glob('*.csv'))))))

def save_figure(figure, path):
    figure.savefig(path.with_suffix('.png'), dpi=150, metadata={'Software': 'Matplotlib'})
    figure.savefig(path.with_suffix('.svg'), metadata={'Date': None})
    svg = path.with_suffix('.svg')
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines())+'\n')
    plt.close(figure)

if __name__ == '__main__':
    main()
