"""Render a scoped primary-source/model-validity review from verified evidence."""
import argparse
import html
import json
from pathlib import Path
import shutil


def read(p):return json.loads(p.read_text())
def esc(v):return html.escape(str(v))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audit',type=Path,required=True);p.add_argument('--check',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    out=a.output.resolve();assert not out.exists();out.mkdir()
    source=read(a.audit/'source-audit.json');traffic=read(a.audit/'feedback-audit.json');verified=read(a.check/'verification.json')
    assert verified['passed'] and verified['new_network_runs']==0
    for path,name in [(a.audit/'source-audit.json','sources.json'),(a.audit/'feedback-audit.json','summary.json'),
                      (a.check/'verification.json','verification.json'),(a.check/'witness.json','witness.json')]:shutil.copy2(path,out/name)
    failed=traffic['cases'][1]
    stream=max(failed['streams'],key=lambda s:s['ack_tx_50us']['peak_count'])
    peak=stream['ack_tx_50us'];rows=[]
    for c in traffic['cases']:
        rows.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in [c['case'],str(c['completed_work'])+'/'+str(c['offered_work']),
          '完成' if c['passed'] else '5ms截止，未完成',c['unique_np_CE_packets'],c['inferred_CNP_ACKs_received'],
          max(s['ack_tx_50us']['peak_count'] for s in c['streams'])])+'</tr>')
    comparison=[
        ('反馈产生（NP）','标记数据包到达即在传输ACK/NACK里置CNP位；此路径无50us抑制定时器。',
         '§3.1及图6：按流生成独立CNP；该部署N=50us，同一流每周期最多一个。','机制不同；50us不是所有现代NIC的通用校准值。'),
        ('发送端降速（RP）','CNP位设置布尔反馈标志，周期4us检查；初次降速检查在首个反馈后4001ns。',
         '收到CNP时按当前alpha降速；图7明确列出对应状态重置。','不能把多个ACK通知位计成同数次降速。'),
        ('发送端恢复','默认RPTimer=1500us，EwmaGain=1/16；固定mode1路径仅由恢复计时器增速。',
         '§3.1/图7同时使用计时器和发送字节计数；§5最终示例采用55us与10MB。','缺少该字节触发路径；改一个timer不能证明整体等价。'),
        ('参数依据','1500us与1/16来自TypeId默认。stock Clos配置另外使用CC_MODE=12和一组覆盖值。',
         '§5把1.5ms、g=1/16等作为初始方案并给出未收敛例，再调整多项设置。','仅两个数字相同，不等于整套初始方案相同；不能照搬论文数字冒称校准。'),
        ('跨仿真器验证','固定ASTRA与阿里库的10个DCQCN控制函数去注释后的token完全相同。',
         'HOTI 2022也明确说明ASTRA集成Alibaba ns-3，研究incast/All-To-All及训练。','共享机制不构成独立硬件或独立模型验证；不能泛化为整个平台完全相同。'),
    ]
    compare=''.join('<tr>'+''.join('<td>'+esc(c)+'</td>' for c in row)+'</tr>' for row in comparison)
    refs=''.join('<li><a href="'+esc(r['url'])+'">'+esc(r['id'])+'</a>：HTTP '+esc(r['http_status'])+'；PDF SHA256 <code>'+esc(r['sha256'])+'</code></li>' for r in source['papers'])
    quotes=''.join('<details><summary>'+esc(k)+'（原文提取行 '+str(v['start_line'])+'—'+str(v['end_line'])+'）</summary><pre>'+esc(v['text'])+'</pre></details>' for k,v in source['paper_excerpts'].items())
    doc='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DCQCN反馈抽象与论文研究边界核查</title><style>
