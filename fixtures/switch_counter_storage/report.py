"""Readable Chinese report generated only from verified functionality evidence."""
import argparse
import html
import json
from pathlib import Path


def render(value):
    assert value['passed']
    storage=value['storage'];resources=storage['observed_resources'];large=value['large']
    rows=''
    for mode,title in [('legacy_dense','固定1025槽'),('port_dense','按实际端口')]:
        resource=resources[mode];payload=storage['legacy_payload_bytes' if mode=='legacy_dense' else 'port_payload_bytes']
        rows+=f'<tr><td>{title}</td><td>{payload:,} B</td><td>{resource["sampled_process_tree_peak_rss_bytes"]/2**20:.1f} MiB</td><td>{resource["wall_seconds"]:.3f} s</td></tr>'
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Scale-Out交换机存储验收</title><style>body{font:17px/1.7 system-ui,sans-serif;color:#182638;background:#f1f5f9;margin:0}main{max-width:980px;margin:auto;padding:32px 24px}section{background:white;padding:24px;border-radius:12px;margin:20px 0}h1{line-height:1.3}h2{font-size:22px}table{border-collapse:collapse;width:100%;font-size:15px}th,td{padding:12px 8px;border-bottom:1px solid #d6e0eb;text-align:left}strong{color:#085d59}.metric{font-size:36px;font-weight:700;color:#096b60}.note{color:#4f6075}code{background:#edf2f7;padding:2px 5px}a{color:#145aa0}.scroll{overflow:auto}</style>
<main><p>2026-10-27 · 原生ns-3 · 平台功能验收</p><h1>相同的网络执行，减少交换机计数占用</h1>
<section><div class="metric">'''+f'{storage["payload_reduction_fraction"]*100:.3f}%' +'''</div><p>本次千端点拓扑中，<strong>入口×出口×优先级计数矩阵</strong>的载荷内存减少比例。完整RSS还有MMU、路由、连接、数据包、结果与Python进程等开销。</p>
<div class="scroll"><table><tr><th>存储方式</th><th>矩阵载荷</th><th>采样进程树峰值RSS</th><th>一次公开CLI耗时</th></tr>'''+rows+'''</table></div><p class="note">每种模式只运行一次。耗时受调度、缓存和采集影响，不据此宣称稳定加速；宿主内存下降不等于真实MoE推理时延下降。</p></section>
<section><h2>改了什么</h2><p>原生交换机原来不论有多少端口，都保存1025×1025×8个计数，即每台33,620,000字节。新选项在物理端口创建完后，按实际端口数加slot0分配，仍保存完全相同的计数。没有删除排队、反馈、恢复事件或安全检查。</p><p>默认保持<code>legacy_dense</code>，可在原生协议JSON显式设置<code>"switch_counter_storage": "port_dense"</code>。须重建当前锁定后端；本选项不用于UB。</p></section>
<section><h2>怎样证明行为保留</h2><p>旧二进制默认、新二进制默认、显式固定和紧凑模式在新共享多rail拥塞输入下逐项一致，包含实际包顺序与时刻、正反向网卡、队列及其积分、CNP、工作回调与连接末态。固定/紧凑模式在断链失败和千端点短任务下也完整一致，计数快照相同。</p><p>共享拥塞小例实际产生 '''+str(value['healthy']['cnp_packets'])+''' 个CNP；断开唯一跨Pod链路后，两种模式都保留相同的有限恢复失败。失败不是优化收益。独立核验覆盖小例逐跳路径、串行传播、收发守恒和全部端口/队列；千端点仅采端点包，中间路径采用最短跳数和全网字节守恒核对，不能称全网逐包路径已采集。</p><p>4项新增Python输入检查及153项Scale-Out回归通过；六类证据篡改均被拒绝。独立原生C++对象检查器漏链接applications库，8个初始化/索引条件未执行。命令已修正，但本轮两次构建额度关闭，未额外编译，不能计入通过；全部验收尚未完成。</p></section>
<section><h2>千端点输入的代表范围</h2><p>1024端点、8Pod、32ToR/16Agg/2Core、1104物理链路，200Gb/s与2us/链路；256个EP4组，一次All-to-All→声明本地200ns→All-to-All，每对每阶段384B。共 '''+str(large['streams'])+' 条连接、'+str(large['work'])+' 个任务节点，模拟排空 '+str(large['finish_ns'])+''' ns。</p><p>这是新参数下的合成功能容量检查，没有使用LLMServingSim、真实专家路由或GPU Profile；不是单组EP1024或完整千卡MoE推理。以前完整模型超时的结论保留。</p></section>
<section><h2>对研究的意义</h2><p>这是让后续规模研究使用更强工程基线的一步。本阶段没有证明新的网络配置决策优势或减少包处理的宿主净收益，两项论文创新仍待成立；不能把本次普通精确存储改进包装成论文贡献。</p><p><a href="verification.json">完整核验摘要</a> · <a href="README.md">复核与证据说明</a> · <a href="../../examples/scaleout_counter_storage/README.md">公开使用示例</a></p></section></main></html>\n'''


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--verification',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.write_text(render(json.loads(a.verification.read_bytes())))
