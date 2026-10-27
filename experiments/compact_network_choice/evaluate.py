"""Evaluation reads truth only after predictions and captured graph are frozen."""
import argparse
from copy import deepcopy
from pathlib import Path

from experiments.static_dag_transfer.diagnose_event_graph import semantic_identity, replace_operation_keys
from experiments.compact_network_choice.prepare import CONDITIONS, load, save


def modern_metrics(root):
    result, execution = load(root/'result.json'), load(root/'modern-execution.json')
    assert result['passed'] and result['session']['finished'] and execution['runtime']['all_events_fired']
    assert result['packet_capture']['omitted_events'] == 0
    assert result['transactions']['transport']['physical_drained']
    assert not execution['runtime']['memory_state']['active_source_leases']
    return dict(application_ns=max(e['time_ns'] for e in execution['events'] if e['kind'] == 'application_stage_complete'),
        all_events_ns=max(e['time_ns'] for e in execution['events']), drain_ns=result['finish_ns'],
        source_packets=len(result['transactions']['transport']['source_packets']),
        capture_events=result['packet_capture']['observations'],
        decisions=result['session']['decisions'], operations=len(execution['native_operations']),
        events=len(execution['events']), connections=len(load(root/'initial.json')['connections']))


def compare(root, baseline):
    detailed = load(root/'modern/modern-execution.json'); reference = load(baseline/'modern-execution.json')
    lookup = {semantic_identity(t):int(i) for i,t in reference['native_tasks'].items()}
    assert len(lookup) == len(reference['native_tasks'])
    mapping = {int(i):lookup[semantic_identity(t)] for i,t in detailed['native_tasks'].items()}
    assert set(mapping.values()) == set(lookup.values())
    assert all(detailed['native_tasks'][str(i)] == reference['native_tasks'][str(j)] for i,j in mapping.items())
    def remap(rows):
        rows = replace_operation_keys(rows, mapping)
        for r in rows: r['id'] = mapping[r['id']]
        return sorted(rows, key=lambda r:r['id'])
    old, new = load(root/'modern/result.json'), load(root/'generic/result.json')
    logs = [{k:e[k] for k in ('event','time_ns','native_operation','native_event','native_time_ns') if k in e}
            for e in detailed['events']]
    for e in logs:
        if 'native_operation' in e: e['native_operation'] = mapping[e['native_operation']]
    transactions = replace_operation_keys(old['transactions'], mapping)
    transactions['operations'] = remap(old['transactions']['operations'])
    def commits(path, mapped):
        result = []
        import json
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row['direction'] != 'frontend': continue
            m = row['message']
            if m['submissions'] or m['finish']:
                nodes = deepcopy(m['submissions'])
                if mapped:
                    nodes = replace_operation_keys(nodes, mapping)
                    for node in nodes: node['id'] = mapping[node['id']]
                result.append(dict(time_ns=m['now_ns'], nodes=nodes, finish=m['finish']))
        return result
    checks = dict(graph_work_and_after=remap(detailed['native_operations']) == reference['native_operations'],
        transactions_and_final_memory=transactions == new['transactions'],
        local_work=remap(old['local_work']) == sorted(new['local_work'],key=lambda r:r['id']),
        application_event_times_and_order=logs == load(root/'generic/event-execution.json')['events'],
        callbacks=replace_operation_keys(old['session']['events'],mapping) == new['session']['events'],
        registrations=[dict(operation=mapping[r['operation']], time_ns=r['time_ns']) for r in old['session']['registrations']]
                      == [dict(operation=r['operation'],time_ns=r['time_ns']) for r in new['session']['registrations']],
        productive_commits=commits(root/'modern/control.jsonl',True) == commits(root/'generic/control.jsonl',False))
    for name in ('packet_capture','queue_events','queue_observations','ports','finish_ns'):
        checks[name] = old[name] == new[name]
    assert old['packet_capture']['omitted_events'] == new['packet_capture']['omitted_events'] == 0
    return dict(equivalent=all(checks.values()), checks=checks,
        renamed_task_ids=sum(i!=j for i,j in mapping.items()), metrics=modern_metrics(root/'modern'))