body{font:16px/1.8 system-ui,sans-serif;background:#f4f7fb;color:#183045;margin:0}main{max-width:1160px;margin:36px auto;padding:32px;background:white;border-radius:14px}
h1,h2{line-height:1.4;color:#113963}h2{margin-top:32px}table{border-collapse:collapse;width:100%;font-size:14px}td,th{border:1px solid #d6e1ec;padding:11px;text-align:left;vertical-align:top}th{background:#eaf2fb}
.lead{background:#fff4dd;border-left:5px solid #d99b28;padding:18px}.evidence{background:#eaf6ef;padding:16px}code{word-break:break-all}pre{white-space:pre-wrap;background:#f4f6f8;padding:14px;font-size:13px}a{color:#155da0}li{margin:8px 0}.muted{color:#596c7d}
</style><main><p class="muted">2026-10-22 · 起点68ad3fe · 只读研究审计 · 没有新的网络/GPU执行</p>
<h1>先核查拥塞模型，再解释MoE长尾</h1>
<div class="lead"><b>结论：当前较深队列的长尾已在声明的字节模型中发生，但尚不能归因于真实MoE集群。</b>
原生逐包ECN-ACK反馈、发送端恢复规则与DCQCN原论文的部署机制存在明确差异。这是模型有效性缺口；仅记录参数或调快恢复周期不能消除它。</div>
<h2>这轮发现改变了什么判断</h2>
<p>DCQCN 2015原论文§3.1规定接收端按流生成拥塞通知（NP），其部署每50us最多一个CNP；发送端（RP）在通知到达后调整速率。
当前锁定后端则把通知位附在逐包传输ACK里，mode1按4us检查反馈。固定路径的恢复只由计时器推进，没有原文同时使用的字节计数触发。
这是协议抽象差异，不能据此断言有意简化的上游软件有bug，更不能直接断言现代网卡一定采用论文同一参数。</p>
<p>另一个重要线索是默认1500us恢复周期与g=1/16。原论文§5明确将这两个数纳入初始参数，并展示该完整初始方案未收敛；后续改变多项机制/参数。
我们的其他设置和实现不同，所以不能引用那一图来证明当前模型必然不收敛，也不能把55us直接替换进来就叫硬件校准。</p>
<h2>同一份旧记录的包级核查</h2><table><tr><th>已有条件</th><th>ACK完成工作</th><th>状态</th><th>目的端唯一CE包</th><th>源码推导的ACK通知位</th><th>同一连接50us内ACK通知峰值</th></tr>'''+''.join(rows)+'''</table>
<div class="evidence">见证连接'''+esc(stream['stream'])+'（GPU '+esc(stream['source'])+'→'+esc(stream['destination'])+'），实际ACK发送区间 ['+esc(peak['peak_window_start_ns'])+', '+esc(peak['peak_window_start_ns']+50000)+') ns 中有 <b>'+esc(peak['peak_count'])+'''</b> 个关联通知位。
<a href="witness.json">逐包见证</a>含数据/ACK UID、累计序号及目标收包、ACK发出、源收ACK时刻。源端任意4us区间最多收到14个关联通知位，但这不等于14次降速。</div>
<p>旧记录没有直接保存ACK flags，因此通知位由锁定ReceiveUdp的ECN→CNP位分支及实际端点/序号重建，不能冒称直接抓取真实网卡CNP。
2293是唯一目的端标记包数，不能用逐跳重复观测替代；旧失败前缀有一个源包尚未到达目的端。没有把截止时刻当作最终完成时间。
ACK里的标志位不是独立CNP报文，因此109个通知位也不能换算成109倍额外网络报文或额外字节。</p>
<p>独立程序从已校验的旧归档重读两份result，逐字节确认轨迹未变，核对9,094个源包、36,373条端点事件和2,293组反馈关联；用另一套bisect区间算法检查全部连接的4us/50us密度。
没有过滤后重跑网络，没有预测另一反馈模型下的完成时间，也没有得到生产收益。</p>
<h2>模型差异必须分开处理</h2><table><tr><th>维度</th><th>当前固定实现</th><th>原论文或一手依据</th><th>研究影响</th></tr>'''+compare+'''</table>
<h2>近期研究怎样收敛</h2>
<ol><li><b>先建立可区分的参考反馈模型。</b>保留历史native_ack_feedback行为及结果；另立明确参考模式，分别描述NP按流抑制、独立CNP发送和真实到达驱动的RP状态。
需要声明N、alpha、降/升速规则、timer与byte counter、优先级和同纳秒事件顺序。不能只换timer，也不能将功能参考模式称为已复现生产NIC。</li>
<li><b>按机制验收，再比较系统选择。</b>人工因果用例分别验证首次标记、50us内多标记、边界/空闲、反向排队、ACK与CNP分离、QP跨轮持续状态。
完成后再预注册对照，保持请求、专家选择、数据字节、流标识与拓扑不变，比较机制是否改变batch、放置或并发选择的排序；保留不变和反例。</li>
<li><b>匹配校准与独立输入仍不可省。</b>原论文是2015年特定NIC/网络部署，不是A100/H100的校准表。需要匹配设备/库的实测CNP、速率和完成语义，以及独立请求。
当前四个MMLU推理请求不足以支持训练/生产分布或p99结论。</li></ol>
<h2>论文主张及创新边界</h2>
<p>可保留的结论是：真实选定路由在本数据-only、0ns同步根就绪、ACK释放信用的模型中触发拥塞反馈，深度8到5ms仍有工作未完成；新证据说明反馈模型需要进一步审查。
目前不能宣称“热点专家导致生产网络此种长尾”“参数已经校准”或“我们的联合仿真器比其他仿真器更准确”。</p>
<p>HOTI 2022已经研究ASTRA+Alibaba ns-3、incast、分块All-To-All和端到端训练，讨论了拥塞控制的反馈开销、调参和业务暴露通信。
所以“用联合仿真发现DCQCN在集合通信中表现不好”不是本项目新贡献。未来须证明：MoE的真实共选、请求依赖、通信库资源与跨轮状态带来既有方法未覆盖的问题，并在可信模型/实测及强基线下给出可泛化的新解释或方法。</p>
<p><b>A类论文主贡献仍未成立。</b>本轮减少了把模型假设误当生产规律的风险，并明确了下一步机制实现与判别门槛；审计完成不等于论文目标完成。</p>
<h2>一手证据与复核</h2><ul>'''+refs+'''</ul><p>两篇PDF均实际取得并解析，未执行下载代码。下列摘录与提取文本行逐项核对；完整PDF、源码快照与旧归档保存在检查点。
<a href="sources.json">来源及源码hash</a> · <a href="summary.json">完整连接统计</a> · <a href="verification.json">独立核查</a> · <a href="maintenance.json">命令及资源</a> · <a href="archive.json">归档</a></p>'''+quotes+'''
<p class="muted">根卷低于8GiB预留，原始检查点仅存/dev/shm，易失且尚无持久备份。源码和本报告保存在Git。所有范围以本轮固定提交、已读文献及已有轨迹为限。</p></main></html>'''
    (out/'report.html').write_text(doc)
    print(json.dumps(dict(output=str(out),report_bytes=len(doc.encode()),new_network_runs=0)))


if __name__=='__main__':main()
