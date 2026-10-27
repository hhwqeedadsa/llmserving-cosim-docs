"""Post-execution evaluation and mechanism receipts; no prediction fitting."""
import argparse
import html
from pathlib import Path

from experiments.compact_network_choice.evaluate import modern_metrics
from experiments.shared_uplink_choice.evaluate import compare
from experiments.ecmp_baseline_audit.verify import native_check
from .audit import inspect
from .data import CONDITIONS, load, save


def check(stage,batch,name):
    root = stage/f'truth/batch{batch}/{name}/modern'
    prediction = load(stage/f'predictions/batch{batch}.json')['predictions'][name]
    metrics = modern_metrics(root)
    declaration = load(stage/f'predictions/batch{batch}-declaration.json')
    a,b = native_check(declaration,load(root/'modern-plan.json'),load(root/'initial.json'),load(root/'modern-execution.json'))
    audit = inspect(root,prediction)
    save(root.parent/'modern-check.json',dict(passed=True,metrics=metrics,audit=audit,
        native_connections=a,native_remote_operations=b))


def compare_case(stage,batch,name):
    root = stage/f'truth/batch{batch}'
    result = compare(root/name,root/'single/modern')
    save(root/name/'comparison.json',result)
    assert result['equivalent'],result['checks']


def render(lines):
    fragments,table = [],False
    for line in lines:
        if line.startswith('|'):
            cells = [c.strip() for c in line.strip('|').split('|')]
            if all(set(c) <= set('-: ') for c in cells): continue
            tag = 'td' if table else 'th'
            if not table: fragments.append('<div class="scroll"><table>');table=True
            fragments.append('<tr>'+''.join(f'<{tag}>'+html.escape(c)+f'</{tag}>' for c in cells)+'</tr>')
        else:
            if table: fragments.append('</table></div>');table=False
            if line.startswith('# '):fragments.append('<h1>'+html.escape(line[2:])+'</h1>')
            elif line:fragments.append('<p>'+html.escape(line)+'</p>')
    if table:fragments.append('</table></div>')
    return '<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>多路径网络选择对照</title><style>body{max-width:1120px;margin:40px auto;padding:0 24px;font:16px/1.8 system-ui;color:#203040;background:#f6f8fa}table{border-collapse:collapse;background:#fff;min-width:720px;width:100%;font-size:14px}td,th{padding:9px 12px;text-align:left;border-bottom:1px solid #dde3ec}th{background:#e8f0fa}.scroll{overflow-x:auto;margin:22px 0}h1{font-size:28px}p{max-width:95ch}</style>'+''.join(fragments)+'</html>\n'


