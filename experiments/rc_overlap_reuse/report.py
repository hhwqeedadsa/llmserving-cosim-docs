"""Offline Chinese report from independently verified summaries."""
import argparse
import html
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_bytes())


def execute(stage, output):
    assert not output.exists()
    v = read(stage/'verification-with-witness.json')
    p = read(stage/'prediction.json')
    assert v['passed'] and not v['new_innovation_proven']
    terminal,gated = v['cases']['terminal'],v['cases']['gated']
    difference = gated['loss_finish_ns']-gated['normal_finish_ns']
    reference = 100*difference/gated['loss_finish_ns']
    error = 100*abs(gated['diagnostic_finish_error_ns'])/gated['loss_finish_ns']
    table = []
    for label,row in [('终端回复',terminal),('带后继的回复',gated)]:
        table.append((label,f"{row['normal_finish_ns']/1000:.3f}",f"{row['loss_finish_ns']/1000:.3f}",
            f"{(row['loss_finish_ns']-row['normal_finish_ns'])/1000:.3f}",
            '准入且逐项吻合' if row['admitted'] else '事前拒绝、详细重算',str(row['diagnostic_finish_error_ns'])))
    text = f'''# 网络还在忙，是否一定要全部重算

本轮实现并检验了普通端口/时间非干扰证书：终端回复丢失时，全网尚未排空仍可复用；回复控制后续发送时，事前拒绝。本轮没有形成两个新论文贡献，也没有测得完整宿主提速。

## 本轮结果与收益空间

| 构造程序 | 正常/μs | 丢回复/μs | 净差/μs | 复用判断 | 普通RTO平移完成误差/ns |
|---|---:|---:|---:|---|---:|
'''
    text += ''.join('| '+' | '.join(row)+' |\n' for row in table)
    text += f'''
终端条件的故障连接晚完成14μs，但整体仍为51.093μs，改善这个连接不会使本片段提前完成：在该对照目标上收益为0。重试在28.835μs开始、40.901μs完成，另一连接直到51.093μs才完成。全局空闲规则会拒绝；普通端口/时间规则发现12次新增逐跳发送都避开原端口服务，完成、内存、旧值、副作用及全部尾部时刻与包级执行一致。850次原逐跳TX可保留，但不等于少处理850个全部后端事件，也不是CPU加速比。

门槛条件正常50.548μs、故障51.794μs，净差{difference/1000:.3f}μs。若只讨论恢复到正常对照，对应故障运行时间的{reference:.2f}%；这是这两个对照之间的差额，不是全网络可优化空间的数学上界，更不是已取得收益。普通RTO平移参考的整片段误差只有52ns（{error:.3f}%）；不能因为它不完全精确就宣称有很大的新模型价值。

## 未丢包连接为什么反而更快

gated中，连接1的第一个原子通知从25.655μs提前到25.562μs，提前93ns。其前一WRITE仍在13.496μs完成。实际请求的源网卡等待由17ns降为0；交换机4/5/8/7四个输出各少等19ns，最后一交换机本来没有等待。17+4×19=93ns，回复侧没有新增等待。占用者均是故障连接2原本会更早发送的数据；前置回复丢失推迟了这些数据。这不是凭总体相关性推断，逐跳占用者、PSN及服务起止保存在独立核验中。后续阶段又发生竞争，连接1最终只提前2ns。不能把局部93ns累计成可消除的任务损失。

## 输入、网络与可用范围

1808真实原件数组0/层3/token0的专家为9、69、75、77、167、197、204、2；EP4连续64专家/rank、源rank0，目的rank去重为1/2/3。H7168、声明BF16，每目的14,336B、总43,008B功能数据。其他两连接各拆为两个7,168B WRITE并逐段通知；连接2保留完整WRITE，或在前面增加一个必须完成的原子门槛。分段/门槛是人为协议依赖微基准，不是固定现代DeepEP执行或真实请求时刻。没有Combine、专家计算、LLMServingSim组批或GPU。

4台单卡服务器、单rail，经两组ToR/Agg和共享Core，全部100G/1000ns；GPU类型NONE。MTU256、RTO14μs、8MiB buffer、ECN kmin32KiB。固定速率RC、每QP一个在途WQE、ICRC占位。没有现代RoCE/DCQCN反馈、完整Hybrid、硬件校准或千卡执行；这轮不能回答训练热点或生产故障频率。

## 方法、强基线与正确性边界

先执行两个正常网络并独立核验，再只读observed生成预测，在两故障网络前封存hash。普通证书要求终端回复、唯一树路径、无反馈与完整捕获；逐项检查新增请求/缓存回复的输出服务与原服务严格分离，以全部端口峰值之和加新增包保守限制ECN/MMU。任一不足就回退。它是普通保守资源方法，第一轮的“全网已空”只是更窄的准入条件；放宽该条件本身没有证明MoE独有创新。

初次评价后的源码复查发现还须限制NIC轮询游标：新增发送即使不碰撞，也可能改变未来多个QP的选择。现增加“重试以后源NIC至多一个其他请求发送者”的保守前提，保留执行前源码。加固后的预测与故障前封存文件逐字节相同；这项额外审查不冒充完全事前盲测，也没有重跑网络。两条件最终旧值均0、目标数据各写一次、通知各生效一次。独立解包/目标内存重放、五类篡改拒绝及逐跳等待分解通过。

普通RTO平移只是诊断参考；不能把它当最强队列方法。本轮准入证书本身应进入以后方法的通用强基线。CLEM已有真实CCL/QP/CQ反馈，ASTRA已有DAG与ns-3；不是只有本项目能表达后继或共享资源。

## 对两个研究候选的决定

1. 为网络配置选择必要精度：这轮的93ns局部差异和0.100%的整片段诊断误差不足以证明显著决策收益，不开发针对该小例的新预测算法。必须在正确分层通信和普通逐链路/资源图基线上找到会改变方案选择的实质误差，才能继续主张。
2. 带适用性检查的增量重仿真：已把全网空闲限制扩展成局部资源/时间证书，并保留带后继的回退。下一真正门槛是包含缓存准备、检查、回退和输出的完整配对宿主成本，以及相对普通增量执行的额外价值；当前没有加速结论。不能以一次准入、保留850条记录或14μs模拟等待换成性能收益。

两个方向分别服务“可靠选择网络方案”和“减少方案变化后的重复仿真”。本轮只推进第二方向的可执行参考，第一方向没有得到新的正面收益证据。四条件各执行一次，额度关闭，不追加有利参数。源码、实际执行、失败/拒绝范围与复现方法见本目录README和protocol。
'''
    output.mkdir()
    (output/'report.md').write_text(text)
    def esc(value):
        return html.escape(str(value))
    headers = ('构造程序','正常 μs','故障 μs','净差 μs','事前复用判断','普通平移误差 ns')
    rows = ''.join('<tr>'+''.join('<td>'+esc(c)+'</td>' for c in row)+'</tr>' for row in table)
    # Static scientific result: real final completion bars, with a fixed zero axis.
    bars = []
    max_ns = 60000
    for label,mode in [('终端回复','terminal'),('带后继回复','gated')]:
        for condition in ('normal','loss'):
            states = read(stage/(f'observed/{mode}/run/result.json' if condition=='normal' else f'truth/{mode}/result.json'))['rc_concurrent']['connections']
            for q in states:
                end = q['operations'][-1]['completed_ns']
                bars.append(f'<div class="barrow"><span>{label} · {"正常" if condition=="normal" else "故障"} · QP{q["id"]}</span><div class="track"><i style="width:{100*end/max_ns:.5f}%;background:{"#2b6fbb" if condition=="normal" else "#ca7636"}"></i></div><b>{end/1000:.3f} μs</b></div>')
    witness = v['peer_wait_witness']
    witnesses = ''.join(f'<tr><td>{n["node"]}/{n["port"]}</td><td>{n["waiting_ns"]}</td><td>{f["waiting_ns"]}</td><td>'+esc(', '.join(f'QP{b["connection"]} PSN{b["psn"]}' for b in n['blockers']) or '无')+'</td></tr>' for n,f in zip(witness['normal'],witness['loss']))
    paragraphs = []
    for section in text.split('\n\n'):
        if section.startswith('# '):
            continue
        if section.startswith('## '):
            paragraphs.append('<h2>'+esc(section[3:])+'</h2>')
        elif section.startswith('| '):
            continue
        else:
            paragraphs.append('<p>'+esc(section).replace('\n','<br>')+'</p>')
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    page += '<title>忙碌网络中的故障复用</title><style>body{max-width:1120px;margin:32px auto;padding:0 22px;font:16px/1.8 system-ui,sans-serif;color:#243347;background:#f6f8fb}main{background:white;padding:28px;border-radius:12px}h1,h2{line-height:1.4}h2{margin-top:32px}table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:9px;text-align:left;border-bottom:1px solid #dce3ed}a{color:#286aa6}.notice{padding:16px;background:#fff5db;border-left:4px solid #ca7636}.bars{padding:18px 0}.barrow{display:grid;grid-template-columns:240px 1fr 100px;gap:10px;align-items:center;font-size:13px}.track{background:#eff3f8;height:14px}.track i{display:block;height:100%}b{font-weight:500}.scroll{overflow:auto}@media(max-width:650px){main{padding:15px}.barrow{grid-template-columns:145px 1fr 85px;font-size:11px}}</style><main>'
    page += '<h1>网络还在忙，是否一定要全部重算</h1><p>2026-10-28 · 真实选择来源 + 构造协议片段 · 四个包级对照</p>'
    page += '<p class="notice">普通资源方法可准确复用一个忙碌网络分支；带后继的分支回退。尚未形成两个论文创新，也没有完整宿主提速证据。</p>'
    page += '<div class="scroll"><table><tr>'+''.join('<th>'+esc(h)+'</th>' for h in headers)+'</tr>'+rows+'</table></div>'
    page += '<h2>最终操作完成时间：从0开始，横轴统一为0～60 μs</h2><div class="bars">'+''.join(bars)+'</div>'
    page += '<h2>93ns等待减少的逐跳证据</h2><table><tr><th>节点/端口</th><th>正常等待/ns</th><th>故障等待/ns</th><th>原占用者</th></tr>'+witnesses+'</table>'
    page += ''.join(paragraphs)
    page += '<p><a href="verification.json">独立核验</a> · <a href="recovery.json">离线恢复</a> · <a href="closeout.json">资源与收尾</a> · <a href="../../protocol.md">冻结方法</a> · <a href="../../README.md">使用说明</a></p></main></html>'
    (output/'report.html').write_text(page+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    execute(args.stage.resolve(),args.output.resolve())