def report(stage, output):
    output.mkdir(exist_ok=False)
    ledger = load(stage/'execution.json'); batches = {}
    for batch in (4,8):
        roots = {name:stage/'truth'/f'batch{batch}'/name for name in CONDITIONS}
        metrics = {name:modern_metrics(root/'modern') for name,root in roots.items() if (root/'modern/result.json').exists()}
        comparisons = {name:load(root/'comparison.json') for name,root in roots.items() if (root/'comparison.json').exists()}
        pred = load(stage/'predictions'/f'batch{batch}.json')
        evaluation = {}
        if len(metrics) == 3:
            times = {name:row['application_ns'] for name,row in metrics.items()}
            best = min(times.values()); best_set = [n for n in CONDITIONS if times[n] == best]
            for method,choice in pred['choices'].items():
                selected = choice['selected']
                evaluation[method] = dict(**choice, actual_best=best_set,
                    choice_regret_fraction=(times[selected]-best)/best,
                    correct=selected in best_set,
                    relative_errors={name:(row['estimated_application_ns']-times[name])/times[name]
                                     for name,row in pred['methods'][method].items()})
        batches[str(batch)] = dict(metrics=metrics, comparisons=comparisons, evaluations=evaluation,
            representation_compact_bytes=pred['representation_compact_bytes'],plan_compact_bytes=pred['plan_compact_bytes'])
    diagnosis = load(stage/'diagnosis.json') if (stage/'diagnosis.json').exists() else None
    verification = load(stage/'verification.json') if (stage/'verification.json').exists() else None
    summary = dict(execution=ledger, batches=batches, diagnosis=diagnosis, verification=verification, hardware_calibrated=False,
        novel_semantic_decision_benefit_proven=False, end_to_end_simulator_speedup_proven=False,
        source_remote_revision_verified=False)
    save(output/'summary.json', summary)
    lines = ['# 新真实请求与batch4/8：网络选择研究', '',
        '输入来自用户新增的八份完整MMLU请求，EP4、首个Decode入口/第3层。真实路由来源与本地hash有记录，远端版本未独立核实。激活/计算/provider均为声明的功能模型，没有GPU执行或硬件校准。', '',
        '| 每组请求数 | 网络配置 | 应用阶段 μs | 全部事件/源释放 μs | 排空 μs | 源包数 | 原始严格比较 |',
        '|---|---|---:|---:|---:|---:|---|']
    for batch,row in batches.items():
        for name,m in row['metrics'].items():
            equal = row['comparisons'].get(name,{}).get('equivalent')
            lines.append(f"| {batch} | {name} | {m['application_ns']/1000:.3f} | {m['all_events_ns']/1000:.3f} | {m['drain_ns']/1000:.3f} | {m['source_packets']} | {equal} |")
    lines += ['', '| batch | 精简方法 | 选择 | 实际最优 | 选择后悔值 |', '|---|---|---|---|---:|']
    for batch,row in batches.items():
        for method,e in row['evaluations'].items():
            lines.append(f"| {batch} | {method} | {e['selected']} | {','.join(e['actual_best'])} | {100*e['choice_regret_fraction']:.2f}% |")
    lines += ['', '后悔值指选中配置相对本组最快配置多花的应用时间；带宽翻倍/传播减半是不同网络假设的敏感性，不是同成本方法收益或商业ROI。累计队列等待不等于可消除应用时间。', '',
        f"实际网络尝试 {sum(r['network'] for r in ledger['records'])} 次；阶段状态：{ledger['state']}。失败/未执行条件按原始账目保留。", '',
        'phase_payload/phase_framed是普通阶段、消息和端点服务模型。它们若已选对，不能将作用归给新增MoE语义。完整图只由每batch的baseline生成，保留开始门槛、共享准备、slot同步和已绑定通道；成功不等于零成本编译。', '',
        '表示大小、宿主开销和预测准确性分别报告；未执行同等成本完整加速方案，不宣称模拟器加速或新的论文主贡献。四rank单层、两组便利样本和三种无故障配置不足以证明千卡、跨模型或生产泛化。']
    if diagnosis is not None:
        lines += ['', '最后一项原始严格检查失败仍保留：batch8/propagation的4个屏障ready工作，在任务编号双射改名后，after列表内两项顺序互换。逐项集合相同；固定原生源码以逻辑AND检查所有前驱，列表顺序不改变执行。独立离线归一后，工作/提交也等价，其他包、队列、回调、应用/释放/排空时间和内存原本就相同。没有改图、重跑网络或重开停止门槛，不能把它当成DAG失效或新方法收益。', '',
            '因此六组条件均有行为等价证据，其中五组原检查直接通过，最后一组附after顺序诊断；一项检查器对表示顺序过严的失败不掩盖。']
    lines += ['', '| batch | 符号计划 bytes | 阶段表示 bytes | 两个phase方法的最大绝对相对时延误差 |',
              '|---|---:|---:|---:|']
    for batch,row in batches.items():
        errors=[abs(v) for m,e in row['evaluations'].items() if m.startswith('phase_') for v in e['relative_errors'].values()]
        lines.append(f"| {batch} | {row['plan_compact_bytes']} | {row['representation_compact_bytes']} | {100*max(errors):.2f}% |")
    lines += ['', '本轮结论：总字节模型漏掉消息阶段/传播后会选错；普通阶段基线已消除本轮的配置选择误差。因此尚无证据支持新增MoE精简方法超过合理强基线。选对这三个配置也不代表时延预测准确，上表保留绝对误差；阶段表示是预编译计划的摘要，并非已证明的最小表示或端到端加速。', '',
        '后续应预注册更能区分方法的共享上联、多rail不均衡和路径/排队变化；先证明普通阶段基线会产生有实际代价的误判，再决定是否需要更细语义。不能继续用只看字节这一弱基线制造创新收益。']
    (output/'report.md').write_text('\n'.join(lines)+'\n')


def main():
    p = argparse.ArgumentParser(); p.add_argument('action',choices=('compare','report'))
    p.add_argument('--stage',type=Path,required=True);p.add_argument('--output',type=Path)
    p.add_argument('--batch',type=int);p.add_argument('--name',choices=CONDITIONS)
    a = p.parse_args()
    if a.action == 'report': report(a.stage.resolve(),a.output.resolve());return
    root = a.stage/'truth'/f'batch{a.batch}'
    result = compare(root/a.name,root/'baseline/modern');save(root/a.name/'comparison.json',result)
    assert result['equivalent'], result['checks']


if __name__ == '__main__': main()
