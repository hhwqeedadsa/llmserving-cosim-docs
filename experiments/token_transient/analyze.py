"""Read executed snapshots: payload conservation, per-phase waits and packet paths."""
import argparse
from collections import Counter, defaultdict, deque
import csv
import html
import json
import os
from pathlib import Path
import statistics

from common import load, need, save, sha, originals, account
from moe_ub_cosim.scaleout_arrivals import reconstruct
from moe_ub_cosim.scaleout_data_loss import audit as loss_audit
from moe_ub_cosim.scaleout_verify import node_events, event_time
from moe_ub_cosim.service_terminal import service_terminal


def table(path, rows):
    need(bool(rows), 'empty output table')
    with path.open('x',newline='') as stream:
        writer = csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def stages(directory, waves, result):
    events = node_events(directory)
    gates = {(g['graph_id'],g['id']):g for g in result['service']['phase_completion']['gates']}
    messages = {m['id']:m for m in result['service_transport']['messages']}
    phases, members = [], []
    for wave in waves:
        gid = wave['graph_id']
        for gate in wave['phase_completion']['gates']:
            observed = gates[gid,gate['id']]
            arrivals, production, waits, issue_delays = [], [], [], []
            for member in gate['members']:
                rank = member['rank']
                tasks = {n['id']:n for n in wave['rank_nodes'][str(rank)]}
                task = tasks[member['node']]
                clock = lambda node,kind:event_time(events,gid,rank,node,kind)
                ready = max(clock(d,'callback') for d in task['deps'])
                begin, finish = clock(task['id'],'issue'),clock(task['id'],'callback')
                need(ready <= begin <= finish == observed['released_ns'], 'gate/producer ordering contradicted')
                need(dict(member,arrived_ns=begin) in observed['members'], 'gate member arrival contradicted')
                producer_name = f'L{gate["layer"]}.'+('pack' if gate['phase']=='dispatch' else 'combine_reduce')
                producer = next(n for n in tasks.values() if n['name']==producer_name)
                production.append(clock(producer['id'],'callback'))
                arrivals.append(begin)
                waits.append(finish-begin)
                issue_delays.append(begin-ready)
                members.append(dict(graph_id=gid,group=wave['group_index'],layer=gate['layer'],phase=gate['phase'],
                    rank=rank,predecessors_complete_ns=ready,gate_arrived_ns=begin,released_ns=finish,
                    local_issue_delay_ns=begin-ready,group_wait_ns=finish-begin))
            selected = [messages[m['id']] for m in wave['messages']
                        if m['layer']==gate['layer'] and m['phase']==gate['phase']]
            last_ack = max(m['ack_ns'] for m in selected)
            last_receive = max(m['receive_ready_ns'] for m in selected)
            critical = [m for m in selected if m['ack_ns']==last_ack]
            release = observed['released_ns']
            need(release == max(arrivals) and last_receive <= last_ack <= release, 'stage transmission completion contradicted')
            phases.append(dict(graph_id=gid,group=wave['group_index'],layer=gate['layer'],phase=gate['phase'],
                last_receive_ns=last_receive,last_source_ack_ns=last_ack,last_producer_ns=max(production),
                released_ns=release,post_last_source_ack_ns=release-last_ack,
                positive_group_wait_ranks=sum(w>0 for w in waits),max_group_wait_ns=max(waits),
                median_group_wait_ns=statistics.median(waits),max_local_issue_delay_ns=max(issue_delays),
                last_ack_message_ids=[m['id'] for m in critical]))
    need(len(phases)==len(gates)==16 and len(members)==1024, 'wrong stage/member count')
    return phases,members


