"""Bind complete legacy functional acceptance to retained raw inputs and audits.

Reads existing executions only; never launches a network or changes budgets.
"""
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
    p.add_argument('--publish-dir',type=Path,required=True)
    a=p.parse_args();root=a.root.resolve();stage=root/'authorized-capacity-100k'
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    repo=Path(__file__).resolve().parents[2]
    load=lambda path:json.loads(path.read_bytes())
    sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    auth=load(stage/'authorization.json');assert auth['capacity_change_approved'] and auth['proposal_activated']
    assert auth['changed_fields']==['dcqcn_reference.max_events'] and auth['other_budgets_unchanged']
    assert sha(root/'normal/result.json')==auth['old_failure_sha256']
    assert load(root/'inputs/transport.json')['dcqcn_reference']['max_events']==10000
    for name,expected in auth['inputs_sha256'].items():assert sha(stage/'inputs'/name)==expected
    for name,expected in auth['source_sha256'].items():assert sha(repo/name)==expected
    build=load(root/'build/backend.json')
    assert sha(Path(build['legacy_deepep']))==auth['backend_sha256']
    for path in build['linked_ns3_libraries']:assert sha(Path(path))==auth['library_hashes'][Path(path).name]
    summaries={};witnesses={};raw_hashes={}
    for case,ep,experts,calls in [('normal',32,256,1),('fault',32,256,1),('cross-call',32,128,2)]:
        run=stage/case;receipt=load(run/'inputs.json');work=load(run/'execution.json')
        result=load(run/'result.json');audit=load(stage/(case+'-audit.json'))
        assert result['passed'] and result['drained'] and result['legacy']['finished'] and audit['passed']
        assert result['gpu_count']==128 and work['ranks']==ep and work['experts']==experts
        assert not receipt.get('validation_only',False) and 'command' in receipt
        for name,expected in receipt['hashes'].items():assert sha(Path(name))==expected
        assert max(op[1] for op in work['operations'])+1==calls
        net=result['legacy']['transport'];reference=net['dcqcn_reference']
        assert reference['config']['max_events']==100000 and len(reference['events'])<=100000
        assert audit['operations']==len(work['operations']) and audit['events']==len(work['events'])
        for suffix in ('','-audit'):
            monitor=load(root/'monitors'/('authorized-'+case+suffix)/'probe.json')
            assert monitor['status']=='completed' and monitor['child_exit_code']==0 and not monitor['remaining_live_group_pids']
        raw_hashes[case]={name:sha(run/name) for name in ('inputs.json','execution.json','result.json')}
        raw_hashes[case]['audit.json']=sha(stage/(case+'-audit.json'))
        summaries[case]=dict(audit,endpoints=128,ep_size=ep,experts=experts,calls=calls,
            reference_events=len(reference['events']),reference_event_kinds=dict(Counter(e['kind'] for e in reference['events'])),
            raw_result_bytes=(run/'result.json').stat().st_size)
        if case in ('normal','fault'):
            candidates=[i for i,(op,row) in enumerate(zip(work['operations'],result['legacy']['operations']))
                        if result['legacy']['contexts'][row[0]][3]==0 and row[1]==0]
            assert len(candidates)==1
            i=candidates[0];op=work['operations'][i];row=result['legacy']['operations'][i]
            assert op[3]=='count' and op[9]==-1 and op[4:7]==[0,1,8]
            receive=[j for j,e in enumerate(work['events']) if e[0]=='dispatch_packed' and e[1]==1]
            assert len(receive)==1;event=receive[0];times=result['legacy']['event_times_ns']
            assert op[10][2] in work['events'][event][2]
            latest=max(work['events'][event][2],key=lambda j:times[j])
            witnesses[case]=dict(operation=op[0],source_rank=op[4],destination_rank=op[5],expert=op[6],
                notification_value=op[9],visible_ns=times[op[10][2]],source_completed_ns=row[8],
                receive_barrier_ns=times[event],latest_dependency=latest,latest_dependency_ns=times[latest],
                notification_precedes_last_dependency=times[op[10][2]]<times[latest],
                source_packets=[packet for packet in net['source_packets'] if packet[1]==0 and packet[5]==0])
            if case=='fault':
                assert result['fault_injection']['drop_counts']==[1]
                witnesses[case]['drops']=result['fault_injection']['events']
                recovery=net['recovery']
                witnesses[case]['recovery_events']=[dict(zip(recovery['columns'],row)) for row in recovery['events']
                                                   if row[recovery['columns'].index('message_id')]==0]
        del result,work
    assert summaries['normal']['final_memory_sha256']==summaries['fault']['final_memory_sha256']
    assert summaries['normal']['logical_transport_bytes']==summaries['fault']['logical_transport_bytes']==1832832
    assert summaries['fault']['retransmitted_payload_bytes']==36
    assert (stage/'normal/execution.json').read_bytes()==(stage/'fault/execution.json').read_bytes()
    assert all(w['notification_precedes_last_dependency'] for w in witnesses.values())
    stages=[]
    for path in sorted((root/'monitors').glob('*/probe.json')):
        value=load(path);assert not value['remaining_live_group_pids']
        stages.append(dict(stage=path.parent.name,status=value['status'],wall_seconds=value['wall_seconds'],
                           peak_rss_bytes=value['peak_sampled_group_rss_bytes']))
    assert sum(s['wall_seconds'] for s in stages)<1800
    assert max(s['peak_rss_bytes'] for s in stages)<=2*2**30
    sources=dict(auth['source_sha256'])
    for name in ('tests/integration/summarize_legacy_acceptance.py','examples/legacy_native/cross-call-input.json'):
        sources[name]=sha(repo/name)
    evidence=dict(schema_version='moe-legacy-native-acceptance/1',passed=True,
        complete_legacy_functional_execution_accepted=True,new_network_executions=3,
        network='128-endpoint Clos',normal_and_fault_same_inputs_except_fault_rule=True,
        capacity_change_authorized=True,reference_event_limit=100000,other_budgets_unchanged=True,
        authorization_sha256=sha(stage/'authorization.json'),original_failure_preserved=True,
        original_failure_sha256=auth['old_failure_sha256'],conditions=summaries,notification_witnesses=witnesses,
        task_finish_delta_fault_minus_normal_ns=summaries['fault']['last_kernel_complete_ns']-summaries['normal']['last_kernel_complete_ns'],
        fault_final_memory_equals_normal=True,cross_call_reuse_accepted=True,
        cross_call_experts=128,full_E256_multi_call_accepted=False,
        numerical_experts_executed=False,real_routes=False,hardware_calibrated=False,
        algorithm_innovation_validated=False,physical_gpu_executed=False,
        source_hashes=sources,raw_hashes=raw_hashes,stages=stages,
        monitored_wall_seconds_before_summary=sum(s['wall_seconds'] for s in stages),
        peak_sampled_group_rss_bytes=max(s['peak_rss_bytes'] for s in stages),
        scope='Legacy v1.2.1 functional BF16 RDMA-only projection; not modern GIN, RoCE wire, GPU timing or full serving')
    (out/'verification.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    rows=''.join('<tr><td>'+html.escape(name)+'</td><td>'+str(v['experts'])+'</td><td>'+str(v['calls'])+
        '</td><td>'+str(v['operations'])+'</td><td>'+f"{v['last_kernel_complete_ns']/1000:.3f}"+
        '</td><td>'+f"{v['physical_finish_ns']/1000:.3f}"+'</td><td>'+str(v['retransmitted_payload_bytes'])+'</td></tr>'
        for name,v in summaries.items())
    link=os.path.relpath(repo/'examples/legacy_native/README.md',a.publish_dir.resolve())
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>旧DeepEP完整通信：功能验收</title><style>body{font:17px/1.75 system-ui,sans-serif;max-width:1100px;margin:36px auto;padding:0 20px;color:#183047;background:#f5f8fc}h1{line-height:1.3}.pass{padding:18px;background:#e2f4e8;border-left:6px solid #248047}table{border-collapse:collapse;width:100%;background:white}td,th{padding:12px;border-bottom:1px solid #dce3e9;text-align:left}.scroll{overflow:auto}a{color:#0659a8}</style>
<h1>旧DeepEP完整通信：功能验收</h1><p class="pass">三次128端点Clos网络全部完成，独立逐包、依赖图和内存重放检查通过。该结果验证系统功能，没有建立新算法或生产性能收益。</p>
<p>用户授权后，参考日志容量从10,000改为100,000，其余时间、内存和磁盘预算保持。旧失败原件保留。首次失败之后还修复了本地操作误入发送队列等问题，不能把这两版执行当成仅一个参数不同的性能对照。</p>
<div class="scroll"><table><tr><th>条件</th><th>专家数</th><th>调用数</th><th>完整操作数</th><th>任务完成 µs</th><th>网络排空 µs</th><th>重复载体 B</th></tr>'''+rows+'''</table></div>
<p>三个条件都是EP32，网络均为128端点、20交换机、168链路。正常/故障条件为256专家、每rank8专家、4个人工Top8 token；补充的连续两调用为128专家、每rank4专家、共8个人工token，验证跨调用清理与复用，不替代256专家条件。</p>
<p>精确故障只丢一次零token通知载体：在2.002µs被丢，17µs触发超时重传，19.002µs交付，21.004µs源确认。额外发送36B，正常与故障最终内存相同。该通知在接收屏障的最晚前驱之前恢复，局部17µs延迟未形成同等任务延迟。两次任务完成相差−41ns，不作为故障有益或优化收益的证据；微小差值尚未逐跳归因。</p>
<p>发送字节包含MRT1帧头，不是模型有效激活量或全部线上字节。任务完成与最后ACK/排空分别报告；包核验子摘要中的complete_application_proven=false仅表示该子检查独立不证明业务完成，顶层验收还检查完整操作、DAG、内存、源退休和排空。</p>
<p>当前没有执行LLMServingSim请求调度、数值专家或GPU；功能可见性与专家拷贝为零成本假设。不是现代DeepEP/GIN或RoCE线协议，未校准生产硬件，也未验证256专家连续多调用。后续应接入真实专家选择并比较成熟恢复方法之后的任务等待，再评价算法空间。</p>
<p><a href="verification.json">逐条件核验与来源</a> · <a href="'''+html.escape(link,quote=True)+'''">公开操作</a> · <a href="history-before-authorization/report.html">授权前失败记录</a></p></html>'''
    (out/'report.html').write_text(page)
    print(json.dumps(dict(passed=True,new_network_executions=3,normal_and_fault_memory_equal=True,
                         cross_call_reuse_accepted=True,algorithm_innovation_validated=False)))


if __name__=='__main__':main()
