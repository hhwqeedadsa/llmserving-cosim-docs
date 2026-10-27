"""Chinese report of an ordinary baseline; no claims of network speedup."""
import argparse
from html import escape
from pathlib import Path

from experiments.input_graph_baseline.analysis.study import load,save,raw,digest,reduce_graph,factor


def recompute(stage,output):
    results=[]
    for name in load(stage/'state.json')['completed']:
        base=stage/'computed'/name;original=load(base/'original.json')
        reduced=reduce_graph(original);factored=factor(reduced)
        assert reduced==load(base/'reduced.json') and factored==load(base/'factored.json')
        results.append(dict(name=name,reduced_sha256=digest(reduced),factored_sha256=digest(factored)))
    save(output,dict(passed=True,results=results,network_runs=0))


def execute(stage,output):
    assert load(stage/'verification-source-selection.json')['passed'];output.mkdir()
    records=[];table=[]
    for name in load(stage/'state.json')['completed']:
        base=stage/'computed'/name;m=load(base/'metrics.json');g=load(base/'guards.json')
        reduction=100*(1-m['factored_edges']/m['original_edges'])
        data=sum(o['kind']=='token_data' for o in g['operations']);control=m['operations']-data
        guard_bytes=len(raw(g));dep_saved=m['original']['canonical_json_bytes']-m['factored']['canonical_json_bytes']
        # The immutable guard section is a lower bound on common metadata.
        upper=100*dep_saved/(guard_bytes+m['original']['canonical_json_bytes'])
        records.append(dict(name=name,nodes=m['nodes'],operations=m['operations'],control_operations=control,
            data_operations=data,original_edges=m['original_edges'],reduced_edges=m['reduced_edges'],
            factored_edges=m['factored_edges'],join_nodes=m['join_nodes'],edge_reduction_percent=reduction,
            dependency_bytes={k:m[k] for k in ('original','reduced','factored')},
            immutable_guards_canonical_bytes=guard_bytes,dependency_canonical_bytes_saved=dep_saved,
            whole_serialized_fraction_upper_percent=upper))
        table.append([name,str(m['operations']),str(control),str(m['original_edges']),str(m['factored_edges']),
                      f'{reduction:.2f}%',f"{m['original']['canonical_json_bytes']:,} → {m['factored']['canonical_json_bytes']:,}",
                      f'{upper:.2f}%'])
    summary=dict(passed=True,cases=records,network_runs=0,GPU=0,HTTP=0,builds=0,
        original_compilation_cases=6,complete_collective_or_Hybrid_verified=False,
        wall_clock_or_CPU_speedup_measured=False,main_innovations_established=0,overall_goal_complete=False,
        decision='ordinary graph optimizations belong to the strong baseline; dependency serialization is not demonstrated as a valuable mainline',
        limits='direct single-layer declared model; dynamic guards retained; no network or runtime.fire')
    save(output/'summary.json',summary)
    heads=['条件','全部操作','控制操作','原依赖边','共享join后边','边减少','依赖表字节前→后','完整表示节省上界']
    lines=['# 直接从真实输入生成普通图，常规优化已能做到什么','',
        '**结论：本轮把完整普通任务图基线补强了，但没有得到MoE特有的新方法或仿真加速。六组中，普通传递约简与共享join减少了约23%～37%的依赖边；依赖表本身却很小，全部操作及动态约束仍必须处理。**','',
        '八份用户提供的R1-AWQ/MMLU文件，各取entry1或entry1..2/layer3，形成batch8/16，以轮转方式放入EP4/8/16。E256、Top8、H7168，激活采用声明的BF16；AWQ模型来源不等于仿真的激活精度或硬件校准。空rank不复制token。EP是本次单层专家并行组大小，不是完整模型卡数。','',
        '## 对照怎样做','',
        '原图直接来自公开输入编译器和静态依赖接口，没有读取任何已执行网络绑定或未来truth。包括运行时补入的同token全部slot同步；本轮不调用fire，不确定动态slot或Combine通道。节点改用连续整数，先消除长字符串重复，再做通用传递约简和重复前驱集合共享。三个表示保留相同原事件和全部操作、slot、binding、动态约束；join只是“这一组前驱全完成”的零时长逻辑门。','',
        '传递约简的含义是：A必须先于B、B必须先于C时，图中不再另存A→C；共享join的含义是：多处等待同一组前驱时，只保存一份该组。它们都属于普通图算法，不能算我们的创新。','',
        '| '+' | '.join(heads)+' |','|'+'|'.join('---' for _ in heads)+'|']
    lines+=['| '+' | '.join(row)+' |' for row in table]
    lo=min(r['whole_serialized_fraction_upper_percent'] for r in records);hi=max(r['whole_serialized_fraction_upper_percent'] for r in records)
    lines+=['',
        '“完整表示节省上界”只计算canonical JSON：节省的依赖表字节÷（原依赖表＋不可删除的operation/slot/动态约束描述）。分母还没有计其他共同元数据，所以只是宽松的存储比例上界，不能当RSS或运行加速。'+f'六组为{lo:.2f}%～{hi:.2f}%。',
        '',
        '## 为什么这与研究收益有关','',
        '最大的整数依赖表为104,206B，gzip后27,926B；共享join后为71,375B，gzip后21,981B。EP4中共享join虽进一步减边，gzip却比只做传递约简略大。必须分别报告结构大小、压缩存储和执行成本，不能把边数百分比直接当仿真提速。','',
        'EP16/batch16的1710项操作中，1536项属于当前direct模板控制，174项是token数据操作。图约简不删除这些操作。此比例不是生产DeepEP/Hybrid的大EP控制开销，也不说明这些消息在真实库中都可删。它说明：对本平台而言，若只改变依赖图写法，很可能触及不到主要工作。','',
        '## 对两项创新候选的取舍','',
        '第一候选仍需证明：相对正确分层算法、普通符号buffer和已优化完整图，在什么输入下能以更低完整成本保持网络决策可靠。这里没有展示MoE特有信息增益，整数化、传递约简或共享join全部归入强基线。下一项不能再用长字符串原图作弱对照。','',
        '第二候选仍需证明：在共享队列、ACK/CNP/重传和应用反馈下，哪些实际执行可安全减少，以及完整流程能节省多少。这里减少的是存储边，没有少发包、少做操作或执行现代Hybrid，不能将其写成网络优化。','',
        '本轮没有足够证据推进“压缩静态依赖表”作为论文主线。真正值得继续判别的是已有控制/数据算法和实际处理的成本：先限定完整分层路径的目标协议，再确认普通惰性展开/增量执行已经做到什么；若没有强基线之外的收益，正常淘汰，而非继续换名称。两项创新尚未成立。','',
        '## 核验及边界','',
        '独立拓扑排序与位集闭包逐项核对全部原事件可达关系；join展开及含join闭包也一致。配置反查八原件选中的token，按真实目的rank独立核算操作数量。必要边删除、反向边、join篡改及动态约束删除四类拒绝。没有完整CCL、网络包、时延、GPU、千卡部署、同资源算法收益或商业ROI证明。CPU/墙钟仅作为本离线命令诊断保留，未做全流程配对测量。','',
        '原件与实际命令位于 `runs/input_graph_baseline_20261027/`。方法、离线入口、逐成员hash归档、复制源码空cwd复算及实际预算均由README/recovery/closeout给出。原先全部已关闭的网络和HTTP条件没有重开。','']
    (output/'report.md').write_text('\n'.join(lines))
    rows=''.join('<tr>'+''.join('<td>'+escape(c)+'</td>' for c in row)+'</tr>' for row in table)
    html=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>输入生成普通任务图强基线</title><style>body{max-width:1180px;margin:36px auto;padding:0 22px;background:#f4f7fa;color:#15283a;font:17px/1.85 system-ui}h1{font-size:29px}h2{margin-top:38px}table{border-collapse:collapse;background:white;font-size:14px;width:100%}td,th{padding:9px;border:1px solid #c6d1dc;text-align:left}section{overflow-x:auto}.result{border-left:5px solid #237466;padding:14px;background:#e3f1ed}a{color:#086c97}</style>']
    in_table=False
    for line in lines:
        if line.startswith('|'):
            if not in_table:
                html.append('<section><table><tr>'+''.join('<th>'+escape(c)+'</th>' for c in heads)+'</tr>'+rows+'</table></section>');in_table=True
            continue
        if line.startswith('# '):html.append('<h1>'+escape(line[2:])+'</h1>')
        elif line.startswith('## '):html.append('<h2>'+escape(line[3:])+'</h2>')
        elif line:html.append('<p'+(' class="result"' if line.startswith('**') else '')+'>'+escape(line.strip('*'))+'</p>')
    html.append('<p><a href="summary.json">六组数据</a> · <a href="report.md">文字报告</a></p></html>\n')
    (output/'report.html').write_text(''.join(html))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['report','recompute'])
    for name in ('stage','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    (execute if a.action=='report' else recompute)(a.stage.resolve(),a.output.resolve())
