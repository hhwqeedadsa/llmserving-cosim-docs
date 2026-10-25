"""Export a self-contained report; no new networks or third-party dependencies."""
import argparse
import html
import json
from pathlib import Path
from experiments.extreme_fault_stress.stage import load, save


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--stage',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();s=a.stage.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    d=load(s/'summary.json');v=load(s/'verification.json');assert v['passed']
    save(out/'summary.json',d);save(out/'verification.json',v)
    names={'uniform-healthy':'均匀分布 / 正常网络','hotspot-healthy':'87.5%热点 / 正常网络','hotspot-uplink':'热点 / 一条上联失效',
           'hotspot-core':'热点 / 核心16全部相邻边失效','hotspot-static-core':'热点 / 首包前核心失联','hotspot-partition':'热点 / 热点ToR全部上联失效'}
    rows=[];bars=[]
    maximum=max(x.get('application_completion_ns',0) for x in d['cases'])
    for r in d['cases']:
        executed=r['status']!='not_run';name=names[r['name']]
        rows.append('<tr><th>'+name+'</th><td>'+('已完成' if executed else '已构造，未运行')+'</td><td>'+
                    (f"{r['application_completion_ns']/1e6:.3f} 毫秒" if executed else '—')+'</td><td>'+
                    (('完整保存' if r['capture_complete'] else f"遗漏 {r['capture_omitted']:,} 条") if executed else '—')+'</td></tr>')
        if executed:bars.append(f'<div class="barrow"><span>{name}</span><div class="track"><div style="width:{r["application_completion_ns"]/maximum*100:.4f}%"></div></div><b>{r["application_completion_ns"]/1e6:.3f} ms</b></div>')
    fault=d['effects']['fault_effect'];hot=d['effects']['hotspot_effect'];combined=d['effects']['combined_effect']
    page='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>极端故障压力测试</title><style>