def packets(result, config, phases):
    native = result['service_transport']['native']
    streams = {s['id']:s for s in native['streams']}
    groups = config['parallel']['groups']
    owner = {sid:next(gi for gi,g in enumerate(groups) if s['src'] in g) for sid,s in streams.items()}
    need(all(s['dst'] in groups[owner[sid]] for sid,s in streams.items()), 'unexpected cross-group stream')
    tx_count,tx_bytes,ce,drops,residence = (Counter() for _ in range(5))
    common, pending = defaultdict(Counter),defaultdict(deque)
    data_arrivals = all_drops = 0
    critical_ids = next(p['last_ack_message_ids'] for p in phases
                        if p['graph_id']=='g0.w0' and p['layer']==0 and p['phase']=='dispatch')
    descriptors = {m['id']:m for m in result['service_transport']['messages']}
    critical = {mid:dict(message=descriptors[mid],source_packets=[],endpoint_packets=[],recovery_requests=[])
                for mid in critical_ids}
    for row in result['packet_capture']['events']:
        p = dict(zip(result['packet_capture']['columns'],row))
        sid,node = p['message_id'],p['node']
        if p['ip_protocol']!=17 or sid not in streams:
            continue
        s,gi = streams[sid],owner[sid]
        if node == s['src'] and p['event']==0:
            tx_count[sid] += 1
            tx_bytes[sid] += p['data_payload_bytes']
        if node == s['dst'] and p['event'] in (1,2):
            data_arrivals += 1
            all_drops += p['event']==2
            if p['event']==2:
                drops[gi] += 1
            if p['event']==1 and p['ecn']==3:
                ce[gi] += 1
        for description in critical.values():
            m = description['message']
            if sid==m['stream'] and m['begin']<=p['sequence']<m['end']:
                if node==s['src'] and p['event']==0:
                    description['source_packets'].append(p)
                if node==s['dst'] and p['event'] in (1,2):
                    description['endpoint_packets'].append(p)
        if node < 128:
            continue
        key = node,p['packet_uid'],sid,p['sequence'],p['data_payload_bytes'],p['model_bytes']
        if p['event']==1:
            pending[key].append(p['time_ns'])
        elif p['event']==0:
            need(bool(pending[key]), 'switch TX without prior RX occurrence')
            wait = p['time_ns']-pending[key].popleft()
            need(wait>=0,'negative per-hop residence')
            residence[gi] += wait
            common[node,p['port']][gi] += p['model_bytes']
    need(not any(pending.values()), 'unmatched switch packet at drain')
    for sid,s in streams.items():
        need((tx_count[sid],tx_bytes[sid])==(s['source_data_packets'],s['source_payload_bytes']), 'source counters differ')
    recovery = native['recovery']
    for row in recovery['events']:
        event = dict(zip(recovery['columns'],row))
        for description in critical.values():
            m = description['message']
            if event['message_id']==m['stream'] and m['ready_ns']<=event['time_ns']<=m['ack_ns']:
                description['recovery_requests'].append(event)
    for description in critical.values():
        source = description['source_packets']
        after = [p['time_ns'] for p in source if p['time_ns']>=100000]
        description['first_source_tx_after_fault_end_ns'] = min(after) if after else None
        description['fault_end_to_next_source_tx_ns'] = min(after)-100000 if after else None
        retries = [r for r in description['recovery_requests'] if r['event']==recovery['event_codes']['timeout_retransmit']]
        description['timeout_recovery_requests'] = len(retries)
        description['timeout_requests_with_same_time_source_tx'] = sum(any(p['time_ns']==r['time_ns'] for p in source) for r in retries)
    shared = [dict(node=n,port=p,group0_data_model_bytes=by_group[0],group1_data_model_bytes=by_group[1])
              for (n,p),by_group in sorted(common.items()) if by_group[0] and by_group[1]]
    need(shared and not drops[1], 'no shared exits or healthy group directly lost data')
    return dict(source_data_packets=sum(tx_count.values()),source_payload_bytes=sum(tx_bytes.values()),
        group_direct_drops=[drops[0],drops[1]],group_destination_ce_packets=[ce[0],ce[1]],
        group_switch_residence_sum_ns=[residence[0],residence[1]],shared_directed_exits=shared,
        destination_data_arrivals=data_arrivals,destination_data_drops=all_drops,
        all_cluster_destination_observed_loss_fraction=all_drops/data_arrivals,
        critical_first_dispatch_messages=list(critical.values()),
        scope='offline packet lineage and switch RX-to-TX residence; cumulative work is not task latency')


