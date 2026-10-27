"""Collect bounded acceptance evidence and a small standalone explanatory page."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import shutil

from fixtures.modern_expert_costs.accept import load,save,sha,identity_check
from moe_ub_cosim.host_probe import disk_usage


def collect(repo,stage,output):
    output.mkdir(parents=True,exist_ok=True)
    ledger=load(stage/'execution.json');recovery=load(stage/'restored/recovery.json')
    assert ledger['closed'] and ledger['state']=='completed' and ledger['network_attempts']==3
    assert recovery['passed'] and recovery['additional_networks']==1
    archive=load(stage/'checkpoint-source-bound.tar.receipt.json')
    assert sha(stage/'checkpoint-source-bound.tar.gz')==archive['sha256']==recovery['archive_sha256']
    identity=load(stage/'runtime-identity.json');identity_check(repo,stage/'runtime',identity)
    for name in ('modern_native.py','modern_native_layout.py','cli.py'):
        relative='python/moe_ub_cosim/'+name
        assert sha(repo/relative)==sha(stage/'restored/source'/relative)
    cases={name:load(stage/(name+'-verification.json')) for name in ('fixed','equal','skew')}
    assert all(row['passed'] for row in cases.values())
    assert cases['skew']==load(stage/'restored/migrated-verification.json')
    assert cases['skew']['corruptions_rejected']==['duration','actual_completion','dispatch_prerequisite','declared_source']
    invalid={}
    diagnostics={'beyond-horizon':'invalid expert stage duration','boolean':'invalid expert stage duration',
        'missing-call':'expert stage must cover every call','missing-rank':'expert stage must cover every rank',
        'missing-source':'invalid declared expert stage fields','negative':'invalid expert stage duration',
        'two-sources':'invalid declared local costs fields','wrong-version':'invalid modern native input fields'}
    for name,message in diagnostics.items():
        label='invalid-'+name;probe=load(stage/'monitor'/label/'probe.json')
        stderr=(stage/'monitor'/label/'stderr.log').read_text()
        assert probe['status']=='child_failed' and probe['child_exit_code']==1 and message in stderr
        assert not (stage/('rejected-'+label)).exists()
        invalid[name]=dict(rejected_before_output=True,diagnostic=message,exit_code=1)
    counts={}
    for label in ('units-source-bound','modern_deepep_runtime_test','modern_deepep_windows_test','scaleout_transactions_test'):
        log=(stage/'monitor'/label/'stderr.log').read_text()
        assert log.rstrip().endswith('OK')
        counts[label]=int(re.search(r'Ran (\d+) tests',log)[1])
    assert sum(counts.values())==48
    probes={p.parent.name:load(p) for p in (stage/'monitor').glob('*/probe.json')}
    for name,probe in probes.items():
        if name=='closeout':continue
        assert not probe['remaining_live_group_pids']
        expected=1 if name.startswith('invalid-') else 0
        assert probe['child_exit_code']==expected
        assert probe['status']==('child_failed' if expected else 'completed')
    seconds=sum(p['wall_seconds'] for p in probes.values())+ledger['driver_wall_seconds']
    usage=disk_usage(stage);published=disk_usage(output)
    assert seconds+60<600 and max(usage.values())+max(published.values())+2*2**20<256*2**20
    summary=dict(passed=True,platform_feature_complete=True,overall_research_goal_complete=False,
        test_counts=counts,unique_unit_tests=48,invalid_inputs=invalid,network_executions=4,
        baseline_v1_replay=load(stage/'v1-baseline-replay.json'),equal_cost_comparison=load(stage/'equal-comparison.json'),
        cases=cases,migration=recovery['migration'],corruption_classes=cases['skew']['corruptions_rejected'],
        archive_sha256=archive['sha256'],source_description_bound_correction=load(stage/'source-bound-correction.json'),
        source_packets_per_condition=100,modern_events_per_condition=322,
        matched_hardware_profile=False,new_algorithm_benefit_proven=False)
    save(output/'verification.json',summary)
    for source,name in ((stage/'checkpoint-source-bound.tar.receipt.json','checkpoint.json'),
        (stage/'restored/recovery.json','recovery.json')):shutil.copyfile(source,output/name)
    condition_rows=[]
    for name,title in (('fixed','v1 固定成本'),('equal','v2 等值矩阵'),('skew','v2 异值矩阵')):
        row=cases[name];app=row['application']['application']
        assert app['actual_source_packets']==100 and app['modern_events']==322 and app['modern_operations']==60
        assert app['calls']==2 and app['ranks']==2
        condition_rows.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in (
            title,f"{row['application']['finish_ns']/1000:.3f}",100,322,'通过'))+'</tr>')
    matrix=''.join('<tr>'+''.join('<td>'+str(v)+'</td>' for v in (
        row['call'],row['rank'],row['declared_ns'],row['dispatch_complete_ns'],row['started_ns'],
        row['completed_ns'],row['combine_begin_ns']))+'</tr>' for row in cases['skew']['expert_stages'])
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>专家计算就绪与网络通信：新增能力及边界</title><style>
body{background:#f3f6f8;color:#193246;font:17px/1.8 system-ui,sans-serif;margin:0}main{max-width:1080px;margin:24px auto;padding:32px;background:white;border-radius:12px}
h1{font-size:28px;line-height:1.4}h2{font-size:22px;margin-top:30px}.table{overflow:auto}table{width:100%;border-collapse:collapse;font-size:15px}td,th{padding:10px;border:1px solid #d9e3ea;text-align:left;white-space:nowrap}th{background:#eaf2f7}code{background:#edf2f6;padding:2px 4px}a{color:#166298}@media(max-width:700px){main{margin:0;padding:18px}}
</style><main><h1>让计算就绪时间进入网络对照</h1>
<p>原来所有卡的专家阶段统一用一个固定耗时。现在可以为每次调用、每张逻辑卡分别声明耗时，仍由同一次ns-3网络中的实际回调推进后续通信。此项是平台输入能力，尚不是新算法、真实GPU校准或论文收益。</p>
<h2>它解决什么问题</h2><p>专家访问不均匀既可能改变目的卡流量，也可能改变计算就绪时间。固定1000ns的模式不能描述第二条路径。新增矩阵让我们在同一请求、专家放置和网络资源下，控制计算条件，检查简化模型是否把计算迟到误判成网络排队。矩阵不会自动从专家token数生成耗时，后续仍需匹配的算子与硬件测量。</p>
<p><strong>实际Dispatch完成 → 本地专家工作开始 → 经过声明耗时并真实完成 → 解锁Combine。</strong>零成本也需要真实完成回调；窗口、计数、屏障、动态slot和源保护全部保留。</p>
<h2>四个功能条件</h2><p>两rank、两次调用，H256/E4/Top2，完整长度合成字节；GPU标签NONE，已有选择性重传基线，本轮不注入丢包。表中微秒为整个原生会话排空，不是实测推理请求时延。</p>
<div class="table"><table><tr><th>条件</th><th>会话排空 μs</th><th>源发送包数</th><th>现代事件数</th><th>核验</th></tr>'''+''.join(condition_rows)+'''<tr><td>v2 异值迁移</td><td>110.631</td><td>100</td><td>322</td><td>完整行为相同；仅库根路径不同</td></tr></table></div>
<p>源发送包包含控制请求和原子回复，不含独立ACK/CNP。等值矩阵保持原完整网络result、control和现代执行；v2新增来源说明单列。异值矩阵用于验证接口，改变了计算条件，不能把两行差值解释为优化收益。</p>
<h2>异值矩阵如何实际生效</h2><p>输入为<code>[[0,5000],[7000,100]]</code>纳秒，行是调用、列是逻辑rank，与物理节点编号无关。下表全部时间为同一个原生网络时钟的纳秒；服务时间用完成减实际开始核对，排队另计。</p>
<div class="table"><table><tr><th>调用</th><th>rank</th><th>声明耗时</th><th>Dispatch完成</th><th>实际开始</th><th>实际完成</th><th>Combine开始</th></tr>'''+matrix+'''</table></div>
<h2>验证了什么，还缺什么</h2><p>48项单元检查、8类公开入口非法输入拒绝、四类证据篡改拒绝通过；旧v1控制器对实际回调的完整提交/快照回放一致。复制源码和运行时、空工作目录下，三份离线核验逐字节相同，第四个实际网络保持完整行为。未改依赖，未构建、下载或运行GPU。</p>
<p>未验证真实GPU专家耗时、自动热点计算模型、较大EP完整执行或新方法收益。原始专家路由的长期热度、逐步对齐与目的卡聚集也应分开，不能用热点直方图直接推断网络等待。</p>
<h2>与两个研究候选的联系</h2><p><strong>候选一：更精简但能可靠选择网络的负载模型。</strong>新增能力允许后续检验计算就绪与网络依赖是否影响方案选择，必须与充分使用这些输入的逐链路阶段模型和完整DAG比较，不能只赢弱字节基线。</p>
<p><strong>候选二：在正确保留反馈的前提下降低大EP仿真成本。</strong>计算完成可能触发新通信，不能依靠固定注入时刻跳过反馈。该接口不解决大EP连接/控制表示增长，也没有仿真加速证据；下一步仍要证明相对优化后强基线实际减少的CPU、RSS或事件量。</p>
<p><a href="../../experiments/ep_network_projection/results/20261027/report.html">真实路由、EP差异与平台比较</a> · <a href="../../examples/modern_deepep_native/README.md">输入和运行方法</a> · <a href="verification.json">核验数据</a> · <a href="recovery.json">迁移复现</a></p></main></html>
'''
    (output/'report.html').write_text(page)
    closeout=dict(phase_completed=True,overall_research_goal_completed=False,network_executions=4,
        new_downloads=0,new_builds=0,gpu_executions=0,dependency_modifications=0,pushes=0,
        unit_tests=48,admission_rejection_classes=8,corruption_classes=4,dependency_checkouts=13,
        monitored_seconds_before_closeout=seconds,driver_conservatively_added=True,stage_seconds_limit=600,
        artifact_usage=usage,versioned_usage=published,artifact_limit_bytes=256*2**20,receipts_reserved_bytes=2*2**20,
        archive=archive,previous_archive_preserved=True,source_bound_correction_preserved=True,
        result_sha256={path.name:sha(path) for path in output.iterdir() if path.is_file() and path.name!='closeout.json'})
    save(stage/'closeout.json',closeout);save(output/'closeout.json',closeout)
    print('Published matrix admission, actual causal completion and portable acceptance evidence.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('repository','stage','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();collect(a.repository.resolve(),a.stage.resolve(),a.output.resolve())
