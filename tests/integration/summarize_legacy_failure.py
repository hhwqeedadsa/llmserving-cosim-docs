"""Verify the retained capacity failure and current pre-Fabric rejection evidence."""
import argparse
from collections import Counter
import hashlib
import html
import json
import os
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--publish-dir',type=Path,
                   help='Final HTML directory for relative documentation links; defaults to output')
    a=p.parse_args();root=a.root.resolve();out=a.output.resolve()
    assert not out.exists();out.mkdir(parents=True)
    load=lambda path:json.loads(path.read_bytes())
    sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    work=load(root/'normal/execution.json');r=load(root/'normal/result.json')
    inputs=load(root/'normal/inputs.json');protocol=load(root/'inputs/normal.json')
    for path,expected in inputs['hashes'].items():
        if path==inputs['command'][0]:continue  # Binary was rebuilt for admission fix.
        assert sha(Path(path))==expected,path
    assert not r['passed'] and not r['legacy']['finished'] and not r['drained']
    assert r['execution_error']=='RDMA: reference feedback event budget exceeded'
    ref=r['legacy']['transport']['dcqcn_reference'];count=Counter(e['kind'] for e in ref['events'])
    assert count=={'source_sent':10000} and len(ref['events'])==ref['config']['max_events']==10000
    source_uids={row[-1] for row in r['legacy']['transport']['source_packets']}
    assert all(e['packet_uid'] in source_uids for e in ref['events'])
    packets=0
    for op in work['operations']:
        if op[4]==op[5]:continue
        frames=[bytes(32+op[8])]+([bytes(36)] if op[3]!='data' else [])
        packets+=sum(len(range(0,len(frame),protocol['payload_bytes'])) for frame in frames)
    assert packets==32464 and packets>ref['config']['max_events']
    admission=load(root/'admission/verification.json')
    assert admission==dict(passed=True,public_rejections=1,native_rejections=8,
                          valid_network_executed=False,limits_increased=False)
    posting=load(root/'posting-cpu/verification.json')
    assert posting['passed'] and posting['checks']==1384 and posting['scenarios']==816
    assert posting['network_executed'] is False and posting['lowering_methods']==5
    posting_rejections=load(root/'posting-rejections/verification.json')
    assert posting_rejections['passed'] and posting_rejections['mandatory_posting_span']==2
    assert posting_rejections['valid_network_executed'] is False
    current_admission=load(root/'admission-current/verification.json');assert current_admission==admission
    local_diagnosis=load(root/'local-path-diagnosis.json')
    assert local_diagnosis['retained_failure_local_applied_operations']==264
    assert local_diagnosis['local_operations_with_network_ack_time']==264
    packet_records=load(root/'packet-records-v2/verification.json')
    repo=Path(__file__).resolve().parents[2]
    assert packet_records['passed'] and packet_records['original_files_unchanged']
    assert packet_records['new_network_executions']==0 and not packet_records['complete_legacy_application_verified']
    assert packet_records['source_sha256']==sha(repo/'tests/integration/audit_legacy_native.py')
    assert packet_records['cases'][0]['original_result_sha256']==sha(root/'normal/result.json')
    packet_unit=load(root/'monitors/packet-audit-unit-v2/probe.json')
    assert packet_unit['status']=='completed' and packet_unit['child_exit_code']==0
    validation_unit=load(root/'monitors/validate-only-unit-v2/probe.json')
    assert validation_unit['status']=='completed' and validation_unit['child_exit_code']==0
    assert 'Ran 8 tests' in (root/'monitors/validate-only-unit-v2/stderr.log').read_text()
    validation_probe=load(root/'monitors/validate-only-public/probe.json')
    assert validation_probe['status']=='completed' and validation_probe['child_exit_code']==0
    proposal=load(root/'pending-capacity-proposal/verification.json')
    assert proposal['passed'] and not proposal['capacity_change_approved'] and not proposal['proposal_activated']
    assert proposal['new_network_executions']==0 and not proposal['native_admission_executed']
    assert proposal['original_inputs_unchanged'] and proposal['retained_failure_unchanged']
    assert proposal['changed_input_files']==['transport.json']
    for name,expected in proposal['original_input_sha256'].items():assert sha(root/'inputs'/name)==expected
    for name,expected in proposal['proposed_input_sha256'].items():
        assert sha(root/'pending-capacity-proposal/proposed-inputs'/name)==expected
    for name,expected in proposal['source_sha256'].items():assert sha(repo/name)==expected
    assert not (root/'fault').exists()
    stages=[]
    for path in sorted((root/'monitors').glob('*/probe.json')):
        v=load(path);assert not v['remaining_live_group_pids']
        stages.append(dict(stage=path.parent.name,status=v['status'],wall_seconds=v['wall_seconds'],
            peak_rss_bytes=v['peak_sampled_group_rss_bytes'],command=v['command']))
    assert sum(s['wall_seconds'] for s in stages)<1800
    assert max(s['peak_rss_bytes'] for s in stages)<=2*2**30
    build=load(root/'build/backend.json');provenance=load(root/'build/provenance.json')
    for path,expected in provenance['sources'].items():assert sha(repo/path)==expected,path
    libraries=build['library_hashes']
    for path in build['linked_ns3_libraries']:assert sha(Path(path))==libraries[Path(path).name]
    sources={str(path.relative_to(repo)):sha(path) for path in [
        repo/'backend/rdma/legacy_deepep_probe.cc',repo/'backend/rdma/legacy_event_graph.hh',
        repo/'backend/rdma/legacy_execution_validation.hh',repo/'python/moe_ub_cosim/legacy_native.py',
        repo/'backend/rdma/legacy_posting_admission.hh',
        Path(__file__).resolve(),repo/'tests/integration/check_legacy_admission.py',
        repo/'tests/integration/check_legacy_posting_cpu.py',repo/'tests/unit/legacy_posting_capacity_test.cc',
        repo/'tests/integration/check_legacy_posting_rejections.py',
        repo/'tests/integration/check_legacy_packet_records.py',repo/'tests/unit/legacy_packet_audit_test.py',
        repo/'tests/integration/audit_legacy_native.py',repo/'examples/legacy_native/prepare.py',
        repo/'tests/integration/check_legacy_validate_only.py',repo/'tests/unit/legacy_native_test.py',
        repo/'python/moe_ub_cosim/cli.py']}
    evidence=dict(failure_verified=True,complete_execution_accepted=False,
        endpoints=128,ep_size=32,experts=256,experts_per_rank=8,artificial_tokens=4,
        logical_operations=len(work['operations']),logical_events=len(work['events']),
        registered_contexts=r['legacy']['registered_contexts'],carrier_streams=work['limits']['streams'],
        carrier_work=work['limits']['work'],minimum_source_sent_events=packets,
        configured_reference_events=10000,actual_reference_events=dict(count),
        failed_at_sim_ns=r['finish_ns'],normal_status='failed_capacity',fault_status='not_run',
        admission=admission,source_hashes=sources,posting_cpu=posting,
        posting_rejections=posting_rejections,current_admission=current_admission,
        local_path_diagnosis=local_diagnosis,
        local_path_changed_since_retained_failure=True,native_local_execution_validated=False,
        packet_record_audit=packet_records,packet_audit_unit_methods=4,
        validation_only=proposal,validation_only_unit_methods=8,
        original_failed_binary_sha256=inputs['hashes'][inputs['command'][0]],
        current_admission_fixed_binary_sha256=sha(Path(build['legacy_deepep'])),
        native_commit=build['native_commit'],compiler_options=build['compiler_options'],
        library_hashes=libraries,stages=stages,
        monitored_wall_seconds=sum(s['wall_seconds'] for s in stages),
        peak_sampled_group_rss_bytes=max(s['peak_rss_bytes'] for s in stages),
        raw_result_sha256=sha(root/'normal/result.json'),
        scope='人工路由、功能字节；非完整推理、GPU性能或新算法收益。')
    (out/'verification.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    facts=[('模拟网络','128端点 Clos；EP32；每rank8专家'),
           ('输入','4个人工token、Top8、H7168/BF16；完整Dispatch和Combine计划'),
           ('计划','16,448操作 / 66,144事件；不删除零token通知'),
           ('载体','15,872有向流 / 31,792请求及回复工作'),
           ('发现','正常条件在4,237ns触发10,000条拥塞控制日志上限'),
           ('容量依据','无丢包也至少需要32,464条源发送日志；重传/反馈另占容量'),
           ('修复','Python与原生入口在创建网络前拒绝不可能的配置'),
           ('队列问题','未发布批次不能收到ACK；新增最小发布跨度准入，本例至少2槽，配置32槽'),
           ('本地路径修复','本地拷贝/存储绕开发送队列，不生成WQE、门铃或网络ACK；原生完整执行待验收'),
           ('验证','816种CPU队列情形 / 1,384断言；5项Python方法；新增公开/原生队列拒绝各1类'),
           ('逐包核对','旧EP32失败原件：10,001源包 / 3,562已交付包；只证明中断前的记录一致'),
           ('重传口径','已有事务恢复原件：608B唯一数据 / 792B实际发送 / 184B重复发送；没有新跑网络'),
           ('审计修正','核对实际TX/RX与负载、拒绝缺失最终存储记录；正常条件不预设零重传'),
           ('仅检查模式','公开--validate-only；8项CPU方法及完整正常/故障输入检查通过，不调用原生后端'),
           ('未启用提案','独立目录拟分配100,000条日志，仅transport.json不同；原配置仍10,000条，等待确认'),
           ('尚未完成','完整正常执行、故障执行、独立全量字节/DAG审计'),
           ('下一步','等待日志容量调整确认，保持其余时间、内存和磁盘预算')]
    table=''.join('<tr><th>'+html.escape(k)+'</th><td>'+html.escape(v)+'</td></tr>' for k,v in facts)
    public_link=os.path.relpath(repo/'examples/legacy_native/README.md',a.publish_dir.resolve() if a.publish_dir else out)
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>完整旧低延迟计划：实现与容量问题</title><style>
body{font:17px/1.7 system-ui,sans-serif;max-width:1040px;margin:40px auto;padding:0 22px;color:#182d43;background:#f5f8fc}
h1{line-height:1.3} .state{padding:18px;background:#fff0d8;border-left:6px solid #b56a09}
table{border-collapse:collapse;width:100%;background:white;margin-top:24px}th,td{padding:14px;border-bottom:1px solid #dde4ed;text-align:left;vertical-align:top}th{width:130px}a{color:#0659a8}
</style><h1>完整旧低延迟计划：实现与容量问题</h1>
<p class="state"><b>当前状态：尚未验收完整网络执行。</b><br>首次失败已保留，已补运行前容量检查；没有自动增加限额或重跑。</p><table>'''+table+'''</table>
<p>本页区分代码已实现、检查已通过和实际网络已完成。4,237ns是异常停止时刻，不是推理完成时间。816种情形是CPU队列核心检查，采用消费后显式给定的完成回调，不是816次网络实验。完整专家数值、GPU可见性、RoCE硬件和研究收益均未验证。</p>
<p><a href="verification.json">核验与资源摘要</a> · <a href="'''+html.escape(public_link,quote=True)+'''">公开命令</a></p></html>'''
    (out/'report.html').write_text(page)
    print(json.dumps(dict(failure_verified=True,complete_execution_accepted=False,minimum_source_sent_events=packets,
                         monitored_wall_seconds=evidence['monitored_wall_seconds'])))


if __name__=='__main__':main()
