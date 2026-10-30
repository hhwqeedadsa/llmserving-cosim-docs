"""Package a source-backed, offline-readable recovery-scope audit."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from prepare import ROOT,files,load,save,sha

class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):self.links.extend(v for k,v in attrs if k in ('href','src'))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--task-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();task=a.task_root.resolve();out=a.output.resolve()
    frozen=load(task/'input/frozen.json');result=load(task/'analysis/analysis.json');checks=load(task/'analysis/verification.json')
    assert frozen['passed'] and result['passed'] and checks['passed']
    assert sha(Path(__file__).with_name('analyze.py'))==result['analyzer_sha256']
    assert sha(Path(__file__).with_name('dependencies.py'))==result['dependency_analyzer_sha256']
    for name,digest in frozen['source_sha256'].items():assert sha(ROOT/name)==digest
    for name,digest in frozen['analyzer_sources_sha256'].items():assert sha(ROOT/name)==digest
    for record in frozen['records'].values():assert files(Path(record['path']))==record['sha256']
    assert checks['negative_cases']==16 and checks['new_network_executions']==0
    out.mkdir(parents=True,exist_ok=False);(out/'figures').mkdir()
    for name,source in [('analysis.json',task/'analysis/analysis.json'),('verification.json',task/'analysis/verification.json'),('frozen.json',task/'input/frozen.json')]:
        shutil.copyfile(source,out/name)
    for source in (task/'analysis').glob('*.csv'):shutil.copyfile(source,out/source.name)
    requests=result['per_request'];first=result['first_stage'];backup=result['complete_backup'];subset=result['subset_accounting_only']
    rows=''.join(f'<tr><td>{q["primary_request"]}</td><td>{q["file_id"]}</td><td>{"是" if q["directly_has_overdue_input"] else "否"}</td><td>{q["first_remote_inputs_received_by_trigger"]}/{q["first_remote_inputs"]}</td><td>是</td><td>{q["network_payload_bytes"]:,}</td><td>{q["declared_compute_ns"]/1000:.3f}</td></tr>' for q in requests)
    plt.rcParams.update({'font.family':'DejaVu Sans','svg.hashsalt':'recovery-scope-dependencies-v1'})
    fig,axes=plt.subplots(1,2,figsize=(11,4.5));x=np.arange(len(requests));colors=['#c26038' if q['directly_has_overdue_input'] else '#126985' for q in requests]
    axes[0].bar(x,[q['network_payload_bytes']/1000 for q in requests],color=colors)
    axes[1].bar(x,[q['declared_compute_ns']/1000 for q in requests],color=colors)
    for ax in axes:
        ax.set_xticks(x,[str(q['primary_request']) for q in requests]);ax.set_xlabel('Original request identity (rank/slot mapping)');ax.grid(axis='y',alpha=.18)
    axes[0].set(ylabel='Effective network payload (kB)',title='Complete two-step backup: payload by request')
    axes[1].set(ylabel='Sum of declared node work (us)',title='Work attribution is not independent execution')
    fig.text(.5,.015,'Orange: directly has an overdue input. Blue: no overdue input, but still blocked by the group.',ha='center',fontsize=9)
    fig.tight_layout(rect=(0,.055,1,1))
    fig.savefig(out/'figures/request-work.png',dpi=150,metadata={'Software':'Matplotlib'})
    fig.savefig(out/'figures/request-work.svg',metadata={'Date':None});plt.close(fig)
    svg=out/'figures/request-work.svg';svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines())+'\n')
    small_bytes=sum(b['network_payload_bytes'] for b in result['missing_input_bundles'])
    small_work=sum(b['declared_expert_and_reduce_ns'] for b in result['missing_input_bundles'])
    source_rows=''.join(f'<tr><td>{name}</td><td>{s["posted"]}</td><td>{s["confirmed"]}</td><td>{len(s["pending"])}</td><td>{len(s["overdue"])}</td></tr>' for name,s in result['source_only_status'].items())
    missing_rows=''.join(f'<tr><td>{b["dispatch_message"]}</td><td>{b["primary_request"]}</td><td>{b["dst"]}</td><td>{", ".join(map(str,b["experts"]))}</td><td>{b["actual_input_receive_ns"]/1000:.3f}</td><td>{b["actual_source_ack_ns"]/1000:.3f}</td></tr>' for b in result['missing_input_bundles'])
    page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>恢复粒度：少量缺失为什么卡住整个MoE任务</title><style>
body{{margin:0;background:#f2f5f8;color:#20313d;font:16px/1.8 system-ui,sans-serif}}main{{max-width:1080px;margin:auto;padding:32px 20px 60px}}h1{{font-size:31px;line-height:1.4}}h2{{font-size:23px}}section{{background:white;border-radius:12px;margin:20px 0;padding:22px 26px}}.lead{{font-size:19px}}.note{{background:#fff2d8;padding:14px;border-left:4px solid #dc8d27}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{border-bottom:1px solid #dce3e9;padding:10px;text-align:right;white-space:nowrap}}td:first-child,th:first-child{{text-align:left}}.scroll{{overflow:auto}}img{{width:100%;height:auto}}a{{color:#126985}}small{{color:#536879}}</style>
<main><small>2026-10-30 · 128模拟端点Clos · 五份已保存网络只读 · 零新网络/构建/GPU</small>
<h1>恢复粒度：少量缺失为什么卡住整个MoE任务</h1>
<p class="lead">本例只缺3份首阶段输入，却通过全组完成门限约束全部4个目标请求。完整备用确实重复工作，但“只重做这3个请求”在现有消费者下不能保证提前交付。本轮交付依赖与成本分析工具，为下一步接口修改提供依据，没有新增恢复收益。</p>
<section><h2>承接的强基线与本轮问题</h2><p>前轮在相同真实专家选择和128端点Clos上，19.030微秒启动完整备用，使局部故障目标从201.866降至142.428微秒，少29.44%；额外有效通信27.51%。同刻直接启动与源确认检查的故障逐包结果相同，成熟早备用已覆盖这部分加速。本轮不再重跑或重新包装这项收益，只核对重复工作能否缩小。</p>
<p>保持四组EP32、每rank每层8专家、E256/Top8/H7168；15份不同路由原件，目标4份/健康11份，原层3/4、两个Decode入口。只读control-normal/control-local、fixed-local、guarded-normal/guarded-local五份网络，全部文件SHA保持。</p></section>
<section><h2>先分清“未完成”和“超时”</h2><p>源端判断只读取实际发送/完成回调和已提交图，在19.030微秒之前截断。同纳秒按原生交错顺序保留；接收时间、未来路由结果和包路径只用于事后评价，不输入判断器。</p>
<div class="scroll"><table><thead><tr><th>条件</th><th>已post</th><th>已源确认</th><th>仍pending</th><th>超过18µs期限</th></tr></thead><tbody>{source_rows}</tbody></table></div>
<p>正常任务也有26条pending，来自已推进到的后续通信；它们还没等满期限。故障任务只剩3条pending，却已超时。因此“pending多”或“还有没完成的消息”都不能单独作为故障或备用启动条件。18微秒来自前轮正常214条消息跨度标定，本轮没有改参，也不估计生产误判率。</p></section>
<section><h2>三个缺失输入都涉及专家56</h2><div class="scroll"><table><thead><tr><th>Dispatch消息</th><th>原请求</th><th>原目标rank</th><th>专家</th><th>实际输入到达 µs</th><th>实际源确认 µs</th></tr></thead><tbody>{missing_rows}</tbody></table></div>
<p>首轮26条远端输入中，23条已经收到，共332,672字节；另外3条尚未收到，共43,392字节。这3份来自不同请求的选择恰好都指向rank28上的专家56，不是随机生成的热点，但这一小窗口不能代表整个模型长期热点分布。</p>
<p>实际数据最后在86.676微秒到达，最后源确认在90.680微秒到达，当前guarded运行的首Dispatch门限随后释放。两者相差4.004微秒，来自当前完整传输flush口径中保留的源确认义务；它是事件间隔，不能直接宣称为真实通信库可消除的等待。</p></section>
<section><h2>为什么第4个请求也被卡住</h2><p>当前图为每rank建立Dispatch门限，每个门限的完成又要求本EP组所有成员的本地依赖完成。专家计算要等这个门限；下一Decode步还要等前一步整图退休。分析工具展开这些真实约束后，三个原SEND确认根或三个原RECV根都能约束32个首层专家节点，并沿后续依赖到达全部4个目标请求的末端。</p>
<div class="scroll"><table><thead><tr><th>原请求</th><th>路由文件</th><th>直接有超时输入</th><th>首阶段远端输入已到</th><th>受组依赖约束</th><th>完整备用字节</th><th>声明工作 µs</th></tr></thead><tbody>{rows}</tbody></table></div>
<p>请求0的6份远端输入都已收到，仍受其他请求的门限约束。它在本次原尝试中的末端完成时间也是201.880微秒；当前结果消费者只接受整个尝试的最终图退休，不能混用3个备用请求和1个原请求便声称142.428微秒完成。若改变流量，必须重新执行共享网络，不能从这份记录拼接反事实时延。</p>
<p class="note">闭包表示“执行结构存在约束路径”，不是每条路径都造成实际时延，也不是可消除延迟。SEND源确认和RECV数据就绪分别保留，没有把ACK当成接收的前驱。分析没有删除任何门限或伪造确认。</p></section>
<section><h2>有哪些已完成工作可以保留</h2><p>触发之前28/32个成员已经到达首Dispatch门限，但32个首层专家节点均未issue，也均未完成。完成的声明计算只有4,120纳秒，来自首层attention和pack；23份远端激活已经到达。</p>
<p>因此本例可以讨论复用已到输入和打包结果，不能说已经保留了大量健康专家计算。完整两步备用共111个token→rank贡献包、128次专家贡献，传输3,088,448字节、声明计算31,460纳秒。这些是工作量之和，不是GPU内核独立耗时或端到端时延。</p><img src="figures/request-work.png" alt="完整两步备用按请求分摊的真实投影字节和声明计算，直接超时请求与连带阻塞请求分色">
</section>
<section><h2>账面节省与可执行优化必须分开</h2><div class="scroll"><table><thead><tr><th>方案或单位</th><th>字节</th><th>声明计算 ns</th><th>当前能否交付同一完整结果</th></tr></thead><tbody>
<tr><td>完整4请求、两步备用</td><td>{backup['network_payload_bytes']:,}</td><td>{backup['declared_compute_ns']:,}</td><td>可以，已有实际执行</td></tr>
<tr><td>仅3个直接关联请求的完整重做</td><td>{subset['selected_network_bytes']:,}</td><td>{subset['selected_declared_compute_ns']:,}</td><td>尚不可以，消费者和未复制请求仍受约束</td></tr>
<tr><td>仅首阶段3个缺失贡献的输入+返回</td><td>{small_bytes:,}</td><td>{small_work:,}</td><td>尚不可以，仅为一个阶段的候选单位</td></tr>
</tbody></table></div>
<p>第三行只计三份声明输入/结果和专家工作，不包含其他健康贡献、后续层/步、控制、缓冲和清理；与第一行任务范围不同，不能相减得出“97%优化收益”。第二行排除了请求0的750,464字节和8,040纳秒备用工作，但还没有等价交付方案，也不能把账面差当成实际节省。</p>
<p>已有inflight_helpers可以真实执行一个token在目标rank上的完整专家贡献，且有一次提交与退休记录；它仍不替代原图门限和原SEND/RECV。逻辑贡献先返回不等于原任务已经继续。whole-call模式与helper模式也不能直接混开。</p></section>
<section><h2>下一步应改什么，如何验证</h2><ol>
<li>先核查固定通信实现中的数据就绪、通知与消费接口，确定是否允许按token/rank贡献推进。若真实实现仍要求全组完成，就不把删门限作为优化。</li>
<li>若接口允许，显式增加应用贡献消费者：按请求、原层、Decode入口、专家集合与输入代际核对，原/备用首份有效结果只消费一次；原连接源确认、图退休和缓冲释放另行完成。</li>
<li>原/备用使用隔离缓冲与尝试代际，迟到写不能覆盖已消费或已复用槽；正常与帮助计算共用同一物理GPU队列，全部通信共用原128端点Clos。</li>
<li>在相同消费语义与资源下比较原地SR、成熟完整早备用和贡献补齐，保留全部原结果及清理。只有目标收益保持、重复工作下降且正常开销可接受，才继续讨论方法差异和投稿。</li>
</ol><p>候选研究点可以收敛为“可见进展约束下，按缺失贡献补齐，并隔离迟到结果的应用推进”。这仍是待验证机制，不能凭本轮依赖图证明新颖性或性能。当前最小缺项是可替代贡献驱动的真实应用消费接口。</p></section>
<section><h2>验证和边界</h2><p>7项CPU规则测试覆盖跨rank/跨步传播、无门限反例、缺前驱/环/非法成员以及源确认前缀；真实输入/图/源事件16项篡改拒绝通过。五份旧网络不变，零新网络、构建、依赖修改、下载或GPU。小型CPU图不替代128端点网络，闭包不模拟新的执行。</p>
<p>专家选择是真实数据，消息布局与计算是声明，没有激活张量、权重或数值等价验证；只含两层两个Decode步，不是完整671B、p99、稳态服务或商业收益。图已查看；网页做静态链接/资源核查，不声称浏览器渲染验收。</p>
<p><a href="analysis.json">分析与依赖路径</a> · <a href="per-request.csv">逐请求工作</a> · <a href="missing-bundles.csv">缺失贡献</a> · <a href="verification.json">拒绝检查</a> · <a href="frozen.json">冻结输入</a> · <a href="receipt.json">命令与资源</a> · <a href="final-audit.json">交付核查</a> · <a href="README.md">结论</a> · <a href="../../README.md">复现方法</a> · <a href="../../protocol.md">执行前约定</a> · <a href="../../../ack_guard_whole/results/20261030/report.html">前轮强基线</a></p></section></main></html>'''
    (out/'report.html').write_text(page)
    (out/'README.md').write_text('''# 恢复粒度与依赖核查结果

[网页](report.html)、[分析](analysis.json)、[逐请求工作](per-request.csv)、[缺失贡献](missing-bundles.csv)、[核验](verification.json)、[固定输入](frozen.json)、[命令与资源](receipt.json)、[交付核查](final-audit.json)。

19.030µs时正常已有52次post、26次源确认、26条pending但0条超时；故障26次post、23次源确认、3条超时。不能以pending数代替源端期限。

首阶段26份远端输入已有23份/332,672B收到，3份/43,392B尚未收到。3份缺失来自请求4/8/12，全部涉及专家56/原目标rank28；这是实际原件选择，不代表长期热点。全组门限与跨步图退休依赖使4个目标请求都受约束；请求0的6份远端输入已全收到，仍不能独立推进。SEND确认与RECV根分别可达32个首层专家节点及全部4个末端，闭包只是结构约束，不是可消除时延。

触发前28/32个成员到达门限，首层32个专家节点均未issue或完成。已完成声明计算4,120ns是attention+pack，不能声称已保留健康专家计算。当前guarded运行输入最后86.676µs到达、源确认最后90.680µs到达并释放门限；4.004µs差距只是当前flush口径的事件间隔，不证明通信库可消除。

完整备用包含111个token/rank贡献包、128次专家贡献，3,088,448B/31,460ns声明工作。仅复制3个直接相关请求账面为2,337,984B/23,420ns，但现消费者不接受子集或跨尝试拼结果；仅3个首阶段贡献为86,592B/300ns，尚未包含其他贡献/后续层步与控制清理，不能与完整任务相减声称优化率。当前helper有实际资源/一次提交语义，但仍不替代原图门限。

本轮没有新性能收益或平台执行修改。下一步先核实通信通知/消费接口是否允许按贡献推进，再定义应用就绪与原连接退休分离、一次消费与迟到隔离；若需新执行模式，成熟早备用基线必须采用相同语义和资源。不能直接删全组门限或伪造ACK。

7项CPU规则测试和16项真实前缀/图/配置篡改拒绝通过，五份旧网络逐文件SHA不变；零新网络、构建、依赖改动、下载/GPU。128端点Clos、四组EP32、15份真实选择、原层3/4/两个Decode入口，计算/BF16布局/现成副本为声明；不是完整671B、数值等价、生产p99或经济收益。整体目标active。
''')
    for name in ('receipt.json','final-audit.json'):save(out/name,dict(passed=False,status='checking'))
    stages={p.parent.name:load(p) for p in (task/'monitors').glob('*/probe.json')}
    assert all(v['status']=='completed' and v['child_exit_code']==0 and not v['remaining_live_group_pids'] for v in stages.values())
    elapsed=sum(v['wall_seconds'] for v in stages.values());assert elapsed+120<600
    usage={};seen=set()
    for base in (task,ROOT/'experiments/recovery_scope'):
        apparent=allocated=0
        for path in (base,*base.rglob('*')):
            st=path.lstat();key=st.st_dev,st.st_ino
            if key in seen:continue
            seen.add(key);apparent+=st.st_size;allocated+=st.st_blocks*512
        usage[str(base.relative_to(ROOT))]=dict(apparent_bytes=apparent,allocated_bytes=allocated)
    used=sum(max(v.values()) for v in usage.values());assert used+2**20<256*2**20 and shutil.disk_usage(task).free>=8*2**30
    receipt=dict(passed=True,preregistration_commit='1385a85bb4',network_executions=0,read_only_runs=5,
        actual_commands_and_status=stages,wall_upper_bound_seconds=elapsed+120,cumulative_wall_budget_seconds=600,
        artifact_usage=usage,artifact_bytes_before_final_receipts=used,receipt_reserve_bytes=2**20,artifact_budget_bytes=256*2**20,
        free_bytes=shutil.disk_usage(task).free,min_free_bytes=8*2**30,builds=0,dependencies_modified=False,downloads=0,gpu_used=False,
        source_sha256={str(path.relative_to(ROOT)):sha(path) for path in Path(__file__).parent.iterdir() if path.is_file()},
        new_performance_benefit_proven=False,overall_goal_complete=False)
    (out/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    parser=Links();parser.feed(page);links=[]
    for value in parser.links:
        assert not value.startswith(('http:','https:'))
        path=out/value;assert path.exists(),value;links.append(value)
    final=dict(passed=True,links_checked=links,plots=1,images_visually_inspected=False,browser_rendering_checked=False,
        graph_rule_tests=7,negative_real_cases=16,unchanged_networks=5,new_networks=0,
        scientific_scope='Structural constraints and declared work, not a modified execution or novel performance benefit',
        artifact_sha256={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='final-audit.json'})
    (out/'final-audit.json').write_text(json.dumps(final,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(passed=True,links=len(links),artifact_bytes=used,wall_upper_bound_seconds=elapsed+120)))

if __name__=='__main__':main()
