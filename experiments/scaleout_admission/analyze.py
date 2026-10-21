"""Offline only: independent resource/queue arithmetic and a self-contained review page."""
import argparse
from collections import Counter, defaultdict, deque
import html
import json
from pathlib import Path
import re

def read(p):return json.loads(p.read_text())
def save(p,v):
    # Keep each numeric event row on one line for reviewable, lossless diffs.
    text=json.dumps(v,indent=2,ensure_ascii=False)
    text=re.sub(r'\[\s*-?\d+(?:\s*,\s*-?\d+)*\s*\]',
                lambda m:json.dumps(json.loads(m.group()),separators=(',',':')),text)
    p.write_text(text+'\n')

def queue_metrics(result):
    cap=result['packet_capture']
    assert cap['complete'] and not cap['omitted_events']
    selection=cap['selection']
    assert not selection['nodes'] and selection['start_ns']==0 and selection['end_ns']>=result['finish_ns']
    columns=cap['columns'];states={};peak=0;area=0;source_bytes=0;wire_bytes=0
    queued=defaultdict(deque);packet_residency_area=0
    gpu_count=result['gpu_count']
    for row in cap['events']:
        e=dict(zip(columns,row));t=e['time_ns'];event=e['event'];size=e['model_bytes']
        if event==0:
            wire_bytes+=size
            if e['node']<gpu_count and e['ip_protocol']==17:source_bytes+=e['data_payload_bytes']
        if event not in (3,4):continue
        key=e['node'],e['port'];old,when=states.get(key,(0,0));observed=e['egress_queue_bytes']
        packet_key=(*key,e['packet_uid'])
        assert t>=when
        area+=old*(t-when)
        if event==3:
            assert observed==old+size,('enqueue conservation',key,t,old,observed,size)
            after=observed
            queued[packet_key].append((t,size))
        else:
            assert observed==old and old>=size,('dequeue conservation',key,t,old,observed,size)
            after=observed-size
            assert queued[packet_key],('dequeue without captured enqueue',packet_key)
            enter,entered_size=queued[packet_key].popleft()
            assert entered_size==size
            packet_residency_area+=size*(t-enter)
        peak=max(peak,observed);states[key]=after,t
    for after,t in states.values():area+=after*(result['finish_ns']-t)
    for packets in queued.values():
        packet_residency_area+=sum(size*(result['finish_ns']-t) for t,size in packets)
    assert area==packet_residency_area, 'Queue state integral disagrees with per-packet residence sum'
    assert source_bytes==sum(m['source_data_payload_bytes'] for m in result['messages'])
    assert wire_bytes==sum(p['model_tx_bytes'] for p in result['ports'])
    return dict(beq_byte_ns=area,beq_peak_bytes=peak,source_data_payload_bytes=source_bytes,
                model_wire_bytes_all_ports=wire_bytes,beq_final_bytes=sum(s[0] for s in states.values()),
                per_packet_residency_crosscheck=True,
                ce_data_rx_hook_observations=sum(p['ecn_ce_seen'] for p in result['ports']),
                pfc_pause_hook_observations=sum(p['pfc_pause'] for p in result['ports']),
                messages_with_dcqcn_feedback=sum(f.get('dcqcn_feedback_seen',False) for f in result['messages']))

def resource_metrics(result):
    events=result['lifecycle_capture'];assert events['complete'] and not events['omitted_events']
    states={};integral=0
    for e in events['events']:
        t,_,_,src,count,*_=e
        old,when=states.get(src,(0,0));integral+=old*(t-when);states[src]=count,t
    integral+=sum(old*(result['finish_ns']-t) for old,t in states.values())
    flows=result['messages'];finish=result['finish_ns']
    granted=[f for f in flows if f['admission_granted_ns'] is not None]
    pending=[f for f in flows if f['admission_granted_ns'] is None]
    interval=sum((f['sender_resource_released_ns'] if f['sender_resource_released_ns'] is not None else finish)
                 -f['admission_granted_ns'] for f in granted)
    assert interval==integral
    return dict(software_wait_granted_ns=sum(f['admission_granted_ns']-f['ready_ns'] for f in granted),
        pending_wait_censored_ns=sum(finish-f['ready_ns'] for f in pending),pending_messages=len(pending),
        software_active_gpu_ns=integral,
        data_to_ack_sum_ns=sum(f['ack_ns']-f['receive_ready_ns'] for f in flows if f['ack_ns'] is not None),
        data_ready_without_ack_ids=[f['id'] for f in flows if f['receive_ready_ns'] is not None and f['ack_ns'] is None])

