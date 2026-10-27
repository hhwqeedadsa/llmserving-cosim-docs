"""Explain observed queue locations and physical feedback tails; no new networks."""
import argparse
from collections import Counter, defaultdict
import html
from pathlib import Path
from experiments.shared_uplink_choice.prepare import CONDITIONS, load, save


def explain(stage, output):
    output.mkdir()
    summary=load(stage/'report/summary.json')
    verification=load(stage/'verification.json')
    details={}
    for batch in (4,8):
        for name in CONDITIONS:
            directory=stage/'truth'/f'batch{batch}'/name/'modern'
            result=load(directory/'result.json')
            execution=load(directory/'modern-execution.json')
            app=max(e['time_ns'] for e in execution['events'] if e['kind']=='application_stage_complete')
            release=max(e['time_ns'] for e in execution['events'])
            cap=result['packet_capture']
            events=cap['events']
            headers=cap['wire_headers']
            ports={(r['node'],r['port']):r for r in result['ports']}
            waiting={}
            queue_by_tier=Counter()
            queue_by_port=Counter()
            late=Counter()
            packet_sets=defaultdict(set)
            for row,index in zip(events,headers['event_header_indices']):
                key=tuple(headers['headers'][index])+(row[7],row[8],row[11])
                identity=row[1],row[2],key
                if row[3]==3:waiting[identity]=row[0]
                elif row[3]==4:
                    delay=row[0]-waiting.pop(identity)
                    queue_by_tier[ports[row[1],row[2]]['tier']]+=delay
                    queue_by_port[row[1],row[2]]+=delay
                if row[3]==0:
                    packet_sets[row[6]].add(key)
                    if row[0]>release:late[str(row[6])]+=1
            assert not waiting
            audit=verification['audits'][f'batch{batch}-{name}']
            assert sum(queue_by_tier.values())==audit['queue_wait_ns']['total_ns']
            assert max(r[0] for r in events if r[3]==1)==result['finish_ns']
            dominant=sorted(queue_by_port.items(),key=lambda row:(-row[1],row[0]))[:3]
            details[f'batch{batch}-{name}']=dict(queue_wait_by_tier_ns=dict(sorted(queue_by_tier.items())),
                dominant_queue_ports=[dict(node=n,port=p,tier=ports[n,p]['tier'],wait_ns=ns) for (n,p),ns in dominant],
                distinct_CNP_lineages=len(packet_sets[255]),CNP_link_transmissions=audit['cnp_transmissions'],
                TX_after_all_application_events_by_ip_protocol=dict(sorted(late.items())),
                application_ns=app,all_events_ns=release,drain_ns=result['finish_ns'],
                last_data_RX_ns=max(r[0] for r in events if r[3]==1 and r[6]==17),
                drain_is_last_physical_RX=True)
    save(output/'mechanisms.json',details)
    benefits={}
    for batch in ('4','8'):
        times={n:v['application_ns'] for n,v in summary['batches'][batch]['metrics'].items()}
        benefits[batch]={n:dict(saved_ns=times['baseline']-t,reduction_fraction=1-t/times['baseline']) for n,t in times.items()}
    save(output/'benefits.json',benefits)
    lines=['# 本轮结论与机制边界','','## 升级收益有多少','',
        '| batch | 接入升级减少等待 | 核心升级减少等待 |','|---|---:|---:|']
    for b in ('4','8'):
        a,c=benefits[b]['access'],benefits[b]['core']
        lines.append(f"| {b} | {a['saved_ns']/1000:.3f}μs / {a['reduction_fraction']*100:.2f}% | {c['saved_ns']/1000:.3f}μs / {c['reduction_fraction']*100:.2f}% |")
    lines+=['','口径是本次单层MoE通信调用的应用阶段；不含完整模型计算、请求调度或整请求时延。两种升级使用的资源不同，尚无设备价格和同成本优化，不能换算投资回报。', '',
        '## 机制和不同于简单假设的情况','',
        '提高接入速率会让数据更快汇入未扩容的共享上联。两个batch的累计包等待、最大队列和CNP发送均增加，但应用仍略快；累计包等待不能直接当成可消除应用时延。位置、具体端口与反馈尾部见mechanisms.json，全部包路径、串行传播、端口字节及队列积分另有独立审计。','',
        '| batch/配置 | 累计包等待 μs | 最大队列 B | CNP包谱系 | CNP逐跳发送 |','|---|---:|---:|---:|---:|']
    for key,row in details.items():
        a=verification['audits'][key]
        lines.append(f"| {key} | {a['queue_wait_ns']['total_ns']/1000:.3f} | {a['maximum_queue_bytes']} | {row['distinct_CNP_lineages']} | {row['CNP_link_transmissions']} |")
    lines+=['','CNP包谱系按完整原生报文身份去重；逐跳发送同一报文会重复计数。应用完成、全部事件/源释放、物理排空分别报告；剩余ACK/CNP不能都算成应用等待，也不能忽略其对后续共享网络的潜在影响。当前只有一次调用，不由此推断连续服务的收益。','',
        '## 对创新候选的去留','',
        '只看总字节、端点阶段或每NIC阶段会错选接入，选择后悔值为22.98%/36.85%；普通逐链路阶段模型已选对，最大绝对相对时延误差仍为13.09%/11.92%。说明配置选择需要保留共享上联，不证明必须增加新的MoE语义。不能把修正已知拓扑输入说成论文创新。','',
        '六组完整通用图与现代详细执行的包、队列、回调、应用、源释放及内存等价。因此暂停把完整依赖表达能力当差异点。第一候选需收紧到跨输入的可靠误差/决策边界；第二候选仍须实际减少有流量、多跳和反馈处理并测量宿主成本，本轮没有仿真加速证据。','',
        '本轮完成的是研究筛选和可复现网络基准，两项论文主创新均未成立。四活动rank和便利样本没有证明千卡或生产泛化；硬件与真实provider性能仍需匹配校准。']
    (output/'conclusions.md').write_text('\n'.join(lines)+'\n')
    def esc(value):return html.escape(str(value))
    rows=[]
    choices=[]
    for b in ('4','8'):
        for name,m in summary['batches'][b]['metrics'].items():
            saving=benefits[b][name]['reduction_fraction']*100
            rows.append(f'<tr><td>{b}</td><td>{esc(name)}</td><td>{m["application_ns"]/1000:.3f}</td>'
                f'<td>{m["all_events_ns"]/1000:.3f}</td><td>{m["drain_ns"]/1000:.3f}</td><td>{saving:.2f}%</td></tr>')
        for method,e in summary['batches'][b]['evaluations'].items():
            choices.append(f'<tr><td>{b}</td><td>{esc(method)}</td><td>{esc(e["selected"])}</td>'
                f'<td>{100*e["regret_fraction"]:.2f}%</td><td>{100*max(map(abs,e["errors"].values())):.2f}%</td></tr>')
    document='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>共享上联：带宽升级应该放在哪里</title><style>body{font:16px/1.75 system-ui,sans-serif;color:#183047;background:#f4f7fa;margin:0}main{max-width:1080px;margin:auto;padding:40px 24px 72px}h1{font-size:32px;line-height:1.3}h2{margin-top:36px}p{max-width:900px}.tag{color:#5c6b7b}section{background:white;border:1px solid #dce5ee;border-radius:12px;padding:22px;margin-top:20px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{padding:11px 14px;border-bottom:1px solid #e1e7ed;text-align:left;white-space:nowrap}th{background:#edf3f8}.key{font-size:21px;color:#155a54}.note{padding:14px 18px;border-left:4px solid #558290;background:#eaf2f5}a{color:#12679d}code{font-size:14px}</style><main>
