"""Render saved evidence as a standalone Chinese HTML review report."""
import argparse
from html import escape
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output;out.mkdir(parents=True,exist_ok=False)
    summary=json.loads((a.run/'summary.json').read_text())
    names={'baseline':'无故障','middle-data':'中间数据包丢失','final-ack':'最终ACK丢失','switch-first':'交换机处首包丢失'}
    def micro(t):return '未发生' if t is None else f'{t/1000:.3f}'
    rows=[];details=[];timelines={}
    for label,c in summary['cases'].items():
        flow=c['target'];completed=[w['finished_ns'] for w in c['group_history'] if 'finished_ns' in w]
        finish=micro(max(completed)) if c['run_passed'] else '5 ms内未完成'
        rows.append('<tr>'+''.join(f'<td>{escape(str(x))}</td>' for x in [names[label],
            flow['bytes'],flow['source_data_payload_bytes'],micro(flow['receive_ready_ns']),micro(flow['ack_ns']),finish])+'</tr>')
        report=json.loads((a.run/label/'result.json').read_text());cap=report['packet_capture']
        index=cap['columns'].index('message_id')
        events=[dict(zip(cap['columns'],e)) for e in cap['events'] if e[index]==summary['target']['id']]
        timelines[label]=dict(selection=cap['selection'],complete=cap['complete'],events=events)
        event_names={0:'发送',1:'接收',2:'丢弃',3:'入队',4:'出队'}
        protocol={17:'DATA',252:'ACK',253:'NACK',254:'PFC',255:'CNP'}
        body=[]
        for e in events:
            body.append('<tr>'+''.join(f'<td>{escape(str(x))}</td>' for x in [micro(e['time_ns']),e['node'],e['port'],
                event_names[e['event']],protocol.get(e['ip_protocol'],e['ip_protocol']),e['sequence'],e['packet_uid'],e['egress_queue_bytes']])+'</tr>')
        details.append(f'<details><summary>{names[label]}：目标消息的{len(events)}条包事件</summary><div class="scroll"><table><thead><tr>'+''.join('<th>'+h+'</th>' for h in ['时间 μs','节点','端口','事件','协议','字节序号','包UID','出口队列 B'])+'</tr></thead><tbody>'+''.join(body)+'</tbody></table></div></details>')
    html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>MoE网络故障实验</title>
<style>body{font:16px/1.75 system-ui,sans-serif;color:#172536;background:#f4f7fb;margin:auto;padding:40px;max-width:1120px}main{background:white;padding:32px;border-radius:12px}h1{font-size:28px}h2{font-size:21px;margin-top:32px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border-bottom:1px solid #dce3ec;text-align:left;padding:10px}th{background:#eef3f9}p{max-width:1000px}.callout{background:#eef6ff;border-left:4px solid #347bd1;padding:14px 20px}summary{cursor:pointer;padding:12px;background:#eef3f9;margin-top:12px}.scroll{max-height:540px;overflow:auto}footer{color:#5b6678;font-size:13px;margin-top:24px}</style><main>
<h1>一个包丢失，为什么会拖住MoE推理？</h1><p>2026-10-21 · 8端点 · 两组交错EP4 · 单一原生RDMA ns-3 · ready-KV Decode</p>
<p class="callout">最终ACK丢失时，接收数据和后续计算可以完成，但发送事务尚未结束。故障组首轮64个节点完成63个，下一轮没有提交；另一组仍完成两轮。</p>
<h2>固定哪些条件</h2><p>32个起点就绪请求、每rank batch 4、H1024/F4096/E16/Top-K2、1层2步、三层Clos与100G链路。专家路由、放置和声明计算成本固定。只在指定接收设备丢一次包，共同提交的任务图逐字节相同。故障组尚未提交的未来图不能计入“相同已执行流量”。GPU标签不构成硬件校准。</p>
<h2>实际对照</h2><p>目标为消息1：GPU 0→4的Dispatch，6144 B。接收与ACK时刻均为绝对仿真时间；整段完成时间包括两组全部请求。</p>
<table><thead><tr><th>场景</th><th>业务字节 B</th><th>源发送字节（含重传）B</th><th>完整接收 μs</th><th>发送ACK完成 μs</th><th>整段完成 μs</th></tr></thead><tbody>'''+''.join(rows)+'''</tbody></table>
<h2>如何解释</h2><p>中间包丢失后，后续乱序包触发NACK，发送端重传，接收与ACK完成都推迟。最终ACK丢失时，接收在11.649 μs就绪，但源端直到5 ms窗口结束也没有完成回调。故障组的64个节点只有一个SEND未结束，另一组的16个请求仍完成两步。不能把全局运行失败误判成所有组或交换机都卡住。</p>
<p>平台探针还验证：较早的ACK丢失可由后续累计ACK覆盖；尾包、NACK丢失和重复丢包可以产生停滞。它们不是“每次丢包都增加一个固定时延”的模型。</p>
<h2>下探到包级</h2><p>展开查看每次发送、接收、BEgressQueue入/出队和接收错误丢弃。DATA序号是字节偏移，ACK/NACK序号是下一期望字节；UID是包谱系，不是独立传输编号。BEgressQueue不包含NIC数据QP或单独的ACK优先队列；入队读数在加字节后，出队读数在减字节前。此处为内部评价记录，不作为调度策略的全网可见输入。</p>'''+''.join(details)+'''
<h2>结论的边界与下一步</h2><p>本实验定位了当前锁定后端的协议恢复与事务完成边界，未实现新的恢复协议，也没有证明生产RDMA会如此停滞。有限5 ms窗口、声明计算成本及单次确定性干预不能用于生产故障率、完整模型TPOT或p99结论。后续应核查资源申请与释放，再建立合法的通信准入基线；提前推进下一轮仍需目标API、缓冲与资源安全证据。</p>
<footer>重现入口：experiments/scaleout_faults/run.py；原始记录与命令在运行目录；summary.json与timeline.json随本报告保存。丢包负例保留非零退出，不计作仿真成功。</footer></main></html>'''
    (out/'report.html').write_text(html)
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (out/'timeline.json').write_text(json.dumps(timelines,indent=2)+'\n')
    print(out/'report.html')


if __name__=='__main__':main()
