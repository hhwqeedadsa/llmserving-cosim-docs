"""Offline inference/network reliability analysis with conditional certificates."""
import argparse
import hashlib
import html
import json
from pathlib import Path

def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')

def packet_components(run,mid):
    r=read(run/'result.json');capture=r['packet_capture'];assert capture['complete']
    f=next(f for f in r['messages'] if f['id']==mid)
    assert f['source_data_payload_bytes']==f['bytes'],'This decomposition requires one source attempt per byte'
    rows=[dict(zip(capture['columns'],row)) for row in capture['events'] if row[5]==mid and row[6]==17]
    sequence=max(e['sequence'] for e in rows)
    last=[e for e in rows if e['sequence']==sequence]
    assert len({e['packet_uid'] for e in last})==1
    tx=[e for e in last if e['event']==0];rx=[e for e in last if e['event']==1]
    assert len(tx)==len(rx) and tx[0]['node']==f['src'] and rx[-1]['node']==f['dst']
    assert rx[-1]['time_ns']==f['receive_ready_ns']
    enqueued={};residence=[]
    for e in last:
        key=e['node'],e['port']
        if e['event']==3:assert key not in enqueued;enqueued[key]=e['time_ns']
        elif e['event']==4:
            residence.append(dict(node=key[0],port=key[1],enqueue_ns=enqueued.pop(key),dequeue_ns=e['time_ns']))
    assert not enqueued
    for e in residence:e['residence_ns']=e['dequeue_ns']-e['enqueue_ns'];assert e['residence_ns']>=0
    link=sum(b['time_ns']-a['time_ns'] for a,b in zip(tx,rx))
    assert all(a['time_ns']<=b['time_ns'] for a,b in zip(tx,rx))
    forwarding=sum(tx[i+1]['time_ns']-rx[i]['time_ns'] for i in range(len(tx)-1))
    queued=sum(e['residence_ns'] for e in residence);other=forwarding-queued;assert other>=0
    emitted=tx[0]['time_ns']-f['first_tx_ns'];delivery=f['receive_ready_ns']-f['first_tx_ns']
    assert delivery==emitted+link+queued+other
    return dict(id=mid,last_sequence=sequence,last_packet_uid=last[0]['packet_uid'],
        source_emission_span_ns=emitted,link_serialization_and_propagation_ns=link,
        switch_queue_residence_ns=queued,other_forwarding_ns=other,data_delivery_ns=delivery,
        ack_tail_ns=f['ack_ns']-f['receive_ready_ns'],ports=residence)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--capture',type=Path);a=p.parse_args();source=a.input.resolve();out=a.output.resolve();out.mkdir(exist_ok=False)
    exp=read(source/'experiment.json');mid=exp['target']['id'];cases={};raw={}
    for definition in exp['cases']:
        name=definition['name'];r=read(source/name/'result.json');d=read(source/(name+'-diagnosis.json'))
        graphs={path.parent.name:hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted((source/name/'graphs').glob('*/wave.json'))}
        assert graphs==definition['graphs'],'Submitted graph differs from execution manifest'
        for graph,checksum in graphs.items():
            assert checksum==hashlib.sha256((source/'fixed-clean/graphs'/graph/'wave.json').read_bytes()).hexdigest(), 'Common graph changed'
        f=next(f for f in r['messages'] if f['id']==mid);states=r['recovery']['states'];s=next(s for s in states if s['id']==mid)
        history=read(source/name/'frontend.json')['history'];complete=all(h.get('finished_ns') is not None for h in history)
        finish=max(h['finished_ns'] for h in history) if complete and r['passed'] else None
        cases[name]=dict(passed=r['passed'],network_finish_or_stop_ns=r['finish_ns'],frontend_complete_ns=finish,
            network_drain_after_frontend_ns=r['finish_ns']-finish if finish is not None else None,
            submitted_messages=len(r['messages']),completed_messages=sum(m['ack_callbacks']==1 for m in r['messages']),
            source_payload_bytes=sum(m['source_data_payload_bytes'] for m in r['messages']),
            timeout_retransmissions=sum(s['timeout_retransmissions'] for s in states),
            rtt_samples=sum(s.get('rtt',{}).get('samples',0) for s in states),
            discarded_rtt_probes=sum(s.get('rtt',{}).get('discarded_probes',0) for s in states),
            duplicate_ack_responses=r['recovery']['duplicate_ack_responses'],target=f,target_recovery=s,
            target_protocol_events=[e for e in r['recovery']['events'] if e[3]==mid],
            conditional_bounds=d['recovery']['silence_bounds'],source_credit_obstructions=d['source_credit_obstructions'],
            queue_metrics=d['network_queues'],graph_progress=d['graph_progress'],frontend_history=history,
            result_sha256=hashlib.sha256((source/name/'result.json').read_bytes()).hexdigest())
        raw[name]=r
    paths={m['id']:[(h['node'],h['port']) for h in m['first_packet_path']] for m in raw['fixed-clean']['messages']}
    for r in raw.values():
        for m in r['messages']:
            if m['first_tx_ns'] is not None:assert paths[m['id']]==[(h['node'],h['port']) for h in m['first_packet_path']]
    for condition in ['clean','short']:
        for key in ['messages','ports','service','queue_events']:
            assert raw['fixed-'+condition][key]==raw['adaptive-'+condition][key],(condition,key)
    assert cases['adaptive-short']['rtt_samples']==0 and cases['adaptive-short']['discarded_rtt_probes']>0
    normal=cases['fixed-clean']['source_payload_bytes'];short=cases['adaptive-short']['source_payload_bytes']
    assert short>normal
    for name in ['fixed-persistent','adaptive-persistent']:
        c=cases[name];f=c['target'];bound=next(b for b in c['conditional_bounds'] if b['id']==mid)
        assert f['receive_ready_ns'] is not None and f['ack_ns'] is None and f['sender_resource_released_ns'] is None
        assert bound['failure_deadline_ns']==c['network_finish_or_stop_ns'] and bound['outcome']=='failed_at_bound'
        source0=c['source_credit_obstructions']['sources'][0]
        assert source0['held_logical_bytes']==6144 and source0['remaining_bytes']==2048
        assert source0['reason']=='fifo_head_bytes_do_not_fit' and not source0['policy_eligible_now']
        assert source0['data_ready_but_unconfirmed_owner_ids']==[mid]
    observed={}
    if a.capture:
        assert read(a.capture/'capture.json')['passed']
        for name in ['fixed-loss','adaptive-loss']:
            r=read(a.capture/name/'result.json');r.pop('packet_capture');assert r==raw[name]
            graphs={p.parent.name:p.read_bytes() for p in (a.capture/name/'graphs').glob('*/wave.json')}
            expected={p.parent.name:p.read_bytes() for p in (source/name/'graphs').glob('*/wave.json')}
            assert graphs==expected,'Packet-observation graph changed'
            observed[name]={str(i):packet_components(a.capture/name,i) for i in [49,60,62]}
            f={m['id']:m for m in raw[name]['messages']}
            assert f[60]['ready_ns']<f[49]['sender_resource_released_ns']==f[60]['admission_granted_ns']
            assert f[62]['ready_ns']<f[60]['sender_resource_released_ns']==f[62]['admission_granted_ns']
    summary=dict(platform_commit=exp['platform_commit'],analysis_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        target=exp['target'],cases=cases,packet_components=observed,
        common_graphs_identical=True,common_first_paths_identical=True,
        clean_and_short_execution_pairs_identical=True,scope='offline synthetic inference reliability evaluation; no hardware calibration')
    save(out/'summary.json',summary)
    md=['# 推理通信脆弱性与自适应恢复对照','',
        '本实验保持MoE任务和网络不变，检验最终确认、源额度、恢复参数和前端推进的关系。计算与超时下限均未做硬件校准，不声称生产p99改善。','',
        '固定8GPU、两组交错EP4、batch/rank4、H1024/E16/Top2、1层2步ready-KV Decode、100G Clos、每源3消息/8192B FIFO额度。共同已提交图逐字节相同，已发消息首包路径相同。','',
        '| 条件 | 前端全部图完成 ns | 网络结束/失败停止 ns | 源payload B | 超时重传决策 | 有效RTT样本 |',
        '|---|---:|---:|---:|---:|---:|']
    for name,c in cases.items():
        md.append('| '+' | '.join(str(v) for v in [name,c['frontend_complete_ns'],c['network_finish_or_stop_ns'],c['source_payload_bytes'],c['timeout_retransmissions'],c['rtt_samples']])+' |')
    md+=['','None表示没有完成；persistent是明确失败停止，不能把较早停止当成推理加速。失败条件只提交了部分后续任务，不能用其总字节与完整成功运行比较收益。','',
        '## 1. 最终确认的改变如何传到业务','']
    fixed=cases['fixed-loss'];learned=cases['adaptive-loss'];rtt=learned['target_recovery']['rtt']
    actual=learned['target_protocol_events'];timeout=next(e[0] for e in actual if e[1]==2)
    target=learned['target'];source_events=[e for e in raw['adaptive-loss']['recovery']['rtt_events'] if e[2]==mid]
    first_source=next(e[0] for e in source_events if e[1]==0)
    first_feedback=next(e[0] for e in source_events if e[1]==1)
    last_progress=max(e[0] for e in actual if e[1]==1 and e[0]<timeout)
    repeated_bytes=target['source_data_payload_bytes']-target['bytes']
    ack_delta=fixed['target']['ack_ns']-target['ack_ns']
    frontend_delta=fixed['frontend_complete_ns']-learned['frontend_complete_ns']
    md += [f'目标消息{mid}为{target["src"]}→{target["dst"]} Dispatch{target["bytes"]}B。接收端{target["receive_ready_ns"]}ns已收齐。首个原始包从{first_source}ns到{first_feedback}ns取得RTT样本{first_feedback-first_source}ns，偏差{rtt["rttvar_ns"]}ns，基准RTO为{rtt["base_rto_ns"]}ns。最后ACK进展{last_progress}ns后，在{timeout}ns超时；实际重发{repeated_bytes}B，经真实反向ACK在{target["ack_ns"]}ns确认并释放资源。', '',
        f'固定故障条件在{fixed["target"]["ack_ns"]}ns确认，前端{fixed["frontend_complete_ns"]}ns完成；自适应分别为{learned["target"]["ack_ns"]}ns与{learned["frontend_complete_ns"]}ns。两者只重传一次，额外业务payload相同，差异来自超时和随后共享网络时序，不是少执行任务。', '',
        f'目标ACK提前{ack_delta}ns，整体前端提前{frontend_delta}ns。这已经不满足“只有一个通信节点时长改变、其他时长/顺序固定”的条件，不能硬套单节点扰动上界。','']
    if observed:
        md+=['补充两次完整包观察与原执行除包记录外逐字段相同；按实际最后数据包分解后续消息，单位ns。源发出跨度从消息首包发出算到最后原始包发出；链路项含模型序列化/传播，队列项由逐端口入/出队时间直接计算。','',
            '| 条件/消息 | 源发出跨度 | 链路项 | 交换机驻留 | 其他转发 | 数据交付总段 | ACK尾部 |',
            '|---|---:|---:|---:|---:|---:|---:|']
        for name,items in observed.items():
            for mid2,c in items.items():md.append('| '+' | '.join(str(v) for v in [name+'/'+mid2,c['source_emission_span_ns'],c['link_serialization_and_propagation_ns'],c['switch_queue_residence_ns'],c['other_forwarding_ns'],c['data_delivery_ns'],c['ack_tail_ns']])+' |')
        delta=observed['fixed-loss']['49']['switch_queue_residence_ns']-observed['adaptive-loss']['49']['switch_queue_residence_ns']
        later={i:observed['adaptive-loss'][i]['switch_queue_residence_ns']-observed['fixed-loss'][i]['switch_queue_residence_ns'] for i in ['60','62']}
        accounted=ack_delta+delta-sum(later.values())
        md+=['',f'消息49的源发出跨度和链路项相同，交换机驻留减少{delta}ns；这部分有逐包证据，不能归为GPU计算变化。其确认释放后消息60获准，消息60确认后消息62获准，均有实际额度事件；原图已先就绪，等待确实穿过源字节额度。',
            f'消息60、62的队列驻留分别增加{later["60"]}ns、{later["62"]}ns，说明共享网络的变化有正有负。{ack_delta}ns提前加上{delta}ns减少、再减去后两段增加，合计{accounted}ns；实测完成差为{frontend_delta}ns，未解释余量{frontend_delta-accounted}ns。这是这组实际路径的局部证据，不把它推广为唯一普遍关键路径。','']
    md+=['## 2. 过低初值并没有被自动纠正','',
        f'2μs初值下，固定与自适应消息、端口、服务和队列执行完全相同；自适应有效样本{cases["adaptive-short"]["rtt_samples"]}，作废测量包{cases["adaptive-short"]["discarded_rtt_probes"]}。短消息在反馈前已进入重传，Karn规则排除有歧义样本；没有新原始包可测，不能宣称发生了学习优化。','',
        f'正常源payload {normal}B，短初值为{short}B，即{short/normal:.2f}倍；产生{cases["adaptive-short"]["timeout_retransmissions"]}次超时重传决策和{cases["adaptive-short"]["duplicate_ack_responses"]}次重复ACK响应。没有丢包也会制造协议负担。','',
        f'短初值前端在{cases["adaptive-short"]["frontend_complete_ns"]}ns完成，但到{cases["adaptive-short"]["network_finish_or_stop_ns"]}ns网络才排空，尾部差{cases["adaptive-short"]["network_drain_after_frontend_ns"]}ns。不能把排空时间直接充当请求延迟；已有流量仍可能干扰后续请求，需要持续负载另行验证。','',
        '## 3. 明确失败的形式化界与源额度','']
    for name in ['fixed-persistent','adaptive-persistent']:
        c=cases[name];b=next(b for b in c['conditional_bounds'] if b['id']==mid)
        md += [f'{name}：最后计时区间从{b["origin_ns"]}ns开始，重置RTO={b["reset_rto_ns"]}ns；两次重试后的无进展预算{b["no_progress_budget_ns"]}ns，预测失败{b["failure_deadline_ns"]}ns，与实际停止精确一致。']
    md+=['','两者源0仍占6144B，8192B额度只余2048B；候选队首消息13需要4096B，故当前不能获准。接收已齐却未确认的所有者就是目标1，槽数尚未满；这里是字节额度阻塞，不能归为“所有消息槽用完”。交换机末态队列为0。较早失败是有界检测，不是成功率提升。','',
        '这些证书只保证已启动且没有新ACK进展的协议区间，不覆盖准入/首次发送前等待，也不保证推理在SLO内完成。持续丢失、永久PFC暂停或过小上限仍可失败；当前失败会停止整个共享运行。','',
        f'平台提交`{exp["platform_commit"]}`。输入、命令、成功/失败、来源hash和资源记录在run.py输出；包观察另由capture.py保存。无千卡、训练、真实热点或硬件校准。','']
    (out/'results.md').write_text('\n'.join(md))
    parts=[];table=False
    for line in md:
        if line.startswith('|'):
            if line.startswith('|---'):continue
            if not table:parts.append('<div class="scroll"><table>');table=True
            parts.append('<tr>'+''.join('<td>'+html.escape(x.strip())+'</td>' for x in line.strip('|').split('|'))+'</tr>');continue
        if table:parts.append('</table></div>');table=False
        if line.startswith('# '):parts.append('<h1>'+html.escape(line[2:])+'</h1>')
        elif line.startswith('## '):parts.append('<h2>'+html.escape(line[3:])+'</h2>')
        elif line:parts.append('<p>'+html.escape(line)+'</p>')
    if table:parts.append('</table></div>')
    (out/'report.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>推理通信脆弱性对照</title><style>body{font:17px/1.8 system-ui,sans-serif;max-width:1180px;margin:40px auto;padding:0 24px;color:#182936}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}td{border:1px solid #ccd5dd;padding:8px}tr:first-child{background:#e8f1f5}h1{font-size:30px}h2{font-size:23px;margin-top:36px}</style>'+''.join(parts)+'<p><a href="summary.json">详细数据</a> · <a href="results.md">Markdown</a></p></html>')
    print(json.dumps(dict(passed=True,cases=len(cases),packet_runs=len(observed),output=str(out))))

if __name__=='__main__':main()
