"""Explain the failed baseline without claiming a generic-DAG limitation."""
import argparse
import hashlib
import json
from pathlib import Path


def load(p): return json.loads(p.read_bytes())
def save(p, v): p.write_text(json.dumps(v, ensure_ascii=False, indent=2)+'\n')


def analyze(stage):
    reference = load(stage/'reference/baseline/result.json')
    actual = load(stage/'baseline/result.json')
    model = load(stage/'reference/baseline/modern-execution.json')
    plan = load(stage/'reference/baseline/modern-plan.json')
    program = load(stage/'program.json')
    execution = load(stage/'execution.json')
    assert execution['network_executions'] == 1 and len(execution['records']) == 1
    assert not actual['passed'] and not actual['session']['finished'] and actual['session']['decisions'] == 256
    assert actual['finish_ns'] == 35853 and len(program['operations']) == 164
    before = reference['transactions']['transport']['source_packets']
    after = actual['transactions']['transport']['source_packets']
    first = next(i for i, (a,b) in enumerate(zip(before, after)) if a != b)
    assert first == 24 and len(after) == 170
    old, new = before[first], after[first]
    assert old[1:] == new[1:] and (old[0], new[0]) == (6246, 6146)
    old_payload = reference['transactions']['transport']['source_payload_packets'][first]
    new_payload = actual['transactions']['transport']['source_payload_packets'][first]
    assert {k:v for k,v in old_payload.items() if k!='time_ns'} == {k:v for k,v in new_payload.items() if k!='time_ns'}
    tasks = model['native_tasks']; nodes = {n['id']:n for n in program['operations']}
    events = {e['id']:e for e in plan['events']}
    local_id = next(int(k) for k,v in tasks.items() if v.get('modern_operation') == 'm0.dispatch.rank_count.s0.d0')
    local_name = tasks[str(local_id)]['event']
    block_id = next(int(k) for k,v in tasks.items() if v.get('modern_operation') == 'm0.dispatch.expert_count.s0.d1')
    assert local_name in events[tasks[str(block_id)]['event']]['deps']
    assert local_id not in {p['operation'] for p in nodes[block_id]['after']}
    old_op = {r['id']:r for r in reference['transactions']['operations']}
    new_op = {r['id']:r for r in actual['transactions']['operations']}
    old_local = {r['id']:r for r in reference['local_work']}
    new_local = {r['id']:r for r in actual['local_work']}
    assert old_local[local_id]['start_ns'] == new_local[local_id]['start_ns'] == 6246
    assert (old_op[block_id]['ready_ns'], new_op[block_id]['ready_ns']) == (6246,6146)
    assert nodes[block_id]['aggregate_requests'] is False
    controls = {}
    for name in ('reference/baseline', 'baseline'):
        rows = [json.loads(row) for row in (stage/name/'control.jsonl').read_text().splitlines()]
        commit = next(r['message'] for r in rows if r['direction']=='frontend' and
                      any(o['id']==29 for o in r['message'].get('submissions', [])))
        controls[name] = dict(now_ns=commit['now_ns'], ids=[o['id'] for o in commit['submissions']])
    assert controls['reference/baseline']['ids'] == [28,29,30,31]
    assert controls['baseline']['ids'] == [28,29,30,31,44,45,46,47]
    return dict(passed=True, outcome='projection_incomplete_and_default_interaction_limit',
        actual_new_networks=1, planned_remaining_networks_executed=0,
        actual_default_decisions=256, failed_snapshot_ns=35853, application_finish_ns=None,
        first_source_difference_index=first, exact_source_prefix_packets=first,
        observed_source_packets=len(after), payload_at_first_difference_identical=True,
        first_source_packet_reference_ns=old[0], first_source_packet_actual_ns=new[0],
        missing_semantic_dependency=dict(parent_event=local_name, native_local=local_id,
            parent_transition='local_started', parent_actual_ns=6246, successor_native=block_id,
            successor_reference_ready_ns=6246, successor_projected_ready_ns=6146,
            frozen_after=nodes[block_id]['after']),
        publication_witness=dict(connection=1, request_operation=29,
            reference_published_ns=old_op[29]['published_ns'], actual_published_ns=new_op[29]['published_ns'],
            following_nonaggregating_operation=block_id),
        commit_witness=controls, network_scheme_ranking_evaluated=False,
        generic_DAG_insufficiency_proven=False, hardware_calibrated=False,
        next_required_baseline='Generic event DAG retaining submitted/local_started gates and existing bounded callback batches; no increase to 256 limit.')


def main():
    p = argparse.ArgumentParser(); p.add_argument('--stage', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True); a=p.parse_args()
    result=analyze(a.stage); a.output_dir.mkdir(parents=True, exist_ok=False)
    save(a.output_dir/'summary.json', result)
    report='''# 普通依赖图对照：本轮未形成有效的完整基线

仅执行了原网络一次，其他两组按事前条件停止。原网络在256次前后端交互处结束于35,853ns，未完成应用；不能与详细参考的69,547ns排空作性能比较。失败不是网络加速，也不是一般DAG无法表达现代MoE的证据。

此前称“完整native DAG”不够准确。虽然复制了164个native工作、29条连接、全部数据/控制/本地操作和after字段，after字段只保留applied/completed证明，不能替代完整应用事件图。

首个实际源包差异在第25包：内容、连接、字节序号均相同，参考6,246ns发送，投影6,146ns发送，前24包完整相同。参考专家计数块必须等待本地rank-count操作开始（native 28的local_started，6,246ns）；投影的after只有计数准备完成条件（native 17的completed，6,146ns），漏掉了开始条件。于是非聚合专家计数块45提前进入同一COMMIT，提前发布连接1此前等待聚合的rank-count工作29。这是依赖转换丢失及发布组织的具体见证，不是新拥塞或恢复现象。

下一条强基线必须保留通用submitted/local_started事件门槛，以及现有有界回调批次能力，维持256次限制；再比较网络方案排序。不能仅补固定100ns等待，也不能按参考绝对时间发包。未执行剩余两组、没有追加重试、未修改平台或依赖。

原始结果/控制记录/程序与固定运行时在持久证据包内；摘要由analyze_failure.py独立读取公开输出计算。当前结果削弱了“复制native after字段就得到完整应用DAG”的假设，没有证明论文创新或收益。两个候选仍须与真正完整的通用DAG及已有包级加速方法比较。
'''
    (a.output_dir/'report.md').write_text(report)
    record=load(a.stage/'execution.json'); record.update(closed=True,state='failed_baseline_preserved_no_more_networks')
    save(a.stage/'execution.json',record)
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__': main()
