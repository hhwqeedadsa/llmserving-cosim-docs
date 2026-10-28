"""Render an offline Chinese page from independently checked packet results."""
import argparse
import json
from pathlib import Path


def render(value):
    assert value['passed'] and len(value['cases']) == 3
    labels = dict(healthy='正常', drop_write_middle='连接0丢中间写入包', drop_atomic_ack='连接1丢原子回复')
    rows, orders = [], []
    for name in ('healthy', 'drop_write_middle', 'drop_atomic_ack'):
        row = value['cases'][name]
        rows.append(f'<tr><td>{labels[name]}</td><td>{row["finish_ns"]/1000:.3f}</td>'
                    f'<td>{row["request_packets"]} / {row["response_packets"]}</td>'
                    f'<td>{row["physical_tx"]} / {row["physical_rx"]} / {row["physical_drop"]}</td>'
                    f'<td>{row["timeouts"]}</td><td>{row["target3_counter"]}</td></tr>')
        atoms = [e for e in row['atomic_arrival_order'] if e['connection'] != 3]
        orders.append('<li>'+labels[name]+'：'+' → '.join(
            f'连接{e["connection"]}取得旧值{e["old"]}' for e in atoms)+'</li>')
    return '''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>多连接共享网络：功能检查结果</title><style>
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f2f5f7;color:#172b3a;font:17px/1.85 system-ui,sans-serif}
main{max-width:1060px;margin:36px auto;padding:38px;background:white;border-radius:16px}h1{font-size:30px;line-height:1.4}h2{font-size:23px;margin-top:32px}
.lead{font-size:19px}.note{border-left:4px solid #18766b;padding:12px 20px;background:#edf8f5}.flow{background:#eaf1f8;border-radius:8px;padding:20px}
table{border-collapse:collapse;width:100%;font-size:15px}th,td{padding:12px;text-align:left;border-bottom:1px solid #dce4e9}th{background:#edf3f7}.scroll{overflow:auto}
a{color:#126ba3}.small{font-size:14px;color:#526573}@media(max-width:700px){main{margin:0;padding:22px}h1{font-size:25px}}
</style><main><p class="small">Scale-Out 平台功能验收 · 2026-10-28 · 无需联网阅读</p>
<h1>四条连接共用网络，丢包会不会弄乱彼此的状态？</h1>
<p class="lead">三组检查均通过：指定连接丢包后只在自身触发重试，其他连接仍推进；共享计数器结果正确，原子回复丢失没有造成重复加法。</p>
<div class="note">本轮补齐多连接正确性参考，服务于后续研究“保留关键网络反馈、降低仿真开销”。接入与经典重传本身不构成论文创新。</div>
<h2>实际执行的任务</h2><p>4台服务器各1个网络端点，共用一套原生ns-3时钟和交换队列。链路100Gb/s、每跳1000ns，单rail、MTU256B。输入是8项人工操作，未执行模型计算或LLMServingSim调度。</p>
<div class="flow">连接0：端点0 → 端点3，写600B后共享计数器+1<br>
连接1：端点1 → 端点3，写600B后同一计数器+2<br>
连接2：端点0 → 端点3，写600B后同一计数器+3<br>
连接3：端点3 → 端点0，反向写32B后另一计数器+4<br>
经过实际ToR、汇聚与共享核心。连接0和2共用源网卡，前三条共享目标内存。</div>
<h2>三组结果</h2><div class="scroll"><table><thead><tr><th>条件</th><th>排空/μs</th><th>源请求/回复包</th><th>逐跳TX/RX/丢弃</th><th>超时次数</th><th>共享计数器</th></tr></thead><tbody>'''+''.join(rows)+'''</tbody></table></div>
<p class="small">一个端点包经过六条链路产生六次TX。网络排空不是推理时延；反向计数器三组均为11。</p>
<p>故障增加的约100μs主要由指定的重传超时决定，不能据此宣称新方法收益、业务损失或硬件恢复性能。本例整体重发当前操作，不是选择性重传性能实验。</p>
<h2>谁先执行原子加法</h2><p>跨连接没有额外顺序保证。请求先到目标者先取得旧值，丢写入包会改变先后，最终都须从41变为47：</p><ul>'''+''.join(orders)+'''</ul>
<p>丢原子回复时加法已发生，重试必须返回缓存的原旧值。三组都只有四次原子效应（含反向的一次）。两组故障中仅指定连接有一次超时，其余三条均先于故障连接完成。</p>
<h2>怎样核对</h2><p>独立程序从实际包字节读取连接、操作码、序号及旧值，按收包顺序交给公开Python状态参考。每连接独立保存序号和重放状态，共享目标字节数组。核对最终内存、逐目标效应、源完成、路径、收发守恒、队列占用及面积；7类篡改均拒绝。</p>
<p>三个网络各只执行一次，CPU驱动编译一次，复用八个固定动态库，不重建ns-3。归档恢复核对哈希、搬迁后的库加载及空工作目录下的离线重算，不重跑网络。</p>
<h2>服务范围与研究判据</h2><p>当前覆盖有限WRITE/fetch-add，每连接一次一项在途操作、固定速率、ICRC零占位。未覆盖完整RoCE拥塞控制、GPU内存序、完整现代MoE通信库或千卡模型，功能正确不代表生产校准。</p>
<p>后续候选须比较相同协议、相同输入的强基线：紧凑表示是否保持瓶颈与部署决策；减少包处理是否保留排队、完成及恢复影响，同时降低完整宿主时间和内存。目前尚无这两项收益结论。</p>
<p>真实路由另有17份同模型MMLU原件，15份完整，899与1479缺层排除。来源确认不等于远端版本独立核验；本轮人工操作未使用这些路由。</p>
<p>见<a href="../../examples/rc_concurrent/README.md">公开示例</a>、<a href="verification.json">核验摘要</a>、<a href="recovery.json">恢复记录</a>和<a href="closeout.json">资源收尾</a>。</p></main></html>
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verification', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists()
    args.output.write_text(render(json.loads(args.verification.read_bytes())))
