"""Post-run packet delivery curves and descriptive publication; never executes ns-3."""
import argparse
from collections import defaultdict
import csv
import hashlib
import html
import json
import os
from pathlib import Path
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from moe_ub_cosim.legacy_audit import decode


def main():
    if sys.flags.optimize:
        raise RuntimeError('Post-run reconstruction requires assertions; do not use Python -O')
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--study',type=Path,required=True);p.add_argument('--analysis',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--publish-dir',type=Path,required=True)
    a=p.parse_args();study=a.study.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    load=lambda path:json.loads(path.read_bytes());sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    analysis=load(a.analysis);assert analysis['passed']
    figure,axis=plt.subplots(figsize=(8,4.6));curves={};details={}
    for case in ('normal','local','prefix'):
        run=study/('run-'+case);assert sha(run/'result.json')==analysis['receipts'][case]['raw_result_sha256']
        work=load(run/'execution.json');r=load(run/'result.json');legacy=r['legacy'];net=legacy['transport']
        chosen={}
        for i,(op,row) in enumerate(zip(work['operations'],legacy['operations'])):
            if op[2:4]==[0,'data'] and op[4]!=op[5]:
                stream=2*legacy['contexts'][row[0]][3];chosen[stream,row[1]]=i
        chunks=defaultdict(list);buffers=defaultdict(bytearray);streams={key[0] for key in chosen}
        for event in net['selective_repeat']['events']:
            if event['kind']=='payload_delivered' and event['stream'] in streams:
                s=event['stream'];data=bytes.fromhex(event['hex']);begin=event['sequence']
                assert begin==len(buffers[s]);buffers[s].extend(data)
                chunks[s].append((begin,begin+len(data),event['time_ns']))
        increments=[];frame_bytes=0;frames_checked=0
        for stream,buffer in buffers.items():
            for sequence,verb,address,key,body,begin,end in decode(buffer):
                if (stream,sequence) not in chosen:continue
                i=chosen[stream,sequence];op=work['operations'][i];assert verb==1
                first=begin+32+16;assert end-first==2*work['hidden_size']==len(body)-16
                selected=[]
                for left,right,t in chunks[stream]:
                    size=max(0,min(right,end)-max(left,first))
                    if size:selected.append((t,size))
                assert sum(size for _,size in selected)==2*work['hidden_size']
                assert max(t for t,_ in selected)==legacy['operations'][i][5]
                increments.extend(selected);frame_bytes+=end-first;frames_checked+=1
        assert frames_checked==len(chosen)
        total=0;rows=[(0,0,0)]
        for t,size in sorted(increments):total+=size;rows.append((t,size,total))
        assert total==frame_bytes==len(chosen)*2*work['hidden_size']
        with (out/(case+'-dispatch.csv')).open('w') as stream:
            writer=csv.writer(stream);writer.writerow(['time_ns','effective_increment_bytes','cumulative_effective_bytes']);writer.writerows(rows)
        axis.step([t/1000 for t,_,_ in rows],[value/1024 for _,_,value in rows],where='post',label=case)
        times=legacy['event_times_ns'];events=work['events']
        dispatch=max((i for i,e in enumerate(events) if e[0]=='dispatch_packed'),key=lambda i:times[i])
        tail=max(events[dispatch][2],key=lambda i:times[i]);op_id=events[tail][3]
        details[case]=dict(network_dispatch_contributions=len(chosen),effective_dispatch_bytes=total,
            last_effective_contiguous_delivery_ns=rows[-1][0],last_dispatch_receive_barrier_ns=times[dispatch],
            selected_latest_dependency_kind=events[tail][0],selected_latest_operation=work['operations'][op_id][0] if op_id is not None else None,
            network_physical_finish_ns=r['finish_ns'],task_complete_ns=analysis['conditions'][case]['last_kernel_complete_ns'],
            repeated_carrier_bytes=analysis['conditions'][case]['retransmitted_payload_bytes'],
            link_failures=r.get('link_failures'),source_packets=analysis['conditions'][case]['packet_verification']['source_packets'])
        curves[case]=dict(rows=len(rows),csv_sha256=sha(out/(case+'-dispatch.csv')))
        del r,work,legacy,net,buffers
    axis.set(xlabel='Native simulated time (us)',ylabel='Contiguous Dispatch activation delivery (KiB)',
             title='Packet-level effective delivery: one real-selection batch')
    axis.grid(alpha=.25);axis.legend();figure.tight_layout();figure.savefig(out/'dispatch-delivery.png',dpi=170);plt.close(figure)
    figure,axis=plt.subplots(figsize=(7,4.3));names=['normal','local','prefix']
    bars=axis.bar(names,[analysis['conditions'][name]['last_kernel_complete_ns']/1000 for name in names],color=['#3478bf','#d58b2b','#38955f'])
    axis.bar_label(bars,fmt='%.3f us',padding=4);axis.set_ylim(0,max(b.get_height() for b in bars)*1.16)
    axis.set(ylabel='Complete communication call (us)',title='Mature repair accounts for most fault overhead')
    figure.tight_layout();figure.savefig(out/'completion.png',dpi=170);plt.close(figure)
    evidence=dict(analysis_sha256=sha(a.analysis),postrun_source_sha256=sha(Path(__file__)),details=details,curves=curves,
        packet_delivery_scope='Unique contiguous delivery of remote Dispatch activation bytes; excludes 32B carrier and 16B Dispatch metadata, local copies, Combine, and ACKs',
        new_network_executions=0,algorithm_innovation_validated=False)
    (out/'delivery.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    rows=''.join('<tr><td>'+name+'</td><td>'+f"{analysis['conditions'][name]['last_kernel_complete_ns']/1000:.3f}"+
        '</td><td>'+f"{analysis['conditions'][name]['physical_finish_ns']/1000:.3f}"+'</td><td>'+str(analysis['conditions'][name]['retransmitted_payload_bytes'])+'</td></tr>' for name in names)
    method=os.path.relpath(Path(__file__).parent/'README.md',a.publish_dir.resolve())
    headline=(f"正常{analysis['normal_task_ns']/1000:.3f}µs，本地过滤{analysis['local_task_ns']/1000:.3f}µs，成熟绕路{analysis['prefix_task_ns']/1000:.3f}µs。"
              f"绕路收回约{100*analysis['recovered_local_extra_wait_fraction']:.1f}%的故障额外等待；当前场景不足以支撑新恢复算法的收益主张。"
              f"残余{analysis['prefix_minus_normal_ns']/1000:.3f}µs是观测差距，不是已证明可消除的收益。")
    population=(f"{analysis['distinct_selected_experts']}个专家被选，最高专家被选{analysis['max_expert_choices']}次")
    phases=''.join('<tr><td>'+name+'</td><td>'+f"{details[name]['last_effective_contiguous_delivery_ns']/1000:.3f}"+
        '</td><td>'+f"{details[name]['last_dispatch_receive_barrier_ns']/1000:.3f}"+'</td></tr>' for name in names)
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>真实专家选择：成熟恢复后的剩余空间</title><style>body{font:17px/1.75 system-ui,sans-serif;max-width:1080px;margin:36px auto;padding:0 20px;color:#183047;background:#f5f8fc}img{max-width:100%;background:white}table{width:100%;border-collapse:collapse;background:white}td,th{text-align:left;padding:12px;border-bottom:1px solid #ddd}.callout{padding:18px;background:#eaf1fb;border-left:5px solid #4377ad}a{color:#075ba6}</style>
<h1>真实专家选择：成熟恢复后的剩余空间</h1><p class="callout">'''+html.escape(headline)+'''</p>
<p>128端点Clos，一个EP32组，15份不同R1-AWQ/MMLU本机原件，每份layer3/entry1的一个Decode token；15个数据源rank，120次真实Top8选择，'''+html.escape(population)+'''。该频率只代表此窗口，不证明长期或训练热点。其余96端点无该组工作。</p>
<table><tr><th>条件</th><th>完整通信调用 µs</th><th>网络排空 µs</th><th>重复载体 B</th></tr>'''+rows+'''</table>
<p>normal：无故障；local：故障交换机从原有ECMP候选中排除坏出口，可靠传输仍使用选择性重传；prefix：同样故障，在原有候选无法转发时使用成熟的显式修复路径。三者不是有无重传的对比。</p>
<img src="completion.png" alt="三组通信调用完成时间"><h2>数据连续交付与任务完成分开观察</h2><img src="dispatch-delivery.png" alt="逐包有效Dispatch连续交付曲线">
<p>曲线由经过完整审计的实际包交付记录重建，按字节区间去重，排除32B载体头与16B Dispatch元数据，只统计跨rank Dispatch激活。它表示连续交付给上层的字节，不是乱序包首次物理到达，也不包含本地复制、Combine或ACK。数据交付完毕不等于控制通知、完整调用或网络排空。</p>
<table><tr><th>条件</th><th>最后有效连续交付 µs</th><th>最晚Dispatch接收屏障 µs</th></tr>'''+phases+'''</table>
<p>三者均交付117份跨rank Dispatch激活，共1,677,312有效字节。故障条件最后一段交付明显拖后，但阶段最大值可能来自不同rank，不能相加为一条关键路径。完整调用最后等待的专家也随条件改变；尚未把0.275µs残差归因于某个固定包或证明它可消除。</p>
<p>三个网络各一次，保留完整16,624操作/66,848事件、全部零token通知与同一发送/完成条件；构网前一次local配置字段错误已保留，仅移除不支持字段，未启动该次网络或重跑正常条件。故障边128–132在4µs关闭、24µs恢复，控制通知延迟60µs，SR初始17µs。</p>
<p>这是旧DeepEP v1.2.1功能通信模型，不执行LLMServingSim服务调度、671B数值模型或GPU。AWQ选择与假定BF16载体分别记录，未知远端采集版本/激活精度不补齐。没有新协议或算法创新验证，也没有证明现代GIN/生产网络存在相同收益。</p>
<p><a href="analysis.json">来源与逐条件审计摘要</a> · <a href="delivery.json">曲线核算与阶段事实</a> · <a href="verification.json">独立字节掩码核验</a> · <a href="../../RESULTS.md">结果解释与研究判断</a> · <a href="'''+html.escape(method,quote=True)+'''">事前方法</a></p></html>'''
    (out/'report.html').write_text(page)
    print(json.dumps(dict(published=True,new_network_executions=0,curves=curves)))


if __name__=='__main__':main()
