"""Scientific figure and Chinese review; never executes a network."""
import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import re


def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def references(repo,run):
    target=run/'references';assert not target.exists();target.mkdir()
    index=[]
    for name,url in (
        ('clem.md','https://github.com/aliyun/SimAI-CLEM'),
        ('vidur.md','https://github.com/microsoft/vidur'),
        ('simai.md','https://github.com/aliyun/SimAI'),
        ('llmservingsim2.txt','https://arxiv.org/html/2602.23036v2')):
        p=repo/'data/research/system_innovation_20261021'/name
        (target/name).write_bytes(p.read_bytes())
        index.append(dict(name=name,url=url,sha256=sha(p),scope='previously_saved_primary_source_read_locally_this_turn'))
    for name in ('sources.json',):
        p=repo/'experiments/network_abstraction_audit/results/20261027'/name
        (target/('prior-'+name)).write_bytes(p.read_bytes())
    prior=load(target/'prior-sources.json')
    for name,expected in (
        ('parsimon-paper.txt',prior['parsimon']['paper_text_sha256']),
        ('mimicnet-readme.txt',prior['mimicnet']['readme_sha256'])):
        p=repo/'runs/network_abstraction_audit_20261027/sources'/name;assert sha(p)==expected
        (target/name).write_bytes(p.read_bytes());index.append(dict(name=name,sha256=expected))
    save(target/'index.json',dict(sources=index,new_http=0,
        scope='cached_versions_not_all_current_branches; no competitor execution'))


