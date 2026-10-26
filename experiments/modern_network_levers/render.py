"""Standalone, script-free reading page generated only from saved summaries."""
import argparse
import html
from pathlib import Path
from experiments.modern_network_levers.analyze import load


def render(stage):
    summary=load(stage/'summary.json');cases=summary['cases'];base=cases['baseline']
    metrics=[('model_application_stage_complete_ns','模型应用阶段'),
             ('full_modern_events_and_source_release_ns','全部现代事件/源保护释放'),('network_drained_ns','网络排空')]
    labels={'baseline':'100G · 传播1μs','bandwidth':'200G · 传播1μs','propagation':'100G · 传播0.5μs'}
    rows=''.join('<tr><th>'+labels[name]+'</th>'+''.join(f'<td>{case[key]/1000:.3f} μs</td>' for key,_ in metrics)+'</tr>' for name,case in cases.items())
    effects=summary['effects']['model_application_stage_complete_ns']
    comparisons=[('LLMServingSim 2.0 / Vidur','服务调度、profile和容量规划','我们仍缺匹配的完整服务与硬件校准'),
        ('ASTRA-sim','依赖图、资源和多网络后端','接ns-3及执行依赖图本身已有先例'),
        ('SimAI / SimAI-CLEM','训练/推理链路；真实CCL接网络与规模优化','不能把竞品说成只做训练；我们当前是声明成本的功能模型'),
        ('ns-3 / 阿里RDMA派生库','逐包、队列和拥塞反馈','需要准确的MoE任务与端点语义；逐包不等于GPU真实'),
        ('SimBricks','主机/设备/网络联合仿真','联合和多保真作为名称不构成创新')]
    comparison_rows=''.join('<tr>'+''.join('<td>'+html.escape(x)+'</td>' for x in row)+'</tr>' for row in comparisons)
    return f'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MoE Scale-Out：收益证据与两个创新候选</title>
<style>body{{margin:0;background:#f5f7fa;color:#1c2d42;font:17px/1.8 system-ui,sans-serif}}main{{max-width:1100px;margin:auto;padding:32px 22px 70px}}h1{{font-size:30px;line-height:1.4}}h2{{font-size:23px}}p{{max-width:950px}}section{{background:white;border:1px solid #dce3eb;border-radius:12px;padding:20px 25px;margin:22px 0}}.tag{{color:#8b4b00;background:#fff4d9;padding:4px 10px;border-radius:5px}}.number{{font-size:32px;font-weight:750;color:#126b75}}.cards{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:15px}}th,td{{text-align:left;vertical-align:top;padding:12px 10px;border-bottom:1px solid #dce3eb}}th{{background:#f2f6fa}}a{{color:#135f9d}}small{{color:#53677d}}@media(max-width:700px){{.cards{{grid-template-columns:1fr}}main{{padding:15px}}section{{padding:16px}}}}</style>
<main><small>2026-10-26 · 四个活动rank · 单层Decode功能模型</small>
<h1>先弄清网络瓶颈，再决定创新做在哪里</h1>
<p>目标是可靠地选择MoE跨机网络和通信方案，并控制得到这个判断所需的仿真成本。当前完成了较强恢复基线接入和三组独立网络对照；<strong>尚无已验证的新方法收益。</strong></p>
<section><h2>这轮实际观察到了多少收益？</h2><div class="cards">
<div><span class="number">{100*effects['bandwidth']['relative_reduction']:.2f}%</span><br>各层带宽翻倍，模型应用阶段缩短<br><small>节省{effects['bandwidth']['saved_ns']/1000:.3f}μs，网络资源增加。</small></div>
<div><span class="number">{100*effects['propagation']['relative_reduction']:.2f}%</span><br>链路传播时延减半，模型应用阶段缩短<br><small>节省{effects['propagation']['saved_ns']/1000:.3f}μs，是参数敏感性。</small></div></div>
<p>这个小batch模型更敏感于多轮通信的时延；传播同时影响控制和数据，不能把改善全部归给控制消息。上述数字不是软件优化收益、价格收益率或生产预测。</p>
<div class="scroll"><table><thead><tr><th>网络条件</th>{''.join('<th>'+label+'</th>' for _,label in metrics)}</tr></thead><tbody>{rows}</tbody></table></div>
<p>阶段完成不授权提前复用源缓冲；等待所有保护释放后，网络中仍可能有报文。这三种时间都不是实测用户请求时延。</p></section>
<section><h2>为什么只看通信总量不够？</h2><p>控制请求/原子回复占MRT1帧字节 <strong>{100*base['control_frame_byte_fraction']:.0f}%</strong>，却占源数据包 <strong>{100*base['control_source_packet_fraction']:.2f}%</strong>（120/264包）。字节小的消息仍有依赖、屏障和往返。</p>
<p>口径不包含独立ACK/NACK/CNP和全部物理头。本实验保持271,200B帧字节与同一现代计划，采用2KB分包；三组均未出现重复源包、恢复超时或CNP命名事件。不能推广为故障拥塞结论。</p></section>
<section><h2>已有平台在哪些地方更强？</h2><div class="scroll"><table><thead><tr><th>平台</th><th>主要长处</th><th>对本项目的约束</th></tr></thead><tbody>{comparison_rows}</tbody></table></div><p><small>按已取得的一手版本核对，未对竞品进行本轮同机实跑；未核实的能力不能标成“不支持”。</small></p></section>
<div class="cards"><section><span class="tag">候选一 · 待验证</span><h2>影响网络决策的最小通信语义</h2><p>找出哪些控制消息、持久连接和完成条件必须保留，避免模型选错网络或部署方案。</p><p><strong>验证：</strong>同路由、同资源做抽象消融，看排序错误和选择后悔值，并做实机留出校准。</p><p><strong>淘汰：</strong>通用DAG基线已给出同样选择，或缺项不影响实际决策。</p></section>
<section><span class="tag">候选二 · 待验证</span><h2>按真实交互范围控制包级成本</h2><p>保留共享队列、反馈、重传和应用依赖，仅在明确条件下合并/近似低影响区间。</p><p><strong>验证：</strong>同时报告宿主时间、RSS、事件数和决策误差，再扩百卡/千卡。</p><p><strong>淘汰：</strong>交互范围始终覆盖全网，或经典批处理已足够快。</p></section></div>
<section><h2>能交付什么，还缺什么？</h2><p>已接通现代完整调用的选择性重传；四组功能验收通过。新研究完成三组网络、576个现代事件核对、完整源帧解析和证据篡改拒绝。</p><p>当前是8卡拓扑中的4个活动rank、一个新路由步、单层、声明成本；未执行完整LLMServingSim服务调度或GPU数值计算。主创新、匹配校准、独立泛化和千卡完整现代调用尚未完成。</p>
<p><a href="../../../../docs/reviews/联合仿真的两个创新候选与网络收益判别_20261026.md">详细中文报告</a> · <a href="summary.json">结果JSON</a> · <a href="verification.json">核验</a> · <a href="sources.json">来源</a></p></section></main></html>'''


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.write_text(render(a.stage))


if __name__=='__main__':main()
