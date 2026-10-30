"""Assemble a portable report from already analyzed runs and exported figures."""
import argparse
import json
from pathlib import Path
import shutil


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('analysis','frozen','figures','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);fig=out/'figures';fig.mkdir()
    shutil.copyfile(a.analysis/'analysis.json',out/'analysis.json');shutil.copyfile(a.frozen,out/'frozen.json')
    for path in a.figures.iterdir():
        if path.is_file():shutil.copyfile(path,fig/path.name)
        elif (path/'arrival_curve.csv').is_file():
            target=fig/path.name;target.mkdir()
            # Git normalizes text CSVs to LF; hash the same portable bytes.
            (target/'arrival_curve.csv').write_text((path/'arrival_curve.csv').read_text())
            shutil.copyfile(path/'summary.json',target/'summary.json')
    summary=json.loads((fig/'slice-summaries.json').read_bytes());single,primary,replica=(summary[k]['slice'] for k in
        ('single-primary','delayed-primary','delayed-replica'))
    assert single['data_ready_ns']==primary['data_ready_ns']
    ack_delta=primary['source_confirmed_ns']-single['source_confirmed_ns']
    html=(a.analysis/'report.html').read_text()
    visual=f'''<h2>结果与收尾要分开看</h2><img style="max-width:100%;height:auto" src="figures/latency-comparison.png" alt="正常和局部故障下，三种策略的结果、收尾和网络排空时间">
    <p class="small">彩条到结果就绪；深灰实线到全部实际尝试完成；浅灰点线到网络排空。上述时间均由实际事件核对。</p>
    <h2>逐包看：最后10%的数据拖住了什么</h2><img style="max-width:100%;height:auto" src="figures/dispatch-arrivals.png" alt="原始层3 Dispatch累计有效数据到达曲线">
    <p>原始层3的Dispatch共{single['logical_bytes']:,}字节、{single['messages']}条消息。原尝试约10微秒已收到90%，剩余10%受局部丢包恢复影响，到{single['data_ready_ns']/1000:.3f}微秒才收齐。延迟备用在70微秒启动，到{replica['data_ready_ns']/1000:.3f}微秒收齐该阶段数据。</p>
    <p>这只是一个通信阶段的数据接收，后面还有确认、专家计算、Combine及下一层。它不能直接当作整段推理完成；完整结果只提前3.735微秒。蓝色与橙色接收曲线重叠，但原尝试源确认从{single['source_confirmed_ns']/1000:.3f}变为{primary['source_confirmed_ns']/1000:.3f}微秒，晚{ack_delta}纳秒；最后原尝试也晚{ack_delta}纳秒。此处记录到确认拖延，尚未用独立端口/负载对照证明其具体成因。</p>
    <p class="small">曲线由完整逐包记录重建，仅表示本切片唯一有效字节已被传输接收端接受的累计量，不表示原始R1运行时抓包或GPU消费。CSV与summary随图保存。</p>'''
    html=html.replace('<h2>这次究竟仿真了什么</h2>',visual+'<h2>这次究竟仿真了什么</h2>')
    html=html.replace('<h2>对创新方向的约束</h2>','<h2>收益之外付出了什么</h2><p>故障下仍执行双份通信和声明计算，而整体收尾由136.829增至137.048微秒。正常虽然没有重复执行，仍为备用预留10,092,544字节KV及完整专家部署。当前没有吞吐或容量机会成本测量，不能把少等3.735微秒直接解释成经济收益。</p><h2>对创新方向的约束</h2>')
    (out/'report.html').write_text(html)
    print(json.dumps(dict(passed=True,portable_figures=2,packet_curve_exports=3,new_network_executions=0)))

if __name__=='__main__':main()
