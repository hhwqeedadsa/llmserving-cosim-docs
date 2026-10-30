"""Publish measured outcomes and their limits without rerunning any network."""
import argparse
import html
import json
from pathlib import Path
import shutil


def load(p):return json.loads(p.read_bytes())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('analysis','figures','frozen','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    r=load(a.analysis/'analysis.json');assert r['passed'];cases=r['cases']
    for src in (a.analysis/'analysis.json',a.frozen):shutil.copyfile(src,out/src.name)
    metrics=out/'metrics';metrics.mkdir()
    for src in a.analysis.glob('*.csv'):
        # The delivered and hashed text has the same LF bytes Git will store.
        (metrics/src.name).write_text(src.read_text())
    shutil.copytree(a.figures,out/'figures')
    c,t=(cases[k+'-local'] for k in ('control','treatment'))
    gain=c['result_ready_ns']['target']-t['result_ready_ns']['target']
    percent=100*gain/c['result_ready_ns']['target'];extra=t['unique_payload_bytes']-c['unique_payload_bytes']
    pct_bytes=100*extra/c['unique_payload_bytes'];pct_compute=100*(t['declared_compute_work_ns']/c['declared_compute_work_ns']-1)
    rows=''
    for condition in ('normal','local'):
        for policy in ('control','treatment'):
            v=cases[policy+'-'+condition]
            rows+=f'''<tr data-condition="{condition}"><td>{condition}</td><td>{policy}</td>
            <td>{v['result_ready_ns']['target']/1000:.3f}</td><td>{v['result_ready_ns']['healthy']/1000:.3f}</td>
            <td>{v['network_drained_ns']/1000:.3f}</td><td>{v['unique_payload_bytes']:,}</td><td>{v['fault_drops']}</td></tr>'''
    links=''.join(f'<li><a href="metrics/{html.escape(src.name)}">{html.escape(src.name)}</a></li>' for src in sorted(metrics.glob('*.csv')))
    page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>128端点Clos：备用收益与独立任务干扰</title><style>
    :root{{color-scheme:light;--ink:#243448;--muted:#566679;--line:#dce4e9;--green:#197866}}
    *{{box-sizing:border-box}}body{{margin:0;background:#f3f6f8;color:var(--ink);font:16px/1.8 system-ui,-apple-system,"Noto Sans CJK SC",sans-serif}}
    main{{max-width:1120px;margin:auto;padding:32px 22px 65px}}h1{{font-size:30px;line-height:1.4}}h2{{font-size:22px;margin-top:34px}}h3{{font-size:18px}}
    .lead{{font-size:19px}}.muted{{color:var(--muted);font-size:14px}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}}
    .card,section{{background:white;border:1px solid var(--line);border-radius:12px;padding:20px;margin:15px 0}}
    .card strong{{display:block;font-size:30px;color:var(--green)}}.card p{{margin:0}}section h2{{margin:0 0 12px}}
    table{{width:100%;border-collapse:collapse;font-size:14px}}td,th{{padding:10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}td:first-child,th:first-child,td:nth-child(2),th:nth-child(2){{text-align:left}}
    .scroll{{overflow-x:auto}}img{{width:100%;height:auto;border-radius:7px}}a{{color:#176481}}code{{background:#edf2f5;padding:2px 5px;border-radius:4px}}button{{padding:7px 14px;margin:5px 5px 10px 0;border:1px solid #a8bbc7;background:white;border-radius:6px;cursor:pointer}}button[aria-pressed="true"]{{background:#23475b;color:white}}li{{margin:6px 0}}.note{{border-left:4px solid #b58142;padding:8px 16px;background:#fff8ed}}
    @media(max-width:720px){{.cards{{grid-template-columns:1fr;gap:0}}main{{padding:18px 12px}}h1{{font-size:25px}}section{{padding:15px}}}}
    </style><main><p class="muted">2026-10-30 · 四次新网络执行，各一次 · 已保存包记录独立分析</p>
    <h1>备用只让故障任务快了3.72%；本轮没有观察到健康任务受拖累</h1>
    <p class="lead">我们把两个独立任务放进同一个128端点Clos网络，检查一个任务启动备用执行后，会不会拖慢另一个任务。结果是：备用有小幅收益和明确资源代价，但健康任务已先结束，不能用这轮结果证明跨任务干扰。</p>
    <div class="cards"><div class="card"><strong>{gain/1000:.3f} µs</strong><p>故障目标任务少等 {percent:.2f}%</p></div>
    <div class="card"><strong>+{pct_bytes:.2f}%</strong><p>两任务合计有效通信量</p></div>
    <div class="card"><strong>0 ns</strong><p>健康任务完成时间变化</p></div></div>
    <section><h2>normal 和 local 是什么</h2><p><code>normal</code>：不人为注入故障，仍保留正常排队和拥塞控制。<code>local</code>：仅在仿真前20微秒，将到达GPU 28的符合规则的DATA包全部丢弃；ACK等控制包保留。这次实际丢了6包，不能称为全网100%丢包。</p>
    <p>两者都是跨服务器Clos网络，local不是本机通信。故障端点在运行前按目标任务首层Dispatch接收字节量确定，不根据运行结果选择。</p>
    <p><code>control</code> 与 <code>treatment</code> 是另一维度：前者两个任务的备用门槛均为4毫秒，本轮均未触发；后者只有目标任务改为70微秒，健康任务仍为4毫秒。备用是完整两层片段的执行，原任务仍按相同选择性重传和参考DCQCN恢复，已经启动的尝试均完成并清理。</p></section>
    <section><h2>完成时间和实际代价</h2><div role="group" aria-label="筛选网络条件"><button aria-pressed="true" onclick="filterCases('all',this)">全部</button><button aria-pressed="false" onclick="filterCases('normal',this)">normal</button><button aria-pressed="false" onclick="filterCases('local',this)">local</button></div>
    <div class="scroll"><table><thead><tr><th>网络条件</th><th>策略</th><th>目标完成 µs</th><th>健康完成 µs</th><th>网络排空 µs</th><th>有效字节</th><th>故障丢包</th></tr></thead><tbody>{rows}</tbody></table></div>
    <img src="figures/completion.png" alt="四条件目标任务、健康任务完成和网络排空时间">
    <p>正常条件下，目标在67.255微秒完成，因此没有启动备用；两策略的完整包记录与原始队列事件完全相同。局部故障下，目标由135.819降到130.771微秒，但网络排空由135.819推迟到136.278微秒，晚459纳秒。</p>
    <p>故障备用额外产生{extra:,}字节有效载荷；目标自身执行双份工作，连同健康任务后总有效通信量增加{pct_bytes:.2f}%，总声明计算工作量增加{pct_compute:.2f}%。实际源端数据载荷还包含43,392字节重传，不能把有效载荷当作完整线上开销。</p>
    <p>四条件均预留46,792,704字节备用KV，其中目标10,092,544字节、健康36,700,160字节。未启动备用在其主任务完成时释放；启动备用在其实际完成时释放，终态均为0。完整专家与等价ready-KV是预先存在的声明条件，未模拟其建立成本或验证数值等价。</p></section>
    <section><h2>为什么没有测到对健康任务的拖累</h2>
    <img src="figures/shared-port-activity.png" alt="71个共同出口上的数据活动和69.995至72.211微秒的关键时间点">
    <p>故障条件下，健康任务在69.995微秒完成，目标备用70.000微秒启动。备用第一包71.030微秒从源端发送，72.211微秒进入交换机，比健康任务完成晚2.216微秒。</p>
    <p>两者确实经过{len(t['shared_backup_healthy_ports'])}个相同交换机出口，但数据活动没有时间重叠。健康任务的2,136次交换机经过全部配对，驻留时间增量均为0；消息就绪、接收、确认、计算节点和阶段门限时刻也完全相同。</p>
    <p class="note">这只能说明当前有限负载没有暴露备用对健康任务的干扰，不能说明备用在持续服务中没有风险。也不能把71个共同出口直接当成71处拥塞。</p></section>
    <section><h2>系统本轮实现了什么</h2><ul>
    <li>两个独立任务可以分别设置备用等待期限，用唯一原生定时器处理最早期限，仍在同一个ns-3网络时钟上推进。</li>
    <li>真实LLMServingSim调度器保留各任务的请求与KV生命周期；原图、阶段门限、接收确认和落后尝试均实际执行。</li>
    <li>70项CPU检查、六份旧结果只读复核，以及两份真实多任务结果的迁移检查通过；12类篡改在两条件下共24项被拒绝。没有新建或重编后端与依赖。</li>
    </ul><p>这些是平台能力与验证工作，不是新恢复算法的创新收益。参见<a href="../../../../fixtures/multicall_deadline/README.md">平台验收</a>与<a href="../../../../examples/scaleout_multicall/README.md">公开双任务示例</a>。</p></section>
    <section><h2>实验服务于什么，限制在哪里</h2><p>目标是解释MoE跨机通信长尾，并判断在相同部署下，成熟备用机制能减少多少等待、付出多少额外通信与计算，以及是否拖累其他任务。</p>
    <ul><li>128模拟GPU端点、16服务器；8接入、8汇聚、4核心交换机，各层400Gbps、每链路1微秒，单rail。四个EP32组，每rank每层8个专家，256个路由专家、Top8、hidden 7168。</li>
    <li>15份不同的R1-AWQ/MMLU真实专家选择原件：目标4份、健康11份，不克隆为独立请求。只回放原层3/4、首个Decode入口、一个Decode步。30条请求清单包含15条备用尝试，不是30份独立原始请求。</li>
    <li>正常条件56个rank实际参与数据通信，故障备用条件81个；128端点不表示128张GPU满载。前端将专家选择转换为按目的rank去重的Dispatch/Combine、声明计算及依赖，后端产生模拟数据包；不是实际运行R1抓到的网络流量。</li>
    <li>原4份具有已核验固定版本身份；新增11份只有用户确认目录和本机SHA，远端修订身份仍未知；两份缺层文件被排除。没有下载更多数据。</li>
    <li>计算成本与BF16消息布局是声明模型，原专家路由由AWQ采集；没有GPU实机执行或A100/H100校准，没有完整671B模型、完整请求、持续到达、吞吐、SLO或经济收益结论。</li></ul>
    <p>后续应优先支持独立任务持续到达或自然多步执行，在运行前固定负载和评价方法，再检查恢复与健康工作的真实重叠。不能回头调整本轮70微秒门槛、请求数或故障位置来制造正面研究结果。新策略收益仍未证明。</p></section>
    <section><h2>可复核证据</h2><p>原生入队钩子记录加入后的字节，出队钩子记录扣除前的字节。分析逐包对齐两类事件，恢复操作后占用，核对所有队列最终为0；积分包含控制和数据包，排除正在串行发送的字节。第一次混合积分的分析失败日志保留，没有重跑网络。</p>
    <p><a href="analysis.json">独立分析JSON</a> · <a href="frozen.json">执行前固定配置</a> · <a href="receipt.json">执行收据</a> · <a href="resources.json">资源</a> · <a href="final-audit.json">交付核查</a> · <a href="../../protocol.md">事前方法</a> · <a href="README.md">复现与结论</a></p>
    <details><summary>逐包驻留、匹配和归一化队列CSV</summary><ul>{links}</ul></details></section>
    <script>function filterCases(condition,button){{document.querySelectorAll('tbody tr').forEach(row=>{{row.hidden=condition!=='all'&&row.dataset.condition!==condition}});button.parentElement.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b===button)))}}</script></main></html>'''
    (out/'report.html').write_text(page)
    print(json.dumps(dict(passed=True,figures=2,metric_files=len(list(metrics.glob('*.csv'))),new_network_executions=0)))


if __name__=='__main__':main()