def case(directory, prepared, output, name):
    result,front,c,p = (load(directory/f) for f in ('result.json','frontend.json','input.json','rdma.json'))
    timing = service_terminal(result,front,require_complete=True)
    need(result['gpu_count']==128 and c['parallel']['groups']==[list(range(0,128,2)),list(range(1,128,2))], 'wrong scale/deployment')
    waves = [load(path) for path in sorted((directory/'graphs').glob('*/wave.json'))]
    need(len(waves)==4 and all(w==load(prepared/'planned'/f'{w["graph_id"]}.json') for w in waves), 'changed executed graph')
    raw = originals(c,directory)
    rows = [account(c,w,raw) for w in waves]
    independent = {k:sum(r[k] for r in rows) for k in rows[0]}
    audit,messages,endpoints,_,_ = reconstruct(result,waves,'g0.w0',0,'dispatch')
    need(independent['layout_bytes']==audit['generated_graph_logical_bytes']==audit['accepted_bytes'], 'source/layout/receiver differ')
    table(output/f'{name}-messages.csv',messages)
    table(output/f'{name}-endpoints.csv',endpoints)
    phases,members = stages(directory,waves,result)
    table(output/f'{name}-phase-members.csv',members)
    table(output/f'{name}-phases.csv',phases)
    packet = packets(result,c,phases)
    table(output/f'{name}-shared-exits.csv',packet['shared_directed_exits'])
    need(packet['source_payload_bytes']==audit['source_tx_payload_bytes'], 'source packet bytes differ from reconstruction')
    need(result['switch_drop_capture']['complete'] and not result['switch_drop_capture']['omitted_events'], 'truncated switch drop capture')
    if name=='local':
        target = load(prepared/'preparation.json')['hotspot_rank']
        need(p.get('data_loss')==dict(kind='periodic_destination_data/1',numerator=1,denominator=1,
             offset=0,start_ns=0,end_ns=100000,nodes=[target]), 'changed fault')
        need(loss_audit(result)['dropped_packets']>0,'fault missed')
    else:
        need('data_loss' not in p and result['fault_drops']==0,'unexpected fault')
    curves = {}
    for gi in (0,1):
        for label,step,layer,phase in (('first-dispatch',0,0,'dispatch'),('last-combine',1,1,'combine')):
            report,_,_,_,curve = reconstruct(result,waves,f'g{gi}.w{step}',layer,phase)
            key = f'g{gi}-{label}'
            curves[key] = curve
            table(output/f'{name}-{key}.csv',curve)
    groups = []
    for gi in (0,1):
        history = sorted((h for h in front['history'] if h['group_index']==gi),key=lambda h:h['step'])
        groups.append(dict(group=gi,finished_ns=max(h['finished_ns'] for h in history),
            graphs=[dict(graph_id=h['graph_id'],start_ns=h['start_ns'],finished_ns=h['finished_ns'],
                         elapsed_ns=h['finished_ns']-h['start_ns']) for h in history]))
    return dict(timing=timing,groups=groups,accounting=independent,packet=packet,phases=phases,
        data_loss=loss_audit(result),source_extra_payload_bytes=packet['source_payload_bytes']-independent['layout_bytes'],
        pfc_pause_events=sum(port['pfc_pause'] for port in result['ports']),
        switch_drop_reasons=result['switch_drop_capture']['all_switch_reason_counts'],
        packet_events=len(result['packet_capture']['events']),result_sha256=sha(directory/'result.json')),curves


