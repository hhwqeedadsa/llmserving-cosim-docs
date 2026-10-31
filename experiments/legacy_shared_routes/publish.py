"""Publish bounded summaries and a standalone HTML page, without raw route files."""
import argparse
from datetime import datetime
import hashlib
from html import escape
import json
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[2]
load=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,required=True)
    p.add_argument('--analysis',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    study=a.study.resolve();root=study.parent;out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    summary=load(a.analysis/'analysis.json');diagnosis=load(a.analysis/'diagnosis.json');verified=load(a.analysis/'verification.json')
    assert summary['passed'] and diagnosis['passed'] and verified['passed']
    for path in a.analysis.glob('*.json'):shutil.copyfile(path,out/path.name)
    for case in ('normal','local','prefix'):shutil.copyfile(root/(case+'-audit.json'),out/(case+'-audit.json'))
    inputs=out/'inputs';inputs.mkdir()
    for name in ('topology-config.json','normal.json','local.json','prefix.json','transport.json','shared.json','selection.json','frozen.json'):
        shutil.copyfile(study/name,inputs/name)
    shutil.copyfile(load(study/'frozen.json')['source_manifest'],out/'source-manifest.json')
    shutil.copyfile(study/'validation/validation.json',out/'validation.json')
    for i in (0,1):
        folder=inputs/f'group-{i}';folder.mkdir()
        for name in ('input.json','binding.json'):shutil.copyfile(study/f'group-{i}'/name,folder/name)
    stages=[]
    for path in sorted((root/'monitors').glob('*/probe.json')):
        record=load(path)
        stages.append(dict(stage=path.parent.name,status=record['status'],wall_seconds=record['wall_seconds'],
            peak_rss_bytes=record['peak_sampled_group_rss_bytes'],command=record['command'],cwd=record['cwd'],
            probe_sha256=sha(path)))
    receipt=dict(passed=all(s['status']=='completed' for s in stages),
        published_shanghai=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        completed_stages=stages,monitored_seconds=sum(s['wall_seconds'] for s in stages),
        peak_rss_bytes=max(s['peak_rss_bytes'] for s in stages),
        cumulative_budget=dict(seconds=2400,artifacts_bytes=3*2**30,min_free_bytes=8*2**30),
        new_network_runs=3,network_reruns=0,builds=0,downloads=0,physical_gpu_executed=False,
        platform_resource_commit='70b996b69e',prospective_method_commit='725c1d3129',
        scope='Completed monitor reports at publication time; publisher and later delivery check excluded from this subtotal',
        code_sha256={str(f.relative_to(ROOT)):sha(f) for f in Path(__file__).parent.glob('*.py')})
    (out/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    rows=''
    for case,label in [('normal','无故障'),('local','旧候选本地过滤'),('prefix','成熟显式绕路')]:
        c=summary['cases'][case]
        rows+=f'<tr><td>{label}</td><td>{c["task_ns"][0]/1000:.3f}</td><td>{c["task_ns"][1]/1000:.3f}</td><td>{c["physical_finish_ns"]/1000:.3f}</td><td>{c["repeated_bytes"][0]:,} / {c["repeated_bytes"][1]:,}</td></tr>'
    bars=[]
    for i,(case,label,color) in enumerate([('normal','无故障','#34495e'),('local','本地过滤','#d48a23'),('prefix','显式绕路','#168575')]):
        for g in (0,1):
            value=summary['cases'][case]['task_ns'][g]/1000;y=30+i*70+g*25
            bars.append(f'<text x="2" y="{y+13}" font-size="12">{label} · 组{g}</text><rect x="125" y="{y}" width="{value*4.1:.2f}" height="18" fill="{color}" opacity="{1 if g==0 else .55}"/><text x="{132+value*4.1:.2f}" y="{y+13}" font-size="12">{value:.3f} µs</text>')
    chart='<svg viewBox="0 0 640 245" role="img" aria-label="两组通信调用完成时刻对比">'+''.join(bars)+'</svg>'
    html=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>128端点Clos：真实双EP32通信与故障恢复</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f5f7;color:#1f303e;font:16px/1.75 system-ui,sans-serif}}main{{max-width:1080px;margin:auto;padding:30px 22px 60px}}h1{{font-size:30px;line-height:1.4}}h2{{font-size:22px;margin:0 0 12px}}section,.card{{background:white;border:1px solid #e0e6eb;border-radius:12px;padding:22px;margin:18px 0}}.lead{{font-size:18px}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}}.card{{margin:0}}strong.metric{{display:block;font-size:29px;color:#137e71}}table{{width:100%;border-collapse:collapse;white-space:nowrap}}td,th{{text-align:left;padding:10px;border-bottom:1px solid #dfe5ea}}.scroll{{overflow:auto}}.muted{{color:#536573;font-size:14px}}a{{color:#126d9b}}svg{{width:100%;max-width:850px;height:auto}}.warning{{border-left:5px solid #d48a23}}@media(max-width:700px){{.cards{{grid-template-columns:1fr}}h1{{font-size:25px}}}}
</style><main><p class="muted">研究记录 · 2026-10-31 · 128端点Clos / 两个独立EP32组</p>
<h1>成熟恢复之后，还剩多少任务等待？</h1>
<p class="lead">本轮把15份真实专家选择接入两个完整旧库通信组，共享同一次原生网络。成熟显式绕路消除了大部分故障额外等待，但没有观察到明显的旁观任务拖累。</p>
<div class="cards"><div class="card"><strong class="metric">98.52%</strong>直接受损组：成熟绕路收回的故障额外等待</div><div class="card"><strong class="metric">0.194 µs</strong>受损组相对正常的剩余差距，尚非可消除上限</div><div class="card"><strong class="metric">+0.024 µs</strong>第二组相对正常的完成时刻变化</div></div>
<section><h2>系统实际完成了什么</h2><p>两组分别使用8、7份不同原件，各跑一个完整Dispatch/Combine调用。每组EP32、256专家、H7168，每rank8专家；共33,008操作、132,736依赖事件、31,744条持续有向流，保留全部32,768个通知操作。所有组共用一份Fabric、仿真时钟和可靠传输状态，逐组推进，最终一起排空。</p><p>拓扑有128端点、20交换机，64端点分配给两组，其余端点没有本轮任务；只有15个源rank持有选定token，其他rank仍参与完整通知。全链路400Gb/s、每跳1µs。关闭机内旁路，不运行GPU或完整R1数值模型。</p><p class="muted">共享入口已允许显式180秒 / RSS3GiB / 地址6GiB / 512MiB结果，原单组上限保持。实际最大结果约203MiB，宿主峰值约{receipt['peak_rss_bytes']/2**30:.2f}GiB。这里只提高资源容量，未修改模拟网络成本。</p></section>
<section><h2>正常、本地改路、显式绕路的对照结果</h2><p>组0经过故障ToR；组1部署避开该ToR，实际DATA、ACK与回复均未经过坏边，也没有捕获到直接丢弃。两组在128、132、133三个已抓包交换机上共同使用8个端口。</p>{chart}<div class="scroll"><table><thead><tr><th>条件</th><th>组0完成 µs</th><th>组1完成 µs</th><th>全网排空 µs</th><th>重复载体字节 组0 / 组1</th></tr></thead><tbody>{rows}</tbody></table></div><p class="muted">完成指kernel_complete；排空是全网完成ACK和资源释放。两者不能混用，组0/1也不是独立运行的反事实。</p></section>
<section><h2>收益和收益空间</h2><p>组0采用本地过滤时多等13.112µs；成熟显式绕路后只多等0.194µs。相对本地过滤，完成提前12.918µs（12.55%），收回98.52%的故障额外等待。这个收益来自已有绕路和重传机制，不是新增算法。</p><p>组1本地过滤时反而早179ns，显式绕路时晚24ns（约0.027%）。其共享端口有排队，但本轮没有形成明显业务拖累。残差194ns或24ns既不是数学最优差距，也不是已证明可被新算法全部消除的空间。</p></section>
<section><h2>为什么暂不做“恢复优先级”算法</h2><p>全网最大交换机队列为正常78,336B、本地过滤78,120B、显式绕路78,120B，均低于131,072B的ECN起标门限；ECN标记观测和PFC暂停均为零。已有共享竞争，但不是持续拥塞或拥塞控制压力测试。</p><p>例如组1在Agg133→Core145端口的最大单包排队为正常1,189ns、显式绕路1,126ns。不能把原本就存在的排队当成恢复新增损失。组1的最终关键操作还会切换，不能固定挑一条变慢的包代表整个任务。</p><p>组0正常与显式绕路有相同的末端数据前驱：发起晚671ns，发起至接受跨度短477ns，合计接受晚194ns。跨度同时包含源端顺序与传输，不能全部归因于交换机。</p></section>
<section class="warning"><h2>代表性与下一步取舍</h2><p>真实的是15份R1-AWQ/MMLU记录中的专家选择；请求划分、部署、BF16激活载体和计算成本是声明的实验条件。每份只取一个Decode token、一层、一次调用，不代表长期热点、训练负载、完整671B服务或128卡满载。</p><p>旧库通知占操作的99.27%，ACK-each-packet与零成本专家拷贝也会影响结果。下一轮应优先确认更大真实token窗口、现代通信语义与有依据的业务负载能否形成持续共享瓶颈，再判断进度感知恢复是否有独立增益；不得仅靠增加故障强度制造研究收益。仍从128/256/512中选一种Clos，不自动扫三档。</p></section>
<section><h2>怎样复核</h2><p>三个网络各执行一次，无网络重跑。公共审计核对全部操作、真实负载、通知、最终内存及退休；独立核验再用来源原件重算120次选择、区间并集重算重复字节，并独立重配对192,937次交换机入队/出队。仅捕获端点及128/132/133三个交换机，未宣称全路径逐包归因。</p><p><a href="analysis.json">指标摘要</a> · <a href="verification.json">独立核验</a> · <a href="diagnosis.json">机理与约束</a> · <a href="receipt.json">资源与命令</a> · <a href="source-manifest.json">来源清单</a> · <a href="../../../README.md">预先冻结的方法</a></p><p><a href="normal-mechanism.json">正常逐组证据</a> · <a href="local-mechanism.json">本地过滤证据</a> · <a href="prefix-mechanism.json">显式绕路证据</a></p><p class="muted">网页为独立静态HTML，内置SVG，无外部依赖；静态链接核验不等于浏览器渲染验收。</p></section></main></html>'''
    # The report is below results/YYYYMMDD, so its study method is two parents up.
    html=html.replace('href="../../../README.md"','href="../../README.md"')
    (out/'report.html').write_text(html)
    (out/'README.md').write_text('''# 真实双EP32共享网络结果\n\n见[结果网页](report.html)、[指标](analysis.json)、[独立核验](verification.json)、[机理](diagnosis.json)、[资源](receipt.json)和[预先冻结的方法](../../README.md)。\n\n原件位于`runs/legacy_shared_routes_20261031`；source-manifest保留15份原文件的相对路径和hash，不包含原始大文件。inputs为实际配置，plan由公开deepep-plan重建；frozen保留原执行身份，因此迁移后需在新目录生成新冻结清单，不能改旧hash。\n''')
    print(json.dumps(dict(published=True,output=str(out),new_network_runs=0)))


if __name__=='__main__':main()
