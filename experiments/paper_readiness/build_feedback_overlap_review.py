"""Build the evidence report for the fixed four-condition overlap study."""
import argparse
import html
import json
from pathlib import Path
import shutil


def load(p):return json.loads(p.read_text())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('task','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();out=a.output;assert not out.exists()
    summary=load(a.task/'analysis/summary.json');v=load(a.task/'checked-refined/verification.json')
    comparison=load(a.task/'comparison/comparison.json')
    assert v['verified'] and v['all_declared_completed'] and len(summary['cases'])==4
    rows=summary['cases'];lookup={r['name']:r for r in rows}
    shallow=lookup['d1-r55000'];deep=lookup['d8-r55000'];slow=lookup['d8-r110000']
    ratios=dict(depth8_to_depth1_55us=deep['final_acked_ns']/shallow['final_acked_ns'],
        depth8_to_depth1_110us=slow['final_acked_ns']/shallow['final_acked_ns'],
        recovery55_to110_percent=100*(slow['final_acked_ns']/deep['final_acked_ns']-1),
        post_feedback_packet_fraction=deep['packets_after_first_cnp']/deep['source_packets'])
    facts=dict(ratios=ratios,network_runs=4,raw_volatile=True,persistent_backup=False,
        paper_main_contribution_established=False,independent_loads=False,hardware_calibrated=False,
        evidence='positive mechanism/decision-sensitivity screening, not production tuning or a new algorithm')
    out.mkdir()
    for dst,source in (('summary.json',a.task/'analysis/summary.json'),('streams.json',a.task/'analysis/streams.json'),
                       ('verification.json',a.task/'checked-refined/verification.json'),('window-comparison.json',a.task/'comparison/comparison.json')):
        shutil.copyfile(source,out/dst)
    shutil.copyfile(Path(__file__).with_name('feedback_overlap_protocol.md'),out/'protocol.md')
    (out/'findings.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2)+'\n')
    table='\n'.join('| '+' | '.join(map(str,(r['name'],r['final_acked_ns'],r['packets_after_first_cnp'],r['unique_destination_ce'],r['cnp_count'],r['peak_switch_queue_bytes'])))+' |' for r in rows)
    text=f'''# MoE窗口扩大后，网络并发建议发生反转

4组真实路由窗口实验全部完成，并通过独立包/端口/状态核验。当前模型中，浅队列约0.600ms完成，深队列约1.441—1.520ms；与上一轮均等6-token窗口的排序相反。本轮找到反馈后持续发包和源端实际限速的证据，不能把结果当现代NIC参数建议或已成立的论文主贡献。

## 服务对象与限定范围

研究对象是MoE专家并行的Scale-Out Dispatch数据传输，关注应用信用、源端拥塞控制、共享物理链路如何共同影响完成。不是完整LLM推理、训练、生产热点或千卡部署。本轮固定8端点Clos，在GPU0/2/4/6上放EP4；接入100G、上层25G、每链路1us、seed11，12条持久QP共享一个ns-3时钟。

使用四份已见R1-AWQ/MMLU请求，层4、entry0，每请求固定最多32 token，保留32/28/10/6的长度差异，共76 token、180份数据、2,603,520B、2,700个源数据包。每token按远端目的rank去重；连续等容量专家放置，H7168/E256/Top8、BF16/direct打包是假设，采集dtype未知。所有根0ns可用是同步压力声明，无计算、Combine、现代通信库控制或真实到达时序。没有复制请求或重跑旧149-token完整Prefill。

## 对照与全部结果

NP通知50us、alpha55us、g1/256、字节恢复10MB、最小100Mb/s、AI5Mb/s、固定HAI50Mb/s、CNP模型帧48B均固定；只比较ACK释放深度1/8与恢复55/110us。每条件独立初始化，但内部QP/网络状态持续存在。保留原ECN阈值与概率，没有用低阈值制造拥塞，没有搜索参数或扩矩阵。

| 条件（d深度/r恢复ns） | 最后业务ACK ns | 反馈后数据包 | 唯一目的CE | CNP | 交换机队列峰值 B |
|---|---:|---:|---:|---:|---:|
{table}

来源更正：原论文关于110us影响较小的叙述指alpha更新周期τ′，不是速率恢复T。上轮方案中的对应引文归属不准确；这里110us为自定的恢复周期敏感性。输入、结果和旧检查点均保留，不把该数值称为原论文结论复现。

所有条件物理排空与最后业务ACK相同；无丢包、重传、PFC暂停或网络失败。深度1的恢复周期没有作用，深度8中周期翻倍增加79,663ns，约{ratios['recovery55_to110_percent']:.2f}%。深度8约为深度1的{ratios['depth8_to_depth1_55us']:.2f}—{ratios['depth8_to_depth1_110us']:.2f}倍完成时间；这是当前功能模型的敏感性结果，不是硬件优化收益。

## 网络机制已经查到哪一步

1. **这次反馈确实约束仍在发送的数据。** 深度8中8条连接存在反馈后数据，共1,630包/1,569,432B，占源包约{ratios['post_feedback_packet_fraction']*100:.2f}%。这1,630个实际TX采样均低于100G，最低QP设置速率195,312,500bit/s。它是发包时QP状态，不是端到端测得吞吐。上一轮同配置6-token窗口的反馈后发包为0，所以不能用那个窗口评价恢复效果。
2. **实际信用与限速等待可以区分。** 深度1有168份工作等待旧ACK释放额度，深度8有93份；独立检查逐条验证published=max(ready,对应旧ACK)。深度8虽更早发布，却不保证更早发完。累计等待存在重叠，不能把36.67ms或14.37ms的逐工作等待相加当成整段时延。
3. **有排除应用缺数据和网口忙的限速见证。** 深度8/55us，stream3的工作56于259,429ns已发布；其源网口上个帧于457,287ns结束，包到471,349ns才发出，恰等于QP的next_avail，空闲14,062ns。此时QP585,937,500bit/s；原生在途字节窗口未启用，没有PFC或丢包，该工作的源信用早已取得。110us条件也有最长21,134ns的同类见证。没有将这些局部空闲累加成可消除的业务收益。
4. **79.663us恢复差异有干净的尾部定位。** 深度8的两个条件，CNP的按流生成/TX/RX序列完全相同。最终均由work71/stream3结束；55us条件最后数据TX=1,427,945ns，110us为1,507,608ns，差恰79,663ns。两者该数据都再用6,720ns到达，ACK再用6,068ns回源；这两段恰等于路径序列化加传播，没有额外排队。差异已落在最后数据发出之前，不能归给最终包交换机排队。
5. **源端已有长队列积压，不能只看提交等待。** 最终work71在55us条件374,769ns就发布，但第一包到1,290,422ns才发出；110us条件发布还早400ns，第一包却更晚。此前QP工作、速率状态与实际进度需要共同解释。源队列深度不是交换机缓存，源端网络拥塞控制也不等于应用计算。

## 强基线与跨窗口比较

四条件的全部数据与必要ACK物理出口服务量一致，最忙出口服务下界443,791.36ns。深度1比下界多155,923.64ns，深度8比下界多996,941.64—1,076,604.64ns。该基线不能区分相同数据路径下的时间状态；它仍是必要下界，差值不是全部可优化损失。

额外只读核对了上一轮的两个匹配条件：二进制hash、拓扑、协议、连接、控制器有效参数均相同，只有输入窗口和有界记录容量不同。均等6-token窗口里depth8比depth1少37,295ns；本轮32-token上限窗口反而多841,018ns。这个比较是在取得两轮结果后完成的描述性核对，不是独立留出，也不能说“长度”单一因素造成反转——增加真实token同时改变总字节量、源分布和目的矩阵。源份数从15/14/15/16变为72/67/25/16，目的份数从13/16/15/16变为38/38/45/59。

## 对论文目标意味着什么

- 已有更实质的问题证据：只用很短的通信窗口，可能给出与有反馈覆盖的窗口相反的并发建议；仅静态流量/最大出口服务量不足以解释当前固定路径条件的完成差异。
- 仍没有新机制贡献：HOTI 2022已研究ASTRA+Alibaba、incast、分块All-to-All与拥塞控制，不能把本轮“拥塞控制影响集合通信”当新发现。源节流、流量准入和集合通信调度都有强先例，需要逐项超过已有方法。
- 数值可靠性还缺关键外证：整套ECN/NP/RP参数不是当前GPU/NIC的校准表；AI5Mb/s是声明的本地选择，2015论文表2流体模型使用RAI40Mb/s，不能以相同的55us就称复刻原部署。源信用和同步就绪同样是模型条件。这个反转可能对参数/提交语义敏感，未验证前不能升级为生产现象。
- 下一步应先在独立真实请求和匹配NIC参数下复核反转，再决定研究在线并发控制还是仿真决策可信度；现代通信库控制/可见性、multi-rail/HPN与完整规模也尚未在本轮覆盖。当前4个请求和小Clos不足以支撑泛化。

## 核验与复现

独立checker重读4个原文件，重新构造180份写；核对10,800个数据包、10,800个ACK、122个CNP，共21,722个端点UID关联、160个物理端口条件、720个工作回调/信用、15,857条参考状态。NP由实际目的CE重算；RP由实际事件独立重放；哈希路径和全物理端口字节互证。端点捕获不是交换机逐跳抓包，功能正确性也不是硬件准确性。

执行前方案、命令/退出状态、输入及binary hash、全部原始结果和有界资源记录均保留。公开复现入口见experiments/paper_readiness/README.md。没有改平台、固定依赖、安装或新构建，4组均一次完成；后续核验未重跑网络。原件与归档仅在/dev/shm/moe-feedback-overlap-20261022，易失且无持久备份。源码、小报告和证据本地提交，不push；论文目标仍进行中。
'''
    (out/'report.md').write_text(text)
    parts=[];inside=False
    for line in text.splitlines():
        row=line.startswith('|')
        if row and not inside:parts.append('<div class="scroll"><table>');inside=True
        elif inside and not row:parts.append('</table></div>');inside=False
        if line.startswith('|---'):continue
        if row:parts.append('<tr>'+''.join('<td>'+html.escape(x.strip())+'</td>' for x in line.strip('|').split('|'))+'</tr>')
        elif line.startswith('# '):parts.append('<h1>'+html.escape(line[2:])+'</h1>')
        elif line.startswith('## '):parts.append('<h2>'+html.escape(line[3:])+'</h2>')
        elif line.strip():parts.append('<p>'+html.escape(line.replace('**',''))+'</p>')
    if inside:parts.append('</table></div>')
    (out/'report.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>反馈覆盖与MoE并发建议反转</title><style>body{font:17px/1.85 system-ui,sans-serif;max-width:1120px;margin:40px auto;padding:0 22px;color:#23334b;background:#f4f7fb}h1{font-size:30px;line-height:1.5}h2{font-size:23px;margin-top:36px;color:#164b7d}p{background:#fff;padding:14px 18px;border-radius:8px}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;background:white;font-size:14px}td{border:1px solid #d3deeb;padding:8px;text-align:right;white-space:nowrap}td:first-child{text-align:left}tr:first-child{background:#e0eafa;font-weight:bold}a{color:#1462a1}</style>'+''.join(parts)+'<p><a href="summary.json">全部指标</a> · <a href="verification.json">包级见证</a> · <a href="window-comparison.json">跨窗口核对</a> · <a href="protocol.md">执行前方案</a></p></html>')
    print(json.dumps(dict(report=True,conditions=4,output=str(out))))


if __name__=='__main__':main()