def analyze(root, output):
    output.mkdir(parents=True,exist_ok=False)
    prepared = root/'prepared'
    preparation = load(prepared/'preparation.json')
    for name,digest in preparation['input_hashes'].items():
        need(sha(prepared/name)==digest,'changed frozen input '+name)
    cases,curves = {},{}
    for name in ('normal','local'):
        need(load(root/'attempts'/f'{name}.json')['status']=='verified','unverified execution')
        cases[name],curves[name] = case(root/name,prepared,output,name)
    clean,fault = cases['normal'],cases['local']
    contrasts = []
    for a,b in zip(clean['phases'],fault['phases']):
        need((a['graph_id'],a['layer'],a['phase'])==(b['graph_id'],b['layer'],b['phase']), 'stage mismatch')
        contrasts.append(dict(graph_id=a['graph_id'],layer=a['layer'],phase=a['phase'],
            release_extra_ns=b['released_ns']-a['released_ns'],
            last_source_ack_extra_ns=b['last_source_ack_ns']-a['last_source_ack_ns'],
            median_group_wait_extra_ns=b['median_group_wait_ns']-a['median_group_wait_ns']))
    first = next(r for r in contrasts if r['graph_id']=='g0.w0' and r['layer']==0 and r['phase']=='dispatch')
    group_changes = [fault['groups'][i]['finished_ns']-clean['groups'][i]['finished_ns'] for i in (0,1)]
    contrast = dict(application_extra_ns=fault['timing']['application_finished_ns']-clean['timing']['application_finished_ns'],
        group_extra_ns=group_changes,first_dispatch_gate_extra_ns=first['release_extra_ns'],
        fault_group_change_after_first_dispatch_ns=group_changes[0]-first['release_extra_ns'],phases=contrasts,
        extra_source_payload_bytes=fault['packet']['source_payload_bytes']-clean['packet']['source_payload_bytes'])
    report = dict(passed=True,preparation_sha256=sha(prepared/'preparation.json'),cases=cases,contrast=contrast,
        measured_novel_gain=False,hardware_calibrated=False,complete_DeepEP=False,
        interpretation='fault costs and actual dependencies, not removable-headroom or calibrated performance claims')
    save(output/'analysis.json',report)
    table(output/'phase-contrast.csv',contrasts)
    os.environ.setdefault('MPLCONFIGDIR',str(output/'.matplotlib'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes = plt.subplots(2,2,figsize=(12,7),constrained_layout=True)
    for gi in (0,1):
        for col,leg in enumerate(('first-dispatch','last-combine')):
            ax = axes[gi,col]
            for name,color in (('normal','#2463b7'),('local','#d36b22')):
                curve = curves[name][f'g{gi}-{leg}']
                for field,style in (('accepted_bytes','-'),('source_confirmed_bytes','--')):
                    ax.step([r['time_ns']/1000 for r in curve],[r[field]/2**20 for r in curve],
                            where='post',color=color,linestyle=style,label=f'{name} {field}')
            ax.set(title=f'Group {gi}: {leg}',xlabel='Simulated time (us)',ylabel='Cumulative layout payload (MiB)')
            ax.grid(alpha=.2)
            ax.legend(fontsize=7)
    fig.savefig(output/'arrival-curves.png',dpi=170)
    plt.close(fig)
    rows = ''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in (
        name,*[f'{t/1000:.3f}' for t in (c['timing']['application_finished_ns'],c['groups'][0]['finished_ns'],
        c['groups'][1]['finished_ns'],c['timing']['network_drained_ns'])],c['source_extra_payload_bytes'],
        c['data_loss'].get('dropped_packets',0)))+'</tr>' for name,c in cases.items())
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>128卡：去重与卡内合并后的局部故障</title><style>body{{max-width:1120px;margin:40px auto;padding:0 20px;font:17px/1.8 sans-serif;color:#18263b}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccd5df;padding:10px}}img{{max-width:100%}}.note{{padding:14px;background:#fff3d8}}a{{color:#2463b7}}</style>
<h1>128卡：去重与卡内合并后的局部故障</h1><p>15份不同真实R1-AWQ/MMLU专家选择，两个EP64组、8/7请求。
128模拟端点，16服务器×8卡，8 ToR/8 Agg/4 Core，各层400G、每链路1us。每rank每层4专家，两层两步Decode；稀疏请求，不是全卡满载。</p>
<p>两条件均按目的卡去重并在卡内合并，专家工作保留，每份额外贡献声明100ns合并成本。normal无故障；local只在GPU{preparation['hotspot_rank']}前100us丢弃目的数据，保留控制包。均开启相同SR与参考DCQCN。</p>
<table><tr><th>条件</th><th>有限任务/μs</th><th>组0/μs</th><th>组1/μs</th><th>网络排空/μs</th><th>额外源载荷/B</th><th>注入丢包</th></tr>{rows}</table>
<p>整体变化{contrast['application_extra_ns']/1000:+.3f}μs；未直接丢包的组1变化{group_changes[1]/1000:+.3f}μs。
组0首Dispatch门限变化{first['release_extra_ns']/1000:+.3f}μs，其后到组0任务结束的时间差变化{contrast['fault_group_change_after_first_dispatch_ns']/1000:+.3f}μs。</p>
<p class="note">这里测得的是故障代价。门限等待、各rank累计等待和逐跳停留不能相加当任务损失；也不能直接认定可通过新算法消除。</p>
<h2>累计接受与源确认</h2><p>实线为协议接受，虚线为源确认；蓝色正常、橙色局部故障。载荷包含元数据/填充，不全是激活张量，也不是线上总字节。</p>
<img src="arrival-curves.png" alt="Cumulative accepted and source-confirmed layout payload">
<h2>如何追踪等待</h2><p>阶段表保存每个门限的最后接收、源ACK、生产计算完成和释放时刻；成员表保留1024个实际前驱/到达/释放。
完整JSON保留首Dispatch全部并列最后确认消息、实际源TX、丢弃/到达及恢复请求。它们是事后评价资料，不参与在线算法或调度。</p>
<p class="note">真实的是专家选择；计算/到达/部署及H100标签均为声明。没有运行完整R1、GPU或Prefill。
零协调成本门限和direct BF16投影不包含完整GIN控制信号、真实CQ/GPU可见性或hybrid，不能据此宣称生产性能。</p>
<p><a href="analysis.json">完整核验</a> · <a href="phase-contrast.csv">阶段对比</a> · <a href="local-phase-members.csv">故障门限成员</a>
 · <a href="normal-messages.csv">正常消息</a> · <a href="local-messages.csv">故障消息</a></p></html>'''
    (output/'report.html').write_text(page+'\n')
    print(json.dumps(dict(passed=True,contrast=contrast)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    analyze(args.root.resolve(),args.output.resolve())
