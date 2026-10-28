"""Render audited curves as standalone scientific figures and offline HTML."""
import argparse
import csv
import html
import json
from pathlib import Path
import shutil


def read(p):return json.loads(p.read_bytes())
def rows(p):return list(csv.DictReader(p.open()))


def render(stage,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    assert not output.exists();output.mkdir(parents=True)
    summary=read(stage/'summary.json');verification=read(stage/'verification.json');assert verification['passed']
    data={};endpoint={};ledger={}
    for case in ('normal','data-and-ack-loss'):
        for phase in ('dispatch','combine'):
            name=case+'-'+phase;audit=stage/(name+'-audit')
            data[name]=[{k:int(v) for k,v in r.items()} for r in rows(audit/'arrival_curve.csv')]
            shutil.copyfile(audit/'arrival_curve.csv',output/(name+'-curve.csv'))
        endpoint[case]=[{k:int(v) for k,v in r.items()} for r in rows(stage/(case+'-dispatch-audit')/'endpoints.csv')]
        shutil.copyfile(stage/(case+'-dispatch-audit')/'endpoints.csv',output/(case+'-endpoints.csv'))
        packets=rows(stage/(case+'-dispatch-audit')/'packet_events.csv')
        ledger[case]=[{k:(r[k] if k=='role' else int(r[k])) for k in
            ('time_ns','message_id','node','port','sequence','data_payload_bytes','accepted_delta_bytes','role')}
            for r in packets if r['role'] not in ('network',)]
    normal=summary['normal-dispatch'];fault=summary['data-and-ack-loss-dispatch']
    figures=[]
    for phase in ('dispatch','combine'):
        fig,axes=plt.subplots(1,2,figsize=(12,4.2),layout='constrained')
        def plot(ax,name,key,label,color,style='-'):
            r=data[name];end=summary[name]['slice']['source_confirmed_ns']*1.12
            visible=[v for v in r if v['time_ns']<=end]
            visible.append(dict(visible[-1],time_ns=int(end)))
            ax.step([v['time_ns']/1000 for v in visible],[v[key]/1024 for v in visible],
                    where='post',label=label,color=color,linestyle=style,linewidth=1.7)
        for case,color in (('normal','#2563eb'),('data-and-ack-loss','#dc5a29')):
            plot(axes[0],case+'-'+phase,'accepted_bytes',case+' accepted',color)
        plot(axes[0],'data-and-ack-loss-'+phase,'unique_arrival_bytes','loss: unique physical arrival','#169977','--')
        for key,label,color,style in (('tx_payload_bytes','Source TX payload','#7c3aed','-'),
                ('rx_payload_bytes','Destination RX payload','#666666','--'),
                ('accepted_bytes','Protocol accepted','#dc5a29','-'),
                ('confirmed_bytes','Source confirmed','#169977',':')):
            plot(axes[1],'data-and-ack-loss-'+phase,key,label,color,style)
        for ax in axes:
            ax.set(xlabel='Simulation time (us)',ylabel='Cumulative payload (KiB)')
            ax.axhline(fault['slice']['logical_bytes']/1024,color='#aaaaaa',linewidth=.8,linestyle=':')
            ax.grid(alpha=.2);ax.legend(fontsize=8,loc='best')
        axes[0].set_title('Normal vs loss: useful arrival');axes[1].set_title('Loss: bytes at different boundaries')
        fig.suptitle('Real R1 routes | EP4 | g0.w0 | source layer 3 | '+phase.capitalize())
        fig.savefig(output/(phase+'-arrival.png'),dpi=170);fig.savefig(output/(phase+'-arrival.pdf'));plt.close(fig)
        figures.append(phase+'-arrival.png')
    model=read(Path(__file__).resolve().parents[2]/'examples/scaleout_arrivals/declared-119b.json')
    # Independent shape arithmetic; no model weights or simulated large-model run.
    m=model['model'];s=m['llm'];h=m['hidden_size'];l=m['layers'];e=m['experts'];f=m['ffn_hidden_size']
    expert=3*l*h*f*e;q=s['attention_heads']*s['head_dim'];kv=s['kv_heads']*s['head_dim']
    replicated=s['vocab_size']*h*2+l*(2*h*q+2*h*kv+2*s['head_dim']+2*h+h*e)+h
    models=dict(declared_GQA_total_parameters=expert+replicated,declared_GQA_EP8_weight_bytes_per_rank=(expert//8+replicated)*2,
        R1_source_total_parameters_reference=671000000000,R1_executed_routed_expert_segment_parameters=3*2*7168*2048*256,
        actual_experts_per_rank_per_layer=64,large_model_network_executed=False)
    artifact=dict(summary=summary,verification=verification,model_scope=models)
    (output/'report-data.json').write_text(json.dumps(artifact,ensure_ascii=False,indent=2)+'\n')
    (output/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2)+'\n')
    def tr(values):return '<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in values)+'</tr>'
    table=''.join(tr([case,r['submitted_message_count'],f"{r['submitted_logical_bytes']:,}",
        f"{r['source_tx_payload_bytes']:,}",f"{r['destination_rx_payload_bytes']:,}",
        f"{r['accepted_bytes']:,}",f"{r['simulation_finished_ns']/1000:.3f}"])
        for case,r in (('正常',normal),('数据与ACK丢失',fault)))
    endtable=''.join(tr([case,r['rank'],r['tx_data_packets'],r['rx_data_packets'],
        f"{r['source_tx_payload_bytes']:,}",f"{r['destination_rx_payload_bytes']:,}",f"{r['accepted_bytes']:,}"])
        for case,items in endpoint.items() for r in items)
    delay=(fault['simulation_finished_ns']-normal['simulation_finished_ns'])/1000
    text=f'''# 真实MoE路由：任务两端统计与collective有效到达

本轮已完成两次真实路由驱动的原生网络执行。四个独立请求、EP4、256专家/Top8、每rank每层64专家，执行原层3—4、Decode入口1—2。H7168、BF16激活，每专家贡献14,336B，按专家贡献复制；同token同目的rank的多专家不去重。计算为声明值，不是完整R1数值执行。

网络为4GPU/4服务器、两Pod、10交换机的三层Clos；100Gb/s接入、25Gb/s上层、每边1µs、单rail。模型GPU标签A100不提供硬件校准。真实上下文105/28/10/6token，输入context_tokens=2048是上限。多消息、多阶段共享一个网络时钟和队列。

| 全任务统计 | 正常 | 数据+ACK故障 |
|---|---:|---:|
| 逻辑消息数 | {normal['submitted_message_count']} | {fault['submitted_message_count']} |
| 逻辑有效字节 | {normal['submitted_logical_bytes']:,} | {fault['submitted_logical_bytes']:,} |
| 源端实际发送载荷 | {normal['source_tx_payload_bytes']:,} | {fault['source_tx_payload_bytes']:,} |
| 目的端物理接收载荷 | {normal['destination_rx_payload_bytes']:,} | {fault['destination_rx_payload_bytes']:,} |
| 协议有效接受字节 | {normal['accepted_bytes']:,} | {fault['accepted_bytes']:,} |
| 服务完成/µs | {normal['simulation_finished_ns']/1000:.3f} | {fault['simulation_finished_ns']/1000:.3f} |

故障任务多等{delay:.3f}µs，约{delay/(normal['simulation_finished_ns']/1000)*100:.2f}%。这是两条预设丢包规则的代价，不是优化收益。

首个Dispatch切片为g0.w0、图内层0/原层3，共8消息、358,400有效字节。有效收齐由53.917µs延后至134.950µs，源端全部确认由59.996µs延后至141.029µs。故障时源端发送528,112B、目的端物理接收524,112B，有效接受仍为358,400B。多跳转发不计作新的应用字节。

数据故障为消息1（0→2、86,016B）序号2000丢两次；该消息84个乱序包、164,032B到达后被GBN丢弃，不能提前算作有效接受。消息6（2→0、71,680B）的最终累计ACK丢一次：数据47.254µs收齐，源端106.685µs才确认。这说明数据到达与发送完成须分开统计。它们是确定性单消息规则，不是全网随机丢包率。

模型与配置：现有完整形状示例Qwen3-30B为30.532B；新增声明GQA MoE为{(expert+replicated)/1e9:.3f}B，EP8每rank每层16专家、BF16权重约{models['declared_GQA_EP8_weight_bytes_per_rank']/2**30:.2f}GiB，未运行该大模型网络。真实路由来自约671B的R1，本轮仅执行两个MoE层网络片段，不能写成完整671B模型仿真。

E256时EP2/4/8/16/64/128/256分别对应每rank每层128/64/32/16/4/2/1专家。改变EP要同步改分组和拓扑，并满足专家数整除EP。已有等容量逐层放置映射可调整专家编号归属，保持每rank数量相等；当前不支持任意不等容量分配、专家复制或动态迁移。完整GQA声明支持1—128层；真实R1服务片段支持1—4个连续MoE层。模型标签、参数声明、容量检查和已执行规模分别报告。

逐包有效曲线通过GBN接收规则从原生MacRx重放；最终前缀、完成时刻和源发包计数逐消息吻合C++记录。独立检查核对原始专家选择、两条件相同任务图、原始TX/RX、丢包命中及曲线增量，均通过。两次网络捕获51,904/53,862事件，均完整。源码、原始数据快照、命令和预算在runs/collective_arrivals_20261028-frontend；首次系统Python缺protobuf在网络启动前失败，保留于旧目录，修正使用已有frontend环境。零安装/下载/编译/GPU运行。

![Dispatch累计到达](dispatch-arrival.png)

![Combine累计到达](combine-arrival.png)
'''
    (output/'report.md').write_text(text)
    html_template='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>真实MoE路由：逐包统计与有效到达</title><style>
body{font:16px/1.7 system-ui,sans-serif;color:#183047;background:#f2f5f8;margin:0}main{max-width:1180px;margin:auto;padding:28px}h1{font-size:30px}h2{margin-top:30px;font-size:22px}section{background:white;border-radius:12px;padding:20px;margin:18px 0}table{border-collapse:collapse;width:100%;font-size:14px}td,th{border-bottom:1px solid #dfe5ec;padding:9px;text-align:right}td:first-child,th:first-child{text-align:left}.scroll{overflow:auto}.note{color:#526578}svg{width:100%;height:auto;background:#fff}select{padding:7px;margin:8px}code{font-size:13px}a{color:#2463c1}.legend{font-size:14px}b{color:#173b60}</style>
<main><h1>真实MoE路由：逐包统计与有效到达</h1><p>四份真实R1专家选择 · EP4 · 每rank每层64专家 · 两层×两步Decode · 100G接入/25G上层Clos</p>
<section><b>已经执行：正常与数据/ACK故障两场景。</b>每个任务76条消息、2,609,152有效字节；服务由333.298µs变为417.998µs。下图把物理到达、有效接受和源端确认分开，重传不会重复计入有效字节。</section>
<section><h2>全任务发送与接收统计</h2><div class="scroll"><table><thead><tr><th>场景</th><th>消息数</th><th>逻辑字节</th><th>实际源发送B</th><th>物理接收B</th><th>有效接受B</th><th>完成µs</th></tr></thead><tbody>__TABLE__</tbody></table></div><p class="note">上表是应用payload；模型包头、控制包单列在endpoints.csv。多跳包数不能相加冒充应用数据量。</p></section>
<section><h2>collective切片的累计曲线</h2><p>切片：<code>g0.w0 / 图内层0 / 原层3</code>，共8条消息、358,400有效字节。</p><label>阶段<select id="phase"><option value="dispatch">Dispatch</option><option value="combine">Combine</option></select></label><label>视图<select id="view"><option value="compare">正常与故障有效接受</option><option value="boundaries">故障时不同统计位置</option></select></label><div class="legend" id="legend"></div><svg id="plot" viewBox="0 0 1060 410" role="img" aria-label="累计字节阶梯曲线"></svg><p id="status"></p><p class="note">蓝/橙：两条件的协议有效接受。故障边界视图显示源发、物理RX、有效接受和源确认。横轴为仿真时间，鼠标在图上移动可读累计值；曲线保持原始阶梯，不做平滑。</p></section>
<section><h2>故障怎样影响通信</h2><p>数据消息1（0→2、86,016B）的序号2000丢两次，84个乱序包共164,032B被GBN丢弃。最终ACK消息6（2→0）数据在47.254µs收齐，到106.685µs才被源端确认。首个Dispatch整体有效收齐由53.917µs变为134.950µs。</p><p>这是同任务上的确定性故障对照，不是随机丢包率扫描，也不是新重传算法或优化收益。</p></section>
<section><h2>每rank的完整任务统计</h2><div class="scroll"><table><thead><tr><th>场景</th><th>rank</th><th>源数据包</th><th>收到数据包</th><th>发送载荷B</th><th>收到载荷B</th><th>有效接受B</th></tr></thead><tbody>__ENDPOINTS__</tbody></table></div></section>
<section><h2>首个Dispatch的端侧逐包打点</h2><label>场景<select id="case"><option value="data-and-ack-loss">数据+ACK丢失</option><option value="normal">正常</option></select></label><label>消息<select id="message"><option value="all">全部</option><option value="1">消息1：数据丢失</option><option value="6">消息6：最终ACK丢失</option></select></label><p id="count"></p><div class="scroll"><table><thead><tr><th>时间µs</th><th>消息</th><th>节点/端口</th><th>字节序号</th><th>payload B</th><th>有效增量B</th><th>事件</th></tr></thead><tbody id="packets"></tbody></table></div><button id="more">显示后续100条</button><p class="note">此表展示端侧事件；完整逐跳、排队、UID及ECN见各audit目录packet_events.csv。重传序号可以相同，不能按包UID去重。</p></section>
<section><h2>模型大小与专家数如何调整</h2><p>真实来源是约671B的DeepSeek-R1；本次执行两个MoE层的通信片段，不执行完整MLA/Dense模型。现有完整GQA形状示例为Qwen3-30B，新增百B声明例子为119.052B、EP8、每rank每层16专家，BF16权重约32.75GiB/rank；该大模型例子只做结构/容量核算。</p><p>E256时，EP2/4/8/16/64/128/256对应每rank每层128/64/32/16/4/2/1个专家。专家数须整除EP；可用逐层映射改变专家归属，当前须等容量，无复制/动态迁移。增大EP并回放真实独立请求，需要相应数量的已核验源请求。</p><p>本轮每个token×专家贡献为14,336B，同目的rank的多个专家不去重；与此前采用目的rank去重的实验通信组织不同，时延不能直接拼成优化对照。A100是标签，计算为声明值，没有硬件校准。</p></section>
<section><h2>复核与下载</h2><p>逐消息接受前缀、实际完成回调、源包计数全部吻合；独立核验原始专家选择、任务图一致性、TX/RX和曲线增量通过。完整抓包为51,904/53,862事件。</p><a href="report.md">文字报告</a> · <a href="verification.json">独立核验</a> · <a href="dispatch-arrival.pdf">Dispatch图PDF</a> · <a href="combine-arrival.pdf">Combine图PDF</a> · <a href="data-and-ack-loss-dispatch-curve.csv">故障Dispatch曲线CSV</a> · <a href="data-and-ack-loss-endpoints.csv">故障两端统计CSV</a></section></main>
<script>const DATA=__DATA__,SUMMARY=__SUMMARY__,LEDGER=__LEDGER__;let shown=100;const $=s=>document.getElementById(s);let lines=[],maxT=0,maxY=0;
function draw(){let p=$('phase').value,v=$('view').value;lines=v==='compare'?[['normal-'+p,'accepted_bytes','正常有效接受','#2563eb'],['data-and-ack-loss-'+p,'accepted_bytes','故障有效接受','#dc5a29'],['data-and-ack-loss-'+p,'unique_arrival_bytes','故障去重物理到达','#169977']]:[['data-and-ack-loss-'+p,'tx_payload_bytes','源发送','#7c3aed'],['data-and-ack-loss-'+p,'rx_payload_bytes','物理RX','#666666'],['data-and-ack-loss-'+p,'accepted_bytes','有效接受','#dc5a29'],['data-and-ack-loss-'+p,'confirmed_bytes','源确认','#169977']];
maxT=Math.max(...lines.map(l=>SUMMARY[l[0]].slice.source_confirmed_ns))*1.12;maxY=Math.max(...lines.flatMap(l=>DATA[l[0]].map(r=>r[l[1]])))*1.08;let x=t=>72+950*t/maxT,y=b=>350-310*b/maxY,s='';for(let i=0;i<=5;i++){let a=i*maxT/5,b=i*maxY/5;s+=`<path d="M${x(a)} 40V350 M72 ${y(b)}H1022" stroke="#e3e8ef" fill="none"/><text x="${x(a)}" y="375" text-anchor="middle" font-size="12">${(a/1000).toFixed(1)}</text><text x="65" y="${y(b)+4}" text-anchor="end" font-size="12">${(b/1024).toFixed(0)}</text>`}
for(let [name,key,label,color] of lines){let r=DATA[name],d=`M${x(0)} ${y(0)}`,last=0;for(let a of r){if(a.time_ns>maxT)break;d+=`H${x(a.time_ns)}V${y(a[key])}`;last=a[key]}d+=`H${x(maxT)}`;s+=`<path d="${d}" stroke="${color}" fill="none" stroke-width="2"/>`}s+='<text x="550" y="402" text-anchor="middle">仿真时间 / µs</text><text x="14" y="25">KiB</text><line id="cursor" x1="72" x2="72" y1="40" y2="350" stroke="#748295" stroke-dasharray="4 4"/>';$('plot').innerHTML=s;$('legend').innerHTML=lines.map(l=>`<span style="color:${l[3]};margin-right:20px">● ${l[2]}</span>`).join('');let n=SUMMARY['normal-'+p].slice,f=SUMMARY['data-and-ack-loss-'+p].slice;$('status').textContent=`有效收齐：正常 ${(n.data_ready_ns/1000).toFixed(3)}µs；故障 ${(f.data_ready_ns/1000).toFixed(3)}µs。`}
$('plot').onmousemove=e=>{let box=$('plot').getBoundingClientRect(),px=(e.clientX-box.left)*1060/box.width,t=Math.max(0,Math.min(maxT,(px-72)*maxT/950));let cursor=$('cursor');cursor.setAttribute('x1',72+950*t/maxT);cursor.setAttribute('x2',72+950*t/maxT);$('status').textContent=`${(t/1000).toFixed(3)}µs：`+lines.map(([n,k,label])=>{let a=DATA[n],lo=0,hi=a.length;while(lo<hi){let m=(lo+hi)>>1;if(a[m].time_ns<=t)lo=m+1;else hi=m}return label+' '+((a[Math.max(0,lo-1)][k])/1024).toFixed(2)+'KiB'}).join('；')};
function packets(){let a=LEDGER[$('case').value].filter(r=>$('message').value==='all'||r.message_id===Number($('message').value));$('packets').innerHTML=a.slice(0,shown).map(r=>`<tr><td>${(r.time_ns/1000).toFixed(3)}</td><td>${r.message_id}</td><td>${r.node}/${r.port}</td><td>${r.sequence}</td><td>${r.data_payload_bytes}</td><td>${r.accepted_delta_bytes}</td><td>${r.role}</td></tr>`).join('');$('count').textContent=`共${a.length}条端侧事件，当前显示${Math.min(shown,a.length)}条。`}
for(let id of ['phase','view'])$(id).onchange=draw;for(let id of ['case','message'])$(id).onchange=()=>{shown=100;packets()};$('more').onclick=()=>{shown+=100;packets()};draw();packets();</script></html>'''
    document=html_template.replace('__TABLE__',table).replace('__ENDPOINTS__',endtable)
    for tag,value in (('__DATA__',data),('__SUMMARY__',summary),('__LEDGER__',ledger)):
        document=document.replace(tag,json.dumps(value,ensure_ascii=False).replace('</','<\\/'))
    (output/'report.html').write_text(document)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();render(a.stage.resolve(),a.output.resolve())
