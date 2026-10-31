"""Publish real-window findings and separate platform/representation evidence."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[2]
load=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
save=lambda p,x:p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();root=a.root.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    analysis=load(root/'analysis/analysis.json');verified=load(root/'analysis/verification.json')
    modern=load(root/'analysis/modern-verification.json');assert analysis['passed'] and verified['passed'] and modern['passed']
    for name in ('analysis.json','verification.json','modern-verification.json','permutations.json'):
        shutil.copyfile(root/'analysis'/name,out/name)
    for source,name in [('admission-corrected/admission-summary.json','legacy-admission.json'),
                        ('modern/summary.json','modern-flat-rejections.json'),('modern/size-diagnosis.json','modern-size.json')]:
        shutil.copyfile(root/source,out/name)
    inputs=out/'inputs';inputs.mkdir()
    for name in ('policy.json','source-manifest.json','topology-config.json','frozen.json','frozen-v2.json'):
        shutil.copyfile(root/'input'/name,inputs/name)
    for group in (0,1):
        for label,directory in [('legacy','admission-corrected'),('modern','modern-compressed')]:
            folder=inputs/f'{label}-group-{group}';folder.mkdir()
            shutil.copyfile(root/directory/f'group-{group}'/'input.json',folder/'input.json')
            shutil.copyfile(root/directory/f'group-{group}/plan/manifest.json',folder/'manifest.json')
            if label=='modern':shutil.copyfile(root/directory/f'group-{group}/check.json',folder/'check.json')
    failed=out/'retained-precheck-failure';failed.mkdir()
    for source,name in [('admission/admission-stderr.log','public-stderr.log'),('admission/normal.json','protocol.json'),
                        ('monitors/admission/probe.json','probe.json')]:shutil.copyfile(root/source,failed/name)
    stages=[]
    for f in sorted((root/'monitors').glob('*/probe.json')):
        row=load(f);stages.append(dict(stage=f.parent.name,status=row['status'],seconds=row['wall_seconds'],
            peak_rss_bytes=row['peak_sampled_group_rss_bytes'],peak_artifact_bytes=row['peak_sampled_artifact_bytes'],
            command=row['command'],cwd=row['cwd'],probe_sha256=sha(f)))
    receipt=dict(passed=True,published_shanghai=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        completed_monitors_at_publication=stages,monitored_seconds=sum(s['seconds'] for s in stages),
        peak_rss_bytes=max(s['peak_rss_bytes'] for s in stages),peak_artifact_bytes=max(s['peak_artifact_bytes'] for s in stages),
        unexpected_failed_stages=[s['stage'] for s in stages if s['status']!='completed'],
        unexpected_failure='Initial packet9000B/receiver8192B mismatch; old attempt retained; only payload/chunk corrected with frozen-v2 lineage.',
        ordinary_flat_plan_rejections=2,old_native_lifetime_rejection_expected=True,
        new_network_runs=0,network_reruns=0,builds=0,downloads=0,hardware_calibrated=False,
        prospective_method_commit='ba1543b8ac',packet_correction_commit='b8914fec71',
        modern_capacity_method_commit='d24bcde89f',platform_storage_commit='1422ba2073',
        compressed_followup_commit='15a5bb8456',
        code_sha256={str(f.relative_to(ROOT)):sha(f) for f in Path(__file__).parent.glob('*.py')},
        scope='Completed monitor reports at publication time; publisher itself excluded; native runtime not executed')
    save(out/'receipt.json',receipt)
    rows='';chart=[]
    for i,sample in enumerate(analysis['samples']):
        expert=sample['observed']['experts']['max_to_mean'];rank=sample['observed']['rank_load']['max_to_mean']
        temporal=sample['observed']['temporal']['4']['mean_previous_top8_next_selection_coverage']
        controls=[r['4']['mean_previous_top8_next_selection_coverage'] for r in sample['shuffled'].values()]
        reduction=1-sample['dispatch_activation_totals'][1]/sample['dispatch_activation_totals'][0]
        rows+=f'<tr><td>组{sample["group"]} / 层{sample["layer"]}</td><td>{expert:.2f}</td><td>{rank:.2f}</td><td>{temporal*100:.2f}%</td><td>{min(controls)*100:.2f}–{max(controls)*100:.2f}%</td><td>{reduction*100:.2f}%</td></tr>'
        y=24+i*39
        chart.append(f'<text x="0" y="{y+12}">组{sample["group"]} 层{sample["layer"]}</text><rect x="95" y="{y}" width="{expert*39:.2f}" height="12" fill="#ba7522"/><rect x="95" y="{y+14}" width="{rank*39:.2f}" height="12" fill="#167d78"/><text x="{102+expert*39:.2f}" y="{y+11}">{expert:.2f}</text><text x="{102+rank*39:.2f}" y="{y+25}">{rank:.2f}</text>')
    svg='<svg viewBox="0 0 530 275" role="img" aria-label="专家与专家归属rank峰均比">'+''.join(chart)+'</svg>'
    compactrows=''.join(f'<tr><td>组{g["group"]}</td><td>{g["operations"]:,}</td><td>{g["events"]:,}</td><td>{g["plain_bytes"]/1e6:.2f} MB</td><td>{g["stored_bytes"]/1e6:.3f} MB</td><td>{g["stored_reduction_fraction"]*100:.2f}%</td></tr>' for g in modern['groups'])
    html=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>连续Decode：热点、网络需求与系统容量</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f5f7;color:#20323e;font:16px/1.75 system-ui,sans-serif}}main{{max-width:1100px;margin:auto;padding:30px 22px 60px}}h1{{font-size:30px;line-height:1.4}}h2{{font-size:22px}}section{{background:white;border:1px solid #dce4e9;border-radius:12px;padding:22px;margin:18px 0}}.lead{{font-size:18px}}.muted{{font-size:14px;color:#526674}}table{{border-collapse:collapse;width:100%;white-space:nowrap}}th,td{{padding:10px;text-align:left;border-bottom:1px solid #dde5e9}}.scroll{{overflow:auto}}svg{{width:100%;max-width:700px;font:13px system-ui}}a{{color:#126a98}}code{{background:#edf2f5;padding:2px 5px}}.warning{{border-left:5px solid #bd7a22}}.good{{border-left:5px solid #168078}}
</style><main><p class="muted">2026-10-31 · 实际拓扑128端点Clos · 本轮零新网络执行</p><h1>把一个token扩展到连续64步，究竟改变了什么？</h1><p class="lead">真实热点存在，但专家偏斜、通信需求和链路拥塞是三件不同的事。本轮查清了连续步骤的表示边界，并修复现代计划的保存容量问题。</p>
<section><h2>先把任务说清楚</h2><p>15份不同R1-AWQ/MMLU原件，分成8个、7个请求的两组，各EP32/E256/H7168，每rank8专家。公共读取器校验全部105,038条Decode层记录，本轮选原层3、31、60及每请求前64步，共960个Decode token、2,880条token-layer记录、23,040次专家选择。</p><p><b>64步是64次顺序的Decode调用，不是把同一请求未来64个token放入当前batch。</b>每步并发请求数仍为8或7。记录没有真实时钟和到达时间，相同步号对齐只是声明假设。固定128端点部署有64个端点属于这两组，不能称128卡满载。</p></section>
<section><h2>专家很热，是否就等于网络堵住？</h2><p>64步累计的专家峰均比为3.50–9.36；按专家归属汇总到32个rank后，为1.53–1.87。橙色是单专家负载，绿色是专家归属rank负载（含本地贡献）。这说明热点被分散，绿色仍不是实际端口排队。</p>{svg}<div class="scroll"><table><thead><tr><th>组 / 层</th><th>专家峰均比</th><th>归属rank峰均比</th><th>4步前窗Top8覆盖后窗选择</th><th>保边际打乱参考</th><th>按目的rank去重的激活量减少</th></tr></thead><tbody>{rows}</tbody></table></div><p class="muted">前三列来自同一64步窗口；覆盖率使用非重叠4步窗口，只读上一窗口Top8，统计下一窗口选择份额，并非token预测准确率。打乱seed为11/29/47，保留每请求全部Top8组合和边际频率；三个seed只作描述参考，不提供显著性结论。</p></section>
<section><h2>网络层可以确定什么</h2><p>逐专家发送与按目的rank去重使用同一选择、同一部署和14,336B激活本体。六个样本去重后，远端Dispatch激活本体减少13.36%–16.27%。这是现代通信已有表示的差别，不是本轮新算法收益，也不是总线上字节或时延减少。</p><p>另独立核算旧BF16布局Dispatch/Combine为14,352/14,336B，现代direct为14,464/14,400B；控制流、包头、ACK和恢复字节没有在流量投影中冒充已计入。</p><p>逐步源/目标access方向按400Gb/s得到的服务量下界：按目的rank去重后，六个样本的最大值均约2.294µs。它只是激活服务量界，不包括排队、传播、控制、计算和上层依赖，不是完整通信或推理时延。没有本轮新的拥塞、丢包或优化收益结论。</p></section>
<section><h2>连续调用究竟被什么限制</h2><p>真实前两步已经生成完整旧库计划：组0需33,024操作，组1需32,992操作，均超过旧native每组32,768操作寿命上限。公开validate-only在Python层拒绝，原生网络没有启动；不能解释为Clos规模不足或第二步不会拥塞。</p><p>现代direct的相同两调用逻辑数在现有上限内，却因约21.8MB明文计划超过16MiB保存预算而失败。这又是不同的问题，原失败记录均保留。</p><p class="muted">初次旧库准入还暴露了实验配置错误：继承9000B载荷却设置8192B接收缓冲。已保留失败并用frozen-v2记录1024B载荷/4096B分块修正，未改来源、步骤、模型或通知。</p></section>
<section class="good"><h2>这轮实际修复的系统能力</h2><p>新增公开<code>modern-deepep-plan --format compressed-json</code>，以标准zlib保存同一完整规范JSON，<code>modern-deepep-check</code>自动识别并核验存储/展开身份。默认flat输出不变；新格式展开限64MiB，实际保存总预算仍16MiB，操作/事件上限不变。</p><div class="scroll"><table><thead><tr><th>真实输入</th><th>完整操作</th><th>完整事件</th><th>原规范JSON</th><th>压缩保存</th><th>保存字节减少</th></tr></thead><tbody>{compactrows}</tbody></table></div><p>公共plan/check和独立枚举确认全部四种屏障、rank/expert计数（含零值）、self-peer、真实token数据及依赖均保留。6项新方法、24项旧计划/事件/窗口回归通过，人工公开流程的默认产物与改动前逐字节一致。</p><p><b>这解决的是计划存储，尚未证明现代EP32两调用或双组共享网络已执行。</b>编译/检查仍会展开计划，不能把磁盘减少95%左右说成运行期内存或网络时间同幅下降。</p></section>
<section class="warning"><h2>下一步与创新判断</h2><p>优先验证现有现代direct运行期能否在同一128Clos中推进真实连续两调用，核查回调、逻辑步、原生节点和缓冲生命周期；必要系统增量单独验收。再验证多组共享和有依据的非零计算成本，形成可代表业务重叠的网络压力。</p><p>只有在成熟通信表示与恢复机制充分启用之后仍有明显业务损失，才继续判断进度感知的恢复/流量组织是否有独立增益。热点统计、热点预测、常规去重或压缩都不能直接称论文创新；也不能将更长离线窗口当成更大并发负载。</p></section>
<section><h2>证据与复现</h2><p><a href="analysis.json">连续窗口指标</a> · <a href="verification.json">原件独立核验</a> · <a href="modern-verification.json">现代完整计划独立核验</a> · <a href="legacy-admission.json">旧库寿命拒绝</a> · <a href="receipt.json">预算与命令</a> · <a href="../../README.md">冻结研究方法</a> · <a href="../../modern_capacity.md">明文容量方法</a> · <a href="../../compressed_followup.md">压缩后续方法</a></p><p>代码、两调用配置和摘要随Git保存；原始数据、完整逐步流量与大计划留在runs。可只用保存的现代input.json重新生成计划，无需先取得原始大文件。来源远端修订、硬件和激活dtype仍未知；BF16仅为本轮功能假设。HTML完成静态链接核验，不声称浏览器渲染验收。</p></section></main></html>'''
    (out/'report.html').write_text(html)
    (out/'README.md').write_text('''# 连续Decode窗口与容量结果\n\n[网页](report.html)、[指标](analysis.json)、[来源核验](verification.json)、[现代计划核验](modern-verification.json)、[资源](receipt.json)。本轮没有新网络运行，所有字节和服务量界均为明确表示投影。\n\n在仓库根用下列命令可从保存配置重新生成一个完整现代计划，无需历史runs或原始大文件：\n\n```bash\nPYTHONPATH=python python3 -B -m moe_ub_cosim scaleout modern-deepep-plan --format compressed-json --config experiments/route_window_network/results/20261031/inputs/modern-group-0/input.json --output runs/my-real-modern-two-call-plan\nPYTHONPATH=python python3 -B -m moe_ub_cosim scaleout modern-deepep-check --plan runs/my-real-modern-two-call-plan --output runs/my-real-modern-two-call-check.json\n```\n\n输入包含原本15份来源中第一组8个请求的前两步选择；实际源文件身份见inputs/source-manifest.json，远端修订仍未知。完整统计需原件和公共归一化流程，见[事前方法](../../README.md)。frozen和v2保留原身份，不修改旧hash来适配新路径。\n''')
    print(json.dumps(dict(published=True,output=str(out),new_network_runs=0)))


if __name__=='__main__':main()
