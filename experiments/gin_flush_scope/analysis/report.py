"""Readable Chinese report of source applicability, not a performance claim."""
import argparse
import html
import json
from pathlib import Path


def execute(stage, output):
    value=json.loads((stage/'analysis.json').read_bytes())
    verification=json.loads((stage/'verification.json').read_bytes())
    assert verification['passed']
    output.mkdir(parents=True,exist_ok=True)
    lines=[
        '# GIN完成等待：哪些能成立，哪些不能外推',
        '',
        '2026-10-27；固定DeepEP与NCCL源码，零GPU、包级网络或性能测量。',
        '',
        '**结论：共享context的完成等待可以覆盖其他通道的已预约请求；但“先等前一个peer，再采样后一个peer”的同lane链并非所有Hybrid配置都有。紧凑rail连接下，EP64/128/256每lane最多扫描一个peer。固定Hybrid又限制最多32个跨机域，8卡/域下不能直接外推到单EP512或EP1024。**',
        '',
        '## 1. 用人话解释等待对象',
        '',
        '一个通道做完自己的发送，不代表它所用的共享发送队列只装了自己的任务。GDAKI按context和provider peer找到队列，再读取已经预约到的位置并等完成。若另一通道已在同一队列预约了更后面的请求，本通道的flush可能一起等它。不同context不因这个采样自动互相等待；仍可能共享NIC、链路等物理资源。',
        '',
        '这里必须区分三件事：flush的API保证、所核GDAKI实现覆盖的请求、MoE应用的完成。文档说put的flush保证本地源buffer可复用，并不单独保证远端应用已消费数据。Hybrid Combine在flush后还发送各通道结束信号，再等待应到信号；不能删掉后两步，也不能把一次flush当整个任务完成。',
        '',
        '声明2SM、每SM2个forward通道且scale-up warp同数的分配示例：可用context索引1/2/4个时，最多分别由4/2/1个forward通道共享一个索引。这是源码分配规则检查，不是实际GPU启动、NIC数、集群QP总数或性能。索引还要拆成provider connection/context身份。',
        '',
        '## 2. 总卡数不能直接决定等待方式',
        '',
        'GIN实际扫描的peer数由EP和ginConnectionStride共同决定。coop是warp，每lane按32跳着扫描。下表源GPU是world rank0，活动远端数据peer只取其rail中的其他域；初始屏障、自peer或其他控制仍可能产生额外请求，表中活动集合不是全部通信。',
        '',
        '| EP | 声明stride | provider peer数 | 每lane最多扫描 | 同lane最多活动远端rail peer | 同lane两个活动peer的串行采样链 |',
        '|---:|---:|---:|---:|---:|:---:|',
    ]
    for row in value['configurations']:
        lines.append('| {ep} | {stride} | {provider_peers} | {max_lane_scan_count} | {max_remote_rail_peers_per_lane} | {mark} |'.format(
            **row,mark='可能' if row['same_lane_late_active_peer_exists'] else '无此同lane链'))
    lines += [
        '',
        'stride8是本报告声明的紧凑rail配置。stride1仅是条件化的展开provider配置：DeepEP host正常选择RAIL；使用RDMA storage时可选择CUSTOM_STRIDE，而实际gin_min_stride来自环境。本机没有取得该运行值，所以不能声称真实Hybrid默认stride1，也不能只用这一组造出等待。',
        '',
        '紧凑rail下没有上述多轮同lane链，不等于所有peer在一个全局原子时刻完成采样，更不等于不同通道互不等待。GIN包装层在provider调用前后执行coop.sync，函数内的各lane采样仍不是全局原子快照；本轮未执行真实GPU指令调度。',
        '',
        '## 3. 一个有限例子说明为什么不能冻结入口快照',
        '',
        '限定一个lane确有两个要扫描的peer。A在入口读第一个队列后，等待自己的旧请求；第二个队列起始为空。并发生产者B可以在此期间向第二个队列预约请求。A等完第一个再读第二个时，可能把B的新请求也纳入等待。',
        '',
        '仅枚举满足“先完成第一个，再采样第二个”“B先预约，再完成”的六种合法顺序：5种会把B纳入采样目标，其中2种在采样时B尚未完成；1种在A读完第二个后B才预约，因此不纳入。这些是有限偏序计数，没有事件发生概率，不能写成83%的故障率或33%的慢请求。',
        '',
        '冻结flush入口时刻的快照会漏掉该条件链。正确的动态资源DAG在采样事件发生时绑定实际目标ticket，能表达同样的依赖；普通flushAsync也早已提供逐peer请求快照。本轮不能据此宣称任务图表达不了MoE、提出新协议或证明仿真创新。',
        '',
        '事件例子仅转录预约与完成的有限关系；没有执行真实GDAKI、描述符准备、发布/门铃、CQ轮询、内存序或完整Hybrid。没有死锁、漏洞、网络长尾和应用收益结论。',
        '',
        '## 4. 千卡应怎样定义',
        '',
        '固定Hybrid Dispatch/Combine的跨机域数有不大于32的编译约束。若每域8卡，则单组上限为256卡；单EP512和EP1024分别需要64、128域，超出这条固定源码路径。该限制属于所核版本与内核，不能外推成所有MoE库都无法支持千卡。',
        '',
        '千卡网络可以研究多个合法EP组共享网络，例如把1024卡声明为4组EP256，再明确DP副本、请求分配、专家复制和组间是否通信；这只是可选部署方案，尚未在本轮执行。不能把4组小EP或256组EP4运行称为一个EP1024完整模型通过。若要单EP1024，需要另选有依据的算法/库或修改算法，并重新验证通知、rank位图、布局和完成条件，不能只把卡数参数改大。',
        '',
        '## 5. 对网络研究和两项候选的影响',
        '',
        '研究场景须先记录EP/DP组、域内GPU数、GIN实际stride、共享context、connection/NIC绑定，再讨论拥塞或恢复怎样拖住通道完成。总卡数相同但这些配置不同，会改变进入网络的请求和等待关系；只比较链路带宽不足以解释。',
        '',
        '现有ns-3/阿里RDMA模型擅长包和队列，ASTRA-sim或通用资源DAG可以保留依赖，CLEM的真实库路径已包含持久QP与CQ反馈。新仿真器的候选价值必须是在正确强基线之外，以更低处理成本保留会改变网络配置选择的依赖。本轮只限定了应保留的完成边界，没有测到新的决策收益或宿主提速，两个主创新仍未成立。',
        '',
        '下一步如果实施完整Hybrid，必须使用输入驱动的Dispatch、域内转发、专家就绪与Combine，按实际反馈推进同一网络时钟，并保留源buffer寿命和各通道信号。研究通道数、context数与网络配置时，要把普通调参、完整动态DAG和逐peer等待列为强基线。当前无收益率或可优化时延上界，API计数空间不能拿来补这个空缺。',
        '',
        '## 6. 可复核来源',
        '',
        '7份缓存源码/文档的tree-entry、Git blob和SHA256已检查，18处锚点及6配置/6偏序均由独立方法核对。四类篡改被拒绝。原固定ref来源边界保持，未重新下载或补远端commit根身份。',
        '',
    ]
    for claim in value['source_claims']:
        lines.append('- [{path}:{line}]({url})：`{anchor}`'.format(**claim))
    lines += ['', '原件：`runs/gin_flush_scope_20261027/`；冻结协议、核验、归档恢复与资源收尾见本实验目录。零网络、GPU、HTTP、原生构建、平台/依赖更改及push。', '']
    markdown='\n'.join(lines)
    (output/'report.md').write_text(markdown)
    # Keep HTML self-contained and literal; Markdown source is available beside it.
    parts=[]
    table=[]
    for line in lines:
        if line.startswith('|'):
            if line.startswith('|---'):
                continue
            cells=[html.escape(c.strip()) for c in line.strip('|').split('|')]
            tag='th' if not table else 'td'
            table.append('<tr>'+''.join('<'+tag+'>'+c+'</'+tag+'>' for c in cells)+'</tr>')
            continue
        if table:
            parts.append('<div class="table"><table>'+''.join(table)+'</table></div>')
            table=[]
        if not line:
            continue
        if line.startswith('# '):
            parts.append('<h1>'+html.escape(line[2:])+'</h1>')
        elif line.startswith('## '):
            parts.append('<h2>'+html.escape(line[3:])+'</h2>')
        elif line.startswith('**'):
            parts.append('<p class="result">'+html.escape(line.strip('*'))+'</p>')
        else:
            parts.append('<p>'+html.escape(line)+'</p>')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>GIN等待范围与千卡约束</title><style>body{max-width:1100px;margin:32px auto;padding:0 22px;background:#f3f6f9;color:#183143;font:17px/1.9 system-ui}h1{font-size:29px}h2{margin-top:34px}.result{padding:18px;background:#dfeee9;border-left:5px solid #327e63}.table{overflow-x:auto}table{border-collapse:collapse;width:100%;background:white;font-size:14px}td,th{border:1px solid #c5d1da;padding:10px;text-align:left}p{overflow-wrap:anywhere}a{color:#076a9b}</style>'''+''.join(parts)+'<p><a href="report.md">文字版与可点击来源</a> · <a href="key_metrics.json">完整数据</a></p></html>'
    (output/'report.html').write_text(page)
    (output/'key_metrics.json').write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    execute(args.stage.resolve(),args.output.resolve())
