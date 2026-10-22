"""Build a readable fixed-study report from public analysis and independent checks."""
import argparse
import html
import json
from pathlib import Path
import shutil


def load(path):return json.loads(path.read_text())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('task','output'):parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args();out=args.output
    assert not out.exists()
    summary=load(args.task/'analysis/summary.json')
    verification=load(args.task/'checked-final/verification.json')
    assert verification['verified'] and summary['conditions']==16
    rows=summary['cases'];effects=summary['paired_effects']
    ranges={}
    for factor in ('depth','np_ns','recovery_ns','cnp_bytes'):
        selected=[v for v in effects if v['factor']==factor]
        ranges[factor]=dict(pairs=len(selected),minimum_delta_ns=min(v['ack_delta_ns'] for v in selected),
            maximum_delta_ns=max(v['ack_delta_ns'] for v in selected),
            minimum_percent=min(v['ack_delta_percent'] for v in selected),
            maximum_percent=max(v['ack_delta_percent'] for v in selected))
    late=sum(s['first_cnp_rx_ns'] is not None for c in verification['cases'] for s in c['stream_feedback_timing'])
    after=sum(s['data_packets_after_first_cnp'] for c in verification['cases'] for s in c['stream_feedback_timing'])
    tx_low=sum(c['tx_samples_below_line_rate'] for c in verification['cases'])
    facts=dict(conditions=16,factor_ranges=ranges,feedback_stream_conditions=late,
        data_packets_after_first_cnp=after,tx_samples_below_line_rate=tx_low,
        independent_counts=verification['counts'],paper_main_contribution_established=False,
        raw_artifacts_volatile=True,persistent_backup=False)
    out.mkdir()
    for name,source in (('summary.json',args.task/'analysis/summary.json'),
                        ('verification.json',args.task/'checked-final/verification.json')):
        shutil.copyfile(source,out/name)
    (out/'findings.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2)+'\n')
    protocol=Path(__file__).with_name('feedback_factorial_protocol.md')
    shutil.copyfile(protocol,out/'protocol.md')
    details='\n'.join('| '+ ' | '.join(map(str,(r['name'],r['acked_ns'],r['finish_ns'],r['unique_destination_ce'],r['cnp_count'],r['peak_switch_queue_bytes'])))+' |' for r in rows)
    text=f'''# 短时 MoE Dispatch 的反馈敏感性：16组结果

本轮完成分因素闭环对照和独立逐包核验，没有提出新算法或证明A类论文主贡献。结果缩小了问题范围：这个短突发不能用来评价拥塞控制的恢复效果；反馈报文本身的网络服务成本则有直接包级证据。

## 输入、网络与限制

四个已见 R1-AWQ/MMLU 请求各取 entry0、层4的前6个真实 token，共24 token；保持真实专家选择，EP4、连续等容量专家放置，每token按远端目的rank去重，共60次写/867,840B/900个源数据包。BF16/direct打包是假设。8端点Clos中使用GPU0/2/4/6，接入100G、上层25G、每链路1us、seed11。不是完整推理或千卡结果，也没有计算、Combine、现代通信库控制或匹配硬件校准。

源深度1/8 × NP间隔5/50us × RP恢复5/55us × CNP模型帧48/512B，共16组；其他参数固定、每条件共享一套真实ns-3网络时钟。没有重跑此前超时的完整Prefill，也没有改变ECN阈值或默认模式。512B是成本敏感性边界，不是生产CNP常态。

## 发现及其证据

1. **深度排序未反转。** depth1的8组最后ACK均157,970ns；depth8为120,675—122,285ns，减少22.59%—23.61%。这是功能额度敏感性，不是经过硬件校准的调优收益。深度8交换机队列峰值约168KB，深度1约34KB；延迟和压力需要一起看。
2. **恢复周期没有改变业务完成。** 8个5→55us配对的ACK差均0，源数据发送时间线也完全相同。{late}个有反馈的“连接×条件”中，第一条CNP到达前自身数据已经全部发出；反馈后再发数据包共{after}个。全部14,400个源数据TX采样均100G（低于线速{tx_low}个），100G是发包时QP设置速率，不是测得的端到端吞吐；即便空闲控制器状态最低降到100Mb/s，也不能说本次数据被该低速限制。此处恢复计时器确实触发，问题在于它没有剩余数据可影响。
3. **反馈字节成本能直接影响数据。** depth8下48→512B使ACK多298—1,490ns；depth1无反馈，变化0。首个分歧在GPU0、stream2、序号43,392：CNP都于10,965ns发出，48B占4ns、512B占41ns，紧随数据分别于10,969/11,006ns发出，差37ns。这是实际网口序列化见证，不是凭相关性猜测。队列和ECN会随成本闭环变化，不能把最终ACK差全部当纯序列化和。
4. **更频繁通知并未更快。** depth8中5us通知产生133/134条CNP，50us产生28条；延长通知间隔使业务ACK少120—1,312ns。通知频率与反向流量同时改变，且反馈对自身数据已迟到，本例不足以说明某个拥塞控制算法更优。
5. **静态出口服务基线依然很强。** 所有条件的数据+必要ACK最忙出口服务下界均107,010.56ns。depth8业务ACK比该下界多13,664.44—15,274.44ns，depth1多50,959.44ns。下界不含释放时序、传播与额外CNP，也不是完整延迟预测；不能把全部剩余时间称可优化损失。
6. **业务结束与物理排空必须分开。** depth8/NP50us中，最后ACK120,675—120,973ns，反馈排空131,605—132,424ns。用后者评价业务会额外计入10,930—11,451ns的反馈尾部。

## 16组原始摘要

| 条件（d深度/n通知ns/r恢复ns/b帧B） | 最后ACK ns | 物理排空 ns | 唯一目的CE包 | CNP | 队列峰值 B |
|---|---:|---:|---:|---:|---:|
{details}

## 核验、可复现与论文去留

独立checker从四个原文件重新构造60次写，另按端点UID关联数据/ACK/CNP，重算ECMP路径及端口字节守恒。共核对14,400个数据包、14,400个ACK、646个CNP、640个物理端口条件、960个工作回调、20,436条RP状态。端点捕获不是所有交换跳的逐包抓包；逐跳总量由独立路径账与实际端口统计互证。平台功能oracle已经独立验收，本轮不冒称硬件准确性验证。

所有16组网络均完成，无失败重试、扩限、丢包、重传或PFC暂停。执行命令、退出状态、输入和binary hash在原始目录run/commands.json、各inputs.json及monitor中；本报告不是新的网络执行。复现入口见仓库experiments/paper_readiness/README.md。

本轮是一次有用的负面筛选：不继续在短突发上搜索恢复参数收益；后续候选必须先证明实际数据发送跨过拥塞反馈到达，并区分应用信用、共享网口和交换机瓶颈。独立真实负载、匹配硬件校准、现代通信库语义和完整规模仍缺，不能把本轮当作新机制论文。此前完整Prefill的超时结论不因这60次写被改写。

原始记录与运行时检查点仅在易失的/dev/shm/moe-feedback-factorial-20261022，尚无持久备份；Git保存源码、方案和小摘要。论文目标保持进行中。
'''
    (out/'report.md').write_text(text)
    body=[]
    for line in text.splitlines():
        if line.startswith('# '):body.append('<h1>'+html.escape(line[2:])+'</h1>')
        elif line.startswith('## '):body.append('<h2>'+html.escape(line[3:])+'</h2>')
        elif line.startswith('|'):
            if line.startswith('|---'):continue
            cells=[html.escape(s.strip()) for s in line.strip('|').split('|')]
            body.append('<tr>'+''.join('<td>'+s+'</td>' for s in cells)+'</tr>')
        elif line.strip():body.append('<p>'+html.escape(line).replace('**','')+'</p>')
    # Place each consecutive table-row sequence inside one scrollable table.
    wrapped=[];in_table=False
    for item in body:
        if item.startswith('<tr>') and not in_table:wrapped.append('<div class="table"><table>');in_table=True
        if not item.startswith('<tr>') and in_table:wrapped.append('</table></div>');in_table=False
        wrapped.append(item)
    if in_table:wrapped.append('</table></div>')
    (out/'report.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MoE反馈分因素实验</title><style>body{max-width:1120px;margin:40px auto;padding:0 20px;color:#203047;background:#f6f8fb;font:17px/1.85 system-ui,sans-serif}h1{font-size:30px}h2{margin-top:36px;font-size:23px;color:#164a7b}p{background:white;border-radius:8px;padding:14px 18px}.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px;background:white}td{border:1px solid #dbe2ec;padding:7px;text-align:right;white-space:nowrap}td:first-child{text-align:left}tr:first-child{font-weight:bold;background:#e4edf8}a{color:#1564a5}</style>'+''.join(wrapped)+'<p><a href="summary.json">全部指标与32个配对</a> · <a href="verification.json">独立核验与37ns见证</a> · <a href="protocol.md">执行前方案</a></p></html>')
    print(json.dumps(dict(report=True,output=str(out),conditions=16)))


if __name__=='__main__':main()
