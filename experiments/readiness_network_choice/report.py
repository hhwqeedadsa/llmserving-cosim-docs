"""Report decisions, parameter benefit and compute/communication boundaries."""
import argparse
from html import escape
from pathlib import Path
from experiments.compact_network_choice.evaluate import modern_metrics
from experiments.readiness_network_choice.prepare import PROFILES, CONDITIONS, load, save


def report(stage, output):
    output.mkdir()
    ledger = load(stage/'execution.json')
    verification = load(stage/'verification.json')
    assert verification['passed']
    profiles = {}
    for profile in PROFILES:
        prediction = load(stage/'predictions'/(profile+'.json'))
        metrics = {name: modern_metrics(stage/'truth'/profile/name/'modern') for name in CONDITIONS}
        times = {name: row['application_ns'] for name, row in metrics.items()}
        best = min(times.values())
        evaluations = {}
        for method, choice in prediction['choices'].items():
            evaluations[method] = dict(**choice, actual_best=[n for n in CONDITIONS if times[n] == best],
                regret_fraction=(times[choice['selected']]-best)/best,
                maximum_absolute_error_ns=max(abs(prediction['predictions'][n]['methods'][method]['estimated_application_ns']-times[n])
                                              for n in CONDITIONS),
                errors={n: (prediction['predictions'][n]['methods'][method]['estimated_application_ns']-times[n])/times[n]
                        for n in CONDITIONS})
        profiles[profile] = dict(expert_durations_ns=prediction['expert_durations_ns'], metrics=metrics,
            evaluations=evaluations, network_improvement_fraction={n: (times['baseline']-times[n])/times['baseline']
            for n in CONDITIONS}, core_absolute_improvement_ns=times['baseline']-times['core'],
            expert_max_fraction={n: max(prediction['expert_durations_ns'])/times[n] for n in CONDITIONS})
    contrasts = {}
    for suffix in ('1', '10'):
        balanced, routed = profiles['balanced'+suffix], profiles['routed'+suffix]
        contrasts[suffix] = {}
        for name in CONDITIONS:
            balanced_experts = verification['audits']['balanced'+suffix+'-'+name]['expert']['ranks']
            routed_experts = verification['audits']['routed'+suffix+'-'+name]['expert']['ranks']
            assert [r['start_ns'] for r in balanced_experts] == [r['start_ns'] for r in routed_experts]
            assert all(r['queued_ns'] == 0 for r in balanced_experts+routed_experts)
            application_delta = routed['metrics'][name]['application_ns']-balanced['metrics'][name]['application_ns']
            compute_delta = max(routed['expert_durations_ns'])-max(balanced['expert_durations_ns'])
            completion_delta = max(r['completed_ns'] for r in routed_experts)-max(r['completed_ns'] for r in balanced_experts)
            contrasts[suffix][name] = dict(application_increase_ns=application_delta, max_compute_increase_ns=compute_delta,
                latest_expert_completion_increase_ns=completion_delta, latest_rank_change_offset_ns=completion_delta-compute_delta,
                post_last_expert_tail_change_ns=application_delta-completion_delta,
                balanced_latest_rank=max(balanced_experts, key=lambda r: r['completed_ns'])['rank'],
                routed_latest_rank=max(routed_experts, key=lambda r: r['completed_ns'])['rank'],
                dispatch_and_expert_starts_unchanged=True)
    gap = any(p['evaluations']['link_phase']['regret_fraction'] > .05 for p in profiles.values())
    all_link_choices_correct = all(p['evaluations']['link_phase']['regret_fraction'] == 0 for p in profiles.values())
    summary = dict(profiles=profiles, paired_contrasts=contrasts,
        meaningful_link_choice_gap=gap, all_link_choices_correct=all_link_choices_correct, decision_regret_threshold=.05,
        full_graph_equivalence_count=len(verification['audits']), network_attempts=sum(r['network'] for r in ledger['records']),
        hardware_calibrated=False, new_MoE_semantic_benefit_proven=False, simulator_speedup_proven=False,
        costs_declared_not_measured=True, independent_holdout=False)
    save(output/'summary.json', summary)
    lines = ['# 专家就绪差异与网络配置选择', '',
        '八份真实MMLU路由，各取第一个Decode token、第3层；EP4、总batch8、E256/Top8/H7168/BF16。四个活动rank、同plane双网卡共享Agg/Core，一个持续原生ns-3网络时钟。激活为完整尺寸标记字节，MRT1为功能事务，计算/provider为声明成本，没有GPU执行或硬件校准。', '',
        '## 先看结果', '',
        ('逐链路阶段参考出现了超过5%的选择后悔值；须进一步分辨原因，不能据此宣称新方法。' if gap else
         '四种专家耗时条件下，普通逐链路阶段参考均未出现超过5%的配置选择后悔值。本轮没有建立“需要新增MoE语义才能选对网络”的证据。'), '',
        f"实际执行{summary['network_attempts']}次网络；{summary['full_graph_equivalence_count']}组现代调用与冻结完整图行为等价。完整图从各条件baseline执行提取，不是无执行成本的输入编译。", '',
        '| 计算条件 | 各rank耗时 μs | 网络 | 应用完成 μs | 相对baseline改善 | 源释放 μs | 排空 μs |',
        '|---|---|---|---:|---:|---:|---:|']
    for profile, row in profiles.items():
        durations = '/'.join(f'{n/1000:g}' for n in row['expert_durations_ns'])
        for name, metric in row['metrics'].items():
            lines.append(f"| {profile} | {durations} | {name} | {metric['application_ns']/1000:.3f} | {100*row['network_improvement_fraction'][name]:.2f}% | {metric['all_events_ns']/1000:.3f} | {metric['drain_ns']/1000:.3f} |")
    lines += ['', 'baseline=接入100G/核心25G；access=200G/25G；core=100G/50G。传播均1μs。升级资源不同，上表是配置敏感性，不是同成本算法收益、可消除上界或商业ROI。', '',
        f"核心升级节省的绝对时间为{min(r['core_absolute_improvement_ns'] for r in profiles.values())/1000:.3f}～{max(r['core_absolute_improvement_ns'] for r in profiles.values())/1000:.3f}μs，基本保持。相对改善24.02%～7.78%的差别主要来自计算增加后总时延分母变大。", '',
        '## 简单模型是否会选错', '', '| 条件 | 模型 | 选择 | 实际最优 | 后悔值 | 最大绝对相对时延误差 | 最大绝对时延误差 μs |',
        '|---|---|---|---|---:|---:|---:|']
    for profile, row in profiles.items():
        for method, ev in row['evaluations'].items():
            lines.append(f"| {profile} | {method} | {ev['selected']} | {','.join(ev['actual_best'])} | {100*ev['regret_fraction']:.2f}% | {100*max(map(abs,ev['errors'].values())):.2f}% | {ev['maximum_absolute_error_ns']/1000:.3f} |")
    lines += ['', '所有参考都知道专家耗时向量最大值。volume只计端点总数据字节；endpoint_phase加入七阶段/帧/传播；nic_phase拆到绑定网卡；link_phase进一步保留共享有向链路。后三者仍是普通服务量近似，不能把排序正确当时延精确。完整图保留全部本地工作、依赖和基线动态绑定，不含参考时刻。', '',
        '逐链路参考绝对误差仍约11.3～11.7μs；长计算下相对误差减小主要来自分母增大，不能称预测能力提高。', '',
        '## 计算与网络如何共同影响结果', '',
        '真实选择对应四个专家分区的贡献数为28/10/10/16。均分和按路由条件总计算耗时相同，但最长单卡耗时不同；线性贡献计时只是敏感性假设，不能当作真实GPU的专家热点计算成本。', '',
        '| 系数 ns/贡献 | 网络 | 应用增加 μs | 最长计算增加 μs | 最晚专家完成增加 μs | 完成者变化偏移 μs | 后续尾段变化 μs |',
        '|---|---|---:|---:|---:|---:|---:|']
    for suffix, rows in contrasts.items():
        for name, row in rows.items():
            delta, maximum = row['application_increase_ns'], row['max_compute_increase_ns']
            lines.append(f"| {int(suffix)*1000} | {name} | {delta/1000:.3f} | {maximum/1000:.3f} | {row['latest_expert_completion_increase_ns']/1000:.3f} | {row['latest_rank_change_offset_ns']/1000:.3f} | {row['post_last_expert_tail_change_ns']/1000:.3f} |")
    lines += ['', '配对Dispatch与专家开始时刻完全相同，没有本地专家排队。均分条件最后完成的是rank3，按路由条件变为rank0；开始时刻不同，使最晚完成的增量略小于耗时向量最大值的增量。表中把差额精确分成这项偏移与最后专家完成后的尾段变化；尾段包含控制屏障、通信等，不能单独归为拥塞或称可消除收益。', '',
        '计算增加本来就会抬高完成时间；真正的研究问题是能否在相同资源和正确性下，比已有强方法减少关键等待。', '',
        '## 证据与后续取舍', '',
        f"独立审计{len(verification['audits'])}组实际物理包、NIC路径、串行传播、帧负载与队列积分；逐卡核对专家工作和Dispatch/Combine依赖。拒绝{len(verification['rejected_corruptions'])}类篡改，移除truth后的四份预测逐字节一致。", '',
        '此次没有新调度或网络算法，不证明两项论文创新。若逐链路参考已足够选对，不能继续靠更多同类小场景把输入摘要包装成创新；后续应先确定实际配置决策缺口或可省的宿主重复处理，并验证成本、误差和留出泛化。', '',
        '范围只覆盖单层小样本、三种健康网络与声明耗时。不能推出丢包恢复、其他GPU、完整R1推理、千卡或生产性能。未提高预算或追加网络条件。']
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    # The HTML contains the same computed result tables, with no external assets.
    tables, current = [], []
    for line in lines:
        if line.startswith('|'):
            current.append(line)
        elif current:
            tables.append(current); current = []
    if current:
        tables.append(current)
    rendered = []
    for table in tables:
        rows = [r.strip('|').split('|') for i, r in enumerate(table) if i != 1]
        rendered.append('<div class="scroll"><table>'+''.join('<tr>'+''.join(
            f'<{"th" if i == 0 else "td"}>{escape(cell.strip())}</{"th" if i == 0 else "td"}>'
            for cell in row)+'</tr>' for i, row in enumerate(rows))+'</table></div>')
    conclusion = ('存在超过5%的逐链路选择缺口，需要机制诊断。' if gap else
                  '逐链路模型全部选对；本轮没有证明新增语义的决策收益。' if all_link_choices_correct else
                  '逐链路模型有误选，但未超过事前5%判据；须区分小误差与有意义的收益。')
    html = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
    html += '<title>专家就绪与网络选择</title><style>body{font:16px/1.7 system-ui,sans-serif;background:#f5f7fa;color:#172638;max-width:1150px;margin:auto;padding:28px}h1{line-height:1.3}.card{background:white;padding:22px;border-radius:12px;margin:20px 0}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;white-space:nowrap;font-size:14px}th,td{padding:9px 12px;border-bottom:1px solid #e1e6ed;text-align:left}th{background:#eaf1f7}.note{color:#506477}</style>'
    html += '<h1>专家就绪差异，会让网络方案选错吗？</h1><div class="card"><b>'+escape(conclusion)+'</b><p>八份真实路由 · 四个活动rank · 单层Decode · 声明计算成本</p><p>24次小规模网络执行，12组完整行为等价对照；没有GPU性能校准。</p></div>'
    labels = ('配置变化的实际结果', '预测方法与误选代价', '均分与按路由计算的差别')
    for label, table in zip(labels, rendered):
        html += '<section class="card"><h2>'+label+'</h2>'+table+'</section>'
    html += '<section class="card"><h2>这些数字说明什么</h2><p>核心升级节省的绝对时间约30.34～30.39μs，四条件基本保持。相对改善24.02%～7.78%的差别主要来自计算增加后分母变大；逐链路相对误差变小也不能称预测能力提高，其绝对误差仍约11.3～11.7μs。</p><p>这些是不同网络资源配置的参数收益，不能换算为同成本算法收益或商业回报。按路由与均分条件总计算量相同、最长单卡耗时不同；线性计时是假设，不是真实GPU模型。</p><p>配对Dispatch与计算开始时刻相同，没有本地专家排队；最后完成者由rank3变为rank0。表中将差额分成完成者变化与后续尾段变化，不把尾段差值直接当拥塞或可消除收益。</p><p>完整图读取各条件baseline的实际绑定后冻结，不含参考时刻；所有通信仍在持续网络内由真实完成回调推进。它能表达这些行为，本身不构成新的MoE创新。</p></section>'
    html += '<p class="note">数据与来源、限制、因果核验见同目录 report.md / summary.json / verification.json。两项论文创新仍需独立方法和强基线证据。</p></html>'
    (output/'report.html').write_text(html+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report(args.stage.resolve(), args.output.resolve())
