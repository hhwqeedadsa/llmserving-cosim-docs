"""Chinese standalone report generated from checked logical evidence."""
import argparse
import html
from pathlib import Path

from .study import load,save


def render(lines):
    body=[];table=False
    for line in lines:
        if line.startswith('|'):
            if set(line.replace('|','').replace('-','').replace(':','').strip())==set():continue
            cells=[s.strip() for s in line.strip('|').split('|')]
            if not table:body.append('<div class="scroll"><table>');table=True
            body.append('<tr>'+''.join('<td>'+html.escape(c)+'</td>' for c in cells)+'</tr>')
        else:
            if table:body.append('</table></div>');table=False
            if line.startswith('# '):body.append('<h1>'+html.escape(line[2:])+'</h1>')
            elif line.startswith('## '):body.append('<h2>'+html.escape(line[3:])+'</h2>')
            elif line:body.append('<p>'+html.escape(line)+'</p>')
    if table:body.append('</table></div>')
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<title>Hybrid通信：动态轮询是否必要</title><style>body{max-width:1080px;margin:40px auto;padding:0 24px;'
        'font:16px/1.9 system-ui;background:#f3f6fa;color:#193249}h1{font-size:29px}h2{margin-top:36px;font-size:21px}'
        'p{max-width:100ch}table{border-collapse:collapse;width:100%;background:white}td{padding:12px;border-bottom:1px solid #d9e3ec}'
        'tr:first-child{background:#ddeaf6;font-weight:600}.scroll{overflow-x:auto}</style>'+''.join(body)+'</html>\n')