def report(stage,output):
    output.mkdir()
    ledger = load(stage/'execution.json')
    assert ledger['closed']
    batches = {}
    for batch in (4,8):
        prediction = load(stage/f'predictions/batch{batch}.json')
        metrics,comparisons,audits = {},{},{}
        for name in CONDITIONS:
            root = stage/f'truth/batch{batch}/{name}'
            if (root/'modern-check.json').exists():
                checked = load(root/'modern-check.json')
                metrics[name],audits[name] = checked['metrics'],checked['audit']
            if (root/'comparison.json').exists(): comparisons[name] = load(root/'comparison.json')
        evaluations = {}
        if len(metrics) == 3:
            actual = {n:m['application_ns'] for n,m in metrics.items()}
            best = min(actual.values())
            for method,choice in prediction['choices'].items():
                selected = choice['selected'];delta=actual[selected]-best
                evaluations[method] = dict(**choice,actual_best=[n for n in CONDITIONS if actual[n] == best],
                    regret_ns=delta,regret_fraction=delta/best,
                    meaningful_gap=delta >= 2000 and delta/best >= 0.05,
                    errors={n:(prediction['predictions'][n]['methods'][method]['estimated_application_ns']-actual[n])/actual[n]
                        for n in CONDITIONS})
        batches[str(batch)] = dict(metrics=metrics,comparisons=comparisons,audits=audits,evaluations=evaluations)
    summary = dict(schema_version='ecmp-network-choice/1',execution=ledger,batches=batches,
        hardware_calibrated=False,new_MoE_semantic_benefit_proven=False,simulator_speedup_proven=False)
    save(output/'summary.json',summary)
    lines = ['# 相同核心上联总带宽：多路径实际网络对照','',
        '已完成batch4的三种网络和对应通用图，共6次包级执行；batch8仅有事前输入与预测，6次网络因严格比较失败而未执行。每次是EP4、单层一次Decode调用，逻辑rank0/1/2/3部署到拓扑GPU节点0/1/4/5。输入来自真实MMLU路由，有来源与本地hash，但不是严格留出或生产分布；计算/provider为声明成本，未执行GPU/GIN或完整R1服务。', '',
        'single/dual/quad分别为1×100G、2×50G、4×25G核心上联，每Agg总带宽相同。交换机/端口数量和每链路速率不同，不能当作设备成本相同或商业ROI。', '',
        '| batch | 配置 | 应用阶段 μs | 全部事件/源释放 μs | 排空 μs | 完整图严格比较 | CNP物理发送跳 |',
        '|---|---|---:|---:|---:|---|---:|']
    for batch,row in batches.items():
        for name,m in row['metrics'].items():
            lines.append(f"| {batch} | {name} | {m['application_ns']/1000:.3f} | {m['all_events_ns']/1000:.3f} | {m['drain_ns']/1000:.3f} | {row['comparisons'].get(name,{}).get('equivalent')} | {row['audits'][name]['cnp_transmissions']} |")
    lines += ['', '| batch | 普通预测方法 | 选择 | 实际最优 | 误选多花 μs | 后悔值 | 达到事前缺口门槛 |',
        '|---|---|---|---|---:|---:|---|']
    for batch,row in batches.items():
        for method,e in row['evaluations'].items():
            lines.append(f"| {batch} | {method} | {e['selected']} | {','.join(e['actual_best'])} | {e['regret_ns']/1000:.3f} | {e['regret_fraction']*100:.2f}% | {e['meaningful_gap']} |")
    lines += ['', 'equal_path_phase按最短路径等分；hashed_link_phase保留实际输入连接哈希；hashed_rtt_phase再对原子屏障加往返传播。它们都保留普通阶段、帧和声明本地成本，不模拟细粒度ACK/CNP、队列或速率；选对配置不代表时延准确，以下保留误差。', '',
        '| batch | 方法 | 三配置最大绝对相对时延误差 |','|---|---|---:|']
    for batch,row in batches.items():
        for method,e in row['evaluations'].items():
            lines.append(f"| {batch} | {method} | {100*max(map(abs,e['errors'].values())):.2f}% |")
    lines += ['', '本例只有四核心条件生成1次拥塞通知（CNP），6次物理发送表示该通知沿途的发送跳数，不能当成6次通知。', '',
        '上联总带宽相同，并不保证少量固定连接得到相同带宽：一条连接按哈希选定一条路径，未必能同时使用其余路径；拆成更多低速链路还改变每条连接的服务速率。该普通机制已解释大部分配置差异，不能把本例推广为核心交换机越少越好。']
    if (stage/'diagnosis.json').exists():
        diagnosis=load(stage/'diagnosis.json')
        assert diagnosis['passed'] and diagnosis['strict_failure_preserved']
        lines += ['', '保留的严格比较失败：四核心的865条原生回调中，有20个列表位置不同，集中在6个相同时间戳桶；每个操作的回调历史和每个批次的回调多重集合完全相同，全部64个native消息在只整理批内回调顺序后相同。包、队列、应用事件顺序与时间、最终内存原本就严格相同。该诊断解释本组差异，不重开原严格门槛，不声称所有比较通过；剩余batch8六次网络未执行。', '',
            '两种控制器均先收齐一个回调批次再推进工作，该局部差异没有在这次执行中改变后继提交。不能把数字身份改名后的同时间记录排列，包装成MoE额外语义收益或DAG失效。', '',
            '| 相对single | 实际应用增量 μs | 普通哈希阶段预测增量 μs | 增量残差 μs |',
            '|---|---:|---:|---:|']
        for name,row in diagnosis['configuration_increment_comparison'].items():
            lines.append(f"| {name} | {row['actual_delta_ns']/1000:.3f} | {row['ordinary_predicted_delta_ns']/1000:.3f} | {row['increment_residual_ns']/1000:.3f} |")
        lines += ['', '残差只是两种配置增量的模型差，不是独立可消除的拥塞或反馈损失；普通模型的绝对时间误差仍需保留。']
    completed = all(len(row['metrics']) == len(row['comparisons']) == 3 and all(c['equivalent'] for c in row['comparisons'].values()) for row in batches.values())
    gaps = [e['meaningful_gap'] for row in batches.values() for m,e in row['evaluations'].items() if m != 'equal_path_phase']
    if completed and gaps and not any(gaps):
        lines += ['', '去留结论：两种保留连接哈希的普通参考均未出现达到事前门槛的配置误选；完整通用图也已覆盖本组详细执行。停止继续用小batch、健康EP4拓扑选择来支持新增MoE语义主创新；不再更换种子/路由或ECN阈值寻找正例。仿真器的价值仍是可靠表达网络，新的论文贡献需要其他有实际损失的缺口及强基线证据。']
    else:
        lines += ['', '本轮尚不能收尾为两项创新成立。若发生完整图失配或执行失败，保留全部原件并关闭剩余网络；需要先判别绑定、依赖及观察完整性，不把参考输入冻结的局限当通用图表达能力不足。若普通参考存在误选，还须解释机制并排除更简单参考，不直接开发新算法。']
        if batches['4']['evaluations'] and not any(e['meaningful_gap'] for m,e in batches['4']['evaluations'].items() if m!='equal_path_phase'):
            lines += ['', '已完成的batch4三组现代网络足以给出一个负面筛选结果：保留连接哈希的两种普通参考均选对single，后悔值0；等分方法则三配置并列，仅由事前并列规则选中single。1×100G比4×25G在本样本应用阶段快17.311us（22.81%），这是网络结构敏感性，不是新方法收益。结合此前多组健康EP4负面筛选，不再继续用此类带宽/路径升级案例支持主创新；batch8未执行，不能声称已覆盖两种batch或生产泛化。']
    lines += ['', f"实际网络尝试{sum(r['network'] for r in ledger['records'])}次；状态{ledger['state']}。完整图由各batch的single实际公开绑定生成，不含参考绝对时刻，不是独立输入编译器。", '',
        '实际包路径独立按物理NIC、拓扑候选及哈希核对；CNP捕获不含fid，按真实生成事件与stream/fid关联评价，不声称从抓包完整解码。累计队列等待不能直接当作应用损失或可消除上界。没有宿主加速测量、千卡执行或新网络协议收益。']
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    (output/'report.html').write_text(render(lines))


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('check','compare','report'))
    p.add_argument('--stage',type=Path,required=True);p.add_argument('--batch',type=int,choices=(4,8))
    p.add_argument('--name',choices=CONDITIONS);p.add_argument('--output',type=Path)
    a=p.parse_args()
    if a.action == 'report':report(a.stage.resolve(),a.output.resolve())
    elif a.action == 'check':check(a.stage.resolve(),a.batch,a.name)
    else:compare_case(a.stage.resolve(),a.batch,a.name)
