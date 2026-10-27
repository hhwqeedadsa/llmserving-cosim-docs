"""Compare all detailed behavior; normalize only IDs and conjunctive after ordering."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from experiments.compact_network_choice.evaluate import modern_metrics, compare as strict_compare
from experiments.compact_network_choice.diagnose import canonical_after
from experiments.static_dag_transfer.diagnose_event_graph import semantic_identity, replace_operation_keys
from experiments.shared_uplink_choice.prepare import CONDITIONS, load, save


def compare(root, baseline):
    result=strict_compare(root,baseline)
    detailed=load(root/'modern/modern-execution.json')
    reference=load(baseline/'modern-execution.json')
    lookup={semantic_identity(t):int(i) for i,t in reference['native_tasks'].items()}
    mapping={int(i):lookup[semantic_identity(t)] for i,t in detailed['native_tasks'].items()}
    def canonical_work(rows,mapped):
        rows=deepcopy(rows)
        if mapped:
            rows=replace_operation_keys(rows,mapping)
            for row in rows:row['id']=mapping[row['id']]
        return sorted([canonical_after(r) for r in rows],key=lambda r:r['id'])
    def commits(path,mapped):
        rows=[]
        for line in path.read_text().splitlines():
            row=json.loads(line)
            if row['direction']!='frontend':continue
            m=row['message']
            if m['submissions'] or m['finish']:
                nodes=deepcopy(m['submissions'])
                if mapped:
                    nodes=replace_operation_keys(nodes,mapping)
                    for node in nodes:node['id']=mapping[node['id']]
                # Submission order matters, whereas each after list is an AND set.
                rows.append(dict(time_ns=m['now_ns'],nodes=[canonical_after(n) for n in nodes],finish=m['finish']))
        return rows
    result['strict_checks']=deepcopy(result['checks'])
    result['checks']['graph_work_and_after']=canonical_work(detailed['native_operations'],True)==canonical_work(reference['native_operations'],False)
    result['checks']['productive_commits']=commits(root/'modern/control.jsonl',True)==commits(root/'generic/control.jsonl',False)
    result['equivalent']=all(result['checks'].values())
    return result


def report(stage, output):
    output.mkdir()
    ledger=load(stage/'execution.json')
    batches={}
    for batch in (4,8):
        prediction=load(stage/f'predictions/batch{batch}.json')
        metrics,comparisons={},{}
        for name in CONDITIONS:
            root=stage/'truth'/f'batch{batch}'/name
            if (root/'modern/result.json').exists(): metrics[name]=modern_metrics(root/'modern')
            if (root/'comparison.json').exists(): comparisons[name]=load(root/'comparison.json')
        evaluations={}
        if len(metrics)==3:
            times={n:v['application_ns'] for n,v in metrics.items()}
            best=min(times.values())
            for method,choice in prediction['choices'].items():
                selected=choice['selected']
                evaluations[method]=dict(**choice,actual_best=[n for n in CONDITIONS if times[n]==best],
                    regret_fraction=(times[selected]-best)/best,
                    errors={n:(prediction['predictions'][n]['methods'][method]['estimated_application_ns']-times[n])/times[n] for n in CONDITIONS})
        batches[str(batch)]=dict(metrics=metrics,comparisons=comparisons,evaluations=evaluations,
            plan_bytes=prediction['plan_bytes'],message_representation_bytes=prediction['message_representation_bytes'])
    summary=dict(execution=ledger,batches=batches,hardware_calibrated=False,
        new_MoE_semantic_benefit_proven=False,simulator_speedup_proven=False)
    save(output/'summary.json',summary)
    lines=['# 共享上联与多物理网卡：网络配置选择结果','','真实MMLU路由，四活动rank、单层单次Decode调用；一个原生ns-3时钟。功能协议MRT1、声明计算/provider成本，未执行GPU或生产硬件校准。','',
        '| batch | 配置 | 应用阶段 μs | 源释放 μs | 排空 μs | 完整图等价 |','|---|---|---:|---:|---:|---|']
    for b,row in batches.items():
        for name,m in row['metrics'].items():
            lines.append(f"| {b} | {name} | {m['application_ns']/1000:.3f} | {m['all_events_ns']/1000:.3f} | {m['drain_ns']/1000:.3f} | {row['comparisons'].get(name,{}).get('equivalent')} |")
    lines+=['','| batch | 预测方法 | 选择 | 实际最优 | 选择后悔值 | 最大绝对相对时延误差 |','|---|---|---|---|---:|---:|']
    for b,row in batches.items():
        for method,e in row['evaluations'].items():
            lines.append(f"| {b} | {method} | {e['selected']} | {','.join(e['actual_best'])} | {100*e['regret_fraction']:.2f}% | {100*max(map(abs,e['errors'].values())):.2f}% |")
    lines+=['',f"网络尝试{sum(r['network'] for r in ledger['records'])}次；阶段状态`{ledger['state']}`。未执行条件与失败均保留。",'',
        'baseline为接入100G/核心25G，access为接入200G/核心25G，core为接入100G/核心50G；传播均1000ns。不同升级改变的资源不同，只能说明参数收益，不能称同成本优化或商业ROI。', '',
        'endpoint_phase只计端点总NIC容量；nic_phase按实际设备拆分；link_phase进一步保留共享上联负载。后三者都是普通七阶段帧服务模型，忽略细粒度队列与反馈，正确选择不代表时延准确。', '',
        'Combine负载由slot双射的多重集合事前确定，没有读取未来token–slot绑定。完整通用图仅从每batch原网络提取；失配须区分冻结选择与表达能力。普通逐链路基线若足以选对，不能将收益包装成新增MoE语义创新。']
    (output/'report.md').write_text('\n'.join(lines)+'\n')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=('compare','report'))
    p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--output',type=Path)
    p.add_argument('--batch',type=int,choices=(4,8))
    p.add_argument('--name',choices=CONDITIONS)
    a=p.parse_args()
    if a.action=='report':report(a.stage.resolve(),a.output.resolve());return
    root=a.stage/'truth'/f'batch{a.batch}'
    result=compare(root/a.name,root/'baseline/modern')
    save(root/a.name/'comparison.json',result)
    assert result['equivalent'],result['checks']


if __name__=='__main__':main()
