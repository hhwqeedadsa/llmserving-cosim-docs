"""Audit metric comparisons from actual transports/packet paths and write a readable review."""
import argparse
from collections import Counter,defaultdict
import csv
from fractions import Fraction
import hashlib
from itertools import combinations
import json
from pathlib import Path

def load(path):return json.loads(path.read_text())
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def direction(a,b):return (a>b)-(a<b)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('observed','run','analysis','metrics','packet_check','output'):parser.add_argument('--'+name.replace('_','-'),required=True,type=Path)
    args=parser.parse_args();cfg=load(args.observed/'index.json')['config'];m=load(args.metrics);checked=load(args.packet_check)
    assert checked['passed'] and checked['rounds']==72 and len(checked['cases'])==18
    assert not checked['repeat_network_run_performed'] and checked['repeat_analysis_byte_identical']
    summary_path=args.analysis/'summary.json';summary=load(summary_path)
    assert hashlib.sha256(summary_path.read_bytes()).hexdigest()==checked['summary_sha256']
    assert hashlib.sha256((args.analysis/'packets.csv').read_bytes()).hexdigest()==summary['packets_csv_sha256']
    audited_hashes={row['case']:row['result_sha256'] for row in checked['cases']}
    with (args.analysis/'packets.csv').open() as stream:packets=list(csv.DictReader(stream))
    indexed={(r['network'],r['condition'],r['step']):r for r in m['rows']};assert len(indexed)==len(m['rows'])==72
    exact={};actual_durations={};actual_ports_checked=0
    for network in cfg['network_profiles']:
        topology=load(args.run/('topology-'+network)/'topology.json');edges={}
        for edge in topology['links']:
            edges[edge['src'],edge['dst']]=edge['rate_bps'];edges[edge['dst'],edge['src']]=edge['rate_bps']
        for condition in cfg['placement_conditions']:
            case=network+'-'+condition;result_path=args.run/case/'result.json';result=load(result_path);work=result['persistent_transport']['work']
            assert hashlib.sha256(result_path.read_bytes()).hexdigest()==audited_hashes[case]
            streams={s['id']:s for s in result['persistent_transport']['streams']}
            for step in range(4):
                key=network,condition,step;row=indexed[key];selected=[w for w in work if w['id']//16==step]
                assert row['work_count']==len(selected) and row['source_window']==cfg['source_windows'][step]
                ready={w['ready_ns'] for w in selected};assert len(ready)==1
                actual_durations[key]=max(w['acked_ns'] for w in selected)-ready.pop()
                assert actual_durations[key]==row['ack_duration_ns']
                counts=Counter()
                for w in selected:
                    s=streams[w['stream']];counts['in',s['dst']]+=1;counts['out',s['src']]+=1
                assert row['max_gpu_data_copies']==max(counts.values())
                traffic=Counter()
                for packet in packets:
                    if packet['case']!=case or int(packet['step'])!=step:continue
                    path=list(map(int,packet['path'].split('>')))
                    for edge in zip(path,path[1:]):traffic[edge]+=int(packet['model_bytes'])
                service={edge:Fraction(value*8*10**9,edges[edge]) for edge,value in traffic.items()}
                peak=max(service.values());endpoint=max(value for (src,dst),value in service.items() if min(src,dst)<8)
                assert abs(row['static_max_port_service_ns']-float(peak))<1e-7
                assert abs(row['endpoint_service_ns']-float(endpoint))<1e-7
                assert row['static_projection_port_count']==len(service);actual_ports_checked+=len(service)
                assert peak<=row['ack_duration_ns']
                exact[key]=dict(work_count=len(selected),max_gpu_data_copies=max(counts.values()),
                    endpoint_service_ns=endpoint,static_max_port_service_ns=peak)
            assert sum(actual_durations[network,condition,s] for s in range(4))==max(w['acked_ns'] for w in work)
    assert actual_ports_checked==m['static_projection_checks']
    assert {row['max_gpu_data_copies'] for row in m['rows']}=={3}
    metrics=list(next(iter(exact.values())));round_counts={metric:Counter() for metric in metrics};overall_counts={metric:Counter() for metric in metrics}
    def outcome(predicted,actual):
        if predicted==0:return 'both_tie' if actual==0 else 'metric_tie'
        if actual==0:return 'latency_tie'
        return 'agrees' if predicted==actual else 'reverses'
    expected_pairs={};expected_overall={}
    for network in cfg['network_profiles']:
        for left,right in combinations(cfg['placement_conditions'],2):
            values={}
            for step in range(4):
                first,second=(network,left,step),(network,right,step)
                observed=direction(actual_durations[first],actual_durations[second])
                values={metric:outcome(direction(exact[first][metric],exact[second][metric]),observed) for metric in metrics}
                expected_pairs[network,step,left,right]=values
                for metric,status in values.items():round_counts[metric][status]+=1
            observed=direction(sum(actual_durations[network,left,s] for s in range(4)),sum(actual_durations[network,right,s] for s in range(4)))
            values={metric:outcome(direction(sum(exact[network,left,s][metric] for s in range(4)),
                sum(exact[network,right,s][metric] for s in range(4))),observed) for metric in metrics}
            expected_overall[network,left,right]=values
            for metric,status in values.items():overall_counts[metric][status]+=1
    assert round_counts==m['comparison_counts'] and overall_counts==m['overall_comparison_counts']
    assert len(expected_pairs)==72 and len(expected_overall)==18
    assert {(v['network'],v['step'],v['left'],v['right']):v['metrics'] for v in m['pair_comparisons']}==expected_pairs
    assert {(v['network'],v['left'],v['right']):v['metrics'] for v in m['overall_comparisons']}==expected_overall
    for value in m['seed_sensitivity']:
        durations={cfg['network_seeds'][network]:actual_durations[network,value['condition'],value['step']]
            for network,rate in cfg['network_profiles'].items() if rate==value['upper_rate_bps']}
        assert {str(seed):tm for seed,tm in durations.items()}==value['observations']
        low,high=min(durations.values()),max(durations.values())
        assert (value['min_ack_ns'],value['max_ack_ns'])==(low,high)
        assert abs(value['relative_spread']-float(Fraction(high-low,low)))<1e-12
    args.output.mkdir(exist_ok=False)
    verification=dict(passed=True,actual_rounds=72,actual_whole_run_comparisons=18,actual_port_services_checked=actual_ports_checked,
        comparisons_recomputed_from_actual_transport_and_packets=True,round_comparison_counts=round_counts,
        overall_comparison_counts=overall_counts,raw_packet_audit=dict(packet_lineages=checked['packet_lineages'],
            physical_transmissions=checked['physical_transmissions'],switch_ecmp_choices=checked['switch_ecmp_choices'],
            work_callbacks=checked['work_callbacks'],capture_events=checked['capture_events']),
        scope='bounded descriptive baseline test; same four requests; no prospective optimization/hardware/modern GIN claim')
    save(args.output/'verification.json',verification);save(args.output/'metrics.json',m)
    rows=[]
    for network in cfg['network_profiles']:
        seed=cfg['network_seeds'][network];rate=cfg['network_profiles'][network]//10**9
        times=[sum(actual_durations[network,c,s] for s in range(4))/1000 for c in cfg['placement_conditions']]
        rows.append(f'<tr><td>{seed}</td><td>{rate}G</td>'+''.join(f'<td>{tm:.3f}</td>' for tm in times)+'</tr>')
    names=dict(work_count='总数据份数',max_gpu_data_copies='逐窗最忙GPU数据份数之和',endpoint_service_ns='逐窗最忙接入端口服务量之和',static_max_port_service_ns='逐窗最忙物理出口服务量之和')
    metric_rows=''.join(f"<tr><td>{names[name]}</td><td>{v.get('agrees',0)}</td><td>{v.get('metric_tie',0)}</td><td>{v.get('reverses',0)}</td></tr>" for name,v in overall_counts.items())
    text=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>真实路由与ECMP：强基线判别</title>
<style>body{{font:17px/1.8 system-ui,sans-serif;color:#18283b;max-width:1050px;margin:40px auto;padding:0 22px}}h1{{line-height:1.3}}aside{{background:#edf5fc;border-left:4px solid #3575af;padding:15px}}table{{border-collapse:collapse;width:100%;font-size:15px}}td,th{{padding:9px;text-align:left;border-bottom:1px solid #ccd5df}}a{{color:#165b9d}}</style>
<h1>真实路由跨窗口、跨选路种子的网络判别</h1><p>2026-10-22 · 平台4ecf681 · 固定18条件</p>
<aside>较少的总数据量不保证更快：尾部Prefill放置为42份，全量Prefill为45份，但尾部方案在6种网络配置中的5种更慢。已有的链路服务量基线解释了绝大多数排序；本结果收紧新方法的创新门槛。</aside>
<h2>这次验证的范围</h2><p>四个真实R1请求，层4/32、Decode入口5/6共四窗口；EP4逻辑rank映射到四台服务器、两个Pod。对连续、全量Prefill LPT、尾部10-token Prefill LPT三种已有等容量放置，比较上层100G/25G与ECMP种子11/29/47。选择在执行前固定，不按结果扩矩阵。这些窗口此前出现在全量离线统计中，仅是新的包级评价窗口，不是独立留出数据。</p>
<p>每份Dispatch数据14,464B，12个持久连接、depth1；下一窗只等本窗全部真实ACK后开始。没有Combine、计算、控制/GIN完整语义，不是一次完整推理或生产长尾实验。</p>
<h2>相同任务换路径后，结果怎样变化</h2><table><tr><th>ECMP种子</th><th>上层速率</th><th>连续36份（μs）</th><th>全Prefill45份（μs）</th><th>尾Prefill42份（μs）</th></tr>{''.join(rows)}</table>
<p>尾部方案少3份，即少6.67%数据；但除seed29/上层25G外都更慢。其中seed29/全100G仅差4ns，没有实际收益意义，不能把它渲染为显著差异。同一连续放置在上层25G时，总完成为91.465—109.656μs，跨种子跨度19.89%；逐窗最大跨度21.59%。这些是固定条件的范围，不是总体置信区间。</p>
<h2>简单基线是否已经足够解释</h2><p>对每个网络配置中三方案做两两比较，共18次整段比较。服务量按模型字节×8/出口速率计算，每窗取最大，再对四个串行ACK窗口求和；数据包头和反向ACK均保留。静态计算只使用输入、拓扑和种子，全部2,284个出口负载与实际逐包发送量一致。代码在执行后实现，只作描述性基线核对，没有前瞻部署策略。</p>
<table><tr><th>指标</th><th>排序一致</th><th>指标并列/实际不同</th><th>排序相反</th></tr>{metric_rows}</table>
<p>链路指标17次排序一致，剩下一次是4ns差异而指标并列；单GPU最大数据份数在所有方案中每窗都是3，无法区分排序。72次逐窗比较里，链路指标60次方向一致，其余为指标或时延并列，没有反向。这说明该小场景应先使用已有物理链路基线，不能把复杂统计或预测器带来的表面改进当成新贡献。链路服务量仍只是下界，不等于完成时间，不排除传播、发包间隔、流水线和排队时序的作用。</p>
<h2>核验与保留的限制</h2><p>独立检查22,140条数据/ACK路径、116,280次物理发送、94,140次交换机哈希选择、738个工作回调；576个交换出口队列积分和72窗完成分解通过。分析重复四份产物逐字节一致，没有为复现再执行18次网络。全部条件无ECN/PFC/丢包，因此结论只涉及共享路径竞争，不证明拥塞控制长尾或生产收益。</p>
<p>仍只有四个请求；尚无独立请求集、现代通信库的匹配包级/控制语义和硬件校准。这批结果不足以支撑A类论文主贡献，也不支持将更好的配对贪心或新链路指标作为创新。下一阶段应针对简单链路基线解释不了的在线依赖/真实并发问题开展研究，先补语义与独立输入，再验证收益。</p>
<p><a href="metrics.json">全部窗口与比较</a> · <a href="verification.json">独立指标核验</a> · <a href="packet-verification.json">完整包级核验</a> · <a href="maintenance.json">命令及资源</a> · <a href="protocol.md">执行前方法</a> · <a href="archive.json">本机检查点</a></p></html>'''
    (args.output/'report.html').write_text(text)
    print(json.dumps(dict(passed=True,whole_run_counts=overall_counts),ensure_ascii=False))


if __name__=='__main__':main()
