"""Report completed measurements without converting an interrupted plan into success."""
import argparse
import html
import json
from pathlib import Path


def read(p):return json.loads(p.read_bytes())


def execute(stage,output):
    assert not output.exists()
    v=read(stage/'verification.json'); assert v['passed'] and not v['complete'] and v['engineering_gate'] is None
    p=v['pairs'][0]; cpu_saved=100*(1-p['reuse_cpu_ns']/p['direct_cpu_ns'])
    wall_saved=100*(1-p['reuse_wall_ns']/p['direct_wall_ns'])
    values=[('直接执行',p['direct_cpu_ns']/1e9,p['direct_wall_ns']/1e9,0,6,0),
            ('准备缓存后复用/回退',p['reuse_cpu_ns']/1e9,p['reuse_wall_ns']/1e9,2,3,3)]
    text=f'''# 复用正常网络结果，完整成本到底省不省

**比较未完成：计划三对，只完成第一对。** 首对包含11个实际网络执行；六个故障条件的完成、内存、原子旧值、目标副作用及包数摘要完全一致。第二个工作流后的计时核验因整数取整错误失败，按事前规则停止剩余两对；只修正离线核验，没有补跑或重开额度。

## 已完成一对的真实原数

| 方法 | CPU/秒 | 墙钟/秒 | 正常缓存执行 | 故障详细执行 | 直接复用查询 |
|---|---:|---:|---:|---:|---:|
'''
    text+=''.join(f'| {name} | {cpu:.6f} | {wall:.6f} | {normal} | {fault} | {reuse} |\n' for name,cpu,wall,normal,fault,reuse in values)
    text+=f'''
CPU比为{p['cpu_ratio']:.3f}倍，对应首对CPU减少{cpu_saved:.2f}%；墙钟比{p['wall_ratio']:.3f}倍，对应减少{wall_saved:.2f}%。这是仿真程序在宿主上的成本，不是被模拟GPU推理加速。正常结果缓存从零建立，两个正常执行、读取/哈希、六次证书检查、三次回退和摘要输出都计入reuse。一次共同的原件接收/配置生成以及已经完成的编译不在两方计时内。

三项terminal最终回复均准入，三项gated非终端回复均回退。故而不是少执行三次网络：从direct六次变为reuse的两次正常加三次回退，总共只少一次（6→5）。每个公共CLI又包括启动、输入检查、内部资源监控、原生运行及序列化；不能把这个完整差额全部归因于包事件减少，也不能把模拟中的RTO微秒数换成宿主CPU节省。

## 为什么不能写成已证明提速

本轮“冷启动”只表示正常结果缓存重新生成，没有清理操作系统页缓存。首对顺序direct→reuse，后者可能受库/文件页已热影响；原定后两对的交错没有执行。工作流仅约1～2秒，启动和监控成本明显。单对没有方差/稳定性证据，不能声称统计显著，不能外推更大batch、千卡、生产查询比例或其他仿真器速度。

事前门槛是三对CPU比全部大于1.05且中位数至少1.10。现在缺两对，判据为“未评价”（JSON为null），而非通过或失败；不把一个1.085倍写成三对中位数。已完成数据支持成本可测、存在有限收益的线索，不支持独立论文贡献或默认推广。

## 停止原因与修正

Linux wait4返回浮点秒，reuse原数user=0.897871、system=0.22969699999999998。测量程序按最近纳秒保存为1,127,568,000ns；初版独立核验器用十进制直接截断，错误得到1,127,567,999ns。我已将核验改为同一已声明单位下的四舍六入五成双整数取整，保留执行前源码与失败日志。原始测量、工作流、输入、顺序与网络结果均未修改，后四个工作流标记为未执行，零补跑。纳秒是存储单位，不表示wait4真的具有纳秒分辨率。

修正后的独立核验重新解包全部11个RC物理/端点记录、目标内存与PSN，并重算原始rusage；五类CPU/墙钟/先后/完成/内存篡改均拒绝。直接流程没有可选Fabric捕获，但固定RC驱动自身每次物理发送/接收仍完整保存，达到80,000条会报错而不是截断；没有伪造可选捕获的完整标志。两流程需要的摘要相同，原完整记录只用于评价。

## 输入和模型边界

6447原件数组0/层3/token0的Top8为50、68、92、132、147、194、218、35。EP4连续64专家/rank，源0，远端rank去重为1/2/3。H7168、声明BF16，每目的14,336B，总43,008B功能字节。分段通知及前置原子门槛是构造协议微基准，只有选择来自真实记录，不是实际现代DeepEP、完整MoE服务或LLMServingSim组批。

四台单卡服务器，单rail，两组ToR/Agg与共享Core；全部100G/1us，GPU类型NONE。MTU4096、RTO25us、8MiB buffer，ECN kmin32KiB/kmax128KiB；固定速率RC、每QP一项在途WQE、ICRC占位。没有GPU/硬件校准、现代拥塞反馈或完整Hybrid。六查询是三个终端回复和三个非终端回复，配置hash不同，不是重复同一故障来让直接基线反复执行。

## 对研究方向的实际影响

这轮将“少执行若干记录”推进到可核对的完整CPU/墙钟成本；它也表明缓存准备和回退不可免费。对这组查询，准入三项后只少一次网络，收益空间远小于命中率看起来的50%。查询组合、每种网络实际成本、已有正常缓存是否需要生成，都会改变净收益；简单次数不是普遍性能上界。

第一候选仍是为Scale-Out网络配置选择必要精度，必须在正确分层通信/逐链路资源图基线上证明有实质决策价值，本轮没有新增证据。第二候选是带适用性检查的增量重仿真；当前证书属于普通强基线，首对成本线索没有证明优于通用增量执行，也不等于MoE独有创新。

ASTRA已有DAG与ns-3接入，CLEM已有真实CCL/QP/CQ反馈，原生ns-3/阿里派生库已有逐包队列和反馈。连接这些工具、读取真实路由和经典缓存不是本项目独有贡献。本项目可核对输入、反馈、回退和完整成本是继续研究的基础；两个已成立的论文创新仍未取得。完整平台比较与此前淘汰方向见仓库研究综述。

本轮关闭，后四工作流未执行；不通过换顺序、扩大查询或调整阈值把本组补成正例。后续须另定独立工作负载及强基线，且先确认有意义的净收益，不能重开本轮条件。

首次归档的hash核验另发现一份仍在追加的监控samples.jsonl；原包和失败保留。修正措施是只打包已经结束、具有终态probe.json的监控目录，并排除会重写的归档清单本身，另建修正包。该修正仅影响离线证据保存，不改测量、输入、网络结果或停止条件；恢复收据另列。
'''
    output.mkdir(); (output/'report.md').write_text(text)
    esc=html.escape
    rows=''.join('<tr>'+''.join('<td>'+esc(str(x))+'</td>' for x in
        (name,f'{cpu:.6f}',f'{wall:.6f}',normal,fault,reuse))+'</tr>' for name,cpu,wall,normal,fault,reuse in values)
    bars=[]
    for metric,key1,key2 in [('CPU','direct_cpu_ns','reuse_cpu_ns'),('墙钟','direct_wall_ns','reuse_wall_ns')]:
        for label,key,color in [('直接',key1,'#326faa'),('复用',key2,'#419684')]:
            bars.append(f'<div class="row"><span>{metric} · {label}</span><div class="track"><i style="width:{p[key]/2e9*100:.4f}%;background:{color}"></i></div><b>{p[key]/1e9:.6f}s</b></div>')
    blocks=[]
    for section in text.split('\n\n'):
        if section.startswith('# ') or section.startswith('| '):continue
        if section.startswith('## '):blocks.append('<h2>'+esc(section[3:])+'</h2>')
        else:blocks.append('<p>'+esc(section).replace('\n','<br>')+'</p>')
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RC复用完整成本：未完成的三对比较</title>'
    page+='<style>body{font:16px/1.8 system-ui,sans-serif;max-width:1080px;margin:30px auto;padding:0 20px;background:#f4f7fb;color:#26394d}main{background:white;padding:28px;border-radius:12px}h1,h2{line-height:1.4}h2{margin-top:30px}.note{background:#fff4d9;border-left:4px solid #c4832e;padding:16px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:10px;text-align:left;border-bottom:1px solid #dae3eb}.row{display:grid;grid-template-columns:110px 1fr 130px;gap:10px;align-items:center}.track{background:#eff3f6;height:14px}.track i{display:block;height:100%}a{color:#286aa6}.scroll{overflow:auto}@media(max-width:650px){main{padding:12px}.row{grid-template-columns:90px 1fr 100px;font-size:12px}}</style><main>'
    page+='<h1>复用正常网络结果，完整成本到底省不省</h1><p>2026-10-28 · 计划3对，完成1对 · 原件6447 · 11个实际网络</p>'
    page+='<p class="note">首对CPU比1.085、墙钟比1.113；比较未完成，三对性能门槛未评价。不能称稳定提速或论文创新。</p>'
    page+='<div class="scroll"><table><tr><th>方法</th><th>CPU/秒</th><th>墙钟/秒</th><th>正常缓存执行</th><th>故障执行</th><th>直接复用</th></tr>'+rows+'</table></div>'
    page+='<h2>宿主成本：统一从0开始，横轴0～2秒</h2>'+''.join(bars)+''.join(blocks)
    page+='<p><a href="verification.json">独立核验</a> · <a href="failure.json">失败与停止记录</a> · <a href="recovery.json">离线恢复</a> · <a href="closeout.json">资源与收尾</a> · <a href="../../protocol.md">冻结方法</a> · <a href="../../README.md">复现说明</a></p></main></html>\n'
    (output/'report.html').write_text(page)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True); args=p.parse_args(); execute(args.stage.resolve(),args.output.resolve())
