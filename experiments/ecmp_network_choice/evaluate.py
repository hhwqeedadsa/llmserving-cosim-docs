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
        '固定真实MMLU路由的batch4/8、EP4、单层一次Decode调用，rank0/1/4/5参与。输入路由有来源与本地hash，但不是严格留出或生产分布；计算/provider为声明成本，未执行GPU/GIN或完整R1服务。', '',
        'single/dual/quad分别为1×100G、2×50G、4×25G核心上联，每Agg总带宽相同。交换机/端口数量和每链路速率不同，不能当作设备成本相同或商业ROI。', '',
        '| batch | 配置 | 应用阶段 μs | 全部事件/源释放 μs | 排空 μs | 完整图等价 | CNP物理发送跳 |',
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
    completed = all(len(row['metrics']) == len(row['comparisons']) == 3 and all(c['equivalent'] for c in row['comparisons'].values()) for row in batches.values())
    gaps = [e['meaningful_gap'] for row in batches.values() for m,e in row['evaluations'].items() if m != 'equal_path_phase']
    if completed and gaps and not any(gaps):
        lines += ['', '去留结论：两种保留连接哈希的普通参考均未出现达到事前门槛的配置误选；完整通用图也已覆盖本组详细执行。停止继续用小batch、健康EP4拓扑选择来支持新增MoE语义主创新；不再更换种子/路由或ECN阈值寻找正例。仿真器的价值仍是可靠表达网络，新的论文贡献需要其他有实际损失的缺口及强基线证据。']
    else:
        lines += ['', '本轮尚不能收尾为两项创新成立。若发生完整图失配或执行失败，保留全部原件并关闭剩余网络；需要先判别绑定、依赖及观察完整性，不把参考输入冻结的局限当通用图表达能力不足。若普通参考存在误选，还须解释机制并排除更简单参考，不直接开发新算法。']
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
