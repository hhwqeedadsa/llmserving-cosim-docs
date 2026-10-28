"""Draw actual generated graphs and assemble an offline evidence report."""
import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import shutil


def read(p): return json.loads(p.read_bytes())
def save(p,v): p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def table(headers,rows):
    def row(items,tag): return '<tr>'+''.join(f'<{tag}>'+html.escape(str(x))+f'</{tag}>' for x in items)+'</tr>'
    return '<div class="scroll"><table>'+row(headers,'th')+''.join(row(r,'td') for r in rows)+'</table></div>'


def draw(topology,config,record,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.lines import Line2D
    import numpy as np
    nplanes=record['planes'];pods=record['pods'];nodes=topology['nodes']
    palette=plt.get_cmap('tab10');positions={};counts=[]
    fig,axes=plt.subplots(nplanes,1,figsize=(14,5.2 if nplanes==1 else 8),squeeze=False)
    for plane in range(nplanes):
        ax=axes[plane][0];pos={}
        gpus=[n for n in nodes if n['kind']=='gpu']
        for pod in range(pods):
            left=pod/pods;right=(pod+1)/pods
            ax.axvspan(left,right,color='#eff4fa' if pod%2==0 else '#f9fafb',zorder=0)
            ax.text((left+right)/2,0.015,f'Pod {pod}',ha='center',fontsize=9)
            for kind,y in (('gpu',.14),('tor',.43),('agg',.68)):
                sort_key=(lambda n:(n['server'],n['gpu_slot'])) if kind=='gpu' else (
                    lambda n:(n.get('rail',0),n.get('segment',0),n['id']))
                selected=sorted([n for n in nodes if n['kind']==kind and n.get('pod')==pod and
                                 (kind=='gpu' or n['plane']==plane)],
                                key=sort_key)
                for node,x in zip(selected,np.linspace(left+.025/pods,right-.025/pods,len(selected))):
                    pos[node['id']]=(x,y)
        cores=[n for n in nodes if n['kind']=='core' and n['plane']==plane]
        for node,x in zip(cores,np.linspace(.08,.92,len(cores))):pos[node['id']]=(x,.91)
        edges=[e for e in topology['links'] if e['src'] in pos and e['dst'] in pos]
        assert {e['tier'] for e in edges} <= {'access','tor_agg','agg_core'}
        for kind,color in (('access','#8795a8'),('tor_agg','#3274b6'),('agg_core','#289875')):
            subset=[e for e in edges if e['tier']==kind]
            ax.add_collection(LineCollection([(pos[e['src']],pos[e['dst']]) for e in subset],
                              colors=color,linewidths=.55,alpha=.17 if kind=='access' else .26,zorder=1))
        for kind,marker,size,color in (('gpu','.',11,'#64748b'),('tor','s',30,'#3274b6'),
                                        ('agg','s',40,'#a16ac1'),('core','s',48,'#289875')):
            subset=[n for n in nodes if n['kind']==kind and n['id'] in pos]
            colours=[palette(n['gpu_slot']%8 if kind=='gpu' else n.get('rail',0)%8) for n in subset]
            ax.scatter([pos[n['id']][0] for n in subset],[pos[n['id']][1] for n in subset],
                       s=size,marker=marker,c=colours if record['rails']>1 and kind in ('gpu','tor') else color,zorder=3)
            ax.text(-.018,dict(gpu=.14,tor=.43,agg=.68,core=.91)[kind],
                    f'{kind.upper()}\n{len(subset)}',ha='right',va='center',fontsize=9)
        ax.set(xlim=(-.065,1.015),ylim=(-.015,1.04));ax.axis('off')
        access=config['links']['access']['rate_bps']//10**9
        ax.set_title(f'Plane {plane}: {access} Gb/s access; 400 Gb/s ToR-Agg and Agg-Core; '
                     +('same GPU endpoints shown in both planes' if nplanes>1 else 'one physical link per GPU'),fontsize=10)
        positions[plane]=pos;counts.append(dict(plane=plane,nodes=len(pos),links=len(edges)))
    assert sum(r['links'] for r in counts)==len(topology['links'])
    handles=[Line2D([0],[0],color=c,label=t) for c,t in
             (('#8795a8','GPU-ToR'),('#3274b6','ToR-Agg'),('#289875','Agg-Core'))]
    fig.legend(handles=handles,loc='lower center',ncol=3,frameon=False,fontsize=9)
    fig.suptitle(f"{record['case']} | {record['gpus']} GPUs / {record['servers']} servers | "
                 f"ToR {record['tor']} / Agg {record['agg']} / Core {record['core']} | {record['links']} physical links\n"
                 'Generated graph only: no packet execution or latency result',fontsize=13)
    fig.tight_layout(rect=(.025,.04,1,.89 if nplanes==1 else .92))
    for ext in ('png','pdf','svg'):fig.savefig(output/(record['case']+'.'+ext),dpi=160)
    plt.close(fig)
    return dict(case=record['case'],drawn_planes=counts,all_physical_links_drawn=True,
                plane_views_repeat_gpu_endpoints=nplanes>1)


def render(repo,stage,arrivals,output):
    assert not output.exists();output.mkdir(parents=True)
    top=read(stage/'topologies/summary.json');models=read(stage/'models/summary.json')
    curves=read(arrivals/'report-data.json');assert curves['verification']['passed']
    assert top['topology_checks_passed'] and top['network_executions']==0
    inputs=output/'inputs';inputs.mkdir();draws=[]
    for record in top['topologies']:
        name=record['case'];config=repo/'examples/scaleout_large_models'/(name+'.json')
        assert hashlib.sha256(config.read_bytes()).hexdigest()==record['config_sha256']
        topology=read(stage/'topologies'/name/'topology.json')
        draws.append(draw(topology,read(config),record,output))
        shutil.copyfile(config,inputs/config.name)
        for filename in ('topology.astra.txt','topology.simai.txt','audit.json'):
            shutil.copyfile(stage/'topologies'/name/filename,inputs/(name+'-'+filename))
    for model in models['models']:shutil.copyfile(stage/'models'/model['file'],inputs/model['file'])
    for phase in ('dispatch','combine'):
        for ext in ('png','pdf'):shutil.copyfile(arrivals/(phase+'-arrival.'+ext),output/(phase+'-arrival.'+ext))
        for case in ('normal','data-and-ack-loss'):
            filename=case+'-'+phase+'-curve.csv';shutil.copyfile(arrivals/filename,output/filename)
    save(output/'topologies.json',top);save(output/'models.json',models);save(output/'arrivals.json',curves)
    verification=dict(passed=True,topology_checks=top['topology_checks_passed'],drawings=draws,
                      curve_source_verification=curves['verification'],large_network_executions=0,
                      scope='static graphs/model capacity and previously audited EP4 arrival figures; no hundreds-GPU timing')
    save(output/'verification.json',verification)
    top_table=table(['结构','GPU / 服务器','Pod','ToR / Agg / Core','链路数','rail / plane','ToR下行/上行','Agg下行/上行'],
        [[r['case'],f"{r['gpus']} / {r['servers']}",r['pods'],f"{r['tor']} / {r['agg']} / {r['core']}",r['links'],
          f"{r['rails']} / {r['planes']}",r['capacity_ratios']['tor'],r['capacity_ratios']['agg']] for r in top['topologies']])
    model_table=table(['模型输入','层数','专家 / Top-K','EP / 副本组','每rank每层专家','BF16权重/rank','KV预留/rank'],
        [[r['file'],94,'128 / 8',f"64 / {r['replica_groups']}",2,f"{r['weight_bytes_per_rank']/2**30:.2f} GiB",
          f"{r['kv_reserved_bytes_per_rank']/2**30:.3f} GiB"] for r in models['models']])
    source_manifest=read(repo/'experiments/large_model_scaleout/upstream/manifest.json')
    sources={r['file']:r for r in source_manifest}
    def link(name,label):return '<a href="'+html.escape(sources[name]['url'])+'">'+html.escape(label)+'</a>'
    sources_table='<table><tr><th>上游内容</th><th>确认的范围</th><th>我们当前的支持</th></tr>'+''.join(
        '<tr><td>'+link(name,label)+'</td><td>'+desc+'</td><td>'+scope+'</td></tr>' for name,label,desc,scope in [
        ('qwen235.json','Qwen3-235B-A22B','94层；128专家；Top8；GQA，64个Q头/4个KV头','已转换形状与容量配置；未运行大规模网络'),
        ('qwen235-fp8.json','Qwen3-235B FP8配置','上游FP8配置；model_type等元数据与BF16文件有差异','仅记录来源；没有直接切换为FP8性能模型'),
        ('r1-0528.json','DeepSeek-R1-0528','61层，其中前3层Dense；256路由专家+1共享专家；MLA','真实R1路由片段可回放；当前Scale-Out前端未完整表达该整模型'),
        ('deepseek-v3.json','DeepSeek-V3','671B级MoE的上游配置；支持范围需结合具体后端','未宣称本系统完整支持'),
        ('gpt175-prebuilt.txt','AICB预制GPT175B workload','训练格式，含反向/梯度操作，模型并行组8','可参考训练负载格式，不能作为真实MoE推理流量'),
        ('simai-inference-README.md','SimAI / Vidur 推理说明','说明AICB、SimAI解析和NS-3后端；列出MoE适配中及部分支持','未安装或执行上游SimAI；文件存在不等于所有后端组合已验证')])+'</table>'
    normal=curves['summary']['normal-dispatch'];fault=curves['summary']['data-and-ack-loss-dispatch']
    phase_rows=[]
    for phase in ('dispatch','combine'):
        a=curves['summary']['normal-'+phase]['slice'];b=curves['summary']['data-and-ack-loss-'+phase]['slice']
        phase_rows.append([phase,a['logical_bytes'],f"{a['data_ready_ns']/1000:.3f} → {b['data_ready_ns']/1000:.3f}",
                           f"{a['source_confirmed_ns']/1000:.3f} → {b['source_confirmed_ns']/1000:.3f}"])
    arrival_table=table(['所选collective','有效字节','接收收齐时间：正常→故障（µs）','源确认时间：正常→故障（µs）'],phase_rows)
    topo_sections=''.join(f'''<article id="{r['case']}"><h3>{r['case']}</h3>
      <img loading="lazy" src="{r['case']}.png" alt="{r['case']}实际生成的全部物理链路">
      <p>每个方块为一台交换机，底部每个点为GPU端点，线为实际生成的物理链路。双plane视图重复展示同一批GPU；多rail以颜色区分，Agg/Core由rail共享。</p>
      <p><a href="{r['case']}.pdf">PDF</a> · <a href="{r['case']}.svg">SVG</a> · <a href="inputs/{r['case']}.json">可修改配置</a> ·
      <a href="inputs/{r['case']}-topology.astra.txt">ASTRA拓扑</a> · <a href="inputs/{r['case']}-topology.simai.txt">SimAI拓扑格式</a></p></article>'''
      for r in top['topologies'])
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>百B模型、Scale-Out结构与有效累计到达</title><style>
:root{font-family:system-ui,"Noto Sans CJK SC",sans-serif;color:#193047;background:#edf2f7;line-height:1.75}body{margin:0}
main{max-width:1220px;margin:auto;padding:30px 24px 80px}h1{font-size:32px;line-height:1.35}h2{margin-top:36px}
nav{display:flex;gap:10px;flex-wrap:wrap}a{color:#1263a1}nav a{background:white;padding:7px 14px;border-radius:8px}
article,.card{background:white;border:1px solid #d7e0e8;border-radius:12px;padding:20px;margin:18px 0}
.note{border-left:5px solid #d79125;background:#fff8e9;padding:14px 18px}img{width:100%;height:auto}table{border-collapse:collapse;width:100%;background:white;font-size:14px}
th,td{border:1px solid #dae2eb;padding:9px 12px;text-align:left}th{background:#e5eef7}.scroll{overflow-x:auto}code{background:#e9eff4;padding:2px 5px}
summary{cursor:pointer;font-weight:600}pre{overflow:auto;padding:16px;background:#11263a;color:#e5eff7;border-radius:8px}footer{font-size:13px;color:#536579}</style>
<main><p>2026-10-28 · Asia/Shanghai · 本地静态交付</p><h1>百B模型与跨服务器网络：<br>结构、部署约束与有效数据到达</h1>
<nav><a href="#models">大模型怎么选</a><a href="#deployment">300/500卡怎么部署</a><a href="#topologies">网络结构图片</a><a href="#arrivals">Collective有效累计到达</a><a href="#reproduce">修改与复现</a></nav>
<p class="note"><strong>两类证据分开：</strong>本轮六套300/500及320/512卡拓扑已实际生成并静态核对，没有执行百卡包级网络。下面的累计到达曲线来自已完成的4卡/EP4真实专家路由实验，不是大规模模型的性能结果。</p>
<h2 id="models">1. 有现成的百B以上模型，但不能只改名字</h2><p>SimAI固定版本中已找到Qwen3-235B、DeepSeek-V3及R1-0528配置，AICB也提供训练workload和推理生成器。当前最适合先接入本系统的是Qwen3-235B的GQA全MoE结构。根据上游形状核算为<strong>235,093,634,560参数</strong>，不是先前人为声明的119B模型。</p>
'''+sources_table+'''<p>固定版本：SimAI <code>a57d765c5478</code>；AICB <code>23eec3c48ca2</code>。推理说明中的“部分支持”“适配中”仍然保留，不能把配置文件的存在当作整模型NS-3执行成功。AICB的DeepSeek默认文件还声明32个冗余专家、共288专家，不能直接替代我们真实路由的256个逻辑专家。</p>
<h2 id="deployment">2. 建议用320/512卡；300/500卡结构也已提供</h2>
<p>300卡=75台四卡服务器，500卡=125台四卡服务器；320卡=40台八卡服务器，512卡=64台八卡服务器。每卡NIC总带宽400 Gb/s，GPU型号H100只是描述标签，没有硬件计时校准。</p>
<p>在当前“每个EP组均匀分配全部专家”的约束下，EP大小须整除专家数。若300/500张卡全部划为等大EP组，Qwen的128专家与R1的256专家最多都只能采用EP4。Qwen BF16仅权重即约120.65 GiB/rank，超过声明的80 GiB，因此<strong>精确300/500卡只交付拓扑，不伪造可运行的BF16整模型配置</strong>。可以用296/496张工作卡加4张备用卡、采用不同大小的EP组，或后续引入TP/PP、量化；这些选择要单独建立语义和资源模型。</p>
<p>本轮为320/512卡提供跨Pod交错分组的EP64模型配置：分别5/8个独立推理副本，每rank每层2个专家。<strong>不是一次320/512-rank All-to-All，也不执行训练DP AllReduce。</strong>当前没有TP/PP拆分，注意力与非专家权重在组内复制。</p>
'''+model_table+'''<p>输入为每rank一个已就绪KV的Decode请求，初始上下文2048 token，继续生成2 token；320/512卡全局并发不同，不能直接比较总时延并称为公平强扩展。专家选择为合成路由，R1真实路由不能改名当Qwen路由。显存核算仅含权重和KV，未含激活、工作区与通信缓冲；声明计算成本不预测真实H100性能。</p>
<h2 id="topologies">3. 六套网络结构：参数与实际连线</h2>'''+top_table+'''
<p>下行/上行比大于1表示存在潜在汇聚瓶颈，不能称为无阻塞网络。Clos的所有层均400G；HPN启发结构为8 rail、2 plane，每GPU两条200G接入共享400G NIC预算，上层均400G。它使用普通电交换机，Agg/Core跨rail共享，不包含OCS重构、生产HPN的完整实现或机内NVLink仿真。链路均声明1µs传播时延。</p>
'''+topo_sections+'''<h2 id="arrivals">4. Collective有效累计到达：真实4卡实验</h2>
<p>四份DeepSeek-R1-AWQ/MMLU请求，EP4，256专家/Top8，每rank每层64专家；实际执行源层3—4、两个Decode步骤。100G接入、25G上层的两Pod Clos，多阶段共享同一ns-3网络。以下选择首轮、源层3的Dispatch和Combine，不是全部76条消息的累加曲线。</p>
'''+arrival_table+f'''<p>全任务76条消息，逻辑有效数据2,609,152 B；正常完成{normal['simulation_finished_ns']/1000:.3f}µs，构造故障完成{fault['simulation_finished_ns']/1000:.3f}µs。故障为一个数据序号丢两次、另一消息最终ACK丢一次；不是50%全网随机丢包。</p>
<article><h3>Dispatch：把输入发给专家</h3><img src="dispatch-arrival.png" alt="Dispatch有效累计到达曲线"><a href="dispatch-arrival.pdf">下载PDF</a></article>
<article><h3>Combine：把专家结果发回</h3><img src="combine-arrival.png" alt="Combine有效累计到达曲线"><a href="combine-arrival.pdf">下载PDF</a></article>
<div class="card"><p><strong>怎么看：</strong>横轴为仿真时间，纵轴为累计payload。左图比较正常和故障下接收端实际接受的有效字节；右图区分源发送、物理接收、协议接受和源确认。</p>
<p>重传会让发送/物理接收量增加，但不会增加任务所需有效数据。当前锁定的Go-Back-N接收端只接受连续序号，乱序包即使物理到达也可能被丢弃，因此“去重到达”会早于“协议有效到达”。ACK丢失可能使接收端早已收齐、发送端仍未确认。这里的接受是NIC协议语义，不是GPU计算已可见。</p>
<p>累计数据曲线中的平台只表示有效字节没有增长；要判定由丢包、队列还是前驱依赖造成，应结合逐包与依赖记录，不能只凭曲线归因。</p></div>
<h2 id="reproduce">5. 怎样改参数和复现</h2>
<p>网络输入与模型输入分别保存在<code>inputs/</code>。调整Pod、交换机数量、rail/plane、上联数、链路速率后，重新生成拓扑；调整batch、专家部署与计算声明后，重新核算容量。当前配置没有替用户运行新的大规模网络。</p>
<pre>PYTHONPATH=python python3 experiments/large_model_scaleout/generate.py \
  --repository "$PWD" --output /path/to/new-topologies
PYTHONPATH=python python3 experiments/large_model_scaleout/prepare_models.py \
  --sources experiments/large_model_scaleout/upstream \
  --base examples/scaleout_arrivals/declared-119b.json --output /path/to/new-models</pre>
<p>完整绘图命令见实验目录README。每次使用新输出目录，保留失败与原有结果。<a href="verification.json">核验摘要</a> · <a href="models.json">模型核算</a> · <a href="topologies.json">网络规模</a> · <a href="arrivals.json">原曲线摘要</a></p>
<footer>图片可离线打开；PNG用于观看，PDF/SVG用于报告。拓扑导出文件只是对应格式的输入，不代表已在上游SimAI成功执行。无权重下载、GPU执行、依赖更新或push。</footer></main></html>'''
    (output/'report.html').write_text(page)
    save(output/'artifact-manifest.json',dict(files=[dict(path=str(p.relative_to(output)),bytes=p.stat().st_size,
          sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(output.rglob('*')) if p.is_file()]))
    print(json.dumps(dict(figures=8,topologies=6,report=str(output/'report.html'),verification_passed=True)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('repository','stage','arrivals','output'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();render(a.repository.resolve(),a.stage.resolve(),a.arrivals.resolve(),a.output.resolve())