def report(run,out):
    assert not out.exists();out.mkdir(parents=True)
    verification=load(run/'verification.json');assert verification['passed']
    summary=load(run/'analysis/summary.json');strong=load(run/'marginals/summary.json')
    index=load(run/'references/index.json')
    for row in index['sources']:assert sha(run/'references'/row['name'])==row['sha256']
    groups={(r['batch'],r['ep']):r for r in summary['grouped']}
    matched={(r['batch'],r['ep']):r for r in strong['rows']}
    covers={(r['phase'],r['ep']):r for r in summary['coverage']}
    eps=sorted(r for b,r in groups if b==8)
    table='\n'.join(f"| {ep} | {256//ep} | {covers['decode',ep]['unique_destination_count']['mean']:.3f} | "
        f"{groups[8,ep]['remote_vector_count']['mean']:.3f} | "
        f"{groups[8,ep]['observed_collision_to_uniform_expectation']:.3f} | "
        f"{matched[8,ep]['observed_to_shift_expectation']:.3f} |" for ep in eps)
    control='\n'.join(f"| {r['ep']} | {r['remote_control_operations']:,} | {r['control_operations']:,} | "
        f"{'编译计数已核；未运行网络' if r['compiler_executed'] else '仅按直接模板代数外推'} |"
        for r in summary['control']['conditional_template_counts'])
    b8=groups[8,256];m8=matched[8,256]
    byte_ratio=b8['remote_vector_count']['mean']/b8['uniform_expected_remote_vectors_per_wave']
    md=f'''# 从四卡到大EP：哪些真实路由差异值得仿真

2026-10-27。**同一批真实专家路由，EP4与EP256对应的网络问题不同。** 四卡模型不能直接代表大EP；同时，和均匀随机的偏差不能全部归因于动态热点。本轮完成离线需求与两个解析参考的独立核算，未执行EP8～256的包级网络，也未得到优化收益。

## 输入、范围和约束

15份完整DeepSeek-R1-AWQ/MMLU文件，693个Prefill token、1811个Decode记录，61层中58个MoE层，256专家、Top8。899/1479的缺层原件没有进入统计。来源目录已由用户确认，新增远端修订内容身份未独立核实；没有创建伪称已验证固定版本的manifest。

每EP组的专家连续等容量放置。所有EP条件使用完全相同的请求、层和步骤，四个源rank不变，组内请求序号模4分配源。每token对同一目的rank只投影一份H7168×2B的参考激活数据。它不是实测激活dtype或GPU性能，不包含控制、元数据、Combine、重传或ACK；没有拓扑、包到达时刻和队列，因此不能计算FCT、P99或服务收益。

B4按文件数字顺序形成三组，共15950个层×步骤波次，未分组请求3份、组内多余Decode步骤327个；B8形成一组，共4176波次，共同前缀72步，未分组7份、组内多余步骤339个。相邻步骤和58层不能当独立请求；当前B8结果只覆盖这一组8个真实请求。波次是同步分析窗口，不是实测并发到达时间。

## 1. 同样的总字节，可以对应不同的目的卡聚集

配对碰撞指一个波次中，不同token对同一远端目的rank各发送一次数据。一个目的rank收到n个不同token，计n(n−1)/2对。它描述潜在竞争，不等于实际网络排队。

| EP组rank数 | 每rank专家数 | 全部Decode样本每token平均目的rank数，含本地 | B8每波次平均远端副本 | 碰撞/均匀随机期望 | 碰撞/保留各请求热度的期望 |
|---|---:|---:|---:|---:|---:|
{table}

表中目的rank平均数覆盖全部15份的Decode，后三列只覆盖固定B8组，分母不同，不能直接相乘。完整B4和逐层结果见summary.json及marginals.json。

EP256下，B8平均远端数据副本数为{b8['remote_vector_count']['mean']:.3f}，均匀参考为{b8['uniform_expected_remote_vectors_per_wave']:.3f}，总量只差约{(byte_ratio-1)*100:.3f}%；配对碰撞却是均匀参考的{b8['observed_collision_to_uniform_expectation']:.3f}倍。**仅核对总通信量，会漏掉目的位置的差异。** EP4只有256专家合并后的四个目的rank，而且源在0～3；专家热度与本地旁路共同改变投影，因此不能把跨EP差值解释成同资源优化收益。

## 2. 更强参考削弱了“动态热点”的解释

均匀参考从256专家无放回选8个，token间独立，不是任何竞品平台的实跑结果。第二个参考保留每份请求完整的rank集合、边际热度及循环顺序，只在解析上给各请求独立均匀循环平移，改变彼此的步骤对齐；没有实际打乱文件、生成新请求或运行网络。

B8/EP256实际碰撞是第二个参考的{m8['observed_to_shift_expectation']:.5f}倍，即约高{(m8['observed_to_shift_expectation']-1)*100:.2f}%。B4/EP256仅高{(matched[4,256]['observed_to_shift_expectation']-1)*100:.2f}%。这说明现有组中相对均匀随机的大部分偏差已能由请求本身的长期热度解释；剩余差异不能直接命名为可预测的动态热点，更不是可消除的时延或收益率。没有显著性检验、独立生产留出或未来路由预测。

两种参考均保留：不能只展示均匀随机的较大差异，也不能因为更强参考解释了大部分碰撞就认定网络等待必然相同。是否影响网络方案，仍须让这些请求在相同网络资源、真实就绪条件和较强基线下执行。

## 3. 当前直接控制模板不能机械放大

现有现代符号模板每调用有4个全rank屏障、rank计数和expert计数，分别是4R²、R²、R²个操作；远端控制数6R(R−1)。下表是该模板的条件计数，**不是大规模DeepEP真实算法的开销预测**。

| R | 远端控制操作 | 含self的控制操作 | 证据 |
|---|---:|---:|---|
{control}

R4/R8通过原支持范围内的公开编译接口、零token控制用例核对；没有GPU或网络运行。当前符号入口rank上限32，操作硬上限16384；R64仅控制就需24576项。实际MRT1原生桥仍只支持2～4rank、最多64条预声明连接。因此千卡拓扑、单个EP256组、256个EP4副本和完整千卡服务是四种不同任务，不能互相替代。

下一步应按目标通信实现定义分层控制、连接与网络映射，并在小规模建立独立完成语义与成本验收。不能删除零计数、屏障或断言凑规模，也不能在未验证分层算法时默认大规模仍全互连通知。R256若继续直接模板的391680项远端控制，只是说明机械放大不成立。

## 4. 平台比较对两个主方向的约束

以下只使用本机已保存并逐hash保留的一手版本；本轮零外网、零竞品执行，“未核对”不等于该平台没有能力。

| 平台 | 优点与本研究可借用之处 | 限制或需针对本场景核对之处 |
|---|---|---|
| LLMServingSim 2.0 | 服务调度、运行时反馈、异构硬件与分离式服务；适合请求和部署层 | 本项目现代入口尚非完整服务调度；要核对具体现代EP、控制和恢复语义，不能把更改batch/GPU当独有优势 |
| Vidur | profile驱动的推理调度、容量与成本规划；已有A100/H100等明确配置 | 对本目标的逐包恢复与现代EP细节需另核。我们尚未达到其硬件/服务校准基础，不能用声明1000ns替代profile |
| ASTRA-sim + ns-3 | 通用依赖图、集合通信和详细网络后端；可作强基线 | 需要正确输入与完成约束；联合DAG和ns-3已有先例。本项目通用图来自一次详细基线的slot/channel绑定，且不含参考时刻，不是已实现的任意新输入直接生成器；不能据此宣称通用图做不到 |
| SimAI / SimAI-CLEM | SimAI已有多请求推理与MoE工作负载；CLEM执行真实CCL并接ns-3，已有规模复用技巧 | 所读CLEM版本列出NCCL/MSCCL路径，具体DeepEP直接provider需匹配核对；本项目功能模型没有其真实库执行基础，不能把竞品概括为只有random或只做训练 |
| Parsimon | 无训练的链路分解、流时延分布合成与聚类；适合大网络what-if | 所读原文明确短时瞬态不适用，相关跨跳与持续多处拥塞影响精度，丢包验证留作后续；若做MoE闭环和恢复，必须给具体差异，不能只改分解名称 |
| MimicNet | 包级仿真训练内部/feeder模型后组合较大网络 | 本轮沿用已核作者README；论文PDF此前403，未据全文评价故障/RDMA边界，不给同机速度排名 |

## 5. 最值得推进的两个创新候选

**方向一：让精简的MoE负载模型对网络选择负责。** 不是再发现专家热点，而是确定在给定EP、专家放置、就绪成本与路径下，哪些信息可以省、哪些一省就会选错网络。当前新证据是EP256总量几乎相同但目的聚集不同；强参考同时表明长期热度已解释大部分差异。下一门槛是以真实边际、阶段/逐链路模型及完整通用图作强基线，验证保留联合就绪与控制信息是否能减少有代价的错误选择。需要明确误差与拒绝判断条件，不能把字节模型弱基线的误判算作全部新增语义收益。

**方向二：以可验证的通信表示与局部细化支撑大EP。** 不是放宽rank上限或重复小组，而是控制随EP增长的任务/连接/包处理成本，保留跨阶段反馈、恢复和应用完成。当前直接模板的平方膨胀给出了必须解决的工程障碍；一般分解、循环模板、QP复用和学习替代已有工作，论文贡献必须落在适用条件、跨边界状态处理与真实成本下降。先对目标通信路径交付可执行的小规模等价参考，再在同资源同输入下衡量宿主CPU、RSS、事件数与网络决策误差；不能用独立仿真时间相加或清空网络状态来证明加速。

两者分别回答“仿真是否选对”与“多大代价能选对”，仍是候选，**本轮没有证明新的时延/仿真加速收益或论文创新成立**。硬件匹配、代表性较大EP完整调用、强基线误判和实际算法收益仍是必须补齐的证据。当前收益空间只能说潜在目的竞争与表示增长确实存在，不能给生产收益率或把9.52%碰撞差异写成9.52%推理优化空间。

## 核验与复现

独立位图和两两交集重数145232条token-layer路由、1161856次专家选择和140882个分组EP波次。均匀参考另由784对小宇宙枚举核对，循环平移均值由9对完整偏移枚举及所有实际分组的有理数核对；四类篡改被拒绝。来源、预算、具体命令和失败状态随本地归档保留。原始路由只在忽略目录，不提交Git。复现入口见上级README。
'''
    (out/'report.md').write_text(md)
    # Deterministic standalone scientific SVG, without external assets.
    os.environ['MPLCONFIGDIR']=str(run/'matplotlib-cache')
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    matplotlib.rcParams['svg.hashsalt']='moe-ep-network-projection-20261027'
    fig,axes=plt.subplots(1,2,figsize=(12,4.4),layout='constrained')
    for phase,color in (('prefill','#c46b23'),('decode','#166da5')):
        axes[0].plot(eps,[covers[phase,ep]['unique_destination_count']['mean'] for ep in eps],
            marker='o',color=color,label='Observed '+phase)
    axes[0].plot(eps,[covers['decode',ep]['uniform_expected_destinations'] for ep in eps],
        '--',color='#777',label='Uniform Top-8 expectation')
    axes[0].set(ylabel='Distinct destination ranks per token',xlabel='EP group size',ylim=(0,8.5))
    axes[0].set_title('Same expert trace, different network projection')
    for batch,color in ((4,'#267147'),(8,'#6d3ab0')):
        axes[1].plot(eps,[groups[batch,ep]['observed_collision_to_uniform_expectation'] for ep in eps],
            marker='o',color=color,label=f'Batch {batch}: vs uniform')
        axes[1].plot(eps,[matched[batch,ep]['observed_to_shift_expectation'] for ep in eps],
            '--',marker='s',color=color,label=f'Batch {batch}: vs preserved marginals')
    axes[1].axhline(1,color='#777',linewidth=1)
    axes[1].set(ylabel='Observed / reference pair collisions',xlabel='EP group size')
    axes[1].set_title('Most uniform-baseline gap is marginal popularity')
    for ax in axes:
        ax.set_xscale('log',base=2);ax.set_xticks(eps,labels=list(map(str,eps)))
        ax.grid(alpha=.2);ax.legend(fontsize=8)
    fig.savefig(out/'ep_projection.svg',metadata={'Date':None,'Creator':'MoE EP network projection'})
    # Matplotlib emits trailing spaces on path lines; normalize before both
    # versioning the standalone SVG and embedding the same bytes in HTML.
    svg_path=out/'ep_projection.svg'
    svg_path.write_text('\n'.join(line.rstrip() for line in svg_path.read_text().splitlines())+'\n')
    fig.savefig(out/'ep_projection.png',dpi=150,metadata={'Software':'MoE EP network projection'})
    plt.close(fig)
    body=[];lines=md.splitlines();i=0
    while i<len(lines):
        line=lines[i]
        if line.startswith('|'):
            rows=[]
            while i<len(lines) and lines[i].startswith('|'):
                cells=[c.strip() for c in lines[i].strip('|').split('|')]
                if not all(re.fullmatch('[: -]+',c) for c in cells):
                    tag='th' if not rows else 'td'
                    rows.append('<tr>'+''.join(f'<{tag}>{html.escape(c)}</{tag}>' for c in cells)+'</tr>')
                i+=1
            body.append('<div class="table"><table>'+''.join(rows)+'</table></div>');continue
        match=re.match(r'^(#{1,3}) (.*)',line)
        if match:body.append(f'<h{len(match[1])}>{html.escape(match[2])}</h{len(match[1])}>')
        elif line:body.append('<p>'+html.escape(line)+'</p>')
        i+=1
    rendered='\n'.join(body)
    rendered=re.sub(r'\*\*([^*]+)\*\*',r'<strong>\1</strong>',rendered)
    rendered=re.sub(r'`([^`]+)`',r'<code>\1</code>',rendered)
    svg=(out/'ep_projection.svg').read_text();svg=svg[svg.index('<svg'):]
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>真实路由与大EP网络：价值和边界</title><style>
body{background:#f3f6f8;color:#152e3e;font:17px/1.8 system-ui,sans-serif;margin:0}
article{max-width:1120px;margin:24px auto;padding:36px;background:white;border-radius:12px}
h1{font-size:29px;line-height:1.4}h2{font-size:23px;margin-top:32px}.table{overflow-x:auto}
table{border-collapse:collapse;font-size:15px;width:100%}td,th{border:1px solid #d7e2e9;padding:10px;text-align:left;vertical-align:top}
th{background:#e9f1f6}svg{width:100%;height:auto}code{background:#eef2f5;padding:2px 4px}
@media(max-width:750px){article{margin:0;padding:18px}body{font-size:16px}}
</style><article>'''+svg+rendered+'</article></html>\n'
    (out/'report.html').write_text(page)
    save(out/'key_metrics.json',dict(batch8=[dict(ep=ep,
        remote_vectors=groups[8,ep]['remote_vector_count']['mean'],
        collision_vs_uniform=groups[8,ep]['observed_collision_to_uniform_expectation'],
        collision_vs_preserved_marginals=matched[8,ep]['observed_to_shift_expectation']) for ep in eps],
        plot_library_version=matplotlib.__version__,new_networks=0,novelty_proven=False))
    print('Generated source-backed Chinese review, deterministic SVG and self-contained HTML.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('references','report'))
    p.add_argument('--repository',type=Path);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path);a=p.parse_args()
    if a.action=='references':references(a.repository.resolve(),a.run.resolve())
    else:report(a.run.resolve(),a.output.resolve())