def summarize(directory,meta,target):
    result=read(directory/'result.json');front=read(directory/'frontend.json')
    diag=read(directory.parent/(directory.name+'-diagnosis.json'))
    assert diag['admission_resources']['reconstructed']
    f=next(f for f in result['messages'] if f['id']==target['id'])
    cb=next(c for c in result['service']['message_callbacks'] if c['id']==target['id'])
    return dict(**meta,finish_ns=result['finish_ns'],
        business_finished_ns=max(w['finished_ns'] for w in front['history']) if result['passed'] else None,
        group_history=front['history'],request_states=dict(Counter(q['state'] for q in front['requests'])),
        messages=len(result['messages']),submitted_message_bytes=sum(m['bytes'] for m in result['messages']),
        queues=queue_metrics(result),resources=resource_metrics(result),target=f,target_callbacks=cb,
        fault_drop_counts=result.get('fault_injection',{}).get('drop_counts',[]),
        final_sources=result['admission']['sources'],graph_progress=diag['graph_progress'],
        outstanding_graph_edges=diag['outstanding_graph_edges'],
        lifecycle_events=len(result['lifecycle_capture']['events']),packet_events=len(result['packet_capture']['events']))

def render(summary,output):
    cases=summary['cases'];base=cases['pass']['business_finished_ns']
    fifo=cases['fifo1'];reduction=100*(1-fifo['queues']['beq_byte_ns']/cases['pass']['queues']['beq_byte_ns'])
    ordering=('FIFO与目的端轮转的实际获取序列完全相同，当前输入无法区分两策略。'
              if summary['fifo_rr_actual_grants_equal'] else 'FIFO与目的端轮转改变了实际获取序列，需要结合完成时间判断效果。')
    feedback=any(c['queues'][k] for c in cases.values() for k in
                 ('ce_data_rx_hook_observations','pfc_pause_hook_observations','messages_with_dcqcn_feedback'))
    lead=(f'并发限为1条后，BE队列积分下降{reduction:.2f}%，整轮完成却从{base/1000:.3f} μs变为'
          f'{fifo["business_finished_ns"]/1000:.3f} μs。'+ordering+
          ('本轮出现拥塞反馈，具体计数见summary.json。' if feedback else
           '本轮没有观察到ECN标记、PFC暂停或DCQCN反馈，不能据此声称解决了拥塞控制问题。'))
    rows=[]
    for label,c in cases.items():
        finished=c['business_finished_ns'];q=c['queues'];r=c['resources']
        values=[label,'完成' if c['passed'] else '5ms内未完成',
            f'{finished/1000:.3f}' if finished is not None else '—',
            f'{finished/base:.3f}' if finished is not None else '—',
            str(q['beq_peak_bytes']),str(q['beq_byte_ns']),str(r['software_wait_granted_ns']),
            str(q['source_data_payload_bytes'])]
        rows.append('<tr>'+''.join('<td>'+html.escape(v)+'</td>' for v in values)+'</tr>')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Scale-Out准入与丢包对照</title><style>
