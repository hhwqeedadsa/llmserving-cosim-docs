"""Create scientific figures and a portable offline report from this run only."""
import argparse
from collections import Counter
import csv
import gzip
import hashlib
import html
import json
from pathlib import Path
import shutil

CASES=('clos-normal','clos-loss','hpn-normal','hpn-loss')
def read(p):return json.loads(p.read_bytes())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def rows(p):
    with p.open() as f:return [{k:int(v or 0) for k,v in row.items()} for row in csv.DictReader(f)]
def table(headers,data):
    def row(values,tag):return '<tr>'+''.join('<'+tag+'>'+html.escape(str(v))+'</'+tag+'>' for v in values)+'</tr>'
    return '<div class="scroll"><table>'+row(headers,'th')+''.join(row(v,'td') for v in data)+'</table></div>'


def render(stage,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    import numpy as np
    verdict=read(stage/'independent-verification.json');assert verdict['passed']
    assert not output.exists();output.mkdir(parents=True);data_dir=output/'data';data_dir.mkdir();inputs=output/'inputs';inputs.mkdir()
    summaries=read(stage/'summary.json');config=read(stage/'inputs/service.json')
    sources=read(stage/'inputs/sources.json');figures=[];figure_records={};drawings={};curves={};endpoints={}
    def figure(fig,name,record):
        for ext in ('png','pdf','svg'):fig.savefig(output/(name+'.'+ext),dpi=170)
        plt.close(fig);figures.append(name);figure_records[name]=record
    for case in CASES:
        endpoints[case]=rows(stage/(case+'-dispatch-audit')/'endpoints.csv')
        shutil.copyfile(stage/(case+'-dispatch-audit')/'endpoints.csv',data_dir/(case+'-endpoints.csv'))
        shutil.copyfile(stage/case/'verification.json',data_dir/(case+'-verification.json'))
        for phase in ('dispatch','combine'):
            prefix=case+'-'+phase;audit=stage/(prefix+'-audit')
            curves[prefix]=rows(audit/'arrival_curve.csv')
            shutil.copyfile(audit/'arrival_curve.csv',data_dir/(prefix+'-curve.csv'))
            for name in ('packet_events.csv','messages.csv'):
                target=data_dir/(prefix+'-'+name+'.gz')
                with target.open('wb') as file,gzip.GzipFile(filename='',fileobj=file,mode='wb',mtime=0) as compressed:
                    with (audit/name).open('rb') as original:shutil.copyfileobj(original,compressed)
        assert summaries[case]['verification']['passed']
    for family in ('clos','hpn'):
        t=read(stage/('topology-'+family)/'topology.json');tc=read(stage/'inputs'/(family+'-topology.json'))
        shutil.copyfile(stage/('topology-'+family)/'topology.json',inputs/(family+'-physical-topology.json'))
        shutil.copyfile(stage/'inputs'/(family+'-topology.json'),inputs/(family+'-topology-config.json'))
        r=read(stage/(family+'-normal')/'result.json');active={rank for m in r['messages'] for rank in (m['src'],m['dst'])}
        source_ranks={v['rank_id'] for v in config['routes']['requests']};planes=tc['fabric']['planes'];pods=tc['deployment']['pods']
        fig,axes=plt.subplots(planes,1,figsize=(14,5 if planes==1 else 8),squeeze=False,layout='constrained');drawn=0
        for plane in range(planes):
            ax=axes[plane,0];positions={}
            for pod in range(pods):
                left,right=pod/pods,(pod+1)/pods;ax.axvspan(left,right,color='#f1f5f9' if pod%2==0 else '#fafafa',zorder=0)
                ax.text((left+right)/2,.025,f'Pod {pod}',ha='center',fontsize=9)
                for kind,y in (('gpu',.15),('tor',.43),('agg',.68)):
                    nodes=sorted((n for n in t['nodes'] if n['kind']==kind and n.get('pod')==pod and (kind=='gpu' or n['plane']==plane)),key=lambda n:n['id'])
                    for node,x in zip(nodes,np.linspace(left+.02/pods,right-.02/pods,len(nodes))):positions[node['id']]=(x,y)
            cores=[n for n in t['nodes'] if n['kind']=='core' and n['plane']==plane]
            for node,x in zip(cores,np.linspace(.08,.92,len(cores))):positions[node['id']]=(x,.91)
            edges=[e for e in t['links'] if e['src'] in positions and e['dst'] in positions];drawn+=len(edges)
            for tier,color in (('access','#94a3b8'),('tor_agg','#3b82f6'),('agg_core','#10b981')):
                ax.add_collection(LineCollection([(positions[e['src']],positions[e['dst']]) for e in edges if e['tier']==tier],colors=color,linewidths=.55,alpha=.25,zorder=1))
            for kind,y,marker,size,color in (('gpu',.15,'o',13,'#cbd5e1'),('tor',.43,'s',28,'#3b82f6'),('agg',.68,'s',40,'#8b5cf6'),('core',.91,'s',50,'#10b981')):
                selected=[n['id'] for n in t['nodes'] if n['kind']==kind and n['id'] in positions]
                colors=[('#ef6c39' if i in source_ranks else '#334155' if i in active else '#cbd5e1') if kind=='gpu' else color for i in selected]
                ax.scatter([positions[i][0] for i in selected],[positions[i][1] for i in selected],s=size,marker=marker,c=colors,zorder=3)
                ax.text(-.025,y,kind.upper()+' '+str(len(selected)),ha='right',va='center',fontsize=9)
            ax.axis('off');ax.set(xlim=(-.07,1.02),ylim=(0,1.03));ax.set_title(f'Plane {plane} | access {tc["links"]["access"]["rate_bps"]//10**9} Gb/s; upper 400 Gb/s')
        assert drawn==len(t['links']);drawings[family]=dict(physical_links=len(t['links']),drawn_links=drawn,planes=planes)
        fig.suptitle(f'{family.upper()} reference | 320 GPU endpoints / 40 servers | native execution verified\n15 source ranks (orange); 221 data-active ranks (orange + dark); inactive ranks (light)',fontsize=12)
        figure(fig,family+'-topology',dict(kind='all_physical_links',**drawings[family]))
        for phase in ('dispatch','combine'):
            fig,axes=plt.subplots(1,2,figsize=(12.5,4.3),layout='constrained')
            def plot(ax,case,key,label,color,style='-'):
                series=curves[case+'-'+phase]
                ax.step([v['time_ns']/1000 for v in series],[v[key]/1024 for v in series],where='post',label=label,color=color,linestyle=style,linewidth=1.6)
            for condition,color in (('normal','#2563eb'),('loss','#ea580c')):
                plot(axes[0],family+'-'+condition,'accepted_bytes',condition+' accepted',color)
                complete=summaries[family+'-'+condition][phase]['slice']['data_ready_ns']/1000
                axes[0].axvline(complete,color=color,alpha=.45,linestyle=':',linewidth=1)
                axes[0].annotate(f'{complete:.3f} us',(complete,1344),xytext=(3,-17 if condition=='normal' else 6),textcoords='offset points',fontsize=8,color=color)
            for key,label,color,style in (('tx_payload_bytes','Source transmitted','#7c3aed','-'),('rx_payload_bytes','Physical destination arrival','#64748b','--'),('unique_arrival_bytes','Unique physical arrival','#0891b2',':'),('accepted_bytes','Protocol accepted','#ea580c','-'),('confirmed_bytes','Source confirmed','#059669','--')):
                plot(axes[1],family+'-loss',key,label,color,style)
            for ax in axes:
                ax.grid(alpha=.22);ax.set(xlabel='Simulation time (us)',ylabel='Cumulative payload (KiB)');ax.legend(fontsize=8,loc='lower right')
                ax.axhline(summaries[family+'-loss'][phase]['slice']['logical_bytes']/1024,color='#cbd5e1',linestyle=':',linewidth=.8)
            axes[0].set_title('Useful arrivals: normal vs specified loss');axes[1].set_title('Loss: source, network and receiver boundaries')
            # Only a small final payload is missing during recovery; a full
            # cumulative scale alone hides the application-critical tail.
            inset=axes[0].inset_axes([.38,.25,.59,.4])
            for condition,color in (('normal','#2563eb'),('loss','#ea580c')):
                plot(inset,family+'-'+condition,'accepted_bytes',condition,color)
            target=summaries[family+'-loss'][phase]['slice']['logical_bytes']/1024
            inset.set_ylim(target-20,target+1);inset.grid(alpha=.2);inset.tick_params(labelsize=7)
            inset.set_title('Final 20 KiB (same data)',fontsize=8)
            fig.suptitle(f'{family.upper()} | 320 endpoints / EP256 slice | g0.w0 | R1 source layer 3 | {phase.capitalize()}')
            figure(fig,family+'-'+phase+'-arrival',dict(kind='cumulative_arrival',normal_csv='data/'+family+'-normal-'+phase+'-curve.csv',loss_csv='data/'+family+'-loss-'+phase+'-curve.csv',graph='g0.w0',source_layer=3,phase=phase))
    fig,axes=plt.subplots(2,1,figsize=(13,7),layout='constrained')
    for ax,family in zip(axes,('clos','hpn')):
        normal=endpoints[family+'-normal'];loss=endpoints[family+'-loss'];x=np.arange(320)
        ax.bar(x-.2,[v['source_tx_payload_bytes']/1024 for v in loss],width=.4,label='Source TX with retransmissions',color='#3b82f6')
        ax.bar(x+.2,[v['accepted_bytes']/1024 for v in loss],width=.4,label='Receiver accepted',color='#10b981')
        ax.set(xlabel='Physical GPU rank',ylabel='Whole-segment payload (KiB)',title=family.upper()+' / specified loss / all four waves');ax.grid(axis='y',alpha=.2);ax.legend(fontsize=8)
    figure(fig,'endpoints',dict(kind='whole_segment_endpoint_bytes',csvs=['data/'+f+'-loss-endpoints.csv' for f in ('clos','hpn')]))
    fig,axes=plt.subplots(1,2,figsize=(13,4.6),layout='constrained')
    for ax,family in zip(axes,('clos','hpn')):
        labels=[]
        for ci,condition in enumerate(('normal','loss')):
            f=read(stage/(family+'-'+condition)/'frontend.json')
            for hi,h in enumerate(sorted(f['history'],key=lambda v:(v['group_index'],v['step']))):
                y=ci*5+hi;ax.barh(y,(h['finished_ns']-h['start_ns'])/1000,left=h['start_ns']/1000,color='#2563eb' if ci==0 else '#ea580c')
                labels.append((y,condition+' '+h['graph_id']))
        ax.set_yticks([v[0] for v in labels],[v[1] for v in labels],fontsize=8);ax.invert_yaxis();ax.set(xlabel='Actual native graph interval (us)',title=family.upper());ax.grid(axis='x',alpha=.2)
    figure(fig,'feedback-timeline',dict(kind='frontend_native_graph_intervals',source='frontend.json history verified against native service waves'))
    # Same logical demand in both topologies; show source-to-expert-rank hotspots.
    r=read(stage/'clos-normal/result.json');dispatch=[m for g in (stage/'clos-normal/graphs').iterdir() for m in read(g/'wave.json')['messages'] if m['phase']=='dispatch']
    ranks=sorted({m['src'] for m in dispatch});matrix=np.zeros((len(ranks),320),dtype=np.int64)
    for m in dispatch:matrix[ranks.index(m['src']),m['dst']]+=m['bytes']
    fig,ax=plt.subplots(figsize=(13,4.8),layout='constrained');im=ax.imshow(matrix/1024,aspect='auto',interpolation='nearest',cmap='YlOrRd')
    ax.set(xlabel='Destination expert GPU rank',ylabel='Source request rank',title='Real R1 choices | whole-segment Dispatch demand | per-expert-copy abstraction');ax.set_yticks(range(len(ranks)),ranks,fontsize=8);fig.colorbar(im,ax=ax,label='Logical payload (KiB)')
    figure(fig,'route-demand',dict(kind='source_destination_matrix',rows=ranks,columns=320,total_bytes=int(matrix.sum())))
    for name in ('service.json','normal.json','clos-loss.json','hpn-loss.json','fault-selection.json','sources.json'):
        shutil.copyfile(stage/'inputs'/name,inputs/name)
    shutil.copyfile(stage/'independent-verification.json',output/'verification.json')
    save(output/'report-data.json',dict(summary=summaries,independent_verification=verdict,drawings=drawings,figure_records=figure_records,
        scope=dict(physical_gpus=320,source_requests=15,data_active_gpus=221,model_source='R1-AWQ/MMLU',layers=[3,4],decode_entries=[1,2],gpu_capacity_reference='H100, DeepSeek671B',hardware_calibrated=False)))
    summary_table=table(['场景','消息','有效字节','源发送字节','包事件','服务完成/µs','首Dispatch收齐/µs'],[
        [case,s['verification']['messages'],f"{s['verification']['remote_payload_bytes']:,}",f"{s['dispatch']['source_tx_payload_bytes']:,}",f"{s['packet_events']:,}",f"{s['finish_ns']/1000:.3f}",f"{s['dispatch']['slice']['data_ready_ns']/1000:.3f}"] for case,s in summaries.items()])
    topo_table=table(['参考结构','GPU/服务器','Pod','Rail/Plane','ToR/Agg/Core','物理边','接入/上层 Gb/s'],[
        ['Clos','320 / 40',5,'1 / 1','20 / 20 / 8',440,'400 / 400'],['HPN启发','320 / 40',2,'8 / 2','64 / 16 / 8',960,'每plane 200 / 400']])
    source_table=table(['原文件','源rank','字节','SHA256'],[[v['name'],v['rank_id'],v['bytes'],v['sha256']] for v in sources['files']])
    def picture(name,caption):
        return '<figure><a href="'+name+'.png"><img loading="lazy" src="'+name+'.png" alt="'+html.escape(caption)+'"></a><figcaption>'+caption+' <a href="'+name+'.pdf">PDF</a> · <a href="'+name+'.svg">SVG</a></figcaption></figure>'
    sections=''
    for family in ('clos','hpn'):
        sections+='<section class="fabric" data-family="'+family+'"><h2>'+family.upper()+'：实际执行的网络与到达曲线</h2>'
        sections+=picture(family+'-topology','全部物理链路均按本次输入绘制；两plane图重复展示同一批GPU。橙色为请求源，深色为参与收发端点，浅灰为本窗口未参与数据收发端点。')
        for phase,label in (('dispatch','发给专家'),('combine','专家结果返回')):
            sections+=picture(family+'-'+phase+'-arrival',label+'：左图比较正常/故障累计有效到达；右图分开发送、物理到达、去重到达、协议接受和源确认。横轴为本轮实际仿真时间。')
        sections+='</section>'
    downloads=''
    for case in CASES:
        downloads+='<li>'+case+'：<a href="data/'+case+'-endpoints.csv">320个rank双端统计</a> · <a href="data/'+case+'-verification.json">联合执行核验</a>'
        for phase in ('dispatch','combine'):
            pre=case+'-'+phase
            downloads+='；'+phase+' <a href="data/'+pre+'-curve.csv">曲线CSV</a> / <a href="data/'+pre+'-packet_events.csv.gz">逐包CSV.gz</a> / <a href="data/'+pre+'-messages.csv.gz">逐消息CSV.gz</a>'
        downloads+='</li>'
    endpoint_data={case:items for case,items in endpoints.items()}
    report='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SimAI × ns-3 · 320卡真实专家路由</title><style>
+:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f1f5f9;color:#17243c;font:16px/1.75 system-ui,sans-serif}main{max-width:1250px;margin:auto;padding:30px 24px 80px}header{padding:25px 30px;background:#10223d;color:white;border-radius:16px}h1{font-size:30px;margin:5px 0 12px}h2{font-size:23px;margin:34px 0 12px}h3{font-size:18px}a{color:#1762c5}header a{color:#a7d7ff}.chips{display:flex;gap:12px;flex-wrap:wrap}.chips span{background:#ffffff17;padding:5px 14px;border-radius:24px}.card,figure{background:white;border-radius:12px;padding:20px;margin:20px 0;box-shadow:0 3px 14px #14284408}.note{border-left:4px solid #ef8b42;padding:12px 18px;background:#fff7ed}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;background:white;font-size:14px}th,td{padding:9px 12px;text-align:left;border-bottom:1px solid #e2e8f0;white-space:nowrap}th{background:#e7edf6}img{max-width:100%;height:auto;display:block}figcaption{font-size:14px;color:#475569}select,input{font:inherit;padding:5px 10px;border:1px solid #cbd5e1;border-radius:6px}pre{overflow:auto;background:#14243b;color:#d7e8ff;padding:18px;border-radius:8px;font-size:13px}.small{font-size:14px;color:#52637b}.metrics{font-variant-numeric:tabular-nums}li{margin:8px 0}code{overflow-wrap:anywhere}.endpoint{max-height:420px;overflow:auto}
+</style><main><header><div>本轮实际运行 · 2026-10-28</div><h1>SimAI前端 + 原生ns-3：320卡真实专家路由</h1><p>从真实专家选择，到发送矩阵、网络包与完成反馈，展示一次可追溯的跨服务器MoE通信实验。</p><div class="chips"><span>320物理GPU端点</span><span>15个真实请求</span><span>221个数据活动GPU</span><span>四次共享网络执行</span></div></header>
+<div class="card"><h2>先看结果</h2><p>两套结构的正常和指定丢包条件均已执行完成。每次942条消息、13,619,200有效字节；完整捕获62,736—67,304条包事件。三个指定丢包均命中并恢复，SimAI下一轮批次按本次网络实际完成时间推进。</p>SUMMARY<div class="note">这是320端点结构中15个请求的有限通信层段，不是320卡满载、完整R1前向或实机性能预测。较复杂的HPN启发结构在本输入下没有更快，不能据此评判生产HPN。</div></div>
+<h2>任务是怎么进入网络的？</h2><div class="card"><p><b>真实R1专家选择JSON → SimAI实际Cluster/Orca调度 → 按专家部署生成Dispatch/Combine消息与计算依赖 → ASTRA驱动同一个ns-3网络 → 实际完成事件返回SimAI。</b></p><p>前12个请求在EP256组，每rank每层1个路由专家；后3个请求在EP64组，每rank每层4个专家。共256个路由专家、Top8、hidden 7168，BF16激活，每份专家贡献14,336B。仅观察原层3—4、Decode entry1—2，初始KV状态是声明前提。</p><p>前端管理请求、batch和KV资源；后端计算包的路径、链路争用、丢包和恢复时间。没有源token的卡不增加虚假的Attention/pack/merge，收到专家任务时仍执行专家计算。计算采用声明参考值，H100/DeepSeek671B是上游容量配置，未调用计算性能预测器或硬件profile。</p><p>本适配按专家贡献复制载荷；同token同目的rank的多专家不做现代通信库的去重，不包含完整DeepEP控制操作。不能将此抽象称为生产通信库复现。</p></div>
+<h2>网络配置与比较约束</h2>TOPO<p>两结构的交换机、上联资源和Pod分布不同。HPN的单消息依所选plane发送，不把两plane直接合并成单消息400G；同服务器不同GPU也走声明网络，没有NVLink旁路。各场景正常/故障保持本结构相同输入和资源。</p>
+<p>观看结构：<select id="fabric"><option value="all">两种结构</option><option value="clos">只看Clos</option><option value="hpn">只看HPN启发</option></select></p>SECTIONS
+<h2>怎样读累计有效到达图？</h2><div class="card"><p>曲线向上走，表示有更多数据到达。橙线长时间停住，表示部分必要数据仍在恢复；其他消息到齐也不能让整个collective完成。最终有效值是这次g0.w0、原层3、一个Dispatch或Combine的1,376,256B。</p><p><b>发送字节</b>包含重传；<b>物理接收</b>可能包含重复或乱序数据；<b>去重到达</b>表示曾经抵达的不同字节；<b>有效接受</b>按当前Go-Back-N协议只累计连续正确序号；<b>源确认</b>还要等反馈回到发送方。它们可能在不同时间达到目标，有效接受也不等于GPU内存可见。</p><p>恢复配置相同：GBN、初始RTO40µs、最大160µs、最多4次重试。事前从输入选跨Pod消息，数据offset9000丢两次，另一消息最终ACK丢一次；这是特定故障功能对照，不能称为50%随机丢包实验。</p></div>
+<h2>网络如何影响下一轮任务</h2>TIMELINE<p>每一条是原生网络中的实际图执行区间。g0.w1起点等于g0.w0终点，g1同理；两组共享一个持续存在的网络，但一组故障不会凭空改写另一组依赖。这里没有将两个独立仿真耗时相加。</p>
+<h2>热点需求与收发规模</h2>DEMAND ENDPOINTS<p>热点图是本窗口的真实选择所生成的通信需求，不是对整个MMLU或生产请求分布的估计。源请求只在15张卡，接收专家任务的卡更多；全任务有效入量与逻辑发送总量守恒。</p>
+<div class="card"><h3>逐rank查看</h3><label>场景 <select id="case">CASEOPTIONS</select></label> <label>rank <input id="rank" type="number" min="0" max="319" placeholder="全部"></label><div class="endpoint"><table><thead><tr><th>rank</th><th>发送数据包</th><th>接收数据包</th><th>实际发送载荷/B</th><th>物理接收载荷/B</th><th>有效接受/B</th></tr></thead><tbody id="endrows"></tbody></table></div><p class="small">CSV中未触发的Counter字段为空，表格按0显示。该表包含全部320个rank。</p></div>
+<h2>可以得出什么，还缺什么？</h2><div class="card"><ul><li>已证明真实选择、前端调度、320端点共享网络和逐包统计能连起来；942条消息和全部有效字节均完成。</li><li>本次少量指定丢包即可拖住必要的通信阶段，并将等待反馈到后续批次；这是恢复影响的观测，不是新恢复算法或优化收益。</li><li>正常Clos为87.346µs，HPN启发为91.815µs。资源、路径和接入带宽均不同，本实验不支持把差异全部归于拓扑，也没有证明HPN有净收益。</li><li>后续网络研究需要更多独立请求/更大真实batch、统一资源预算的拓扑对照，以及对计算、网络参数和通信库语义的校准。当前15文件是用户方便样本，没有生产代表性保证。</li></ul></div>
+<h2>数据、核验与下载</h2><p><a href="verification.json">独立原件/逐包/曲线核验</a> · <a href="report-data.json">全部图表数据与范围</a> · <a href="inputs/fault-selection.json">执行前冻结的故障选择</a> · <a href="inputs/service.json">模型与请求配置</a></p><ul>DOWNLOADS</ul><details><summary>15份原件来源与SHA256</summary><p>来源：core12345/MoE_expert_selection_trace，cognitivecomputations/DeepSeek-R1-AWQ/mmlu/。用户确认目录，新增文件的下载revision与远端内容身份未独立验证；参考目录版本不冒充下载版本。原件保存在本机运行目录，不在本网页包中。</p>SOURCES</details>
+<h2>复现入口</h2><p>使用仓库experiments/simai_scaleout/run.py，先准备新的结果目录，再执行和审核。真实原件按清单放到data/moe-trace-small，SimAI来源先通过simai-prepare；原生service和protoc由已有固定依赖构建提供。所有命令、有效输入和资源监控保留在本轮运行目录。</p><pre>PYTHONPATH=python python experiments/simai_scaleout/run.py prepare --root runs/my-simai-320
+PYTHONPATH=python python experiments/simai_scaleout/run.py run --root runs/my-simai-320 --runtime "$SIMAI_RUNTIME" --backend "$NATIVE_SERVICE" --protoc "$PROTOC"
+PYTHONPATH=python python experiments/simai_scaleout/run.py audit --root runs/my-simai-320</pre><p class="small">图中时间均为仿真纳秒转换为微秒，不是程序运行耗时。平台与实验代码只保存在本地Git提交，未push。</p></main><script>
+const endpoints=ENDPOINTJSON;
+document.getElementById('fabric').onchange=e=>document.querySelectorAll('.fabric').forEach(s=>s.hidden=e.target.value!=='all'&&s.dataset.family!==e.target.value);
+function show(){const name=document.getElementById('case').value;const filter=document.getElementById('rank').value;const rows=endpoints[name].filter(r=>filter===''||r.rank===Number(filter));document.getElementById('endrows').innerHTML=rows.map(r=>'<tr>'+['rank','tx_data_packets','rx_data_packets','source_tx_payload_bytes','destination_rx_payload_bytes','accepted_bytes'].map(k=>'<td>'+r[k].toLocaleString()+'</td>').join('')+'</tr>').join('');}
+document.getElementById('case').onchange=show;document.getElementById('rank').oninput=show;show();
+</script></html>'''.replace('\n+','\n')
    for key,value in {'SUMMARY':summary_table,'TOPO':topo_table,'SECTIONS':sections,'TIMELINE':picture('feedback-timeline','正常/故障四个图的实际执行区间与前端反馈。'),'DEMAND':picture('route-demand','15个请求源到320个专家GPU的累计Dispatch需求。'),'ENDPOINTS':picture('endpoints','全任务实际发送与有效接受，包含所有rank。'),'CASEOPTIONS':''.join('<option>'+v+'</option>' for v in CASES),'DOWNLOADS':downloads,'SOURCES':source_table,'ENDPOINTJSON':json.dumps(endpoint_data)}.items():report=report.replace(key,value)
    (output/'report.html').write_text(report)
    hashes={str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()}
    save(output/'artifacts.json',dict(files=hashes,figures=figure_records,source_stage=str(stage),source_verification_sha256=sha(stage/'independent-verification.json')))
    print(json.dumps(dict(passed=True,output=str(output),figures=len(figures),files=len(hashes)),indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();render(a.stage.resolve(),a.output.resolve())
