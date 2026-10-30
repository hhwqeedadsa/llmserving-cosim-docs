"""Render measured benefits, costs and attribution as an offline report."""
import argparse
import html
import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

def load(p):return json.loads(p.read_bytes())
def figure(fig,path):
    fig.savefig(path.with_suffix('.png'),dpi=150,metadata={'Software':'Matplotlib'})
    fig.savefig(path.with_suffix('.svg'),metadata={'Date':None})
    path.with_suffix('.svg').write_text('\n'.join(line.rstrip() for line in path.with_suffix('.svg').read_text().splitlines())+'\n')
    plt.close(fig)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--frozen',type=Path,required=True)
    p.add_argument('--analysis',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    analysis=load(a.analysis/'analysis.json');mechanism=load(a.analysis/'mechanism.json');checks=load(a.analysis/'prefix-checks.json')
    assert analysis['passed'] and mechanism['passed'] and checks['passed']
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);(out/'figures').mkdir();(out/'metrics').mkdir()
    shutil.copyfile(a.frozen,out/'frozen.json')
    for name in ('analysis.json','mechanism.json','prefix-checks.json'):shutil.copyfile(a.analysis/name,out/name)
    for path in a.analysis.glob('*.csv'):shutil.copyfile(path,out/'metrics'/path.name)
    cases=analysis['cases'];effects=analysis['effects'];cal=analysis['calibration']
    order=[policy+'-'+condition for condition in ('normal','local') for policy in ('control','fixed','guarded')]
    labels=[name.replace('control','No backup').replace('fixed','Fixed early').replace('guarded','ACK guard') for name in order]
    plt.rcParams.update({'font.family':'DejaVu Sans','svg.hashsalt':'ack-guard-complete-v1'})
    fig,axes=plt.subplots(1,2,figsize=(13,5.2),gridspec_kw={'width_ratios':[1.65,1]});ys=np.arange(6)
    for shift,key,label,color in [(-.23,'target','Target result','#126985'),(0,'healthy','Healthy result','#19a285'),(.23,'drain','Network drained','#dc8d27')]:
        values=[(cases[n]['network_drained_ns'] if key=='drain' else cases[n]['result_ready_ns'][key])/1000 for n in order]
        axes[0].barh(ys+shift,values,height=.21,color=color,label=label)
    for shift,key,label,color in [(-.16,'unique_payload_bytes','Effective payload','#126985'),(.16,'declared_compute_work_ns','Declared compute','#19a285')]:
        values=[(cases[n][key]/cases['control-'+n.split('-')[1]][key]-1)*100 for n in order]
        axes[1].barh(ys+shift,values,height=.30,color=color,label=label)
    for ax in axes:
        ax.set_yticks(ys,labels);ax.invert_yaxis();ax.grid(axis='x',alpha=.18)
        ax.legend(loc='upper center',bbox_to_anchor=(.5,-.15),ncol=2,fontsize=8)
    axes[0].set(xlabel='Actual completion time (us)',title='128-endpoint Clos: target, healthy and drain')
    axes[1].set(xlabel='Increase over matched no-backup case (%)',title='The full copy still costs work')
    fig.tight_layout();figure(fig,out/'figures/comparison')
    race=mechanism['actual_races']['local'];fig,ax=plt.subplots(figsize=(12,4.4))
    for i,role in enumerate(('primary','replica')):
        for j,w in enumerate(race[role]['waves']):
            start,stop=w['submitted_ns']/1000,w['finished_ns']/1000
            ax.barh(i,stop-start,left=start,height=.43,color=('#126985','#19a285')[j])
            ax.text((start+stop)/2,i,f'Decode {j+1}',ha='center',va='center',color='white',fontsize=9)
    for time,label,color in [(race['replica']['submitted_ns']/1000,'Backup starts','#775799'),(race['result_ready_ns']/1000,'Client result','#c26038')]:
        ax.axvline(time,linestyle='--',color=color,label=label)
    ax.set(yticks=[0,1],yticklabels=['Original (continues after loss)','Complete backup'],xlabel='Simulation time (us)',
        title='Local fault: actual execution of complete attempts')
    ax.invert_yaxis();ax.grid(axis='x',alpha=.18);ax.legend(loc='lower center',bbox_to_anchor=(.5,-.32),ncol=2,fontsize=8)
    fig.tight_layout();figure(fig,out/'figures/race')
    effect=effects['guarded-local'];normal=cases['control-normal'];local=cases['control-local'];guard=cases['guarded-local']
    fault_penalty=local['result_ready_ns']['target']-normal['result_ready_ns']['target']
    recovered=effect['target_saved_ns']/fault_penalty*100
    residual=guard['result_ready_ns']['target']-normal['result_ready_ns']['target']
    extra_percent=effect['extra_effective_bytes']/local['unique_payload_bytes']*100
    compute_percent=effect['extra_declared_compute_ns']/local['declared_compute_work_ns']*100
    names={'control':'不启动备用','fixed':'19.030µs直接启动','guarded':'源确认超时才启动'}
    rows=''
    for name in order:
        policy,condition=name.split('-');c=cases[name];attempt=c['calls']['target']['attempts']['replica']
        start='跳过' if attempt['submitted_ns'] is None else f'{attempt["submitted_ns"]/1000:.3f}'
        rows+=f'<tr><td>{"正常" if condition=="normal" else "局部故障"}</td><td>{names[policy]}</td><td>{start}</td><td>{c["result_ready_ns"]["target"]/1000:.3f}</td><td>{c["result_ready_ns"]["healthy"]/1000:.3f}</td><td>{c["network_drained_ns"]/1000:.3f}</td><td>{c["unique_payload_bytes"]:,}</td></tr>'
    phase_rows=''.join(f'<tr><td>{g["graph"]}</td><td>{g["phase"]}</td><td>{g["change_ns"]}</td><td>{len(g["tight_predecessors"])}</td></tr>' for g in sorted(mechanism['healthy_gates']['local'],key=lambda g:g['treatment_release_ns']))
    links=''.join(f'<li><a href="metrics/{html.escape(path.name)}">{html.escape(path.name)}</a></li>' for path in sorted((out/'metrics').glob('*.csv')))
    page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>源确认超时与完整备用：收益来自哪里</title><style>