body{font:16px/1.75 system-ui,sans-serif;margin:40px auto;padding:0 24px;max-width:1200px;color:#182438;background:#f5f7fa}
h1{font-size:30px}table{border-collapse:collapse;background:white;width:100%;font-size:14px}th,td{border:1px solid #d6deea;padding:10px;text-align:right}th:first-child,td:first-child{text-align:left}th{background:#e6edf7}p,li{max-width:950px}a{color:#1659b2}.scroll{overflow-x:auto}.note{background:white;border-left:4px solid #2167bd;padding:16px}
</style><h1>Scale-Out软件准入与丢包对照</h1>
<p>固定8 GPU、两组EP4、32请求、1层2步ready-KV Decode，三层Clos 100G。声明计算成本及合成路由；这不是硬件容量校准或生产恢复验证。</p>
<p class="note">队列变短不保证推理变快。软件并发限额、发送顺序、决策成本与故障分别比较；失败行不以5ms观察终点冒充业务完成时延。</p>
<p>'''+html.escape(lead)+'''</p>
<div class="scroll"><table><thead><tr><th>条件</th><th>状态</th><th>整轮完成 μs</th><th>相对透传倍数</th><th>单端口BE队列峰值 B</th><th>BE队列积分 B·ns</th><th>已获额度消息累计待发 ns</th><th>源数据字节含重传 B</th></tr></thead><tbody>'''+''.join(rows)+'''</tbody></table></div>
<h2>如何解释</h2><ul>
<li>FIFO1与轮转1使用同一软件额度；透传与FIFO1/2有意改变软件允许的并发，物理资源保持不变。</li>
<li>BE队列积分从完整、有类型事件按原生入出队时点重建，不含NIC数据QP及单独高优先ACK队列；累计量不能当作可消除的业务损失。</li>
<li>数据齐备释放接收消费者，完整ACK后确认发送映射移除才归还软件额度。最终ACK丢失可让源端持续占用额度；另一组是否完成见原始前端历史。</li>
<li>失败运行中未获额度的消息等待是截至5ms的删失量，单独保存在summary.json，不混入成功时延。</li>
<li>本轮不证明新策略贡献，也不依据负收益另造热点。目标库QP复用、硬件共享资源与恢复机制仍需实机校准。</li></ul>
<p><a href="summary.json">完整摘要与占用者</a> · <a href="target-timeline.json">目标消息与图进展</a></p></html>'''
    (output/'report.html').write_text(page)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    source=a.run.resolve();out=a.output.resolve();out.mkdir(exist_ok=False)
    experiment=read(source/'experiment.json');cases={}
    for meta in experiment['cases']:cases[meta['label']]=summarize(source/meta['label'],meta,experiment['target'])
    def grants(label):
        result=read(source/label/'result.json')
        return [e[:4] for e in result['lifecycle_capture']['events'] if e[1]==1]
    same_order=grants('fifo1')==grants('rr1')
    baseline=read(source/'pass/result.json')
    base_paths={m['id']:[(h['node'],h['port']) for h in m['first_packet_path']] for m in baseline['messages']}
    base_groups={w['group_index']:w['finished_ns'] for w in cases['pass']['group_history'] if w['step']==1}
    for name,c in cases.items():
        raw=read(source/name/'result.json')
        fixed_paths=all([(h['node'],h['port']) for h in m['first_packet_path']]==base_paths[m['id']] for m in raw['messages']
                        if m['first_tx_ns'] is not None)
        assert fixed_paths, 'Changed first-attempt ECMP path; analyze separately before comparing'
        c['first_attempt_paths_fixed']=fixed_paths
        group_ratios={str(w['group_index']):w['finished_ns']/base_groups[w['group_index']]
            for w in c['group_history'] if w['step']==1 and 'finished_ns' in w}
        c['completed_group_last_step_ratios']=group_ratios
        c['worst_group_ratio']=max(group_ratios.values()) if c['passed'] else None
    stalled=cases['fifo1-ack'];f=stalled['target']
    assert f['receive_ready_ns'] is not None and f['ack_ns'] is None and f['sender_resource_released_ns'] is None
    assert stalled['target_callbacks']['recv_callback_ns'] is not None and stalled['target_callbacks']['send_callback_ns'] is None
    assert any(s['src']==f['src'] and f['id'] in s['active_ids'] for s in stalled['final_sources'])
    assert len([w for w in stalled['group_history'] if w['group_index']==1 and 'finished_ns' in w])==2
    summary=dict(experiment=experiment,cases=cases,fifo_rr_actual_grants_equal=same_order,
        scope='offline evaluation only; fixed declared-cost synthetic-route Decode, no hardware calibration')
    save(out/'summary.json',summary)
    timelines={}
    for name,c in cases.items():
        raw=read(source/name/'result.json');capture=raw['packet_capture'];column=capture['columns'].index('message_id')
        timelines[name]=dict(target=c['target'],callbacks=c['target_callbacks'],graph_progress=c['graph_progress'],
            group_history=c['group_history'],final_sources=c['final_sources'],
            packet_columns=capture['columns'],target_packet_events=[e for e in capture['events'] if e[column]==f['id']],
            lifecycle_columns=raw['lifecycle_capture']['columns'],
            source_lifecycle_events=[e for e in raw['lifecycle_capture']['events'] if e[3]==f['src']],
            fault_injection=raw.get('fault_injection'))
    save(out/'target-timeline.json',timelines)
    render(summary,out)
    print('Independent queue/resource arithmetic and review page generated')

if __name__=='__main__':main()