def execute(run,output):
    assert not output.exists();output.mkdir(parents=True)
    v=load(run/'verification.json');assert v['passed']
    p=load(run/'analysis/predictions.json');control=p['hypothetical_parameter_control']
    lines=['# Hybrid通信：哪些动态细节可以用普通任务图表达','',
        '结果：在本轮固定条件下，单个转发器的有效数据块、阻塞来源和结束条件，与保留进度及结束通知的普通静态任务图一致。没有发现必须靠动态轮询才能表达的数据顺序变化。这个结果排除了一个过宽的创新理由；没有测出新的推理收益或仿真加速。','',
        '## 1. 用人话说明问题','',
        '不同服务器把数据送到接收入口后，转发器轮流查看各个来源。它会先选一个来源，然后等这个来源有数据或声明结束；其他来源先到，也不直接跳过去。我们检查：究竟需要逐次模拟这些查看动作，还是事先列好数据块和等待条件就能表达相同结果？','',
        '正确的普通任务图不能只写“发多少字节”。它要保留每个来源的有序数据、每6项的进度，以及最后的结束通知。即使没有数据，或者长度刚好是6的倍数，结束通知也不能省掉。','',
        '## 2. 实际完成了什么','',
        '| 检查 | 范围 | 结果 |','|---|---|---|',
        f"| 有限状态核查 | {v['exhaustive_roots']:,}个根条件，{v['states']:,}个联合状态，{v['edges']:,}条非平凡转换 | 全部一致 |",
        '| 公开平台逻辑回放 | 13个人工输入和1个真实路由前缀，每个3种到达/消费策略 | 42次一致 |',
        '| 输入与源码 | 15份完整路由、6份固定库源码，Git tree/blob/SHA核对 | 通过 |',
        '| 独立核验 | 按块序号排序生成另一份参考；核对原token、通道、slot、数据ID、等待来源和最终前缀 | 通过 |',
        '| 证据完整性 | 修改门槛、来源、数据ID、通道、最终前缀和动作身份 | 6类全部拒绝 |','',
        '| 来源数 | 根条件 | 联合状态 | 非平凡转换 |','|---|---:|---:|---:|']
    for d,r in v['families'].items():lines.append(f"| {d} | {r['roots']:,} | {r['states']:,} | {r['edges']:,} |")
    lines+=['','真实输入为DeepSeek-R1-AWQ/MMLU。用户已确认新增13份与原4份来自相同目录；17份中899和1479缺层，本轮沿用15份完整原件。远端数据修订未独立核实，原件没有真实到达时间。','',
        '真实条件声明EP16、2服务器域×8卡、256专家、Top8、隐藏维7168、BF16；每份文件entry0/层3各取最多16个原token，按文件数字顺序绑定rank0～14，rank15保持空。只检查ingress0/channel0，其两个来源为rank0和rank8，共14/16份目标域数据。其他rank输入用于生成完整逻辑计划，没有声称回放了所有转发器。','',
        '三种环境为：全部进度先发布，再消费；来源逆序，每次发布后消费到阻塞；来源交替，每次发布只尝试消费一次。发布时刻与调用次序由实验声明，不是GPU或网络测量。','',
        '## 3. 为什么这次一致','',
        '固定源码的默认发布步长与消费上限均为6。非最终进度只在6的倍数处前进，故每次有效消费都是下一块6项，或者仅在结束已知后消费最后不足6项。缓存旧进度可能让转发器多等一会儿，但不会凭空产生另一个大小的数据块。','',
        '归纳说明：各来源的消费前缀起始均为0。假设此前有效块一致，下一来源由同一循环顺序确定；缓存有数据时必然提供相同下一块，没有数据时刷新发布，未满足门槛则双方都等待同一来源。最后余数由结束放行。已知结束且无数据的来源可跳过；未知结束的等待必须保留。于是消除零工作轮询后，两者下一有效块和阻塞来源一致。','',
        '这是对所转录单转发器逻辑的解释和有限检查，不是对完整CUDA程序、弱内存序或任意参数的形式化证明。转换hash是确定性复核凭据，不是证明证书。','',
        '## 4. 哪些条件下不能直接套用','',
        '另设一个抽象控制：每5项发布进度，每次最多消费6项，两来源长度为1和10。早消费时的有效块为来源1[0:5]、来源0[0:1]、来源1[5:10]；等全部发布后消费，则为来源1[0:6]、来源0[0:1]、来源1[6:10]。总数据相同，块边界不同。','',
        '这个反例说明，不能不检查参数关系就预定数据块。它不是当前默认部署、不修改或运行DeepEP库，也不是网络性能改善。还有跨转发器对最终GPU slot的竞争、真实轮询成本、Combine归约和数据可见性，均不在本次结论范围内。','',
        '## 5. 对两个创新候选的影响','',
        '候选一继续要求证明：更精简的通信表示，在正确分层模型和完整普通任务图之外，能带来实际决策可靠性或净表示/执行成本优势。本轮单转发器的动态轮询规则不足以支持这项创新。','',
        '候选二继续要求证明：在实际共享网络、反馈和应用门槛之间，哪些事件能安全省略，以及完整流程能省多少宿主时间/内存。普通静态化、缓存或删去无工作轮询本身都不是论文贡献。本轮没有运行网络、GPU或性能计时，不能给收益率。','',
        '下一项有依据的工作是另行限定跨转发器资源/slot分配是否随实际完成顺序变化，并与已有可变绑定DAG比较；若普通方法仍能解释，就正常排除，不扩条件制造正例。总体目标和两项论文创新均保持进行中。','',
        '平台固定19670ba；DeepEP固定f6aa38fe06cd4a0489e7730365d4e0114970a2f3。研究代码只调用公开逻辑CLI，预测仅读取observed，公开回放置于truth供核验；本阶段没有修改平台、依赖或执行下载、构建、GPU、网络、push。','']
    (output/'report.md').write_text('\n'.join(lines))
    (output/'report.html').write_text(render(lines))
    save(output/'metrics.json',dict(exhaustive_roots=v['exhaustive_roots'],states=v['states'],edges=v['edges'],
        public_replays=len(v['public_replays']),hypothetical_parameter_control=control,
        measured_speedup=None,measured_inference_benefit=None,paper_innovations_established=0))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();execute(a.run.resolve(),a.output.resolve())
