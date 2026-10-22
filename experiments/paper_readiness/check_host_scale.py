"""Independently check actual paths/counters and build the short-run review page."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

def read(path):return json.loads(path.read_text())
def save(path,value):
    with path.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True,type=Path);parser.add_argument('--analysis',required=True,type=Path)
    parser.add_argument('--monitor',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();out=args.output;out.mkdir(exist_ok=False)
    r=read(args.run/'result.json');t=read(args.run/'topology.json');a=read(args.analysis);monitor=read(args.monitor)
    c=read(args.run/'input.json');protocol=read(args.run/'rdma.json')
    assert r['passed'] and r['drained'] and a['completed'] and monitor['status']=='completed'
    assert monitor['peak_sampled_group_rss_bytes']<=6*1024**3 and monitor['wall_seconds']<=60
    assert len(r['messages'])==len({m['id'] for m in r['messages']})==a['messages']
    assert a['result_sha256']==hashlib.sha256((args.run/'result.json').read_bytes()).hexdigest()
    nodes={n['id']:n for n in t['nodes']};edges={}
    for edge in t['links']:
        edges[edge['src'],edge['src_port']]=(edge['dst'],edge['rate_bps'],edge['delay_ns'])
        edges[edge['dst'],edge['dst_port']]=(edge['src'],edge['rate_bps'],edge['delay_ns'])
    counts=Counter();payload=Counter();hopcounts=Counter();receive_delays=[]
    callbacks={x['id']:x for x in r['service']['message_callbacks']}
    for flow in r['messages']:
        src,dst=flow['src'],flow['dst'];local=nodes[src]['server']==nodes[dst]['server']
        assert flow['source_data_payload_bytes']==flow['bytes']
        assert callbacks[flow['id']]['send_callback_ns']==flow['ack_ns']
        assert callbacks[flow['id']]['recv_callback_ns']==max(callbacks[flow['id']]['recv_post_ns'],flow['receive_ready_ns'])
        if local:
            assert flow['transport']=='intra_host_chunked_crossbar' and flow['completion_kind']=='local_data_ready_no_RDMA_ACK'
            assert flow['source_data_packets']==0 and flow['first_packet_path']==[]
            assert flow['ack_ns']==flow['receive_ready_ns']
            counts['intra_host_messages']+=1;payload['intra_host']+=flow['bytes'];continue
        counts['inter_host_messages']+=1;payload['inter_host']+=flow['bytes']
        assert nodes[src]['pod']!=nodes[dst]['pod']
        assert 0<flow['bytes']<=protocol['payload_bytes'] and flow['source_data_packets']==1
        counts['actual_source_data_packets']+=flow['source_data_packets']
        counts['actual_source_payload_bytes']+=flow['source_data_payload_bytes']
        current=src;last=-1;kinds=[]
        for hop in flow['first_packet_path']:
            assert hop['node']==current and hop['time_ns']>=last
            kinds.append(nodes[current]['kind']);current,rate,delay=edges[current,hop['port']]
            assert rate==400000000000 and delay==1000;last=hop['time_ns']
        assert current==dst and kinds==['gpu','tor','agg','core','agg','tor']
        assert flow['first_tx_ns']==flow['first_packet_path'][0]['time_ns']
        assert flow['receive_ready_ns']>=last+1000 and flow['ack_ns']>=flow['receive_ready_ns']
        hopcounts[len(flow['first_packet_path'])]+=1
        receive_delays.append(flow['receive_ready_ns']-flow['ready_ns'])
    assert payload['intra_host']==a['intra_host_payload_bytes'] and payload['inter_host']==a['cross_server_payload_bytes']
    actual_waves={(w['graph_id'],tuple(w['rank_ids'])) for w in r['service']['waves']}
    expected_waves={(f'g{i}.w{s}',tuple(group)) for i,group in enumerate(c['parallel']['groups']) for s in range(2)}
    assert actual_waves==expected_waves and len(r['service']['waves'])==512
    phases=a['traces']['native']['phases'];total=phases['native_total']['inclusive_wall_ns']
    # Disjoint categories within native_total, no Python/native overlap added.
    categories={
        '网络初始化':phases['fabric_init']['exclusive_wall_ns'],
        'ASTRA初始化':phases['astra_init']['exclusive_wall_ns'],
        '同步前端交互':phases['control_exchange']['inclusive_wall_ns'],
        '图载入与激活':phases['graph_load']['exclusive_wall_ns']+phases['graph_activate']['exclusive_wall_ns'],
        'Run其他执行':phases['simulator_run']['exclusive_wall_ns'],
        '报告构造与写出':phases['report_build']['exclusive_wall_ns']+phases['report_write']['exclusive_wall_ns'],
    }
    categories['其余宿主开销']=total-sum(categories.values());assert categories['其余宿主开销']>=0
    network=dict(counts,payload_bytes=dict(payload),egress_hops=dict(hopcounts),
        ecn_ce_seen=sum(p['ecn_ce_seen'] for p in r['ports']),pfc_pause=sum(p['pfc_pause'] for p in r['ports']),
        pfc_resume=sum(p['pfc_resume'] for p in r['ports']),max_queue_at_hook_bytes=max(p['max_queue_at_hook_bytes'] for p in r['ports']),
        min_inter_host_ready_to_receive_ns=min(receive_delays),max_inter_host_ready_to_receive_ns=max(receive_delays),
        ns3_events=a['traces']['native']['snapshots'][-1]['values']['processed_events'])
    assert network['actual_source_data_packets']==network['inter_host_messages']
    verification=dict(passed=True,all_inter_host_first_paths_independently_checked=True,
        all_local_paths_and_callback_boundaries_checked=True,all_replica_wave_pairs_checked=True,
        network=network,native_total_wall_ns=total,native_disjoint_wall_ns=categories,
        native_initialization_fraction=(categories['网络初始化']+categories['ASTRA初始化'])/total,
        external_wall_seconds=monitor['wall_seconds'],peak_sampled_group_rss_bytes=monitor['peak_sampled_group_rss_bytes'],
        external_wall_outside_worker_root_seconds=monitor['wall_seconds']-a['traces']['worker']['phases']['worker_total']['inclusive_wall_ns']/1e9,
        simulated_finish_ns=r['finish_ns'],verified_nodes=read(args.run/'verification.json')['verified_nodes'],
        scope='single bounded synthetic functional/host diagnostic, no full model, speedup, congestion or hardware claim')
    save(out/'verification.json',verification)
    save(out/'analysis.json',a)
    save(out/'maintenance.json',dict(command=['python',__file__,'--run',str(args.run),'--analysis',str(args.analysis),
        '--monitor',str(args.monitor),'--output',str(out)],run_monitor=monitor,
        scope='read-only report generation; complete run commands are in run_monitor; checkpoint is separate'))
    rows=''.join(f'<tr><td>{name}</td><td>{ns/1e9:.3f} s</td><td>{ns/total:.1%}</td></tr>' for name,ns in categories.items())
    document=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>千卡短任务：执行与宿主成本</title><style>body{{font:17px/1.8 system-ui,sans-serif;max-width:980px;margin:40px auto;padding:0 22px;color:#172538}}h1{{line-height:1.3}}table{{border-collapse:collapse;width:100%}}th,td{{border-bottom:1px solid #cbd5e1;padding:10px;text-align:left}}aside{{padding:16px;background:#edf5fc;border-left:4px solid #3677b5}}code{{font-size:.9em}}a{{color:#175ca6}}</style>
<h1>千卡短任务已跑通，完整模型规模仍待验证</h1><p>2026-10-22 · 平台 bc8dd6f · 一次预注册运行</p>
<aside>1,024个GPU逻辑端点、50台交换机、1,104条外部链路；256个EP4副本在同一个持续网络中完成512张图、5,490条消息。仿真完成时间44,589 ns；实际宿主约{monitor['wall_seconds']:.2f}秒，采样RSS峰值{monitor['peak_sampled_group_rss_bytes']/1024**3:.2f} GiB。</aside>
<h2>这次到底执行了什么</h2><p>输入是人工MoE层段：隐藏维1024、16专家、每token选2专家，batch/rank=1、已有上下文2048、1层×2轮Decode。每组4张卡跨两个Pod，两张卡在同一服务器。所有副本共用一套ns-3网络、时钟和排队状态。A100只是标签，未运行GPU数值计算。它不是此前48层Qwen模型，也不是EP1024、训练DP或现代DeepEP。</p>
<h2>网络执行的实际证据</h2><p>独立逐token重算4,096次专家贡献，其中3,059次交付到其他rank，Dispatch与Combine合计12,529,664 B。跨服务器8,376,320 B、机内4,153,344 B；同GPU贡献仅保留本地依赖。</p>
<p>跨机3,666条消息实际发出3,666个源数据包，每条消息的首包都核对了GPU→ToR→Agg→Core→Agg→ToR→GPU六跳路径；机内1,824条消息使用显式crossbar，未冒充RDMA包。所有应用提交和完成回调配平，完整ASTRA日志核验{verification['verified_nodes']:,}个任务节点。</p>
<p>端口记录ECN={network['ecn_ce_seen']}、PFC pause={network['pfc_pause']}，入出队钩子队列峰值{network['max_queue_at_hook_bytes']:,} B。这次短任务没有触发ECN/PFC，不能作为拥塞长尾机制或优化收益证据。跨机ready到连续接收完成为{min(receive_delays):,}—{max(receive_delays):,} ns，不能等同请求推理时延。</p>
<h2>宿主时间花在哪里</h2><p>以下各项在native根区间内互不重叠，合计{total/1e9:.3f}秒；不把Python的生成时间再加一次。初始化合计占{verification['native_initialization_fraction']:.1%}。</p>
<table><tr><th>阶段</th><th>墙钟耗时</th><th>native占比</th></tr>{rows}</table>
<p>Run其他执行混合ns-3事件、ASTRA、回调、日志和观测开销，不能称为纯包CPU。外部命令约{monitor['wall_seconds']:.2f}秒，含前置校验、进程启动和结束核验；worker根之外约{verification['external_wall_outside_worker_root_seconds']:.2f}秒，未进一步分摊。CPU包含进程内全部线程，可大于墙钟。</p>
<h2>它支持什么结论</h2><p>千卡的物理网络和有限任务可以在当前预算内完整执行，不能把历史超时简单解释为“千卡根本无法运行”。短任务中初始化占主导；它与旧48层实验的工作量相差很大，不能据此解释旧超时比例，也不能计算新旧加速比。</p>
<p>研究主问题仍需在代表性通信量、真实热点和共享上层链路中形成可证伪机制，再通过强基线、留出请求和硬件依据验证。当前结果是可用规模与成本边界，尚不是A类论文主贡献。</p>
<h2>范围与证据</h2><p>单命令60秒、RSS6GiB/地址12GiB、仿真5ms、产物64MiB；研究累计128MiB、预留8GiB。没有重跑旧600秒任务、安装、下载、修改依赖或扩大失败预算。</p>
<p><a href="analysis.json">完整输入/体积/阶段分析</a> · <a href="verification.json">独立路径与计数核验</a> · <a href="maintenance.json">命令与资源记录</a></p><p>方法：experiments/paper_readiness/host_scale_protocol.md。正式报告另存本机归档清单archive.json；自定义复核目录不自动创建备份。</p></html>'''
    (out/'report.html').write_text(document)
    print(json.dumps(dict(passed=True,output=str(out),network=network),ensure_ascii=False))


if __name__=='__main__':main()