body{{margin:0;background:#f2f5f8;color:#1c2d3c;font:16px/1.8 system-ui,sans-serif}}main{{max-width:1120px;margin:auto;padding:30px 20px 60px}}h1{{font-size:32px;line-height:1.35}}h2{{font-size:23px}}section{{background:white;border-radius:12px;padding:22px 26px;margin:20px 0}}.lead{{font-size:19px}}.note{{background:#fff2d8;padding:14px;border-left:4px solid #dc8d27}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:10px;border-bottom:1px solid #dce3e9;white-space:nowrap;text-align:right}}td:first-child,th:first-child{{text-align:left}}.scroll{{overflow:auto}}img{{width:100%;height:auto}}a{{color:#126985}}small{{color:#526678}}</style>
<main><small>2026-10-30 · 128模拟端点Clos · 15份不同真实专家路由 · 四个新网络，各一次</small>
<h1>源确认超时与完整备用：收益来自哪里</h1>
<p class="lead">当前局部短故障中，目标任务少等{effect['target_saved_ns']/1000:.3f}微秒（{effect['target_saved_percent']:.2f}%）。同刻直接启动已有备用机制得到完全相同的故障结果；确认信号的作用是让正常任务避免无效备用。这是成熟机制的可运行基线，尚未证明新的恢复算法。</p>
<section><h2>系统这次实现了什么</h2><p>前端记录实际SEND提交与源完成回调，使用同一ns-3仿真时钟上的控制定时器检查：已经发送、超过期限、仍未收到源确认，才启动完整两步备用。同时保留127微秒整段兜底。后续步由实际前步完成推进，备用未启动才可跳过；已启动的输家继续通信、确认和释放KV，没有假取消，也没有修改选择性重传。</p>
<p>平台新增显式格式/5、检查记录、预算拒绝和公开准备参数；77项CPU检查、四份旧网络公开只读核验与13项固定依赖检查通过。研究另用独立读取器重建全部原件消息矩阵、计算、依赖、门限、逐包/队列、原生决策及KV。{len(checks['negative_cases'])}项原始记录/配置篡改拒绝通过。</p></section>
<section><h2>任务和故障怎样设置</h2><p>128端点Clos，16台模拟服务器，8接入/8汇聚/4核心交换机，单rail，各层400Gbps、每链路1微秒。四组EP32，每rank每层8专家。15份不同R1-AWQ/MMLU专家选择原件分为4份目标、11份独立健康输入；E256、Top8、H7168，只取原层3/4和连续两个Decode入口。</p>
<p>normal没有额外故障；local在仿真前20微秒丢弃到GPU28的DATA，控制包保留，随后停止注入。各故障条件实际丢6包，重传43,392字节。两个条件都使用选择性重传和参考DCQCN。</p>
<p>只从原正常目标全部{cal['normal_messages']}条消息计算post→源完成的最大跨度{cal['normal_max_post_ack_ns']/1000:.3f}微秒，按1微秒向上取整为18微秒；最早post为1.030微秒，直接启动基线固定在19.030微秒。规则在四个新网络前提交，不扫描、不按故障结果调参。</p>
<p class="note">源确认超时只表示“到检查时尚无源完成回调”，不证明数据没到或设备损坏。轮询不保证每条消息精确到期触发。本例3条超时消息的名义期限和实际检查恰好都是19.030微秒，不能推广为零检测延迟。</p></section>
<section><h2>六种条件的实际结果</h2><div class="scroll"><table><thead><tr><th>网络</th><th>策略</th><th>备用启动 µs</th><th>目标 µs</th><th>健康 µs</th><th>排空 µs</th><th>有效载荷 B</th></tr></thead><tbody>{rows}</tbody></table></div><img src="figures/comparison.png" alt="目标、健康与网络收尾时间及额外通信和声明计算成本">
<p>确认检查在正常条件下不启动备用：完整包捕获和原始队列记录与原control完全一致。在故障条件下，两种策略同刻启动，完整逐包和队列记录也彼此完全相同。因此故障时的{effect['target_saved_ns']/1000:.3f}微秒加速来自更早启动；不能把已有超时与备用机制包装成新恢复协议。</p>
<p>相比正常时无条件早启动，确认检查省去3,088,448字节有效通信和31,460纳秒声明计算工作量，避免目标多等0.119微秒、健康多等0.151微秒、网络收尾晚15.860微秒。声明计算是各执行节点工作量之和，不是端到端时延。</p></section>
<section><h2>收益有多少，剩余空间怎样判断</h2><p>局部故障原本让目标从126.626变为201.866微秒，增加{fault_penalty/1000:.3f}微秒。早备用收回{effect['target_saved_ns']/1000:.3f}微秒，相当于本例故障额外等待的{recovered:.2f}%。目标仍比正常参考慢{residual/1000:.3f}微秒；这是参考差距，不是已证明可消除的优化上限。</p>
<p>故障时全任务有效载荷增加{effect['extra_effective_bytes']:,}字节（{extra_percent:.2f}%），声明计算增加{compute_percent:.2f}%。健康任务和全网排空均晚{effect['healthy_change_ns']}纳秒。备用KV仍预先保留，初始合计46,792,704字节，不能称正常零内存成本。结果提前交付后，原任务仍执行到201.880微秒，不能把142.428微秒当成集群资源全部释放时间。</p>
<p class="note">29.44%是单个构造故障下目标片段的条件收益，不是平均服务收益、集群吞吐增长或商业收益率。没有生产故障频率和持续到达负载，就不能换算全年收益。下一步应争取在这个成熟基线上减少额外通信/计算，同时保留目标收益；只有超过强基线、接口成立且独立场景可复现，才值得讨论论文创新。</p></section>
<section><h2>备用为什么这次能追上</h2><img src="figures/race.png" alt="原任务与19.030微秒启动的两步完整备用实际时间线"><p>上轮127微秒才启动的完整备用追不上原任务；本轮19.030微秒开始，两步备用在142.428微秒交付结果。原任务在201.880微秒结束。这个时间线来自实际提交、确认和图退休，原任务剩余时间仅用于事后解释，没有进入在线策略。</p></section>
<section><h2>健康任务受到什么影响</h2><p>健康DATA有4,264次交换机经过，全部可配对。故障条件下累计驻留变化为{effect['healthy_residence_change_sum_ns']:,}纳秒，但最终业务只晚14纳秒。逐阶段枚举全部最晚成员和紧约束前驱，并核对实际末DATA及源ACK路径，阶段释放变化如下：</p>
<div class="scroll"><table><thead><tr><th>图</th><th>阶段</th><th>释放变化 ns</th><th>紧约束前驱数</th></tr></thead><tbody>{phase_rows}</tbody></table></div>
<p>正负变化和并列依赖都保留。后续10/14纳秒的阶段延迟传播到最终健康完成；不能把全部包累计等待当作可回收推理时延。完整节点、消息和包路径见机制JSON与CSV。</p></section>
<section><h2>适用边界与下一步</h2><p>专家选择是真实数据，消息由前端投影并在ns-3中模拟；BF16布局、计算时间和现成等价模型/KV副本均为声明。原四份有固定修订，新增十一份只有用户确认目录与本机SHA，远端版本未知。本例不代表完整671B推理、满载128卡、真实GPU测量、持续到达、p99或生产故障分布。</p>
<p>本轮证明平台能够真正执行信号驱动的完整备用，也明确了成熟方法已经覆盖的收益。继续优化前，应先判断更小恢复单位是否有合法的数据消费与一次归并接口，再研究怎样减少重复工作；不得删掉collective必要依赖或弱化选择性重传来制造加速。扩大输入覆盖时仍保持128/256/512范围内的一种Clos规模，当前继续固定128端点。</p></section>
<section><h2>复查材料</h2><p><a href="analysis.json">独立分析</a> · <a href="mechanism.json">紧约束与包路径</a> · <a href="prefix-checks.json">篡改拒绝</a> · <a href="frozen.json">冻结输入</a> · <a href="receipt.json">执行收据</a> · <a href="resources.json">资源核算</a> · <a href="final-audit.json">最终核查</a> · <a href="README.md">结论</a> · <a href="../../README.md">复现命令</a> · <a href="../../protocol.md">执行前方法</a></p><details><summary>逐包与汇总CSV</summary><ul>{links}</ul></details></section></main></html>'''
    (out/'report.html').write_text(page)
    readme=f'''# 源确认超时联动完整备用的结果

[网页](report.html)、[分析](analysis.json)、[机制](mechanism.json)、[篡改拒绝](prefix-checks.json)、[输入](frozen.json)、[收据](receipt.json)、[资源](resources.json)、[交付核查](final-audit.json)。

| 网络 | 策略 | 目标 µs | 健康 µs | 排空 µs |
|---|---|---:|---:|---:|
'''
    for name in order:
        policy,condition=name.split('-');c=cases[name]
        readme+=f'| {condition} | {names[policy]} | {c["result_ready_ns"]["target"]/1000:.3f} | {c["result_ready_ns"]["healthy"]/1000:.3f} | {c["network_drained_ns"]/1000:.3f} |\n'
    readme+=f'''
正常全部214条目标消息最大post→ACK17.347µs，正常样本标定T=18µs；最早post1.030µs，因此固定早启动19.030µs，完整兜底127µs。执行前固定，不扫描或按故障结果调参。单样本、旧故障已知，不是盲测/p99或生产误启动率。

故障两策略同刻启动，完整逐包/队列执行相同：目标减少59.438µs（29.44%），收益来自更早完整备用。确认信号在正常条件避免无效启动，完整包/队列与control一致，省去3,088,448B额外有效通信、31,460ns声明计算；不是新恢复协议或已证明论文创新。

故障新增有效通信{extra_percent:.2f}%、声明计算{compute_percent:.2f}%；健康与网络排空晚14ns。选择性重传与参考DCQCN保持，各故障均丢6包、重传43,392B。初始备用KV46,792,704B，最终0；目标结果142.428µs不能冒充原任务及资源已释放（原任务201.880µs结束）。累计健康包驻留变化2,362ns不等于业务损失，16处正常/故障健康阶段全部紧约束与DATA/ACK路径已核对。

相对正常参考，故障额外75.240µs中收回{recovered:.2f}%，仍有15.802µs参考差距；后者不是可消除上限。没有故障概率、持续请求或GPU校准，不能换算平均吞吐或经济收益。研究下一步优先在成熟早备用基线上降低重复通信/计算，较小恢复单位必须先有消费、归并和迟到隔离接口依据。

平台/5及公开参数、77项CPU/4份旧网络只读检查分别验收；四份新128端点Clos网络各一次，零重跑/构建/依赖修改/下载/GPU。独立重建全部原件矩阵、计算、依赖/门限、逐包/队列、原生定时/源确认及KV，16项前缀篡改拒绝通过。两份原control逐文件SHA保持。首次扩展回归命令引用不存在模块失败后更正，原日志保留，不隐藏失败。

15份不同真实专家路由（目标4/健康11）、四组EP32、原层3/4/两个Decode入口；真实的是选择，计算/BF16布局/现成等价模型与KV是声明。不是完整671B、满载128卡、生产分布或硬件测量。两图为Matplotlib PNG/SVG，网页静态可离线打开；浏览器渲染状态另见最终核查。整体目标保持active。
'''
    (out/'README.md').write_text(readme)
    print(json.dumps(dict(passed=True,plots=2,csv_files=len(list((out/'metrics').glob('*.csv'))))))

if __name__=='__main__':main()