*{box-sizing:border-box}body{background:#f3f5f1;color:#17382f;margin:0;font:16px/1.8 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1080px;margin:auto;padding:40px 28px}h1{font-size:34px;line-height:1.35}h2{font-size:23px;margin-top:34px}p{max-width:92ch}small,.muted{color:#536c63}.tag{font-size:13px;color:#0b7061;letter-spacing:1px}.box{background:white;border:1px solid #d8e4da;border-radius:10px;padding:22px;margin:20px 0}.note{background:#fff0e0;border-left:3px solid #a95825;padding:16px 20px}.numbers{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}.numbers article{background:#173f37;color:#f0f6f1;border-radius:8px;padding:20px}.numbers strong{display:block;font-size:30px;font-variant-numeric:tabular-nums}.numbers span{font-size:13px}.table{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px;text-align:left}th,td{padding:12px;border-bottom:1px solid #dce5dd}th{font-weight:500}thead th{background:#eaf0e9}.barrow{display:grid;grid-template-columns:210px 1fr 95px;gap:12px;align-items:center;font-size:14px;margin:18px 0}.track{height:23px;background:#eaf0e9;border-radius:3px;overflow:hidden}.track>div{height:100%;background:#137b65}.barrow b{text-align:right;font-weight:500;font-variant-numeric:tabular-nums}a{color:#096c59;text-underline-offset:4px}li{padding-bottom:8px}.foot{border-top:1px solid #cedbd0;padding-top:20px;margin-top:35px;font-size:13px;color:#536c63}@media(max-width:650px){main{padding:22px 16px}h1{font-size:27px}.numbers{grid-template-columns:1fr}.barrow{grid-template-columns:130px 1fr 75px;font-size:12px;gap:7px}.box{padding:18px 13px}}@media print{body{background:white}main{padding:0}.box{break-inside:avoid}.track>div{print-color-adjust:exact;-webkit-print-color-adjust:exact}}
</style></head><body><main><div class="tag">SCALE-OUT NETWORK / 2026-10-25 / 人工合成压力测试</div><h1>把专家热点、带宽瓶颈与故障叠加</h1><p>构造6种固定场景，运行前三种。保持拥塞控制、选择性重传及已有恢复机制，在极端热点下观察任务完成时间；本轮未实现或评估新优化算法。</p>'''
    page+=f'<div class="numbers"><article><strong>{hot["slowdown_percent"]:.1f}%</strong><span>热点相对均匀负载的任务变慢</span></article><article><strong>{fault["slowdown_percent"]:.1f}%</strong><span>故障相对同一热点正常网络的任务变慢</span></article><article><strong>{combined["slowdown_percent"]:.1f}%</strong><span>热点加故障相对均匀正常网络的总变化</span></article></div>'
    page+='<p class="note">90.3%同时包含热点与故障的影响，不能全部归给故障；这些是压力场景中的退化，不是新方法的收益。三组任务都完成，第三组包记录触及上限，未运行余下三个条件。</p><h2>六组场景与实际状态</h2><div class="table"><table><thead><tr><th>场景</th><th>状态</th><th>任务完成时间</th><th>包捕获</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div><div class="box" role="img" aria-label="已运行三条件任务完成时间比较">'+''.join(bars)+'</div>'
    page+=f'<h2>这里的故障影响究竟有多大？</h2><p>热点正常与上联失效分别完成于4.224和4.883毫秒，故障多等约{fault["delta_ns"]/1e3:.3f}微秒，变慢{fault["slowdown_percent"]:.2f}%。若能恢复到当前热点正常时长，对故障任务的缩短参考为{fault["fraction_of_after_percent"]:.2f}%。链路容量损失和必要恢复成本未被分离，这不是严格可优化上界，也没有证明新调度能拿回这些时间。</p>'
    page+='<h2>压力是怎样构造的？</h2><ul><li>4个请求源与4个专家owner分离部署；32逻辑专家、Top1，每源每步256 token、H512、BF16激活布局假设，两步共4 MiB唯一传输量。没有真实GPU数值计算。</li><li>均匀条件每源分别有64 token去4个owner；热点条件为224/11/11/10，87.5%激活集中到一个owner。两者token数、总字节和声明计算成本相同。</li><li>双pod单rail/plane Clos：100G接入、10G上层，强过订阅；同一故障对照中带宽保持不变。GPU型号仅为拓扑导出的兼容标签。</li><li>源端80微秒RTO、选择性重传、DCQCN参考、16微秒乱序等待；故障条件保留本地失效端口过滤。没有为放大效果关闭成熟恢复机制。</li><li>4微秒关闭热点ToR12的一条上联12—14，16微秒更新路由，另一条上联仍在。更严重的核心失联与永久分区已经生成配置，但没有实测结果。</li></ul>'
    page+='<h2>为什么停在第三组？</h2><p>预先规定最多保存100,000条包记录。故障条件实际产生112,070条，遗漏12,070条；任务完成、90项依赖和源字节记录仍可独立核对，但不能宣称整个网络逐包路径和ACK都已重建。按停止规则关闭本轮，不扩记录上限、不重跑成功网络、不追加剩余条件。</p><h2>这些实验能说明什么？</h2><p>可用于测试仿真器承压、定位热点与容量失效的组合影响。不能证明这种热点在生产中常见，不能用永久断网构造无限优化收益，也不能把人工压力结果替代独立真实请求、现代库语义或硬件校准。</p><p><a href="../../config.json">可修改的场景配置</a> · <a href="../../protocol.md">执行前方案与停止规则</a> · <a href="summary.json">数值摘要</a> · <a href="verification.json">独立核验</a> · <a href="../../README.md">运行及证据说明</a></p><p class="foot">本地交付 · 3次实际网络 · 成功网络重复0次 · 未构建、安装、下载、运行GPU或推送</p></main></body></html>'
    (out/'report.html').write_text(page)
    print(out/'report.html')


if __name__=='__main__':main()
