"""Read completed, verified runs; render static scientific figures and offline HTML."""
import argparse
import base64
from collections import defaultdict
import csv
import hashlib
import html
import json
from pathlib import Path
import re
import statistics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from matplotlib.patches import Patch
import numpy as np


def read(path):return json.loads(path.read_text())
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--runs',type=Path,required=True)
    parser.add_argument('--diagnostics',type=Path,help='Completed diagnostic_controls.py directory')
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    root=args.runs.resolve();out=args.output.resolve();out.mkdir(exist_ok=False)
    summary=read(root/'summary.json');assert len(summary)==31 and read(root/'provenance.json')['completed']
    groups=defaultdict(list)
    for row in summary:groups[row['condition']].append(row)
    order=[x for x in groups if x!='isolated-ep4'];figures=[]
    def finish(fig,name):
        fig.tight_layout();fig.savefig(out/f'{name}.png',dpi=180);fig.savefig(out/f'{name}.pdf');plt.close(fig);figures.append(name)
    means={name:statistics.mean(x['finish_ns']/1000 for x in groups[name]) for name in order}
    fig,ax=plt.subplots(figsize=(11,4.5))
    for i,name in enumerate(order):
        values=[x['finish_ns']/1000 for x in groups[name]]
        ax.scatter([i-0.12,i,i+0.12],values,s=24,c=['#2979b8','#d35400','#399c68'])
        ax.plot([i-.3,i+.3],[means[name]]*2,color='black',lw=1.5)
    ax.set_xticks(range(len(order)),order,rotation=25,ha='right');ax.set_ylabel('4-step / 2-layer segment completion (us)')
    ax.set_title('Paired synthetic route seeds 11 / 23 / 37; black line = mean');ax.grid(axis='y',alpha=.2)
    finish(fig,'01_conditions')

    def run(name,seed=11):return root/f'{name}-{seed}'
    base=read(run('clos')/'result.json');topo=read(run('clos')/'topology.json')
    by_id={n['id']:n for n in topo['nodes']};positions={}
    for level,kind in enumerate(('gpu','tor','agg','core')):
        members=[n for n in topo['nodes'] if n['kind']==kind]
        for i,n in enumerate(members):positions[n['id']]=((i+.5)/len(members),level)
    port={(p['node'],p['port']):p for p in base['ports']}
    values=[]
    for edge in topo['links']:
        a=port[edge['src'],edge['src_port']];b=port[edge['dst'],edge['dst_port']]
        values.append(max(a['model_tx_bytes'],b['model_tx_bytes'])*8/(edge['rate_bps']*base['finish_ns']/1e9))
    fig,ax=plt.subplots(figsize=(10,5));norm=Normalize(vmin=0,vmax=max(values));cmap=plt.get_cmap('YlOrRd')
    for edge,value in zip(topo['links'],values):
        a,b=positions[edge['src']],positions[edge['dst']]
        ax.plot([a[0],b[0]],[a[1],b[1]],color=cmap(norm(value)),lw=1+4*norm(value),zorder=1)
    colors={'gpu':'#2979b8','tor':'#5b9c6f','agg':'#e0a634','core':'#b75a58'}
    for n in topo['nodes']:
        x,y=positions[n['id']];ax.scatter(x,y,s=260,color=colors[n['kind']],zorder=2)
        ax.text(x,y+.15,f"{n['kind']} {n['id']}",ha='center',fontsize=8)
    ax.set_ylim(-.4,3.55);ax.axis('off');ax.set_title('Actual T1 fabric: 8 endpoints, 4 ToR, 4 Agg, 2 Core; all links 100 Gb/s')
    fig.colorbar(plt.cm.ScalarMappable(norm=norm,cmap=cmap),ax=ax,label='Maximum directional model-byte utilization, full run')
    finish(fig,'02_actual_topology')

    fig,axs=plt.subplots(1,2,figsize=(10,4))
    expert_counts={}
    for ax,name in zip(axs,('clos','hotspot')):
        matrix=np.zeros((8,8));counts=np.zeros(16,dtype=int)
        for path in sorted((run(name)/'graphs').glob('*/wave.json')):
            w=read(path)
            for m in w['messages']:
                if m['phase']=='dispatch':matrix[m['src'],m['dst']]+=m['bytes']/1024
            for layer in w['routes']:
                for t in layer['tokens']:
                    for expert in t['experts']:counts[expert]+=1
        expert_counts[name]=counts.tolist()
        image=ax.imshow(matrix,cmap='YlOrRd',vmin=0,vmax=160);ax.set_title(name+' dispatch payload (KiB)')
        ax.set_xlabel('Destination rank');ax.set_ylabel('Source rank');fig.colorbar(image,ax=ax)
    finish(fig,'03_dispatch_hotspot')

    fig,ax=plt.subplots(figsize=(10,3.6));x=np.arange(16)
    ax.bar(x-.18,expert_counts['clos'],.36,label='balanced');ax.bar(x+.18,expert_counts['hotspot'],.36,label='50% hot tokens')
    ax.set_xticks(x);ax.set_xlabel('Expert ID');ax.set_ylabel('Token-expert contributions (8 layer-steps)');ax.legend();ax.grid(axis='y',alpha=.2)
    finish(fig,'04_expert_distribution')

    regex=re.compile(r'(issue|callback),sys->id=(\d+), tick=(\d+), node->id=(\d+), node->name=(.*?), node->type=\d+, graph_id=([^\s,]+)')
    def node_events(path):
        result={}
        for logfile in sorted((path/'log').glob('log*')):
            for line in logfile.read_text().splitlines():
                match=regex.search(line)
                if match:
                    kind,rank,tick,node,name,graph=match.groups();result[graph,int(rank),int(node),kind]=(int(tick),name)
        return result
    events=node_events(run('clos'));wave=read(run('clos')/'graphs/g0.w0/wave.json')
    fig,ax=plt.subplots(figsize=(11,5));colors={'attention':'#9474b4','pack':'#73a7b0','experts':'#e3a448','merge':'#4e9669'}
    for rank,tasks in wave['rank_nodes'].items():
        rank=int(rank)
        for task in tasks:
            if task['kind']!='COMP':continue
            start,name=events[wave['graph_id'],rank,task['id'],'issue'];end,_=events[wave['graph_id'],rank,task['id'],'callback']
            stage=name.split('.')[-1]
            ax.broken_barh([(start/1000,(end-start)/1000)],(rank-.12,.24),facecolors=colors[stage])
    flow={m['id']:m for m in base['messages']}
    for m in wave['messages']:
        f=flow[m['id']];y=m['src']+(-.32 if m['phase']=='dispatch' else .2)
        ax.broken_barh([(f['ready_ns']/1000,(f['receive_ready_ns']-f['ready_ns'])/1000)],(y,.1),alpha=.25,
                      facecolors='#2979b8' if m['phase']=='dispatch' else '#ba4c62')
    for label,color in colors.items():ax.plot([],[],color=color,lw=6,label=label)
    ax.set_yticks(range(8));ax.set_xlabel('Actual simulation time (us)');ax.set_ylabel('Rank')
    handles,labels=ax.get_legend_handles_labels()
    handles += [Patch(facecolor='#2979b8',alpha=.25),Patch(facecolor='#ba4c62',alpha=.25)]
    labels += ['Dispatch: ready to receive','Combine: ready to receive']
    ax.set_title('First actual wave: compute and ready-to-receive network intervals')
    ax.legend(handles,labels,ncol=3,fontsize=8,loc='upper center',bbox_to_anchor=(.5,-.15))
    ax.grid(axis='x',alpha=.2);finish(fig,'05_astra_network_timeline')

    slow=read(run('core25')/'result.json')
    candidate=max((p for p in slow['ports'] if p['tier']=='agg_core' and by_id[p['node']]['kind']=='agg'),
                  key=lambda p:p['max_queue_at_hook_bytes'])
    key=candidate['node'],candidate['port'];fig,ax=plt.subplots(figsize=(11,3.8))
    for name in ('clos','core50','core25'):
        r=read(run(name)/'result.json');end=read(run(name)/'frontend.json')['history'][0]['finished_ns']
        points=[(0,0)]+[(t/1000,b/1024) for t,n,p,b in r['queue_events'] if (n,p)==key and t<=end]+[(end/1000,0)]
        ax.step([x for x,y in points],[y for x,y in points],where='post',label=name)
    ax.set_xlabel('Actual simulation time (us)');ax.set_ylabel('Egress queue (KiB)');ax.legend()
    ax.set_title(f'Same physical Agg uplink {key}; seed 11, first wave');ax.grid(alpha=.2);finish(fig,'06_uplink_queue')

    mechanisms={}
    baseline_waves=read(run('clos')/'frontend.json')['history']
    for name in ('core50','core25','hotspot','single-plane','dual-plane'):
        r=read(run(name)/'result.json');actual=read(run(name)/'frontend.json')['history']
        mechanisms[name]=dict(finish_ns=r['finish_ns'],
            wave_duration_ns=[w['finished_ns']-w['start_ns'] for w in actual],
            mean_receive_fct_ns=statistics.mean(f['receive_ready_ns']-f['ready_ns'] for f in r['messages']),
            source_payload_bytes=sum(f['bytes'] for f in r['messages']))
        if name in ('core50','core25'):
            cf={m['id']:m for m in r['messages']};bf={m['id']:m for m in base['messages']}
            mechanisms[name]['same_actual_message_paths']=all(
                [(h['node'],h['port']) for h in f['first_packet_path']]==
                [(h['node'],h['port']) for h in cf[i]['first_packet_path']] for i,f in bf.items())
            assert mechanisms[name]['same_actual_message_paths']
            # Keep a concrete predecessor -> consumer example, rather than
            # inferring a network mechanism from end-to-end times alone.
            changed_events=node_events(run(name))
            changed_wave=read(run(name)/'graphs/g0.w0/wave.json')
            assert changed_wave==wave
            examples=[]
            for rank,tasks in wave['rank_nodes'].items():
                rank=int(rank);by_node={t['id']:t for t in tasks}
                for task in tasks:
                    if task['kind']!='COMP' or not task['name'].endswith('.experts'):continue
                    start=changed_events[wave['graph_id'],rank,task['id'],'issue'][0]
                    baseline_start=events[wave['graph_id'],rank,task['id'],'issue'][0]
                    latest=max(task['deps'],key=lambda dep:changed_events[wave['graph_id'],rank,dep,'callback'][0])
                    dep=by_node[latest]
                    if dep['kind']=='RECV' and start>baseline_start:
                        msg=cf[dep['message']['id']]
                        examples.append(dict(rank=rank,consumer=task['name'],baseline_start_ns=baseline_start,
                            changed_start_ns=start,latest_dependency=dep['name'],
                            latest_dependency_callback_ns=changed_events[wave['graph_id'],rank,latest,'callback'][0],
                            message_id=msg['id'],message_ready_ns=msg['ready_ns'],
                            message_receive_ready_ns=msg['receive_ready_ns']))
            assert examples,'No delayed network predecessor evidence in uplink control'
            mechanisms[name]['network_predecessor_examples']=examples
    mechanisms['baseline']=dict(finish_ns=base['finish_ns'],wave_duration_ns=[w['finished_ns']-w['start_ns'] for w in baseline_waves])
    mechanisms['expert_counts']=expert_counts;save(out/'mechanisms.json',mechanisms)
    portable=[{k:v for k,v in row.items() if k!='run'} for row in summary];save(out/'results.json',portable)
    with (out/'results.csv').open('w') as file:
        columns=['condition','route_seed','batch_per_rank','finish_ns','remote_payload_bytes','max_queue_bytes','ecn_ce_events','pfc_pause_events']
        writer=csv.DictWriter(file,fieldnames=columns,extrasaction='ignore');writer.writeheader();writer.writerows(portable)
    incast=read(root/'incast/result.json')
    diagnostics=dict(incast_completed=incast['completed_messages'],
        ce_observations=sum(p['ecn_ce_seen'] for p in incast['ports']),pfc_pause_observations=sum(p['pfc_pause'] for p in incast['ports']),
        feedback_flows=sum(m['dcqcn_feedback_seen'] for m in incast['messages']),
        middle_loss=read(root/'middle-loss/result.json')['messages'][0],tail_loss=read(root/'tail-loss/result.json')['messages'][0])
    save(out/'protocol_evidence.json',diagnostics)
    zero=None
    if args.diagnostics:
        zero=read(args.diagnostics/'summary.json')
        for name in ('balanced','hotspot'):
            assert zero['results'][name]['passed']
        assert zero['expert_cost_ns']==0
        fig,ax=plt.subplots(figsize=(6,3.6))
        ax.bar(['balanced','hotspot'],[zero['results'][n]['finish_ns']/1000 for n in ('balanced','hotspot')],
               color=['#2979b8','#d35400'])
        ax.set_ylabel('Segment completion (us)');ax.set_title('Zero expert cost: network/dependency diagnostic, seed 11')
        ax.grid(axis='y',alpha=.2);finish(fig,'07_zero_expert_diagnostic')
        save(out/'zero_expert_diagnostic.json',zero)
    content=['<!doctype html><html lang="zh"><meta charset="utf-8"><title>Scale-Out 基础实验</title>',
        '<style>body{max-width:1120px;margin:36px auto;font:16px/1.8 sans-serif;color:#1b2733;padding:0 18px}img{width:100%}table{border-collapse:collapse}td,th{padding:6px 12px;border:1px solid #ddd}.note{background:#f0f4f8;padding:14px}</style>',
        '<h1>Scale-Out 基础实验：真实网络驱动的多轮 MoE 服务</h1>',
        '<p>LLMServingSim 实际 Scheduler → Chakra DAG → ASTRA → 唯一原生 ns-3 RDMA → 接收/ACK 回调 → 下一轮组批。</p>',
        '<p class="note">范围：8 个逻辑 GPU，2 个 MoE 层，4 轮 ready-KV Decode；计算为显式参考成本，路由为合成数据。A100 是描述标签。结果不代表完整模型、实机 A100、千卡容量或生产 p99。</p>',
        '<p>10 个主配置 × 3 个配对路由 seed，另有独立 rail 的合法 EP4 验收及非法 EP8 注入前拒绝。主表每个运行均核对逐 token 字节、ASTRA 前驱、回调与下一轮启动。</p>',
        '<table><tr><th>条件</th><th>完成时间均值（μs）</th><th>3 个 seed 范围（μs）</th></tr>']
    for name in order:
        times=[x['finish_ns']/1000 for x in groups[name]]
        content.append(f'<tr><td>{html.escape(name)}</td><td>{means[name]:.3f}</td><td>{min(times):.3f}—{max(times):.3f}</td></tr>')
    content+=['</table>',
        '<p>对照解释：上联降速保持相同请求、计算耗时、远程字节及实际消息路径；双平面固定 NIC 总带宽，因此不预设收益。热点同时改变专家计算和网络分布，不能仅凭耗时增长断言网络拥塞。</p>',
        '<p>EP4 主对照分别位于两个 Pod，结果同时包含副本变化、远程比例和通信本地化，不能归因为纯 EP 收益。跨 Pod 交错 EP4 的共享出口验收另行报告。</p>',
        f'<p>独立汇聚探针：64 条消息完成，{diagnostics["ce_observations"]} 次 CE 观测，{diagnostics["pfc_pause_observations"]} 次 PFC 暂停，{diagnostics["feedback_flows"]} 条流得到 DCQCN 反馈。CE 是跨端口事件，不能当成唯一标记包数。</p>',
        '<p>中间包丢失后可由 NACK 恢复，源 payload 因重传从 4000 B 增至 7000 B；尾包丢失按预期失败，未误发接收完成。首版服务实验限定无故障网络，未实现通用尾包恢复。</p>']
    if zero:
        delta=zero['results']['hotspot']['finish_ns']-zero['results']['balanced']['finish_ns']
        content.append(f'<p>专家成本统一为零的独立诊断中，热点完成时间增加 {delta/1000:.3f} μs。'
                       '这表明该参考条件下网络分布和依赖仍有影响；时间关系已改变，不能从主实验差值中直接相减作为加法分解。</p>')
    for name in figures:
        data=base64.b64encode((out/f'{name}.png').read_bytes()).decode()
        content.append(f'<h2>{html.escape(name)}</h2><img alt="{name}" src="data:image/png;base64,{data}">')
    content.append('<p>图中均为实际执行数据。结构对照的端口资源不同，不据耗时排序判断架构优劣；3 个路由 seed 不支持生产长尾或统计显著性结论。</p></html>')
    (out/'report.html').write_text('\n'.join(content))
    save(out/'manifest.json',dict(source_runs=str(root),figures=figures,
         script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()}))


if __name__=='__main__':main()
