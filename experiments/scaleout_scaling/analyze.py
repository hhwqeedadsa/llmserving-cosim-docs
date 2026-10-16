"""Read completed public outputs; produce finite-window network/scale figures.

No simulation, private platform imports, parameter fitting or event injection.
Run with an existing Matplotlib environment and an explicit host resource cap.
"""
import argparse
from collections import Counter
import csv
import gc
import hashlib
import html
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyBboxPatch


def read(path):
    return json.loads(path.read_text())


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def case(path):
    verification=read(path/'verification.json')
    assert verification['passed']
    config=read(path/'input.json');topology=read(path/'topology.json')
    protocol=read(path/'rdma.json')
    assert (path/'result.json').stat().st_size<=protocol.get('limits',{}).get('result_json_bytes',16777216)
    result=read(path/'result.json');nodes={n['id']:n for n in topology['nodes']}
    assert result['passed'] and result['service']['service_finished']
    assert len(result['messages'])==verification['messages']
    tiers={};finish=result['finish_ns']
    for port in result['ports']:
        tier=tiers.setdefault(port['tier'],dict(ports=0,wire_bytes=0,data_bytes=0,control_bytes=0,
            capacity_bps=0,max_utilization=0.,max_queue_bytes=0,ecn_ce_seen=0,pfc_pause=0))
        tier['ports']+=1
        for a,b in [('wire_bytes','model_tx_bytes'),('data_bytes','data_tx_bytes'),('control_bytes','control_tx_bytes')]:
            tier[a]+=port[b]
        tier['capacity_bps']+=port['rate_bps']
        tier['max_utilization']=max(tier['max_utilization'],port['model_tx_bytes']*8e9/(port['rate_bps']*finish))
        tier['max_queue_bytes']=max(tier['max_queue_bytes'],port['max_queue_at_hook_bytes'])
        tier['ecn_ce_seen']+=port['ecn_ce_seen'];tier['pfc_pause']+=port['pfc_pause']
    for tier in tiers.values():
        tier['mean_utilization']=tier['wire_bytes']*8e9/(tier['capacity_bps']*finish)
    cross_pod=sum(m['bytes'] for m in result['messages'] if nodes[m['src']]['pod']!=nodes[m['dst']]['pod'])
    pod_payload=Counter()
    for message in result['messages']:
        if nodes[message['src']]['pod']!=nodes[message['dst']]['pod']:
            pod_payload[nodes[message['src']]['pod']]+=message['bytes']
    pod_egress=Counter()
    for port in result['ports']:
        if nodes[port['node']]['kind']=='agg' and nodes[port['peer']]['kind']=='core':
            pod_egress[nodes[port['node']]['pod']]+=port['rate_bps']
    cut_bound=max(size*8e9/pod_egress[pod] for pod,size in pod_payload.items())
    cross_host=[m for m in result['messages'] if nodes[m['src']]['server']!=nodes[m['dst']]['server']]
    fct=sorted(m['receive_ready_ns']-m['ready_ns'] for m in cross_host)
    assert fct
    report=dict(path=str(path),gpus=result['gpu_count'],model=config['model']['name'],
        ep_sizes=sorted({len(g) for g in config['parallel']['groups']}),replicas=len(config['parallel']['groups']),
        batch_per_rank=config['workload']['batch_per_rank'],decode_steps=config['workload']['decode_steps'],
        context_tokens=config['workload']['context_tokens'],layers=config['model']['layers'],
        finish_ns=finish,**{k:verification[k] for k in ['messages','verified_nodes','waves','intra_host_payload_bytes','inter_host_payload_bytes']},
        cross_pod_payload_bytes=cross_pod,rdma_fct_p50_ns=fct[(len(fct)-1)//2],
        rdma_fct_p99_ns=fct[int((len(fct)-1)*.99)],tiers=tiers,
        pod_crossing_payload_bytes=dict(pod_payload),pod_outgoing_capacity_bps=dict(pod_egress),
        pod_payload_service_lower_bound_ns=cut_bound,
        resources=read(path/'execution/probe.json'),
        source_result_sha256=hashlib.sha256((path/'result.json').read_bytes()).hexdigest())
    return report


def topology_figure(topology,output,font):
    """Collapse each real server's eight access links into one labelled edge."""
    nodes=topology['nodes'];positions={};label={};kind={}
    pods=sorted({n['pod'] for n in nodes if n['kind']=='gpu'})
    for n in nodes:
        if n['kind']=='gpu':
            key=('server',n['server']);kind[key]='server';label[key]=f"S{n['server']}\n8 GPU"
    levels=[('server',.08),('tor',.36),('agg',.64)]
    for pindex,pod in enumerate(pods):
        for tier,y in levels:
            if tier=='server':ids=sorted({n['server'] for n in nodes if n['kind']=='gpu' and n['pod']==pod})
            else:ids=sorted(n['id'] for n in nodes if n['kind']==tier and n['pod']==pod)
            for index,identity in enumerate(ids):
                key=(tier,identity);positions[key]=((pindex+(index+.5)/len(ids))/len(pods),y)
                kind[key]=tier
                if tier!='server':label[key]=f'{tier.upper()}\n{identity}'
    cores=[n for n in nodes if n['kind']=='core']
    for i,n in enumerate(cores):
        key=('core',n['id']);positions[key]=((i+.5)/len(cores),.91);kind[key]='core';label[key]=f"CORE {n['id']}"
    physical={n['id']:n for n in nodes}
    def key(identity):
        n=physical[identity]
        return ('server',n['server']) if n['kind']=='gpu' else (n['kind'],identity)
    edges={(key(l['src']),key(l['dst'])) for l in topology['links']}
    fig,ax=plt.subplots(figsize=(13,6.5))
    for a,b in sorted(edges):
        x1,y1=positions[a];x2,y2=positions[b]
        ax.plot([x1,x2],[y1,y2],color='#94a3b8',lw=.8,zorder=1)
    colors=dict(server='#dbeafe',tor='#c7e9df',agg='#fde6b9',core='#dcd5f4')
    for k,(x,y) in positions.items():
        width=.043 if kind[k]=='server' else .069
        ax.add_patch(FancyBboxPatch((x-width/2,y-.035),width,.07,boxstyle='round,pad=.003',
            facecolor=colors[kind[k]],edgecolor='#52677b',lw=.8,zorder=2))
        ax.text(x,y,label[k],ha='center',va='center',fontsize=7.8,zorder=3)
    ax.set(xlim=(-.01,1.01),ylim=(-.045,1.02));ax.axis('off')
    ax.set_title('128 卡实际三层 Clos（每服务器折叠显示 8 条 GPU 接入）',fontproperties=font,fontsize=14)
    ax.text(.5,-.015,'普通电交换 Core；外部每链路 400 Gb/s / 1 μs；机内每 GPU 每方向 450 GB/s 的声明模型',
        ha='center',fontproperties=font,fontsize=10)
    fig.tight_layout()
    for ext in ('png','pdf'):fig.savefig(output/f'01_topology.{ext}',dpi=160,bbox_inches='tight')
    plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',nargs='+',type=Path,required=True)
    parser.add_argument('--topology-run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(exist_ok=False)
    font=FontProperties(fname='/usr/share/fonts/truetype/wqy/wqy-microhei.ttc')
    plt.rcParams.update({'pdf.fonttype':42,'axes.spines.top':False,'axes.spines.right':False})
    reports=[]
    for path in args.cases:
        reports.append(case(path.resolve()));gc.collect()
    save(out/'results.json',reports)
    columns=['gpus','ep_sizes','replicas','batch_per_rank','layers','decode_steps','finish_ns','messages',
        'intra_host_payload_bytes','inter_host_payload_bytes','cross_pod_payload_bytes','rdma_fct_p99_ns']
    with (out/'results.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,columns,lineterminator='\n');writer.writeheader()
        for r in reports:writer.writerow({k:r[k] for k in columns})
    topology_figure(read(args.topology_run/'topology.json'),out,font)
    labels=[f"{r['gpus']} GPU\nEP{','.join(map(str,r['ep_sizes']))} x {r['replicas']}" for r in reports]
    x=list(range(len(reports)))
    fig,ax=plt.subplots(figsize=(10,5));bottom=[0.]*len(reports)
    for field,title,color in [('intra_host_payload_bytes','Intra-host','#3276ac'),
                             ('inter_host_payload_bytes','Inter-host','#e59c3a')]:
        values=[r[field]/2**20 for r in reports];ax.bar(x,values,bottom=bottom,label=title,color=color)
        bottom=[a+b for a,b in zip(bottom,values)]
    ax.set_xticks(x,labels);ax.set_ylabel('Payload (MiB), 48 layers / 2 steps');ax.legend()
    ax.set_title('机内与跨机载荷（每卡 batch=1，规模同时改变全局负载）',fontproperties=font)
    fig.tight_layout()
    for ext in ('png','pdf'):fig.savefig(out/f'02_payload.{ext}',dpi=160)
    plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(12,4.8))
    axes[0].bar(x,[r['finish_ns']/1e6 for r in reports],color='#3276ac')
    axes[0].plot(x,[r['pod_payload_service_lower_bound_ns']/1e6 for r in reports],
        marker='o',color='#e59c3a',label='Pod payload service lower bound')
    axes[0].legend(fontsize=8)
    axes[0].set_ylabel('Two-step finish (ms)')
    axes[1].bar(x,[r['rdma_fct_p99_ns']/1000 for r in reports],color='#6c9f6d')
    axes[1].set_ylabel('Inter-host message FCT p99 (us)')
    for ax in axes:ax.set_xticks(x,labels,fontsize=8)
    fig.suptitle('有限窗口完成与消息延迟；不同 EP/拓扑条件不构成强扩展对照',fontproperties=font)
    fig.tight_layout()
    for ext in ('png','pdf'):fig.savefig(out/f'03_completion.{ext}',dpi=160)
    plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(12,4.8))
    axes[0].bar(x,[r['resources']['peak_sampled_group_rss_bytes']/2**30 for r in reports],color='#9c82b8')
    axes[0].set_ylabel('Sampled worker tree RSS peak (GiB)')
    axes[1].bar(x,[r['resources']['wall_seconds'] for r in reports],color='#839fba')
    axes[1].set_ylabel('Host worker wall time (s)')
    for ax in axes:ax.set_xticks(x,labels,fontsize=8)
    fig.suptitle('宿主资源与仿真时间分开报告；不含独立后验校验进程',fontproperties=font)
    fig.tight_layout()
    for ext in ('png','pdf'):fig.savefig(out/f'04_resources.{ext}',dpi=160)
    plt.close(fig)
    body=['<!doctype html><meta charset="utf-8"><title>MoE Clos scale results</title>',
        '<style>body{max-width:1100px;margin:32px auto;font:16px/1.7 sans-serif;color:#203247}img{width:100%}table{border-collapse:collapse}th,td{padding:8px;border-bottom:1px solid #ddd}</style>',
        '<h1>MoE 三层 Clos：有限窗口执行结果</h1>',
        '<p>Qwen3-30B-A3B 完整 48 层图、两轮 ready-KV Decode、BF16 尺寸；计算与路由为声明参考输入，未做 H100/NVLink 硬件校准。EP4 多副本扩规模与 EP=N 的百卡基线分别解释。消息 p99 为本次有限窗口的描述统计。</p>',
        '<table><tr><th>GPU</th><th>EP / 副本</th><th>逻辑消息</th><th>完成 / ms</th><th>跨 Pod / MiB</th></tr>']
    for r in reports:
        body.append(f"<tr><td>{r['gpus']}</td><td>{r['ep_sizes']} / {r['replicas']}</td><td>{r['messages']:,}</td><td>{r['finish_ns']/1e6:.3f}</td><td>{r['cross_pod_payload_bytes']/2**20:.2f}</td></tr>")
    body.append('</table><p>分层线上字节、端口队列钩子峰值和利用率在 results.json 的 tiers；wire bytes 不含未建模的 Ethernet preamble/FCS/IFG。Pod 下界按每个 Pod 需穿越出口的 payload / 该 Pod 的单方向 Agg→Core 总容量计算，取最大值，排除报头、ACK、传播及计算，因此只是必要下界。summary 队列没有完整时序，不能单凭峰值完成因果归因。</p>')
    for figure in ['01_topology','02_payload','03_completion','04_resources']:
        body.append(f'<img src="{figure}.png" alt="{figure}">')
    (out/'report.html').write_text('\n'.join(body))
    save(out/'manifest.json',dict(script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        source_results={r['path']:r['source_result_sha256'] for r in reports},
        files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()},
        simulation_executed=False))
    print('Analyzed',len(reports),'completed cases')


if __name__=='__main__':main()
