"""Publish recovery opportunity evidence and accurate TCP/RoCE model boundaries."""
import argparse
import html
import json
from pathlib import Path
import shutil


def load(p):return json.loads(Path(p).read_bytes())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();stage,out=a.stage.resolve(),a.output.resolve()
    analysis,proof=load(stage/'analysis.json'),load(stage/'verification.json')
    assert analysis['passed'] and proof['passed']
    out.mkdir(parents=True,exist_ok=False)
    for name in ('analysis.json','verification.json'):shutil.copyfile(stage/name,out/name)
    shutil.copyfile(stage/'input/sources/sources.json',out/'sources.json')
    rows=[]
    for name,c in analysis['cases'].items():
        rows.append([name,str(c['trigger_counts'].get('timeout',0)),str(c['trigger_counts'].get('fast_retransmit',0)),
            str(c['complete_batches']),str(c['max_first_send_wait_ns']),str(c['other_QP_occupied_ns']),str(c['max_batch_span_ns'])])
    md='\n'.join('| '+' | '.join(row)+' |' for row in rows)
    prefixes=[analysis['cases'][n] for n in ('prefix-w20','prefix-w60')]
    count=sum(sum(c['trigger_counts'].values()) for c in prefixes)
    qualified=all(c['complete_batches']==sum(c['trigger_counts'].values()) and
      c['max_first_send_wait_ns']==0 and c['other_QP_occupied_ns']==0 and
      c['same_ns_feedback_ambiguous_batches']==0 for c in prefixes)
    conclusion=('两个prefix条件合计'+str(count)+'次恢复触发均在同纳秒发出首个重传，完整缺口批次都在下次反馈前发齐；'
      '批次内没有其他QP在同NIC占线。因此当前输入不支持把“超时之后的源NIC重传优先级”作为优先开发的性能方案。'
      if qualified else '部分批次存在等待、竞争或不完整覆盖；应按原始记录进一步定位，不从首包时间直接下结论。')
    source_links=[(x['name'],x['reference']) for x in load(out/'sources.json')['sources']]
    references='\n'.join('- ['+n+']('+u+')' for n,u in source_links)
    text=f"""# 恢复计时核查：哪些是模型假设，哪里才有调度空间

**{conclusion}**

## 先纠正“计时器漏洞”的说法

收到确认新数据的ACK后重启计时器，是RFC6298第5.3节推荐的TCP行为。上一轮“部分ACK使剩余尾段晚重试”是该机制在特定丢包、路径和ACK时序下的实际表现，本身不构成实现漏洞或新发现的协议缺陷。项目采用了这项思路，不能据此声称整个恢复实现符合TCP标准；17µs和退避参数也不是TCP规范的参数。

Linux v6.12的软件RoCE（RXE）也存在基于当前jiffies与QP超时重设重试计时器的代码：`rxe_comp.c`的`reset_retry_timer()`由completer的EXIT路径调用，只有RC、尚有未确认PSN、非零超时且QP活跃时才设置。这证明“重新设置计时器”有现成实现依据；不能进一步声称RXE每个部分ACK都与我们的状态机相同，更不能推定mlx5固件如此实现。

## 我们的17µs与真实RoCE参数不是同一回事

| 维度 | 当前仿真参考 | 所核查公开实现或资料 | 研究约束 |
|---|---|---|---|
| 序号 | 连续字节偏移、项目内MSR1选择确认 | RXE按PSN与WQE推进 | 不能称逐包复刻RoCE RC |
| ACK计时 | 累计字节ACK推进后重启QP计时 | RFC6298第5.3节；RXE另有状态机条件 | 机制有先例，不作为创新 |
| 超时参数 | 本轮初始17,000ns、倍增到上限、有限重试 | RXE源码按4096ns×2^timeout换算，并舍入为至少一个jiffy；timeout=0关闭该软件计时 | 17µs不能直接当作硬件QP编码或实测值 |
| ACK节奏 | 选择性重传参考反馈及当前模型配置 | RXE请求ACK与消息结束或累计未请求包数有关 | 反馈数量、尾部检测时刻可能不同 |
| 快速检测 | 已有源NACK快速重传和SACK区间跳过 | RFC8985的RACK-TLP另有发送时间、重排序窗口和尾部探测 | SACK或简单超时不等于完整RACK/IRN |
| 传输恢复 | 原生NIC/队列实际重发、真实ACK后完成 | 公开资料不能校准本机固件 | 观察值与硬件一致性分开证明 |

例如RXE公式中的timeout=2与3分别给出16.384µs和32.768µs的名义值；软件jiffies换算还会进一步改变实际期限。这个算例说明参数映射必须明确，不是建议直接把仿真改成其中一个值。

## 发送端究竟有没有等待恢复的队列

这里沿用同一128端点Clos、四EP32组、15份真实专家选择、两层两个Decode步的原始结果，只读分析，不运行新网络。恢复触发既包括超时，也包括实际NACK快速重传。源端缺口只使用该源已经发出的字节以及真正收到的ACK/SACK，未读取接收端数据是否已齐的真值。

| 条件 | 超时触发 | 快速重传触发 | 发齐缺口批次 | 首个重传最大等待ns | 批次内其他QP占线ns | 最大批次含串行化ns |
|---|---:|---:|---:|---:|---:|---:|
{md}

“发齐”只表示触发时源可知缺口的重发已上链路，不表示接收成功或收到ACK。批次串行化是传输本身，不能全算排队；同纳秒首发也不是零网卡硬件处理开销。遇到下一条反馈就截断批次，未发齐或同纳秒顺序不清的情况单独保留。区间有并发时不能把批次占线累计量当成集群可优化时延。

这项检查只针对既有轨迹里的“到期后再给重传排顺序”。它不排除更早检测、跨源共享交换机调度或不同负载下的收益；这些需要新的因果实验。现有吞吐、消息与计算为声明和功能标记，尚未校准完整R1推理。

## 从网络和系统角度怎样继续

1. **暂停把源NIC的到期后恢复优先级当首要算法。** 先有同资源上同时待发的合法候选，才有调度问题；数据已发出后，在等反馈的QP不是待发队列。
2. **补齐比较对象的语义，再实现新机制。** 若需要缩短尾部检测，应与已有按发送时间检测、尾部探测、ACK/超时规则比较；它们本身不是新贡献。策略不能读未来接收状态，也必须计算探测、重传与乱序缓冲成本。
3. **把真正待证的创新放在跨任务共享网络。** 候选是利用当前可得的集合进度与缺失贡献安排受限的网络服务，使故障恢复少拖累其他MoE任务。必须与FIFO、同信息的流大小/年龄/coflow基线比较，并证明进度信息有独立增量；该策略尚未实现，当前结果没有证明收益。

## 本机硬件可见性

当前sysfs记录了{len(analysis['hardware']['sysfs_devices'])}个mlx5设备条目，端口状态见analysis.json；预检的`ibv_devinfo -d mlx5_0 -i 1`返回255，提示找不到设备。设备条目、DOWN状态下的rate字段和当前用户空间可用RDMA设备是不同概念。不能把这些读数当成已建成RoCE链路，也不能据此校准400Gb/s仿真。

## 来源与复核

{references}

完整相关原文行段保存在analysis.json，来源URL、版本、Git blob SHA-1及SHA-256见sources.json。raw GitHub一次访问超时，官方API成功取得同版本文件；没有重试同一失败URL。RXE是软件实现，不是当前设备固件源码。本文未取得商用网卡内部计时状态或两机测量，相关硬件行为保持未知。

独立核查用bytearray和逐字节集合重算重复字节与缺口，并回查实际源物理TX、MSR1反馈及完整批次。它验证记录一致性，不产生反事实业务完成时间。详情见verification.json。网页仅做静态资源检查；整体研究目标仍active。
"""
    (out/'README.md').write_text(text)
    table=''.join('<tr>'+''.join('<td>'+html.escape(v)+'</td>' for v in row)+'</tr>' for row in rows)
    links=''.join('<li><a href="'+html.escape(url,quote=True)+'">'+name+'</a></li>' for name,url in source_links)
    body=f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>恢复计时与可调度机会核查</title>