<p class="tag">MoE Scale-Out · 2026-10-27 · 已冻结预测、12次网络执行、6组完整图等价</p>
<h1>共享上联：带宽升级应该放在哪里</h1><p class="key">接入带宽翻倍只节省约0.5%的应用等待；核心带宽翻倍节省约19%—27%。</p>
<p>八份真实MMLU路由，batch4/8，四个活动rank，单层完整Dispatch—专家阶段—Combine；两张物理NIC、同一plane、两rail共享Agg/Core。协议与计算采用声明的功能模型，未做硬件性能校准。</p>
<p class="note">收益口径：单层通信应用阶段。接入升级和核心升级改变的资源不同，尚不能计算同成本收益或商业投资回报。</p>
<section><h2>实际运行结果</h2><p>baseline：接入100G/核心25G；access：接入200G/核心25G；core：接入100G/核心50G。链路传播均1μs。</p><div class="scroll"><table><thead><tr><th>batch</th><th>配置</th><th>应用阶段 μs</th><th>源释放 μs</th><th>物理排空 μs</th><th>应用等待减少</th></tr></thead><tbody>'''+''.join(rows)+'''</tbody></table></div>
<p>提高接入速率同时增加了共享上联的排队和拥塞反馈。应用完成、源释放、排空各有不同含义；累计包等待不可直接换算成业务损失。</p></section>
<section><h2>较强基线是否已经够用</h2><p>各方法在任何新网络运行前预测并保存hash；评价时才读取实际结果。Combine仅使用slot双射决定的NIC负载多重集合，未偷看未来绑定。</p><div class="scroll"><table><thead><tr><th>batch</th><th>方法</th><th>选择</th><th>选错多等比例</th><th>最大时延误差</th></tr></thead><tbody>'''+''.join(choices)+'''</tbody></table></div>
<p><code>volume</code>只看总字节；<code>endpoint_phase</code>保留七阶段；<code>nic_phase</code>按网卡拆分；<code>link_phase</code>再保留共享链路负载。后三者都是普通服务量模型。</p></section>
<section><h2>这对研究意味着什么</h2><p>共享链路信息决定了这轮配置选择。普通逐链路模型已经选对，不能把它的收益归给新的MoE语义；6组完整通用事件图也保持详细行为等价。两项论文创新仍待证明。</p>
<p>后续第一候选应研究可靠的误差和决策边界；第二候选须在保留多跳排队、反馈和应用完成的前提下测得仿真开销下降。当前没有新方法推理加速或仿真器加速结果。</p>
<p>局限：四活动rank、单层单次调用、便利样本，不能代表千卡生产集群；真实GPU/provider与整请求仍需校准和验证。</p></section>
<p><a href="../report/report.md">完整时延与选择表</a> · <a href="conclusions.md">机制解释与候选去留</a> · <a href="mechanisms.json">队列位置与反馈尾部</a> · <a href="../verification.json">独立逐包核验</a></p></main></html>'''
    (output/'report.html').write_text(document)


def main():
    p=argparse.ArgumentParser()
    for name in ('stage','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    explain(a.stage.resolve(),a.output.resolve())


if __name__=='__main__':main()
