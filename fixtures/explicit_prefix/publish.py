"""Publish functional forwarding evidence, distinct from MoE research results."""
import argparse
import html
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True)
    a=p.parse_args();proof=json.loads((a.stage/'verification.json').read_bytes());assert proof['passed']
    out=Path(__file__).resolve().parent
    with (out/'verification.json').open('xb') as f:f.write((a.stage/'verification.json').read_bytes())
    rows=[]
    labels={'normal-old':'旧构建正常','normal-new':'新构建正常','live':'故障，仅过滤旧出口',
      'prefix':'故障，显式段','restore':'显式段，12µs恢复','instant':'显式模式，立即全局更新'}
    for name,v in proof['cases'].items():
        c=v['counts'];transport=v['transport']
        rows.append('<tr>'+''.join(f'<td>{html.escape(str(x))}</td>' for x in (
          labels[name],f"{v['first_round_ACK_ns']/1000:.3f}",f"{v['last_ACK_ns']/1000:.3f}",
          f"{v['finish_ns']/1000:.3f}",c.get('repair_1',0),c.get('encapsulation_wire_bytes',0),
          transport['source_payload_bytes']-320000))+'</tr>')
    prefix=proof['cases']['prefix'];counts=prefix['counts']
    content='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>显式绕路已接入实际网络</title><style>body{font:16px/1.8 system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 20px;background:#f5f8fc;color:#173047}table{border-collapse:collapse;width:100%;background:white}td,th{padding:9px;text-align:left;border-bottom:1px solid #ddd}.box{padding:18px;background:#e2edf8;border-left:5px solid #2873b8}code{background:#e9edf3;padding:2px 4px}a{color:#2465a7}svg{width:100%;height:auto}small{color:#516a81}</style>
<h1>从“有一条绕路”到“包真正经过绕路”</h1>
<p>128端点Clos · 单条交换机边故障 · 4条人工持久流 · 320,000B唯一业务数据 · 功能验收</p>
<div class="box">在Agg132的原候选出口全部失效时，把原包与显式段一起封装。中间交换机依据线上段继续转发，到Agg133拆封后恢复旧路由。原ACK、选择性重传和QP寿命保持。</div>
<svg viewBox="0 0 1000 180" role="img" aria-label="故障交换机132封装，经129转发，到133拆封，再经128到GPU1">
<defs><marker id="arrow" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto"><path d="M0 0 L10 4 L0 8" fill="#2873b8"/></marker></defs>
<g fill="#e2edf8" stroke="#7c91aa"><rect x="10" y="40" width="170" height="80" rx="8"/><rect x="215" y="40" width="170" height="80" rx="8"/><rect x="420" y="40" width="170" height="80" rx="8"/><rect x="625" y="40" width="160" height="80" rx="8"/><rect x="820" y="40" width="170" height="80" rx="8"/></g>
<g stroke="#2873b8" stroke-width="2" marker-end="url(#arrow)"><path d="M180 80 H210"/><path d="M385 80 H415"/><path d="M590 80 H620"/><path d="M785 80 H815"/></g>
<g font-family="sans-serif" font-size="18" fill="#173047" text-anchor="middle"><text x="95" y="73">Agg132</text><text x="95" y="102">封装 +38B</text><text x="300" y="73">ToR129</text><text x="300" y="102">执行下一跳</text><text x="505" y="73">Agg133</text><text x="505" y="102">拆封、传回CE</text><text x="705" y="85">ToR128</text><text x="905" y="85">GPU1</text><text x="500" y="160">每一跳真实经过原生队列、MMU及链路；GPU收到原包</text></g></svg>
<h2>六个功能对照</h2><p>相同4个流，各先发64KB、再在100µs之后发16KB。首轮、完整任务与网络排空分别报告，不能把首轮提前量当整项推理收益。</p>
<div style="overflow:auto"><table><tr><th>条件</th><th>首轮全ACK µs</th><th>末业务ACK µs</th><th>排空 µs</th><th>封装包数</th><th>逐跳新增封装B</th><th>源重传B</th></tr>'''+''.join(rows)+'''</table></div>
<h2>检查了什么</h2><p>逐物理TX/RX配对、按实际包长检查序列化时间、重放队列字节积分及接收区间/SACK，源确认必须来自实际反馈包；原生MMU最终排空、各流始终只创建一个QP。封装、转发、拆封三段与实际出队和包长一一对应，普通转发另按已到通知和旧ECMP哈希核路。</p>
<p>旧/新默认正常结果除加载库路径外相同；4类错误结果被独立检查拒绝。另有CPU检查验证4种协议的逐字节封装往返、CE传递、16个原生计划与Python独立计划一致及非法包头/输入。</p>
<h2>边界与后续研究</h2><p>这是自定义IPv4协议250功能模型，不是标准RoCE/MPLS/TI-LFA；没有另计线速封装处理、故障检测、修复路径分发或MTU碎片成本。线上38B和真实队列/链路成本已经计入，控制队列保留原先不计数据MMU的规则。</p>
<p>本页使用人工流，不能宣称真实MoE收益或新协议贡献。下一项研究需固定真实专家选择输入，保持相同重传参数、任务图与资源条件，对比较强现成绕路基线后仍剩多少故障等待和对其他任务的拖累。</p>
<p><a href="README.md">验收说明</a> · <a href="../../examples/scaleout_explicit_prefix/README.md">公开操作</a> · <a href="verification.json">独立证据</a> · <a href="receipt.json">来源与资源</a></p></html>'''
    with (out/'report.html').open('x') as f:f.write(content+'\n')
    print(json.dumps(dict(published=True,encapsulated_packets=counts.get('repair_1',0),
      physical_wire_overhead_bytes=counts.get('encapsulation_wire_bytes',0),MoE_benefit_claimed=False)))


if __name__=='__main__':main()