<style>body{{font:16px/1.8 system-ui,sans-serif;max-width:1120px;margin:32px auto;padding:0 24px;color:#19354c;background:#f5f8fc}}.box{{padding:20px;border-left:5px solid #2869a8;background:#e5effa}}table{{border-collapse:collapse;width:100%;background:white}}th,td{{padding:9px;text-align:right;border-bottom:1px solid #d8e0ea}}th:first-child,td:first-child{{text-align:left}}pre{{white-space:pre-wrap;font:inherit;background:white;padding:20px}}a{{color:#2268a7}}</style>
<h1>恢复慢，先找清楚能改哪里</h1><p>已有128端点Clos结果 · 零新增网络 · 真实源ACK/SACK与实际重发</p>
<div class="box"><strong>{html.escape(conclusion)}</strong><p>部分ACK后重启计时已有标准先例，不能直接叫漏洞；尚无商用网卡内部行为校准。</p></div>
<h2>完整重传批次，而非只看首包</h2><table><tr><th>条件</th><th>超时</th><th>快速重传</th><th>发齐批次</th><th>最大首发等待ns</th><th>其他QP占线ns</th><th>最大批次ns</th></tr>{table}</table>
<p><a href="README.md">详细报告</a> · <a href="analysis.json">逐触发源端证据</a> · <a href="verification.json">独立核查</a> · <a href="sources.json">来源身份</a></p>
<h2>一手资料</h2><ul>{links}</ul><h2>详细解释与改进方向</h2><pre>{html.escape(text)}</pre></html>"""
    (out/'report.html').write_text(body)
    print(json.dumps(dict(published=str(out),prefix_recovery_triggers=count,qualified_no_source_wait=qualified)))


if __name__=='__main__':main()
