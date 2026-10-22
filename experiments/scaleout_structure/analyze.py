"""Offline analysis of official outcomes; never an online admission input."""
import argparse
from copy import deepcopy
import html
import json
from pathlib import Path

def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def case(source,name):
    r=read(source/name/'result.json');d=read(source/(name+'-diagnosis.json'))
    probe=read(source/name/'execution/probe.json')
    queues=d['network_queues'];resources=d['admission_resources']
    phases={};open_stages=[]
    for m in d['messages']:
        stage=m['stage_times']
        if stage['available']:
            for k,v in stage['durations_ns'].items():
                if v is not None:phases[k]=phases.get(k,0)+v
            if stage['open_stage']:open_stages.append(dict(id=m['id'],**stage['open_stage']))
    return dict(passed=r['passed'],finish_or_stop_ns=r['finish_ns'],
        last_complete_ack_ns=max((m['ack_ns'] for m in r['messages'] if m['ack_ns'] is not None),default=None),
        queue_area_byte_ns=queues['area_byte_ns'],peak_queue_bytes=queues['peak_port_bytes'],
        final_queue_bytes=queues['final_bytes'],peak_active_logical_bytes=max(resources['peak_active_logical_bytes'].values(),default=0),
        source_payload_bytes=sum(m['source_data_payload_bytes'] for m in r['messages']),
        posted_logical_bytes=sum(m['bytes'] for m in r['messages']),
        endpoint_ce_receives=sum(p['ecn_ce_seen'] for p in r['ports'] if p['node']<r['gpu_count']),
        pfc_pause_notifications=sum(p['pfc_pause'] for p in r['ports']),
        completed_flows_with_dcqcn_feedback=sum(bool(m.get('dcqcn_feedback_seen')) for m in r['messages']),
        sender_rates_at_complete_ack_bps=sorted({m['sender_rate_at_ack_bps'] for m in r['messages'] if 'sender_rate_at_ack_bps' in m}),
        completed_stage_sums_ns=phases,open_stages=open_stages,
        peak_sampled_group_rss_bytes=probe['peak_sampled_group_rss_bytes'],
        result_json_bytes=(source/name/'result.json').stat().st_size,
        admission_sources=r['admission']['sources'],graph_progress=d['graph_progress'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();source=a.input.resolve();out=a.output.resolve();out.mkdir(exist_ok=False)
    exp=read(source/'experiment.json');cases={c['name']:case(source,c['name']) for c in exp['cases']}
    results={name:read(source/name/'result.json') for name in cases}
    def grants(name):return [e[:4] for e in results[name]['lifecycle_capture']['events'] if e[1]==1]
    equal=grants('bytes-fifo')==grants('bytes-rr')
    def source_orders(name):
        result={}
        for e in results[name]['lifecycle_capture']['events']:
            if e[1]==1:result.setdefault(e[3],[]).append(e[2])
        return result
    same_order=source_orders('bytes-fifo')==source_orders('bytes-rr')
    rr=results['bytes-rr'];flows={m['id']:m for m in rr['messages']};pending={};active={};bypasses=[]
    for t,event,mid,src,*_ in rr['lifecycle_capture']['events']:
        pending.setdefault(src,[]);active.setdefault(src,set())
        if event==0:pending[src].append(mid)
        elif event==1:
            head=pending[src][0];remaining=8192-sum(flows[i]['bytes'] for i in active[src])
            if head!=mid:bypasses.append(dict(time_ns=t,source=src,selected_id=mid,selected_bytes=flows[mid]['bytes'],
                fifo_head_id=head,fifo_head_bytes=flows[head]['bytes'],remaining_logical_bytes=remaining,active_ids=sorted(active[src])))
            pending[src].remove(mid);active[src].add(mid)
        elif event==5:active[src].remove(mid)
    baseline_paths={m['id']:[(h['node'],h['port']) for h in m['first_packet_path']] for m in results['pass']['messages']}
    for name in ('count3','bytes-fifo','bytes-rr','bytes-ack'):
        for m in results[name]['messages']:
            if m['first_tx_ns'] is not None:
                assert baseline_paths[m['id']]==[(h['node'],h['port']) for h in m['first_packet_path']]
    incast=deepcopy(results['incast-full']);capture=incast.pop('packet_capture')
    assert capture['complete'] and capture['selection']==dict(nodes=[],start_ns=0,end_ns=5000000,max_events=100000)
    assert incast==results['incast-summary'],'Observation mode changed execution'
    cnp=sum(e[3]==0 and e[6]==255 and e[1]<incast['gpu_count'] for e in capture['events'])
    assert sum(m['bytes'] for m in incast['messages'])==7*131072
    assert cases['incast-full']['source_payload_bytes']==7*131072
    target=exp['target'];timelines={}
    for name in ('pass','count3','bytes-fifo','bytes-rr','bytes-ack'):
        timelines[name]=next(m for m in read(source/(name+'-diagnosis.json'))['messages'] if m['id']==target['id'])
    failure=timelines['bytes-ack']
    assert failure['ack_ns'] is None and failure['sender_resource_released_ns'] is None
    assert failure['sender_progress']['snd_una_bytes']<target['bytes']
    assert cases['bytes-ack']['final_queue_bytes']==0
    summary=dict(platform_commit=exp['platform_commit'],cases=cases,target=target,target_timelines=timelines,
        fifo_rr_actual_grant_sequence_equal=equal,incast_full_summary_execution_equal=True,
        fifo_rr_per_source_message_order_equal=same_order,rr_bypass_events=bypasses,
        common_injected_message_first_paths_equal=True,
        incast_source_cnp_transmissions=cnp,
        incast_record_events=len(capture['events']),scope='offline_evaluation; separate MoE service and synthetic transport cases')
    save(out/'summary.json',summary)
    rows=[]
    for name in ('pass','count3','bytes-fifo','bytes-rr','bytes-ack'):
        c=cases[name];t=str(c['finish_or_stop_ns']) if c['passed'] else '5ms停止，未完成'
        rows.append([name,t,str(c['queue_area_byte_ns']),str(c['peak_queue_bytes']),str(c['peak_active_logical_bytes'])])
    lead='按当前固定MoE输入评估字节额度，不把新增接口当作性能收益。'
    md=['# Scale-Out结构改进对照结果','',lead,'',
        '固定8GPU、两组交错EP4、32请求、batch/rank4、1层2步ready-KV Decode、100G三层Clos。计算为声明值，路由为原合成输入；共同实际提交的图逐字节一致。','',
        '| 条件 | 完成/停止时间 ns | 队列积分 B·ns | 端口队列峰值 B | 每源活跃逻辑字节峰值 B |',
        '|---|---:|---:|---:|---:|']
    md+=['| '+' | '.join(row)+' |' for row in rows]
    md+=['','队列积分来自全程交换机BEgressQueue，已排除序列化中的包。活跃逻辑字节为整条已获准消息的payload和，不是NIC实际在途量。未完成项不是“5ms完成”。','',
        f'轮转相对同字节额度FIFO的完成时间减少{100*(1-cases["bytes-rr"]["finish_or_stop_ns"]/cases["bytes-fifo"]["finish_or_stop_ns"]):.2f}%，但仍比透传慢{100*(cases["bytes-rr"]["finish_or_stop_ns"]/cases["pass"]["finish_or_stop_ns"]-1):.2f}%。字节FIFO队列积分低于透传，端口峰值却高于透传；不能把减少总排队、降低峰值和改善服务混为一项收益。','',
        '## 系统等待与失败','',
        f'FIFO与轮转实际grant序列（时间、消息、源）完全相同：{equal}；逐源消息身份顺序相同：{same_order}。共同已发消息的首包路径保持一致。',
        f'目标消息{target["id"]}为{target["src"]}→{target["dst"]} Dispatch，{target["bytes"]} B；最终ACK丢失时，源端确认到{failure["sender_progress"]["snd_una_bytes"]} B，发送序号为{failure["sender_progress"]["snd_nxt_bytes"]} B。',
        '故障运行交换机队列末态为0，发送资源仍未回收。分阶段耗时、未完成段、全部额度占用者与图进展保留在summary.json；跨消息阶段累计不是服务关键路径。','',
        '## 独立的网络压力校验','',
        f'7个源各发送128KiB到同一目标，共917504 B。完整与summary观测执行逐字段一致；完整记录{len(capture["events"])}条包事件，源端CNP发送{cnp}次。该输入为合成传输压力，不代表实际MoE热点或训练分布。','',
        '此锁定后端在ACK/NACK头携带CNP标志，由ReceiveAck触发DCQCN处理，因此独立CNP包为0不等于没有反馈。必须同时检查端点CE、发送QP反馈状态与速率。','',
        '| 模式 | 最后完整ACK ns | 队列积分 B·ns | 队列峰值 B | 端点CE接收事件 | 有DCQCN反馈的完成流 | PFC暂停通知 | result.json B |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name in ('incast-full','incast-summary'):
        c=cases[name];md.append('| '+' | '.join(str(v) for v in [name,c['last_complete_ack_ns'],c['queue_area_byte_ns'],c['peak_queue_bytes'],c['endpoint_ce_receives'],c['completed_flows_with_dcqcn_feedback'],c['pfc_pause_notifications'],c['result_json_bytes']])+' |')
    md+=['','两次独立宿主运行的RSS采样保存在summary.json，不用这两个样本宣称普遍的内存或运行时间加速。真实热点、通信库分块、GPU/NIC资源与硬件耗时仍需外部校准。',
        '',f'平台提交：`{exp["platform_commit"]}`。运行输入、命令、失败目录和来源hash在原实验目录；本次没有千卡运行。','']
    if bypasses:
        b=bypasses[0]
        md[md.index('## 系统等待与失败')+2:md.index('## 系统等待与失败')+2]=[
            f'首个跨过全局FIFO队首的实际选择：{b["time_ns"]} ns，源{b["source"]}剩余{b["remaining_logical_bytes"]} B，FIFO队首消息{b["fifo_head_id"]}需要{b["fifo_head_bytes"]} B；轮转选择消息{b["selected_id"]}的{b["selected_bytes"]} B。它保持各目的端内部FIFO，说明这里改变了选择机会，而不只是改变grant时间。','']
    (out/'results.md').write_text('\n'.join(md))
    # Minimal deterministic review renderer. Source is the generated Markdown;
    # no remote scripts, resources, or browser-side simulation is involved.
    parts=[];table=False
    for line in md:
        if line.startswith('|'):
            if line.startswith('|---'):continue
            if not table:parts.append('<table>');table=True
            parts.append('<tr>'+''.join('<td>'+html.escape(x.strip())+'</td>' for x in line.strip('|').split('|'))+'</tr>');continue
        if table:parts.append('</table>');table=False
        if line.startswith('# '):parts.append('<h1>'+html.escape(line[2:])+'</h1>')
        elif line.startswith('## '):parts.append('<h2>'+html.escape(line[3:])+'</h2>')
        elif line:parts.append('<p>'+html.escape(line)+'</p>')
    if table:parts.append('</table>')
    (out/'report.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Scale-Out结构改进结果</title><style>body{font:17px/1.8 system-ui,sans-serif;max-width:1120px;margin:40px auto;padding:0 24px;color:#182936}table{border-collapse:collapse;width:100%;font-size:14px;display:block;overflow:auto}td{border:1px solid #ccd5dd;padding:8px}tr:first-child{background:#e9f2f6}h1{font-size:30px}h2{font-size:23px;margin-top:36px}a{color:#075e91}</style>'+''.join(parts)+'<p><a href="summary.json">详细数据</a> · <a href="results.md">Markdown</a></p></html>')
    print(json.dumps(dict(passed=True,cases=len(cases),output=str(out))))


if __name__=='__main__':main()
