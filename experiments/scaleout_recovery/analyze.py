"""Offline reliability analysis; no online policy imports or test helpers."""
import argparse
from copy import deepcopy
import html
import hashlib
import json
from pathlib import Path

def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();source=a.input.resolve();out=a.output.resolve();out.mkdir(exist_ok=False)
    exp=read(source/'experiment.json');target=exp['target'];mid=target['id'];cases={};raw={}
    for definition in exp['cases']:
        name=definition['name'];r=read(source/name/'result.json');d=read(source/(name+'-diagnosis.json'))
        f=next(m for m in r['messages'] if m['id']==mid);rec=r.get('recovery',{})
        state=next((s for s in rec.get('states',[]) if s['id']==mid),None)
        frontend=read(source/name/'frontend.json')
        cases[name]=dict(passed=r['passed'],finish_or_stop_ns=r['finish_ns'],failure=r.get('failure'),
            completed_messages=sum(m['ack_callbacks']==1 for m in r['messages']),submitted_messages=len(r['messages']),
            source_payload_bytes=sum(m['source_data_payload_bytes'] for m in r['messages']),
            final_queue_bytes=d['network_queues']['final_bytes'],queue_area_byte_ns=d['network_queues']['area_byte_ns'],
            target=f,target_recovery=state,source0_resources=r['admission']['sources'][0],
            target_recovery_events=[e for e in rec.get('events',[]) if e[3]==mid],
            total_timeout_retransmissions=sum(s['timeout_retransmissions'] for s in rec.get('states',[])),
            receiver_duplicate_acks=rec.get('duplicate_ack_responses',0),recovery_audit=d['recovery'],
            pending_messages=[m for m in r['messages'] if m['id'] in (13,14)],
            graph_progress=d['graph_progress'],frontend_history=frontend['history'],
            target_callback=next(m for m in r['service']['message_callbacks'] if m['id']==mid))
        raw[name]=r
    clean=deepcopy(raw['recovery-clean']);clean.pop('recovery')
    assert clean==raw['native-clean'],'Extension changed loss-free execution'
    paths={m['id']:[(h['node'],h['port']) for h in m['first_packet_path']] for m in raw['native-clean']['messages']}
    for name,r in raw.items():
        for m in r['messages']:
            if m['first_tx_ns'] is not None:assert paths[m['id']]==[(h['node'],h['port']) for h in m['first_packet_path']]
    failed=cases['native-loss'];fixed=cases['recovery-loss'];exhausted=cases['persistent-loss']
    assert failed['target']['ack_ns'] is None and failed['target']['receiver_contiguous_bytes']==target['bytes']
    assert failed['target']['sender_progress']['snd_una_bytes']<target['bytes'] and failed['final_queue_bytes']==0
    assert fixed['target']['source_data_payload_bytes']>target['bytes'] and fixed['target']['ack_callbacks']==1
    assert fixed['target']['sender_resource_released_ns']==fixed['target']['ack_ns']
    assert fixed['target_callback']['send_callback_ns']==fixed['target']['ack_ns']
    assert fixed['total_timeout_retransmissions']==1 and fixed['receiver_duplicate_acks']==1
    for n in ('native-loss','persistent-loss'):
        assert cases[n]['target']['sender_resource_released_ns'] is None
        assert mid in cases[n]['source0_resources']['active_ids']
        assert cases[n]['source0_resources']['pending_ids']==[13,14]
    for m in fixed['pending_messages']:assert m['admission_granted_ns']>=fixed['target']['sender_resource_released_ns']
    assert exhausted['failure']['reason']=='recovery_retry_exhausted' and not exhausted['passed']
    assert exhausted['target_recovery']['timeout_retransmissions']==2
    for name in ('native-clean','recovery-clean','recovery-loss'):
        assert raw[name]['drained'] and all(g['declared_nodes']==g['completed_nodes'] for g in cases[name]['graph_progress'])
    summary=dict(platform_commit=exp['platform_commit'],analysis_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),target=target,cases=cases,
        loss_free_execution_identical=True,common_graphs_identical=True,common_first_paths_identical=True,
        scope='offline evaluation, synthetic ready-KV Decode, declared compute and RTO')
    save(out/'summary.json',summary)
    rows=[]
    for name,c in cases.items():
        f=c['target'];stop=str(c['finish_or_stop_ns'])+('（失败停止）' if not c['passed'] else '')
        rows.append([name,stop,str(f['receive_ready_ns']),str(f['ack_ns']),str(f['source_data_payload_bytes']),
            str(c['total_timeout_retransmissions']),f"{c['completed_messages']}/{c['submitted_messages']}"])
    extra=fixed['finish_or_stop_ns']-cases['native-clean']['finish_or_stop_ns']
    ack_extra=fixed['target']['ack_ns']-cases['native-clean']['target']['ack_ns']
    timeout_events=[e for e in fixed['target_recovery_events'] if e[1]==2]
    last_ack=next(e[0] for e in reversed(fixed['target_recovery_events']) if e[1]==1)
    rto=raw['recovery-loss']['recovery']['config']['initial_rto_ns']
    extra_payload=fixed['target']['source_data_payload_bytes']-target['bytes']
    wave0=next(h['finished_ns'] for h in fixed['frontend_history'] if h['graph_id']=='g0.w0')
    failure_timeouts=[e[0] for e in exhausted['target_recovery_events'] if e[1]==2]
    md=['# 固定 MoE 任务的超时恢复结果','',
        '本实验验证仿真器在丢失最终确认时能正确推进或明确失败，未评价新的调度策略。','',
        '固定8GPU、两个交错EP4副本、每rank batch4、H1024/E16/Top2、1层2步ready-KV Decode、100G三层Clos。源端FIFO额度为3消息/8192 B，计算和RTO均为声明值。共同实际提交的任务图逐字节相同，已发消息首包路径相同。','',
        '| 条件 | 完成/停止 ns | 目标接收就绪 ns | 目标完整ACK ns | 目标发送payload B | 全部超时重传决策 | 完成/提交消息 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    md+=['| '+' | '.join(row)+' |' for row in rows]
    md+=['','None表示未发生；失败停止时间不是完成时间。各失败运行没有提交全部后续图，因此已提交业务量与成功运行不同，不能比较其总字节作为优化收益。','',
        '## 发生了什么','',
        f'目标消息{mid}为GPU {target["src"]}→{target["dst"]}的Dispatch，共{target["bytes"]} B。原生故障时，{failed["target"]["receive_ready_ns"]} ns已收齐；源端只确认到{failed["target"]["sender_progress"]["snd_una_bytes"]} B。5ms结束时交换机队列为0，6144 B软件额度仍被占用，消息13/14等待。停滞来自确认缺失，不能归为队列一直堵塞。','',
        f'开启恢复后，最后一次ACK进展为{last_ack} ns；{rto} ns后在{timeout_events[0][0]} ns超时，实际重新发送尾部{extra_payload} B，接收端产生真实累计ACK。{fixed["target"]["ack_ns"]} ns源端收到完整确认并清理QP、释放额度，消息13/14在同一时刻获准。第一组首轮在{wave0} ns结束，真实前端提交下一轮，整个服务在{fixed["finish_or_stop_ns"]} ns完成。','',
        f'恢复后的服务比无故障基线多{extra} ns（{100*extra/cases["native-clean"]["finish_or_stop_ns"]:.2f}%）。目标ACK晚了{ack_extra} ns，业务增加{extra} ns：组间并发与网络重叠会改变影响，不能将单消息等待直接当成端到端损失。这里展示故障代价，不声称恢复比无故障更快。','',
        f'持续丢失时，在{failure_timeouts} ns各作一次超时重传；第三次到期{exhausted["finish_or_stop_ns"]} ns明确失败。目标QP、{target["bytes"]} B额度、等待通信和未完成图保留，不报告正常SEND。另一个副本已完成的工作仍如实记录，整个运行按失败处理。','',
        '## 支持的结论与限制','',
        '无故障原生与恢复结果除新增恢复记录外逐字段一致；故障恢复经实际数据包、反向ACK、原生回调、资源归还和前端下一轮共同验证。网络排空与逐事件恢复审计通过。','',
        '这支持更可靠的仿真执行和更清楚的故障解释，不证明生产RoCE行为精度。没有跑千卡、训练、真实专家热点、故障重路由或硬件RTO校准。当前耗尽重试会终止整个共享仿真，尚不支持单QP失败后其他任务继续。','',
        f'平台提交：`{exp["platform_commit"]}`。输入、命令和失败目录由run.py保存，summary.json为离线分析，不参与在线恢复。','']
    (out/'results.md').write_text('\n'.join(md))
    parts=[];table=False
    for line in md:
        if line.startswith('|'):
            if line.startswith('|---'):continue
            if not table:parts.append('<table>');table=True
            parts.append('<tr>'+''.join('<td>'+html.escape(s.strip())+'</td>' for s in line.strip('|').split('|'))+'</tr>');continue
        if table:parts.append('</table>');table=False
        if line.startswith('# '):parts.append('<h1>'+html.escape(line[2:])+'</h1>')
        elif line.startswith('## '):parts.append('<h2>'+html.escape(line[3:])+'</h2>')
        elif line:parts.append('<p>'+html.escape(line)+'</p>')
    if table:parts.append('</table>')
    (out/'report.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Scale-Out超时恢复结果</title><style>body{font:17px/1.8 system-ui,sans-serif;max-width:1180px;margin:40px auto;padding:0 24px;color:#182936}table{border-collapse:collapse;width:100%;display:block;overflow:auto;font-size:14px}td{border:1px solid #cbd5df;padding:8px}tr:first-child{background:#e9f2f6}h1{font-size:30px}h2{font-size:23px;margin-top:36px}</style>'+''.join(parts)+'<p><a href="summary.json">详细数据</a> · <a href="results.md">Markdown</a></p></html>')
    print(json.dumps(dict(passed=True,cases=len(cases),output=str(out))))

if __name__=='__main__':main()
