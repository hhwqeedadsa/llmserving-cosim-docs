"""Explain conditional API opportunity and the competing channel-count baseline."""
import argparse
from html import escape
from pathlib import Path

from experiments.hybrid_completion_space.analysis.study import load,save


def execute(stage,output):
    assert load(stage/'verification.json')['passed'];output.mkdir()
    summary=load(stage/'analysis/summary.json');cases=summary['cases']
    assert all(r['tokens']==(693 if r['phase']=='prefill' else 15)*58 for r in cases)
    indexed={(r['ep'],r['channels'],r['binding'],r['phase']):r for r in cases}
    competition=[];table=[]
    for ep in (64,128,256):
        for binding in ('contiguous','spread'):
            four,eight,sixteen=[indexed[ep,c,binding,'prefill'] for c in (4,8,16)]
            residual=sixteen['residual_calls'];all_api=sixteen['after_factorized_api_calls']+sum(sixteen[k]['hidden_vectors'] for k in ('dispatch','combine'))
            fewer=100*(1-four['after_factorized_api_calls']/sixteen['after_exact_api_calls'])
            competition.append(dict(ep=ep,binding=binding,
                c16_factorized_control_calls=sixteen['after_factorized_api_calls'],
                c16_exact_control_calls=sixteen['after_exact_api_calls'],
                c4_factorized_control_calls=four['after_factorized_api_calls'],
                c4_fewer_calls_than_c16_exact_percent=fewer,
                c16_residual_percent_including_data_API=100*residual/all_api,
                latency_order_unknown=True))
            table.append([str(ep),binding,f"{four['residual_percent_after_factorized']:.2f}%",
                f"{eight['residual_percent_after_factorized']:.2f}%",f"{sixteen['residual_percent_after_factorized']:.2f}%",
                f'{100*residual/all_api:.2f}%',f'{fewer:.2f}%'])
    assert all(r['residual_calls']==0 for r in cases if r['phase']=='decode')
    result=dict(passed=True,cases=cases,channel_competition=competition,
        screening_passed=summary['screening_passed'],all_decode_residual_calls=0,
        input_prefill_tokens_per_layer=693,input_decode_tokens_per_layer=15,
        source_failure='first independent verifier used true rather than false for Dispatch kSyncAtStart literal; original verifier/log preserved, accounting inputs/results not rerun',
        network_runs=0,HTTP=0,GPU=0,builds=0,safe_new_protocol_verified=False,
        latency_or_simulation_speedup_measured=False,new_innovations_established=0,overall_goal_complete=False)
    save(output/'key_metrics.json',result)
    heads=['EP','源部署','C4剩余控制调用','C8剩余控制调用','C16剩余控制调用','C16含数据调用后的比例','C4普通参考比C16精确参考少调用']
    example=indexed[64,16,'contiguous','prefill']
    common=example['entry_barrier_signal_calls']+example['dispatch_count_put_calls']+example['dispatch_periodic_tail_calls']
    example_rows=[]
    for method in ('dense','source_channel_only','peer_only','factorized','exact'):
        d,c=(example[k][method] for k in ('dispatch','combine'))
        example_rows.append([method,f'{d:,}',f'{c:,}',f'{common+d+c:,}'])
    def table_md(headers,rows):
        return ['| '+' | '.join(headers)+' |','|'+'|'.join('---' for _ in headers)+'|']+['| '+' | '.join(row)+' |' for row in rows]
    lines=['# 分层MoE的结束通知：扣除强基线后，收益空间在哪里','',
        '**结论：decode没有比普通筛除方法更多的调用节省；prefill在C16时剩约10.4%～15.0%的控制API调用空间，但普通C4参考的调用数仍比C16精确参考少5.9%～27.8%。因此，不能将通道位图当成已成立创新，也不能将这些比例换算成时延收益。**','',
        '## 1. 这轮究竟研究什么','',
        '一台服务器中的多张GPU共享跨机路径时，DeepEP Hybrid会按目标服务器域去重发送。数据是稀疏的，协议的结束通知却未必稀疏：各声明通道仍需通知所有rail对端“我结束了”。接收方没有事先得到一致的活动信息时，不能因为这条通道最后没有数据就自行不等。','',
        '本轮核对固定DeepEP源码的Dispatch、Combine、屏障和GIN接口，用15份完整R1-AWQ/MMLU路由检查这部分请求工作量。每层同时安排15请求，prefill共693token，首次decode共15token；EP64/128/256，每服务器8卡，C4/8/16。contiguous将源放在rank0..14，spread将源分散到整个EP组。未用复制token填满空rank，没有真实到达时间。','',
        '输入固定58个MoE层3..60，36组聚合条件/2088层条件。激活声明BF16/H7168；这些是分层路径的需求核算，不是完整GPU部署或已执行的EP256。长prefill与大EP超出现有小型在线Hybrid子集的支持范围，本轮没有放宽其限制。','',
        '## 2. 为什么要先比较普通方法','',
        '只和全通道、全对端的dense通知比较，会把大量明显空源/空通道的节省误归给精确路由。强参考分别知道哪些通道有token、哪些对端有数据，再使用两者的笛卡尔积；精确参考仅补充“哪个通道确实发给哪个对端”。位图和稀疏all-to-all本来就是普通方法，均不能凭名字作为论文贡献。','',
        '这些筛除方法均是假定获得必要信息并正确改写等待后的反事实比较，当前固定库没有由本实验自动启用它们。通知的排队、ACK、源保护、跨epoch迟到数据、元数据一致性和计算开销仍需真正实现及验证；本轮没有将它们偷偷设成免费时延。','',
        '下表为prefill在58层的计数之和：剩余控制调用比例＝精确参考比因子化参考少的调用÷因子化后完整控制调用。分母仍保留两次rail入口屏障、两种计数块与每6份的Dispatch进度通知。','']
    lines+=table_md(heads,table)
    lines+=['',
        '18组首次decode条件中，扣除普通因子化参考后全部剩余差额为0。每个源只有1token，使用的通道集合与对端集合已经决定精确关联。这条结果使“decode普遍需要更复杂通道关联建模”的解释失去支持。','',
        '预先固定的筛选条件是prefill剩余控制API≥10%、裸bitmap小于双向隐藏数据1%。仅六组C16达到；不是新增网络实验成功或论文贡献成立。仅当实际高通道并行确有价值、这些通知又进入任务等待时，这个计数差额才可能值得进一步研究。','',
        '## 3. 看一组具体数字：EP64/C16/contiguous/prefill','',
        '以下是58层累计远端API调用，不是包数或WQE数。Dispatch与Combine分别计算，不能把一侧乘2：Combine入口汇集的原源/通道分布已经改变。','']
    lines+=table_md(['参考','Dispatch终止通知','Combine终止通知','保留其他控制后的全部控制'],example_rows)
    lines+=['',
        f"因子化后控制为{example['after_factorized_api_calls']:,}，精确关联后为{example['after_exact_api_calls']:,}，相差{example['residual_calls']:,}次。加入两方向token数据API调用后，比例为{competition[0]['c16_residual_percent_including_data_API']:.2f}%，不能再写成全部通信少15%。其中每层prefill token数相同，不意味着各层路由相同。",'',
        '## 4. 一个更简单的竞争解释：直接减少通道','',
        '已有C4数据提供了强替代方案：保留普通因子化参考，仅改变声明通道数，就能比C16精确关联使用更少控制调用，六组差额约5.9%～27.8%。这项比较由已固定的36组条件派生，没有增加有利输入或改变筛选阈值。','',
        '但更少调用不保证更快。C4可能降低GPU并发、QP并发及数据注入速率，C16可能更快送完大消息；两者还改变每通道token分布和每6份的进度次数。本轮没有实际GPU/网络服务成本，不能把C4叫作胜者，也不能在未击败它之前把C16通知筛除叫作优越方案。下一次判别必须同时比较通道数、计算/发布服务和实际共享网络完成。','',
        '## 5. API空间、元数据和网络收益分别说','',
        '结束通知属于64位加操作，源码中不消费其旧值；普通信号和计数PUT仍有真实传输义务。API请求可能被聚合或拆分，flush/ACK等还有额外服务，不能将一次API等同一个包，也不能从API数量推断端口字节或队列时间。','',
        '表中的隐藏数据只统计双向BF16向量，不含token元数据和线协议头。按每对端每阶段至少一个32位对齐mask字段核算，prefill的额外bitmap字节约为隐藏数据的0.005%～0.049%；尚未加入epoch/header、同步与GPU生成开销，不是实测协议成本。decode在EP256约2.24%，而额外调用节省仍为0。元数据小不能代替正确性或端到端收益。','',
        '本次不删除计数块、入口屏障、非空通道完成或源缓冲保护，也不把已有成熟选择性重传当作创新。零数据对端仍可能承担屏障；零数据通道也可能因丢失结束通知而影响完成，是否进入关键等待须由真实执行判定。','',
        '## 6. 与现有仿真平台的关系','',
        '| 对象 | 已有长处 | 本研究需要补的具体内容 |','|---|---|---|',
        '| ASTRA-sim及通用任务图 | 可接具体集合算法、依赖和网络后端 | 输入算法须区分分层数据、rail通知和应用完成；不声称图表达不了这些行为 |',
        '| ns-3/阿里RDMA库 | 包、端口队列、反馈及恢复模型 | 需要正确API到端点操作/包的映射；本轮未注入它们或测时延 |',
        '| SimAI-CLEM固定公开路径 | 真实CCL、持续QP、ACK/CQ及双向网络反馈已有实现 | 现代GPU发起的Hybrid需要版本匹配；不是因为使用真实路由就自动优于真实库 |',
        '| 本项目 | 可由本地真实路由和固定源码重建本轮需求及强参考 | 完整Hybrid、provider/GPU成本和网络效果仍缺；该核算不证明新协议或新仿真加速 |','',
        '竞品结论沿用已版本化的CLEM源码审计和平台比较，不新增竞品执行或速度排名。相对优势不能写成“他们都是随机”“他们没有ns-3”或“我们独有网络联动”。','',
        '## 7. 对两项创新的具体约束','',
        '第一项若继续研究“用更省成本的模型支持网络决策”，现在必须包含通道数与数据/控制交互的强参考；单凭稀疏token矩阵或密集通知数就不能选出优化方法。本轮把可能需要细化的范围限定到prefill较多通道，排除首次decode的这项额外关联收益。','',
        '第二项若继续研究“安全减少仿真处理”，必须保留那些可能改变共享队列、反馈和任务门槛的通知。协议本身合法减少通知，与仿真器把未消失的通知省略，是两个问题。现阶段既没有新协议的正确性，也没有安全事件省略及完整宿主加速证据。','',
        '候选下一步的成败条件是：在相同信息与资源条件下，对照通道调参、普通活动集合和完整目标协议，实际减少任务等待或仿真成本，并解释为何。若C4或普通参考已经达到相同效果，应淘汰该候选。两项主创新仍未成立。','',
        '## 8. 核验与保存','',
        '位图算法独立核对两方向活动集合、2088层/36聚合条件、进度阈值和预筛判据；四类篡改被拒绝。另以四个真实prefill前缀，公开Hybrid编译/绑定核对小R4/C4 Dispatch子集，不运行网络。固定缓存的四文件blob/来源及关键调用逐项核验；远端路由revision和新的commit根身份没有独立补取。','',
        '首次独立核验失败是Dispatch入口kSyncAtStart文字锚点误填true；原源码实际为false。保留旧核验器和失败日志，只修正文字锚点后离线核验，需求计数、输入及36条件没有重跑，未放宽语义或预算。','',
        '原件位于 `runs/hybrid_completion_space_20261027/`。归档、复制源码空cwd复算、命令和资源以recovery/closeout为准。零HTTP、原生构建、网络仿真、GPU和依赖修改。','']
    (output/'report.md').write_text('\n'.join(lines))
    html=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>分层MoE结束通知的收益空间</title><style>body{max-width:1120px;margin:34px auto;padding:0 22px;font:17px/1.9 system-ui;color:#172e40;background:#f4f7fa}h1{font-size:29px}h2{margin-top:38px}table{border-collapse:collapse;width:100%;background:white;font-size:14px}th,td{border:1px solid #c7d1db;padding:10px;text-align:left}.table{overflow-x:auto}.result{background:#e0f0e9;padding:16px;border-left:5px solid #287b63}a{color:#086c97}</style>']
    table_lines=[]
    def flush():
        if not table_lines:return
        parsed=[[cell.strip() for cell in line.strip('|').split('|')] for line in table_lines]
        html.append('<div class="table"><table>')
        for i,row in enumerate(parsed):
            if i==1:continue
            tag='th' if i==0 else 'td';html.append('<tr>'+''.join('<'+tag+'>'+escape(c)+'</'+tag+'>' for c in row)+'</tr>')
        html.append('</table></div>');table_lines.clear()
    for line in lines:
        if line.startswith('|'):table_lines.append(line);continue
        flush()
        if line.startswith('# '):html.append('<h1>'+escape(line[2:])+'</h1>')
        elif line.startswith('## '):html.append('<h2>'+escape(line[3:])+'</h2>')
        elif line:html.append('<p'+(' class="result"' if line.startswith('**') else '')+'>'+escape(line.strip('*'))+'</p>')
    flush();html.append('<p><a href="key_metrics.json">全部36组数据</a> · <a href="report.md">文字报告</a></p></html>\n')
    (output/'report.html').write_text(''.join(html))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('stage','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();execute(a.stage.resolve(),a.output.resolve())
