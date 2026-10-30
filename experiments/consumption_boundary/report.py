"""Publish source identities, exact logical probes and implementation decisions."""
import argparse
import html
import json
from pathlib import Path
import shutil

from audit import file_hashes, load, require, save, sha


DECISION = """# 局部恢复：实际消费与缓冲边界

本轮完成固定源码审计和CPU依赖检查，没有新增网络实验或恢复收益。旧低延迟、现代direct及hybrid不是同一完成语义；当前128端点Clos服务的`ep_transport_flush/1`是显式投影，不能将它的源ACK直接等同DeepEP的GPU可见或商用CQ，也不能因另一条路径屏障较少就删除原依赖。

## 新证据

1. 固定旧低延迟API的recv_hook启动整个接收阶段，没有逐专家参数；默认计算流等待通信流。内部每专家/每来源计数就绪，不等于应用已经得到可独立计算的专家结果。零token仍发布-1，不能靠有效载荷到齐代替全部通知。
2. 现代direct退出包含flush、全组信号与接收端等待。四份EP32真实选择投影均可达；暂扣三份受损Dispatch的目标visible，只阻塞逻辑rank7（物理端点28）的Dispatch，但全部32个Combine完成受阻。扣住其中一个源local_complete或一份全零专家计数块visible，两个阶段均全部受阻。它们是无时间依赖探针，不是丢包实测。
3. 暂扣最后Combine退出信号的源local_complete，应用仍全部可达，而该事件未完成。这证明当前图分别表达了消费与源完成；不证明物理缓冲已安全释放，也不能把信号完成直接叫作旧网络中的ACK。
4. hybrid Dispatch末尾只做scale-up屏障，但scale-out尾部已被转发者消费；hybrid Combine写有“No barrier at epilogue”，此前仍flush并等待每个scale-out成员的通道尾标记。不能概括成“没有等待”或“direct全组门限适用于所有路径”。
5. 若未经修改就让两个应用尝试向同一旧计数槽发布，-n-1被加两次后接收方会解成2n+1。若违规提前复用后原尝试迟到写入，逻辑代际不同也不能防止物理地址覆盖。这是对假想改法的CPU反例，不是原库合法用法的漏洞，也不是SR重复包被应用两次。
6. 旧低延迟离线编译器无法接受当前E256/EP32真实首调用：实际拒绝为event budget exceeded；仅16448个操作的四事件下界就已超过65536事件，操作数也超过16384上限。未放大预算或缩小规模。这是本平台离线表示能力缺口，不是DeepEP/GPU不能运行。

## 下一项实现应改哪里

| 对象 | 必须实现的行为 | 当前状态 |
|---|---|---|
| 应用消费 | 以请求/步/原始层/token/专家集合识别应有贡献，全部到齐后才推进，原/备用只合入一次 | 贡献核心已有逻辑提交；尚不替代实际原图 |
| 原/备用输出 | 在可能写入之前分配互不重叠的目标范围及独立通知；逻辑epoch必须映射实际隔离 | helper目前是声明身份/功能传输，不是CUDA缓冲隔离证明 |
| 原尝试收尾 | 保留所有已发工作、源确认、目标写入、计算占用；安全退出前不重用相应源/目标范围 | 现有执行保留原图/ACK；不能提前取消或释放 |
| 专家计算 | 将输入完整性、实际共享GPU队列开始/结束及贡献输出相连；不能从一个聚合expert节点臆造已完成局部结果 | 现有聚合节点不足以支持部分消费 |
| 后继调度 | 显式新模式区分应用就绪与原图退休，处理一rank一个活动图的现有限制；保留后续层、Decode、KV依赖 | 仍待设计和原生执行集成；不能只改Python统计 |
| 通信库适配 | 选择一条固定路径；改造真实消费/打包或隔离完整调用，保留该路径计数、顺序和复用条件 | 不能向现有DeepEP标志直接灌入备用结果 |

先实现带实际范围与写入生命周期的尝试隔离，再接应用消费和原图后台清理。仅新增逻辑赢家字段、放松组门限或伪造原ACK均不满足要求。新模式需要单独输入版本、公开命令、功能验收及128端点Clos实包对照；原默认与既有网络结果保留。

## 如何判断有没有研究价值

完整早备用仍是强基线。后续须保持同路由、计算/副本资源、SR/DCQCN、检测时机与故障，比较目标完整片段完成、健康任务完成、网络排空、额外有效/重传字节、实际声明计算和KV/隔离缓冲峰值。局部方案只有在这些成本计入后仍能减少重复工作或干扰，才有优化价值；不预填收益，也不把源码或依赖检查当论文创新。

当前只有两层两个Decode、15份输入中的四份目标选择；计算/布局/等价副本仍是声明。不是完整671B、生产故障分布、p99、硬件安全验证或商业收益。四个离线计划分别检查，不合并成一次网络执行，不用于和旧网络比较耗时。
"""


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audit', type=Path, required=True)
    p.add_argument('--checks', type=Path, required=True)
    p.add_argument('--monitors', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    analysis = load(a.audit/'analysis.json')
    checks = load(a.checks)
    require(analysis['passed'] and checks['passed'], 'unverified audit')
    out = a.output
    out.mkdir(parents=True, exist_ok=False)
    for name in ('analysis.json','sources.json','frozen.json'):
        shutil.copyfile(a.audit/name, out/name)
    shutil.copyfile(a.checks, out/'checks.json')
    shutil.copytree(a.audit/'inputs', out/'inputs')
    shutil.copytree(a.audit/'commands', out/'commands')
    resources = {p.parent.name: load(p) for p in sorted(a.monitors.rglob('probe.json'))}
    require(resources and all(r['status']=='completed' and r['child_exit_code']==0 for r in resources.values()), 'unsuccessful resource stage')
    require(sum(r['wall_seconds'] for r in resources.values()) <= 600, 'cumulative time exceeded')
    save(out/'resources.json', resources)
    summaries = {}
    for path in sorted((a.audit/'plans').glob('*/manifest.json')):
        summaries[path.parent.name] = load(path)
    save(out/'plans.json', summaries)
    (out/'README.md').write_text(DECISION)
    escape = html.escape
    names = {'all':'完整计划', 'missing_target_visibility':'三份受损输入的目标可见事件',
             'missing_source_completion':'一份受损输入的源完成',
             'zero_count_visibility':'一份零token专家计数块可见事件',
             'final_signal_completion':'最终Combine信号的源完成'}
    rows = ''
    for row in analysis['comparisons']:
        rows += '<tr>' + ''.join(f'<td>{escape(str(v))}</td>' for v in (
            row['input'], names[row['probe']], len(row['dispatch_blocked_ranks']),
            len(row['combine_blocked_ranks']), f"{row['reachable_events']:,}/{row['events']:,}")) + '</tr>'
    evidence = ''
    for source in load(a.audit/'sources.json'):
        evidence += f'<details><summary>{escape(source["path"])} · {source["commit"][:10]}</summary>'
        evidence += f'<p>SHA256：<code>{source["sha256"]}</code></p>'
        for excerpt in source['excerpts']:
            evidence += f'<a href="{escape(excerpt["url"],quote=True)}">源码 L{excerpt["first_line"]}–L{excerpt["last_line"]}</a><pre>{escape(excerpt["excerpt"])}</pre>'
        evidence += '</details>'
    peak = max(r['peak_sampled_group_rss_bytes'] for r in resources.values()) / 2**20
    seconds = sum(r['wall_seconds'] for r in resources.values())
    doc = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>局部恢复：数据可用、发送完成与缓冲复用</title>
<style>body{{margin:0;background:#f3f5f8;color:#172537;font:17px/1.8 system-ui,sans-serif}}main{{max-width:1120px;margin:32px auto;padding:0 22px}}section{{background:white;border:1px solid #dde3ec;border-radius:14px;padding:24px;margin:18px 0}}h1{{font-size:30px;line-height:1.4}}h2{{font-size:23px}}small{{color:#526278}}table{{width:100%;border-collapse:collapse;font-size:15px}}th,td{{text-align:left;padding:12px;border-bottom:1px solid #dde3ec;vertical-align:top}}th{{background:#eef3fa}}.scroll{{overflow:auto}}.lead{{border-left:5px solid #1862a8;padding-left:18px}}code,pre{{font-size:13px;overflow-wrap:anywhere}}pre{{overflow:auto;white-space:pre-wrap;background:#f3f5f8;padding:12px}}summary{{cursor:pointer;font-size:14px}}details{{margin:12px 0;border-bottom:1px solid #dde3ec;padding-bottom:10px}}a{{color:#145d9c}}.cards{{display:flex;gap:18px;flex-wrap:wrap}}.cards p{{flex:1;min-width:180px;background:#eef3fa;padding:16px;border-radius:10px}}.cards b{{font-size:28px}}</style>
<main><small>2026-10-30 · 固定源码审计 · 沿用128端点Clos中的EP32真实选择 · 零新网络</small>
<h1>局部恢复不能只看“备用结果到了没有”</h1>
<p class="lead">还必须回答：应用能否消费这份结果、原传输是否完成、旧写入还会不会落到已复用缓冲。本轮核实这三个边界，为后续实现排除会产生虚假加速或内存覆盖的改法。</p>
<section><h2>这一轮实际交付了什么</h2><div class="cards"><p><b>9</b><br>份源码核验提交、SHA256及Git blob</p><p><b>4</b><br>份真实选择的EP32单调用计划</p><p><b>8＋11</b><br>项依赖探针与篡改拒绝检查</p></div><p>只读取既有网络文件，全部哈希保持；CPU检查累计{seconds:.2f}秒，阶段组RSS采样峰值{peak:.1f}MiB。这里没有新增GPU/网络运行、时延收益或安全性证明。</p></section>
<section><h2>三个路径，三种完成边界</h2><div class="scroll"><table><tr><th>路径</th><th>源码实际等待</th><th>对局部恢复的含义</th></tr>
<tr><td>旧低延迟</td><td>按来源/专家等计数、打包；Combine等专家flag后归并。recv_hook启动完整接收阶段。</td><td>内部细粒度通知不是公开逐贡献消费接口；零token通知仍必要。</td></tr>
<tr><td>现代direct</td><td>退出flush与全组信号；Dispatch打包/展开、Combine归并还有本地边界。</td><td>不能把一条DATA到达直接当整组完成；源完成与目标可见仍需区分。</td></tr>
<tr><td>现代hybrid</td><td>Dispatch末尾保留scale-up屏障；Combine无统一尾部屏障，但此前flush并等所有scale-out通道尾标记。</td><td>不能概括为“不等待”；当前direct计划也不能替代hybrid实测。</td></tr></table></div>
<p>当前服务<code>ep_transport_flush/1</code>是已声明的完成投影；本轮没有修改它，也没有把这些不同路径的时间混为一个性能对照。</p></section>
<section><h2>真实选择投影后的依赖检查</h2><p>来自原层3/4及两个Decode入口，每份4个真实token、E256、Top8、H7168。逻辑rank7对应物理模拟端点28；将图事件暂扣，只计算能否继续，不预测等多久。</p>
<div class="scroll"><table><tr><th>输入</th><th>暂扣什么</th><th>Dispatch受阻rank数</th><th>Combine受阻rank数</th><th>可达事件</th></tr>{rows}</table></div>
<p>最后一项所有应用完成仍可达，但源完成尚未发生，说明“可以消费”和“资源已经退出”确实不是同一件事。这不证明目标GPU可见性或缓冲寿命已经硬件验证。</p></section>
<section><h2>为什么不能直接替原尝试写flag</h2><p>旧Dispatch计数按<code>-n-1</code>编码。若两个应用尝试都向同一槽原子相加，接收端会解成<code>2n+1</code>，而不是n。传输选择性重传的去重不能代替两个应用尝试的去重。</p>
<p>若在旧尝试仍有写入时提前复用内存，迟到旧数据会覆盖新数据；只改逻辑epoch不会拦住写向同一地址的操作。需要隔离实际目标范围、通知以及生命周期。反例针对未经实现的改法，<strong>不是发现原DeepEP合法执行存在漏洞</strong>。</p></section>
<section><h2>同时发现的平台覆盖缺口</h2><p>旧低延迟计划在本次E256/EP32首调用实际返回<code>event budget exceeded</code>。仅16,448个操作的四事件下界就超过65,536事件，操作数也超过16,384上限。错误已保留；没有扩预算或缩小专家数。它是本平台离线编译器的限制，不是DeepEP或GPU的规模限制。</p></section>
<section><h2>下一步怎么改，怎样验收</h2><ol><li>先让原/备用拥有互不重叠的实际输出与通知范围；所有可能迟到的写入退出后才能复用。</li><li>将贡献的一次提交接到真实计算/归并消费，区分应用就绪与原图后台收尾；后继层、Decode和KV依赖全部保留。</li><li>用同资源的完整早备用作强基线，再跑128端点Clos对照。同时报告目标、健康任务、排空、字节、计算与缓冲代价。</li></ol>
<p>现有贡献核心和helper已提供部分生命周期能力，但不能因此声称新消费接口已接通。候选是否有创新价值，要看完整成本计入后是否仍有净收益。</p></section>
<section><h2>可复核结果与源码</h2><p><a href="README.md">详细结论与实现清单</a> · <a href="analysis.json">依赖结果</a> · <a href="sources.json">源码身份</a> · <a href="checks.json">11项拒绝检查</a> · <a href="plans.json">计划摘要</a> · <a href="resources.json">资源记录</a> · <a href="inputs/s0-l0.json">独立EP32输入</a></p>{evidence}</section>
<p><small>范围：两层两步的专家选择与声明布局，不是完整671B数值推理、生产故障率、p99、现代provider性能或商业收益。报告为静态HTML，可直接打开。</small></p></main></html>'''
    (out/'report.html').write_text(doc)
    save(out/'publication.json', dict(passed=True, artifacts=file_hashes(out),
                                     audit_analysis_sha256=sha(a.audit/'analysis.json'),
                                     generated_by=sha(Path(__file__))))
    print(json.dumps(dict(passed=True, output=str(out), files=len(file_hashes(out)))))


if __name__ == '__main__':
    main()
