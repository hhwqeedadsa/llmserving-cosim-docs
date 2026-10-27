"""Generate a bounded source audit and readable report from offline evidence."""
import argparse
import base64
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess


def load(path):
    return json.loads(path.read_bytes())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def report(run, output):
    assert not output.exists()
    output.mkdir(parents=True)
    verification = load(run/'verification.json')
    assert verification['passed']
    summary = load(run/'analysis/summary.json')
    acquisition = load(run/'sources/acquisition.json')
    assert len(acquisition) == 8 and sum(r['bytes'] for r in acquisition) < 16*2**20
    for row in acquisition:
        if row['state'] == 'completed':
            assert hashlib.sha256((run/'sources'/(row['name']+'.body')).read_bytes()).hexdigest() == row['sha256']
    text = subprocess.run(['pdftotext', str(run/'sources/parsimon-paper.body'), '-'],
        capture_output=True, text=True, timeout=20, check=True).stdout
    assert len(text.encode()) < 4*2**20
    normalized = re.sub(r'\s+', ' ', text)
    anchors = [
        'Scalable Tail Latency Estimation for Data Center Networks',
        'arXiv:2205.01234v2',
        'Validation of other transport protocols',
        'studies of short term transient behavior should not use our approach',
        'cross traffic is correlated across multiple hops',
        'https://github.com/netiken',
    ]
    assert all(a in normalized for a in anchors)
    (run/'sources/parsimon-paper.txt').write_text(text)
    readme_metadata = load(run/'sources/mimicnet-readme.body')
    readme = base64.b64decode(readme_metadata['content']).decode()
    assert all(s in readme for s in ('tcp, dctcp, and homa', 'feeder model', 'run_4_mimicnet.sh'))
    (run/'sources/mimicnet-readme.txt').write_text(readme)
    paper_failure = next(r for r in acquisition if r['name'] == 'mimicnet-paper')
    assert paper_failure['state'] == 'failed' and paper_failure['status'] == 403
    repos = load(run/'sources/parsimon-author-repositories.body')
    assert any(r['html_url'] == 'https://github.com/netiken/parsimon' for r in repos)
    sources = dict(accessed_date='2026-10-27', acquisition=acquisition,
        parsimon=dict(title=anchors[0], version='arXiv:2205.01234v2, 2022-09-30',
            paper='https://arxiv.org/abs/2205.01234v2', repository='https://github.com/netiken/parsimon',
            paper_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            relevant_text_anchors=anchors, repository_code_inspected=False),
        mimicnet=dict(repository='https://github.com/eniac/MimicNet',
            readme_blob_sha=readme_metadata['sha'],
            readme_sha256=hashlib.sha256(readme.encode()).hexdigest(),
            paper='https://doi.org/10.1145/3452296.3472926', paper_full_text_obtained=False,
            scope='author_repository_README_and_bibliographic_metadata_only'),
        new_competitor_executions=0, new_networks=0)
    save(output/'sources.json', sources)
    save(output/'summary.json', summary)
    save(output/'verification.json', verification)
    table_rows = []
    for batch, rows in summary['batches'].items():
        for variant, row in rows.items():
            label = {'original':'真实原始选择', 'concentrated':'人工集中到卡内少数专家', 'spread':'人工分散到卡内不同专家'}[variant]
            table_rows.append(f"| {batch} | {label} | {row['active_experts']} | {row['hottest_expert_tokens']} | {row['token_owner_pairs']} | {row['remote_token_copies_each_phase']} | {row['operations']} / {row['events']} |")
    table = '\n'.join(table_rows)
    md = f'''# 真实专家热点如何进入网络：模型边界与直接近邻核查

2026-10-27。**当前模型能表达发往不同卡的流量差异，但不能据此研究所有“专家很热”的后果。** 卡内专家计算仍使用固定时间；本轮实际证明的是一类输入变换下的编译结构不变，没有运行变换后的包级网络。扩大路由样本不自动补齐这一限制。

## 1. 新原件的来源与使用范围

用户确认新增13份均来自 `cognitivecomputations/DeepSeek-R1-AWQ/mmlu`。既有[接收报告](../../../route_coverage/results/local_additions_20261027/report.md)记录11份完整、899和1479末尾缺层，合并旧4份共15份可作完整离线分析。该目录确认不等于核验远端内容修订，原件、旧manifest和全部来源标签保持。

本轮只使用其中已用于网络对照的八份完整原件：900、901、905、1003、1004、1007、1008、1011。固定原始数组下标1、原始层号3；batch4/8在EP4内每rank1/2个token，E256、Top8、H7168，通信向量声明BF16，不从AWQ权重量化推断采集时的激活类型。八原件逐hash核对，并核完整61层结构；batch4是这八份中的前四份，不把两个batch当独立总体样本。

## 2. 为什么“热点专家”不一定增加当前网络流量

在当前连续放置中，每卡负责64个专家。编译器对每个token取 `expert_id // 64` 的目的卡集合：同一卡上选中多个专家，只发送一次token向量；Combine也按token与目的卡分组进行局部归并。专家选择仍改变完整专家计数数组及Top-K位置，不能说这些信息没有保留。

更关键的是，`ModernNativeController._local_event()` 给每卡的 `expert_stage_done` 固定 `expert_ns`。现有八请求输入都声明1000ns，与该卡专家数、每专家token数、热点程度无关。准备、局部复制和归并也是声明成本，激活是校验用标记字节；没有真实GPU kernel、数值专家输出或硬件校准。它可以研究给定就绪条件下的网络竞争，尚不足以评价热点引起的专家排队及其反作用。

## 3. 实际完成的最小判别

保持每个token的8个Top-K位置分别归属哪一卡不变，构造两种**人工**变换：把同一卡的选择集中到少数专家，或分散到不同专家。每token仍有8个不同专家，模型、rank、token数、部署和成本均未变。这不是新增真实请求或生产分布。

| EP组内token数 | 输入 | 活跃专家数 | 最热专家token数 | token与目的卡配对数，含本地 | 每阶段远端token副本数 | 现代操作数 / 事件数 |
|---|---|---:|---:|---:|---:|---:|
{table}

batch8集中与分散的最热专家负载为8比1，但编译出的数据操作、字节数、控制操作、符号内存布局、依赖、slot组和通道绑定约束完全一致，**只排除了专家计数块的数值内容后**才进行该比较。batch4与batch8的双阶段远端token操作数分别20/36，合计操作数包含本地及控制，不能把它们都称为网络流。

六份计划还通过同一种明确的、无时间的调用者事件顺序核对动态slot与通道。它没有提供外部网络完成回调或时延证据。计数内容确实不同，不能写成原始计划逐字节相同、数据包逐字节相同或包级执行已证明时延相同。独立核验不导入平台编译器；专家归属、传输字节、计数内容和热点摘要四类篡改都被拒绝。

这一结果的含义是：**当前输入类别中，卡内热点的严重程度可以变化，而网络工作量结构不变。** 必须分别研究卡内计算不均衡、目的卡负载、NIC竞争和共享交换链路；不能通过热点直方图推导网络拥塞或优化收益。变更专家跨卡放置、Top-K位置归属、计算成本或执行模式可能改变结论，本轮没有验证这些类别。

## 4. 与最直接近邻的真实关系

| 工作与本轮读到的版本 | 已有方法与证据 | 对本项目的要求 |
|---|---|---|
| [Parsimon](https://arxiv.org/abs/2205.01234v2)，2022年arXiv v2全文；作者[源码入口](https://github.com/netiken/parsimon)仅定位未运行 | 把网络拆成独立单链路仿真，合成端到端流完成时间分布，并聚类相似链路；无需训练。论文已有DCQCN、TIMELY验证 | 按交互范围分解或合并不是新概念。原文明确短时瞬态不适用；相关跨跳流量、持续多处拥塞会影响精度，丢包验证留作后续。若研究MoE应用依赖、短时恢复与反馈，须证明具体差异和收益，不能据此断言该系统无法扩展 |
| [MimicNet](https://github.com/eniac/MimicNet)，作者README内容blob固定于sources.json；SIGCOMM 2021论文元数据 | 从包级仿真训练内部及feeder模型，再组合较大集群；README列出TCP、DCTCP、Homa及训练/组合入口 | 学习替代和组合仿真已存在。本轮论文PDF访问403，未据全文核对其精度边界、故障支持或RDMA扩展；没有运行竞品，不作速度排名 |
| 既有完整通用事件图、逐链路阶段模型 | 本项目已保存的batch4/8共享上联对照中，通用图复现详细应用，普通逐链路阶段模型选对网络升级 | 完整依赖或解析路由本身不足以支持新意。须找出更强基线仍有实际代价的误判，并给出较小表示的可靠性与成本证据 |

八次HTTP额度已关闭：七次成功、一次403，响应总量小于16MiB。检索中的同名无关条目没有当作论文证据；Parsimon准确标题与PDF地址来自已保存的文献元数据。来源正文、失败和SHA256保留。没有下载执行代码、安装或修改依赖。

## 5. 两个候选现在应怎样收敛

**候选一：保留哪些信息，才能可靠选择网络。** 当前不能宣称需要额外MoE语义才能选对；已有逐链路强基线已经完成相同选择。本轮还说明，精简表示在一类输入上可能来自功能模型本身的等价，不是算法发现了真实GPU可忽略的信息。下一次判别应固定同样资源与真实路由，检验每卡专家工作量、就绪先后或不同路径下，简化模型是否产生有代价的错误选择；先明确允许误差和拒绝下结论的条件。

**候选二：更快地得到可信网络结果。** Parsimon、MimicNet已经覆盖一般分解/学习替代；普通增量检查、编译优化、通用图与QP复用不单独作创新。只有在真实共享链路、反馈、恢复与应用门槛处给出适用条件，并相对优化后的强基线减少实际宿主时间/内存，才有成立空间。旧O0成本比例及简单FIFO前缀的少量回调缩减不能作为加速承诺。

两项候选仍未成立。本轮没有新增推理加速、仿真加速或收益率，也没有提高千卡能力。现代符号规划允许2～32rank，但本次实际使用的功能原生桥仍只支持2～4rank、预声明连接不超过64条；生成千卡拓扑不能算千卡完整现代调用已经执行。

## 6. 具体改进顺序与验收对象

1. **先补业务就绪的依据。** 独立设计可选择的每rank/每专家token数计算成本接口，分离专家服务、局部归并和网络等待；优先用目标GPU与kernel实测profile校准。不能预设热点必然更慢，分组矩阵计算的批量、填充和调度也可能改变效率。没有实测时只能做显式假设敏感性分析，保留当前固定成本模式，不把模型参数拟合为论文收益。
2. **再证明网络优化机会。** 在同样请求、部署、链路与可见信息下，比较热点跨卡映射和路径/准入方法；同时核队列、ECN/CNP、操作完成、应用阶段与源释放。归因必须落在实际端口和前驱，累计队列等待不是可消除的用户时延。
3. **最后推进规模与抽象。** 先选有依据的适用类别，再验证粗细模型的决策后悔值、误差与宿主成本；对故障、恢复、共享MMU及短时瞬态设置细化或拒绝条件。扩到更多活动rank须独立实现和验收，不能只删除限制或重复四请求。

这些是下一阶段的实施建议，本轮没有新增接口。当前已交付的是可复核的编译结构反例、独立核算和直接近邻证据；完整论文目标继续进行中。
'''
    (output/'report.md').write_text(md)
    # A text-first standalone review page; no remote assets or build dependency.
    blocks = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].startswith('|'):
                cells = [s.strip() for s in lines[i].strip('|').split('|')]
                if not all(re.fullmatch('[: -]+', cell) for cell in cells):
                    tag = 'th' if not rows else 'td'
                    rows.append('<tr>'+''.join(f'<{tag}>{html.escape(c)}</{tag}>' for c in cells)+'</tr>')
                i += 1
            blocks.append('<div class="table"><table>'+''.join(rows)+'</table></div>')
            continue
        match = re.match(r'^(#{1,3}) (.*)', line)
        if match:
            level = len(match[1])
            blocks.append(f'<h{level}>{html.escape(match[2])}</h{level}>')
        elif line:
            blocks.append('<p>'+html.escape(line)+'</p>')
        i += 1
    rendered = '\n'.join(blocks)
    # Convert source links and emphasis after escaping document text.
    rendered = re.sub(r'\[([^\]]+)\]\(((?:https://|\.\./)[^)]+)\)', r'<a href="\2">\1</a>', rendered)
    rendered = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', rendered)
    rendered = re.sub(r'`([^`]+)`', r'<code>\1</code>', rendered)
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>专家热点与网络抽象边界</title><style>
body{margin:0;background:#f3f5f7;color:#172b3b;font:17px/1.8 system-ui,sans-serif}
article{max-width:1060px;margin:32px auto;background:white;padding:38px;border-radius:12px}
h1{font-size:30px;line-height:1.4}h2{margin-top:36px;font-size:23px}p{margin:15px 0}
.table{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:15px}
th,td{border:1px solid #dce3e9;padding:10px;text-align:left;vertical-align:top}
th{background:#eaf1f7}a{color:#155eab}code{background:#eef2f5;padding:2px 4px}
@media(max-width:700px){article{margin:0;padding:20px}body{font-size:16px}}
</style><article>'''+rendered+'</article></html>\n'
    (output/'report.html').write_text(page)
    print('Wrote source-audited report and standalone HTML; no network execution.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    a = parser.parse_args()
    report(a.run, a.output)
