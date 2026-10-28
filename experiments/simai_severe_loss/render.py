"""Offline review webpage and publication-exportable cumulative arrival figures."""
import argparse
import csv
import gzip
import hashlib
import html
import json
from pathlib import Path
import shutil

CASES=('clean400','bottleneck25','loss50','loss75','blackout','persistent')
COLORS=('#2563eb','#0d9488','#eab308','#f97316','#dc2626','#6b7280')
LABELS=('400G / no injected loss','25G / no injected loss','25G / 50% window','25G / 75% window','25G / short blackout','25G / persistent blackout')


def read(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def rows(p):
    with p.open() as f:return [{k:int(v or 0) for k,v in r.items()} for r in csv.DictReader(f)]
def table(headers,data):
    def row(vals,tag):return '<tr>'+''.join('<'+tag+'>'+html.escape(str(x))+'</'+tag+'>' for x in vals)+'</tr>'
    return '<div class="scroll"><table>'+row(headers,'th')+''.join(row(r,'td') for r in data)+'</table></div>'


def render(root,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    v=read(root/'independent-verification.json');assert v['passed'] and not output.exists()
    output.mkdir(parents=True);data=output/'data';data.mkdir();inputs=output/'inputs';inputs.mkdir()
    curves={};figures=[]
    for case in CASES:
        for name in ('scenario.json','scenario.md'):
            shutil.copyfile(root/case/name,data/(case+'-'+name))
        if (root/case/'verification.json').exists():shutil.copyfile(root/case/'verification.json',data/(case+'-verification.json'))
        shutil.copyfile(root/case/'execution/probe.json',data/(case+'-host.json'))
        if (root/'commands'/('loss-check-'+case+'.stdout')).exists():
            shutil.copyfile(root/'commands'/('loss-check-'+case+'.stdout'),data/(case+'-loss-check.json'))
        for phase in v['cases'][case]['slices']:
            audit=root/(case+'-'+phase+'-audit');prefix=case+'-'+phase
            curves[case,phase]=rows(audit/'arrival_curve.csv')
            shutil.copyfile(audit/'arrival_curve.csv',data/(prefix+'-curve.csv'))
            shutil.copyfile(audit/'summary.json',data/(prefix+'-summary.json'))
            for name in ('messages.csv','packet_events.csv'):
                with (data/(prefix+'-'+name+'.gz')).open('wb') as dst,gzip.GzipFile(filename='',fileobj=dst,mode='wb',mtime=0) as z:
                    with (audit/name).open('rb') as src:shutil.copyfileobj(src,z)
        shutil.copyfile(root/(case+'-dispatch-audit')/'endpoints.csv',data/(case+'-endpoints.csv'))
    for p in (root/'inputs').glob('*.json'):shutil.copyfile(p,inputs/p.name)
    for name in ('400','25'):shutil.copyfile(root/('topology'+name)/'topology.json',inputs/('physical-topology'+name+'.json'))
    shutil.copyfile(root/'preparation.json',inputs/'preparation.json')
    shutil.copyfile(root/'execution-inputs.json',inputs/'execution-inputs.json')
    if (root/'cpu-scheduler-check.json').is_file():
        shutil.copyfile(root/'cpu-scheduler-check.json',data/'cpu-scheduler-check.json')
    shutil.copyfile(root/'independent-verification.json',output/'verification.json')

    def figure(fig,name):
        for ext in ('png','pdf','svg'):fig.savefig(output/(name+'.'+ext),dpi=160)
        plt.close(fig);figures.append(name)
    def step(ax,case,phase,key,label,color,style='-'):
        a=curves[case,phase]
        ax.step([p['time_ns']/1000 for p in a],[p[key]/1024 for p in a],where='post',label=label,color=color,linestyle=style,linewidth=1.5)
    for phase in ('dispatch','combine'):
        fig,axes=plt.subplots(1,2,figsize=(13.4,4.7),layout='constrained')
        target=v['cases']['clean400']['slices'][phase]['logical_bytes']/1024
        for case,color,label in zip(CASES,COLORS,LABELS):
            if (case,phase) not in curves:continue
            for ax in axes:step(ax,case,phase,'accepted_bytes',label,color)
        for ax in axes:
            ax.grid(alpha=.2);ax.set(xlabel='Simulation time (us)',ylabel='Cumulative protocol-accepted payload (KiB)',xlim=(0,720))
            ax.axhline(target,color='#94a3b8',linestyle=':',linewidth=.8);ax.axvspan(0,100,color='#fca5a5',alpha=.12)
        axes[0].legend(fontsize=8,loc='lower right');axes[1].set_ylim(target-80,target+8)
        axes[0].set_title('Full payload; shaded: artificial fault window')
        axes[1].set_title('Final 80 KiB: tail completion')
        fig.suptitle(f'R1 source layer 3 / g0.w0 / {phase.title()} | 320 endpoints, EP256 + EP64, batches 6/6/3\nExact-next-sequence acceptance; same source workload in every condition',fontsize=11)
        figure(fig,phase+'-arrivals')
    fig,axes=plt.subplots(2,3,figsize=(15,8),layout='constrained',sharey=True)
    for case,ax,label in zip(CASES,axes.flat,LABELS):
        for key,text,color,style in (('tx_payload_bytes','Source transmitted','#7c3aed','-'),
                ('rx_payload_bytes','Physical RX','#64748b','--'),('unique_arrival_bytes','Unique RX','#0891b2',':'),
                ('accepted_bytes','Protocol accepted','#ea580c','-'),('confirmed_bytes','Source confirmed','#059669','--')):
            step(ax,case,'dispatch',key,text,color,style)
        ax.set(title=label,xlabel='Simulation time (us)',ylabel='Payload (KiB)',xlim=(0,720));ax.grid(alpha=.2)
        ax.axvspan(0,100,color='#fca5a5',alpha=.1)
    axes[0,0].legend(fontsize=7);fig.suptitle('Dispatch boundaries: retransmitted bytes are not new useful data',fontsize=13)
    figure(fig,'dispatch-boundaries')
    fig,axes=plt.subplots(1,2,figsize=(12.8,4.8),layout='constrained')
    for i,(case,color) in enumerate(zip(CASES,COLORS)):
        x=v['cases'][case]
        if x['success']:axes[0].barh(i,x['finish_ns']/1000,color=color)
        else:axes[0].barh(i,x['finish_ns']/1000,facecolor='white',edgecolor=color,hatch='///')
        axes[0].text(x['finish_ns']/1000+5,i,('complete ' if x['success'] else 'FAILED ')+f"{x['finish_ns']/1000:.3f} us",va='center',fontsize=8)
        axes[1].barh(i,x['source_tx_payload_bytes']/2**20,color=color)
        axes[1].text(x['source_tx_payload_bytes']/2**20+.1,i,f"{x['recovery']['timeouts']} RTO",va='center',fontsize=8)
    for ax in axes:ax.set_yticks(range(6),CASES);ax.invert_yaxis();ax.grid(axis='x',alpha=.2)
    axes[0].set(xlabel='Simulation finish / failure time (us)',xlim=(0,920),title='Failed exit is not a completion latency')
    axes[1].set(xlabel='Actual source payload TX (MiB), includes retransmissions',title='Same planned workload; failed run submitted only a prefix',xlim=(0,23))
    figure(fig,'outcomes')
    fig,axes=plt.subplots(6,1,figsize=(13,10),layout='constrained',sharex=True)
    for case,ax in zip(CASES,axes):
        x=v['cases'][case]
        for h in x['frontend_history']:
            y=h['group_index'];end=h.get('finished_ns',x['finish_ns']);color='#2563eb' if y==0 else '#0d9488'
            ax.broken_barh([(h['start_ns']/1000,(end-h['start_ns'])/1000)],(y-.32,.64),facecolors=color,alpha=.85)
            ax.text((h['start_ns']+end)/2000,y,h['graph_id']+(' incomplete' if 'finished_ns' not in h else ''),ha='center',va='center',color='white',fontsize=8)
        ax.set_yticks([0,1],['EP256','EP64']);ax.set_title(case,loc='left',fontsize=10);ax.grid(axis='x',alpha=.2);ax.set_ylim(1.55,-.6)
        ax.axvline(x['finish_ns']/1000,color='#475569',linestyle=':',linewidth=.8)
    axes[-1].set(xlabel='Shared native ns-3 time (us)',xlim=(0,720));fig.suptitle('Actual frontend submissions and graph completion callbacks\nDashed line: final network drain or explicit failure; two EP groups share one fabric')
    figure(fig,'shared-network-timeline')

    summaries=v['cases'];table_rows=[];congestion_rows=[]
    for case in CASES:
        x=summaries[case];loss=x['loss'];a=x['slices']['dispatch'];c=x['congestion']
        fraction='无人工注入' if loss is None else f"{loss['dropped_packets']}/{loss['window_packets']} = {loss['window_fraction']*100:.2f}%"
        whole='—' if loss is None else f"{loss['dropped_packets']}/{loss['eligible_packets']} = {loss['whole_fraction']*100:.2f}%"
        table_rows.append([case,'完成' if x['success'] else '重试耗尽失败',f"{x['finish_ns']/1000:.3f}",fraction,whole,
            '未完成' if a['data_ready_ns'] is None else f"{a['data_ready_ns']/1000:.3f}",x['peak_unconfirmed_messages']])
        congestion_rows.append([case,c['destination_rx_ce_packets'],c['pfc_pause_port_observations'],
            f"{c['peak_switch_egress_queue_bytes']/1024:.1f}",f"{c['queue_area_byte_ns']/1e9:.2f}",x['recovery']['timeouts'],
            f"{x['retransmitted_payload_bytes']/2**20:.3f}",c['dcqcn_feedback_messages'],c['dcqcn_snapshot_unavailable_messages']])
    hardware=table(['项目','固定条件'],[
        ['模拟部署','H100容量参考；320 GPU端点 / 40服务器 / 5Pod / 每服务器8 GPU'],
        ['专家部署','每层256专家、Top8；EP256每rank 1专家，EP64每rank 4专家'],
        ['交换结构','Clos：20 ToR + 20 Agg + 8 Core，440条物理边，1 plane / 1 rail'],
        ['输入与batch','15份不同真实请求；rank0/128/256分别6/6/3，batch上限6/rank'],
        ['模型与片段','R1尺寸；原层3、4；Decode入口1、2；H7168、激活2字节；初始KV已就绪'],
        ['带宽与传播','接入400G；上层400G或25G；每条链路传播1µs'],
        ['共同协议','原生DCQCN / ECN / PFC；Go-Back-N风格接收；项目RTO 40→160µs，4次无进展重试'],
        ['执行边界','计算耗时为声明参考；没有实际GPU、完整R1数值执行或硬件时延校准']])
    options=''.join(f'<option value="{case}">{case}</option>' for case in CASES)
    body=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MoE跨机网络：严重丢包与累计有效到达</title><style>
:root{{color-scheme:light}}*{{box-sizing:border-box}}body{{margin:0;background:#f2f5fa;color:#17263b;font:16px/1.8 system-ui,sans-serif}}main{{max-width:1250px;margin:auto;padding:35px 24px 70px}}h1{{font-size:32px;line-height:1.4}}h2{{font-size:23px;margin-top:38px}}.lede{{font-size:18px}}section,.box{{background:white;border:1px solid #dbe3ed;border-radius:14px;padding:22px;margin:20px 0}}.note{{background:#fff5df;border-left:4px solid #e0a333;padding:14px}}img{{width:100%;height:auto;border:1px solid #e2e8f0;border-radius:8px}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:10px 12px;border-bottom:1px solid #e2e8f0;text-align:left;white-space:normal}}th{{background:#edf3fb}}a{{color:#185bbe}}select{{padding:9px;font-size:16px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;background:#f5f7fb;padding:15px;max-height:520px;overflow:auto}}.links a{{margin-right:16px}}.small{{font-size:14px;color:#52647d}}@media(max-width:600px){{main{{padding:18px 12px}}h1{{font-size:26px}}section{{padding:14px}}}}</style>
<main><p class="small">2026-10-28 · 实际SimAI调度 + 单个原生ns-3网络 · 六条件各运行一次</p>
<h1>故障很严重时，MoE通信究竟怎样停顿、恢复与失败？</h1>
<p class="lede">已用15份真实专家选择构造并发任务。五个条件恢复完成；持续全丢明确失败。图中的“有效到达”只累计接收协议按顺序接受的新字节，重传与乱序丢弃不会重复增加进度。</p>
<p class="note">50%/75%指前100µs内的周期性人工数据丢包；全观察期比例另列。这不是生产网络丢包率，也不能说明拥塞自然造成了这些丢包。当前工作是可核查的故障基准，不是新恢复算法的收益证明。</p>
<section><h2>实验究竟用了什么？</h2>{hardware}<p>真实来源：core12345/MoE_expert_selection_trace，cognitivecomputations/DeepSeek-R1-AWQ/mmlu。来源目录经用户确认，本地原件哈希已核；下载revision未知，未声称远端版本身份已验证。320个物理端点中，完成条件实际有218个端点发送或接收数据，不能写成320卡满载。</p>
<p>所有条件的完整计划为13,533,184有效字节、790条消息；持续黑洞只提交92条消息、1,705,984字节，后续任务没有生成或执行。不能用失败运行的较少工作量比较吞吐。</p></section>
<section><h2>结果：带宽与故障分别增加多少等待？</h2>
{table(['条件','结果','网络终态µs','窗口丢包','全观察期丢包','选定Dispatch收齐µs','最大未确认消息数'],table_rows)}
<p>固定请求与部署时，上层400G→25G，网络终态从171.893µs增至592.996µs。相对25G无人工丢包，50%、75%、短时全丢分别额外增加71.002、97.418、109.710µs（约12.0%、16.4%、18.5%）。这些是故障代价，不能直接当成某项优化可实现的收益。</p>
<p>持续黑洞在603.090µs重试耗尽时终止；有效接受始终为0。斜线条表示失败退出，不能当作较快完成。</p>
<img src="outcomes.png" alt="完成与失败时间、实际发送量和超时次数"><p class="links"><a href="outcomes.pdf">PDF</a><a href="outcomes.svg">SVG</a></p></section>
<section><h2>累计有效到达：先看整体，再看最后一段</h2><p>固定切片为g0.w0、原层3、EP256。Dispatch与Combine各有1,361,920目标有效字节。横轴是共享网络绝对时间；每个rank遵循自身依赖，因此一部分Combine可能在其他rank的Dispatch全部收齐前开始。红色底纹为前100µs故障窗口。</p>
<img src="dispatch-arrivals.png" alt="Dispatch有效到达及尾部放大"><p class="links"><a href="dispatch-arrivals.pdf">PDF</a><a href="dispatch-arrivals.svg">SVG</a></p>
<img src="combine-arrivals.png" alt="Combine有效到达及尾部放大"><p>持续黑洞没有执行到Combine，图中不画一条虚构的Combine曲线。75%窗口的选定Dispatch反而比短时全丢略晚收齐，不能按配置丢包率直接线性推断时延；具体消息受损和排队的作用还需进一步分析。</p></section>
<section><h2>为什么“包已经到了”不等于任务能继续？</h2><p>紫线是源端发送的所有payload，包含重传；灰线是目标物理收到的payload；蓝线将同一字节去重；橙线是协议按连续序号接受的字节；绿线是源端收到的累计确认。乱序包可以物理到达，却被当前Go-Back-N风格接收端丢弃。</p>
<img src="dispatch-boundaries.png" alt="六条件的发送、物理到达、唯一到达、有效接受和源确认"><p class="links"><a href="dispatch-boundaries.pdf">PDF</a><a href="dispatch-boundaries.svg">SVG</a></p></section>
<section><h2>并发是否真的发生在同一个网络？</h2><p>两组EP的首轮任务均在0ns提交给同一个native进程。逐包核算的最大未确认消息数为92至142。图中后续轮次由各自前一轮真实完成回调解锁；竖虚线为网络排空或失败退出，不能与图完成时刻混用。</p><img src="shared-network-timeline.png" alt="两个EP域在同一网络中的实际波次时间线"></section>
<section><h2>拥塞、恢复与一个需要改进的问题</h2>
{table(['条件','目标CE数据包','PFC暂停观测','最大出口队列KiB','队列积分 byte·s','RTO触发','重复TX MiB','已见DCQCN反馈消息','反馈快照缺失消息'],congestion_rows)}
<p>25G无人工丢包仍有126次RTO和约1.915MiB重复发送，400G条件为0。其中86次超时时，已经有能推进该消息确认进度的ACK由目标发出、尚在返回途中。例如消息109：44.535µs发出确认9000字节的ACK，49.619µs源端超时，50.597µs才收到该ACK。这是实际包与定时器记录的关联，不是新增仿真。</p>
<p>慢链路排队和反馈延迟可能超过固定40µs初始RTO，引发不必要重传；需要用后续RTO对照验证可减少的重复发送和任务时延，而不能把这些全部归为故障丢包。已有ACK在途也不证明该消息后续所有字节都无缺失；超时等待区间不能简单相加为可消除的任务收益。</p>
<p>实际记录显示ECN/DCQCN反馈；短时全丢和持续全丢各有PFC暂停/恢复观测。队列峰值不一定随时延上升：25G更显著的变化是队列占用持续时间与积分。CE包计数包括重传；失败未ACK消息没有DCQCN终态快照，缺失不能解释为完全没有网络拥塞。当前自然MMU溢出存在观测盲区，人工注入计数不等于所有自然丢包。</p></section>
<section><h2>RDMA原来有什么，我们补了什么？</h2>
{table(['来源','机制','本次含义'],[
['原生ns-3 RDMA','ECN + DCQCN','队列标记，经ACK/CNP风格反馈调低发送速率；本次不改算法'],
['原生ns-3 RDMA','PFC','按缓冲压力暂停与恢复上游，控制拥塞压力'],
['原生ns-3 RDMA','累计ACK / NACK / Go-Back-N风格回退','接收连续序号；乱序触发NACK，源回退重发；没有启用选择性重传'],
['项目适配','源端RTO、退避、重复数据补ACK、重试耗尽失败','补齐尾包/反馈丢失和长期无进展处理，不把成熟机制称为创新'],
['项目实验与观测','高比例实际包丢弃、硬件摘要、到达曲线与独立核验','用于定位任务级故障代价；离线真值不参与网络决策']])}
<p>这里模拟RoCE风格机制，不是商用NIC固件或完整RoCE协议栈。当前直接收益是把输入、部署、网络与故障结果连起来；尚未证明新的优化方法优于较强基线。</p></section>
<section><h2>逐轮条件与原始分析入口</h2><label for="condition">选择条件：</label><select id="condition">{options}</select><p id="scenario-links"></p><pre id="scenario-text"></pre>
<p class="links"><a href="verification.json">独立逐包核验</a><a href="inputs/sources.json">真实来源与哈希</a><a href="inputs/service.json">服务输入</a><a href="artifacts.json">交付文件哈希</a></p>
<p>每轮包含scenario.json与中文scenario.md；data目录保存逐包明细压缩CSV、累计曲线CSV、端点统计与切片summary。页面完全离线，无外部脚本或网络请求。</p>
<p>独立核验覆盖{v['packet_events_checked']:,}个包事件、{v['curve_rows_checked']:,}行曲线。全部成功条件通信图字节哈希相同；失败只核已生成图和实际提交消息。六条件各执行一次，不代表统计置信区间或生产SLA。</p></section>
<p class="small">下一步：固定拓扑和工作量，对比更适合慢链路的RTO设置，再评估热点任务组织。先排除基线定时器配置影响，才能声称MoE专用方法有额外收益。完整输入和执行预算见实验目录protocol.md；复现需同一批原始路由，本网页不携带原始数据文件。</p>
</main><script>const RUNS=__RUN_DATA__;const select=document.getElementById('condition');function show(){{const k=select.value;document.getElementById('scenario-text').textContent=JSON.stringify(RUNS[k].hardware,null,2);document.getElementById('scenario-links').innerHTML='<a href="data/'+k+'-scenario.md">中文条件摘要</a> · <a href="data/'+k+'-scenario.json">JSON</a> · <a href="data/'+k+'-dispatch-curve.csv">Dispatch累计曲线</a> · <a href="data/'+k+'-dispatch-packet_events.csv.gz">逐包记录</a>';}}select.addEventListener('change',show);show();</script></html>'''
    body=body.replace('__RUN_DATA__',json.dumps(summaries,ensure_ascii=False).replace('</','<\\/'))
    (output/'report.html').write_text(body)
    save(output/'report-data.json',dict(cases=summaries,figures=figures,source=root.name))
    text='''# 真实路由并发任务的严重丢包实验结果

320个模拟H100端点、40服务器、EP256+EP64，每rank每层1/4专家。15个真实请求，rank0/128/256实际batch6/6/3；R1原层3、4和Decode入口1、2，计算为参考、没有硬件校准。完整成功工作量13,533,184有效字节、790消息、218数据活跃rank。

|条件|整段网络终态µs|结果|窗口实际丢包|全观察期丢包|
|---|---:|---|---|---|
'''
    for case in CASES:
        x=summaries[case];loss=x['loss'];wf='无人工注入' if not loss else f"{loss['dropped_packets']}/{loss['window_packets']}";whole='—' if not loss else f"{loss['dropped_packets']}/{loss['eligible_packets']}"
        text+=f"|{case}|{x['finish_ns']/1000:.3f}|{'完成' if x['success'] else '重试耗尽失败'}|{wf}|{whole}|\n"
    text+='''
带宽400G→25G增加421.103µs；相对25G无人工丢包，50%、75%、短黑洞分别增加71.002、97.418、109.710µs。这些是故障与瓶颈代价，不是已取得的优化收益。持续黑洞有效接受0，在603.090µs终止；只提交92消息/1,705,984字节，不能把退出当完成。

25G无人工丢包仍触发126次RTO，重复发送2,008,392字节；400G没有RTO。其中86次超时时已有进度ACK在返程途中；消息109的ACK于44.535µs发出，源49.619µs超时，50.597µs收到。需要验证40µs初始RTO与慢链路排队的匹配，不能把所有重传归因于人工丢包。全丢窗口与75%窗口的阶段完成顺序不完全按比例单调，需结合受损消息与排队解释。

原生ECN/DCQCN/PFC及累计ACK/NACK/GBN保留；源RTO、重复数据补ACK、有限失败是项目适配，均不是新重传创新。逐包有效到达区别物理到达、去重字节、连续接受和源确认。

完整图、逐轮硬件条件与CSV入口：[离线网页](report.html)。所有原件哈希已核，但远端revision未知；320端点不是320满载卡。六条件各执行一次。独立审核与文件哈希见verification.json和artifacts.json。真实输入不随Git分发，复现需先取得对应15份原件。
'''
    (output/'report.md').write_text(text)
    manifest=dict(schema_version='moe-study-artifacts/1',files={str(p.relative_to(output)):dict(bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(output.rglob('*')) if p.is_file()})
    save(output/'artifacts.json',manifest)
    print(json.dumps(dict(passed=True,figures=figures,files=len(manifest['files']),bytes=sum(x['bytes'] for x in manifest['files'].values()),output=str(output)),indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();render(a.root.resolve(),a.output.resolve())
