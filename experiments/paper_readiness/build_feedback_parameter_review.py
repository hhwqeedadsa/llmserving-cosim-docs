"""Build the fixed eight-condition report and an explicit historical citation erratum."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import shutil
import tarfile

ROOT = Path(__file__).resolve().parents[2]
OLD_ARCHIVE_SHA = '3b5700d6c30b55fe19a075046cb2a97bdef46a5f7ce94a85363c6c5a8fe2d7df'


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('task', 'baseline', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    task, baseline, out = args.task.resolve(), args.baseline.resolve(), args.output.resolve()
    assert not out.exists()
    summary = load(task / 'analysis/summary.json')
    verification = load(task / 'checked/verification.json')
    assert verification['verified'] and verification['all_declared_completed']
    assert len(summary['cases']) == 8 and len(summary['effects']) == 12
    assert all(c['status'] == 'complete' for c in summary['cases'])
    assert summary['new_network_runs'] == 6 and summary['reused_successful_runs'] == 2
    compatible = task / 'compat/verification.json'
    assert compatible.read_bytes() == (baseline / 'checked-refined/verification.json').read_bytes()
    archive = baseline / 'checkpoint/checkpoint.tar.gz'
    assert sha(archive) == OLD_ARCHIVE_SHA
    corrected = {
        'experiments/paper_readiness/feedback_overlap_protocol.md': 'source/experiments/paper_readiness/feedback_overlap_protocol.md',
        'experiments/paper_readiness/build_feedback_overlap_review.py': 'source/experiments/paper_readiness/build_feedback_overlap_review.py',
        'docs/review_artifacts/feedback_overlap_20261022/protocol.md': 'report/protocol.md',
        'docs/review_artifacts/feedback_overlap_20261022/report.md': 'report/report.md',
        'docs/review_artifacts/feedback_overlap_20261022/report.html': 'report/report.html',
    }
    errata = []
    history = task / 'citation-history'
    assert not history.exists()
    history.mkdir()
    with tarfile.open(archive, 'r:gz') as tar:
        for name, member in corrected.items():
            old = tar.extractfile(member).read()
            current = (ROOT / name).read_bytes()
            assert old != current
            if name.endswith('protocol.md'):
                assert current.startswith(old), 'Keep the original registered design verbatim'
            snapshot = history / name
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_bytes(old)
            errata.append(dict(path=name, historical_member=member,
                               historical_sha256=hashlib.sha256(old).hexdigest(), corrected_sha256=sha(ROOT / name)))
        old_summary = tar.extractfile('raw/analysis/summary.json').read()
        assert old_summary == (baseline / 'analysis/summary.json').read_bytes()
        for case in load(baseline / 'run/execution.json')['executed']:
            member = 'raw/run/' + case['name'] + '/result.json'
            assert tar.extractfile(member).read() == (baseline / 'run' / case['name'] / 'result.json').read_bytes()
    out.mkdir()
    for dst, source in (
        ('summary.json', task / 'analysis/summary.json'),
        ('verification.json', task / 'checked/verification.json'),
        ('compatibility.json', compatible),
        ('protocol.md', Path(__file__).with_name('feedback_parameter_protocol.md')),
    ):
        shutil.copyfile(source, out / dst)
    save(out / 'erratum.json', dict(
        reason='DCQCN 2015 §5.2: 110us discussion concerns alpha interval tau-prime, not recovery T',
        correction='The old 110us recovery condition is a self-chosen sensitivity, not a replication of that passage',
        historical_archive=dict(path=str(archive), sha256=OLD_ARCHIVE_SHA, unchanged=True),
        historical_summary_sha256=hashlib.sha256(old_summary).hexdigest(),
        historical_four_results_unchanged=True, preregistration_original_prefix_preserved=True,
        compatibility_byte_identical=True, compatibility_sha256=sha(compatible), corrected_files=errata,
        historical_receipts_scope='Old receipts describe old archived snapshots; this erratum records the later documentation changes'))
    depth_effects = [e for e in summary['effects'] if e['factor'] == 'depth']
    by_name = {c['name']: c for c in summary['cases']}
    ratios = [by_name[e['high_case']]['acked_ns'] / by_name[e['low_case']]['acked_ns'] for e in depth_effects]
    save(out / 'findings.json', dict(
        depth8_to_depth1_ratio_range=[min(ratios), max(ratios)], depth8_slower_in_all_four_pairs=True,
        new_network_runs=6, hash_verified_reused_successes=2, independent_rng_seeds=1,
        paper_main_contribution_established=False, new_algorithm=False, hardware_calibrated=False,
        independent_workload=False, raw_volatile=True, persistent_backup=False,
        interpretation='Finite parameter-dependence test in a declared small reference model'))
    rows = '\n'.join('| ' + ' | '.join(map(str, (
        c['name'], '复用' if c['kind'] == 'reused_success' else '新增', c['acked_ns'], c['finish_ns'],
        c['unique_destination_ce'], c['cnp_count'], c['minimum_rate_at_tx_bps'], c['post_feedback_packets']))) + ' |'
        for c in summary['cases'])
    factors = {'depth': '并发深度1→8', 'additive_bps': 'AI 5→40Mb/s', 'pmax': 'pmax 0.01→1'}
    effects = '\n'.join('| ' + ' | '.join(map(str, (
        factors[e['factor']], e['low_case'], e['high_case'], e['ack_delta_ns'], e['cnp_delta'],
        '相同' if e['source_timeline_equal'] else '不同'))) + ' |' for e in summary['effects'])
    text = f'''# MoE Scale-Out并发排序：是否依赖恢复步长和ECN概率

本轮完成8个声明条件：6次新增网络执行、2个成功基准按哈希复用。每个条件均通过独立包级核验。并发深度8在四组参数组合中都比深度1慢，约为{min(ratios):.2f}—{max(ratios):.2f}倍；更快的速率恢复步长缩短长尾，却没有改变排序。这里只排除了“现象必须恰好采用AI5或pmax1”这一狭窄解释，没有证明生产普遍性或新算法贡献。

## 服务对象、输入和边界

服务于MoE专家并行（EP）的跨节点Dispatch网络研究：给定真实专家选择，研究源端允许多少未确认写入、拥塞反馈与物理端口共享如何影响通信完成。并发深度是每条持久连接允许的未ACK工作数，不是batch、交换机缓存或GPU数量。本轮没有改变batch，也没有评估DP选择。

四个已见DeepSeek-R1-AWQ/MMLU请求，entry0、层4，各取最多32 token，实际32/28/10/6，共76 token。256个专家连续等容量放到4个EP rank，每token对远端目的rank去重，生成180份14,464B数据写、2,603,520B有效载荷、2,700个源数据包。H7168/Top8、BF16与direct打包属于重建假设；原采集的激活dtype未知。真实的是专家选择，网络字节和时序经过显式建模。

EP rank放在8端点小Clos的GPU0/2/4/6，接入100G、上层25G、每链路1us，12条持久QP（队列对/连接），seed11。每条件内部全部连接共享一套ns-3网络和时间；8个条件相互独立。所有根工作0ns就绪，只模拟Dispatch数据、真实网络ACK和参考拥塞反馈；不含计算、Combine、完整现代通信库、真实请求到达、训练或千卡部署。本轮未直接运行完整LLMServingSim请求，也未重跑旧失败的完整Prefill。

## 执行前固定的参数对照

深度1/8 × AI5/40Mb/s × pmax1/0.01组成完整8条件。AI是恢复阶段每次增加的速率步长；HAI固定50Mb/s。NP（接收端通知抑制）50us、alpha更新55us、速率恢复55us、字节恢复10MB、g1/256、快速恢复5轮、最低100Mb/s、CNP（拥塞通知包）48B保持不变。ECN（显式拥塞通知）阈值32,768/131,072B不变。

AI40取自DCQCN 2015表2流体模型，pmax0.01取自§5.2/表3示例。它们是有一手来源的敏感性水平，当前100G/25G链路、阈值、其他参数和参考实现的组合并非原部署复刻，也没有经过现代NIC校准。

## 全部结果

| 条件 | 来源 | 最后业务ACK ns | 网络排空 ns | 唯一目的CE包 | CNP数 | 实际发包时最低QP速率 bit/s | 首次反馈后数据包 |
|---|---|---:|---:|---:|---:|---:|---:|
{rows}

CE按目的端去重数据包计数，不能当作逐跳标记次数。最低QP速率是实际发包时记录的设置速率，不是测得的平均吞吐。最后业务ACK和网络排空分别读取，本轮数值恰好相同。全部完成且无丢包、重传、PFC暂停或截断；后续核验没有重新执行网络。

## 三个直接发现

1. 浅并发没有产生目的端CE或CNP，四条件完成时间和源发包时间线均相同：599,715ns。交换机队列峰值33,664B略高于kmin，并不保证会标记；还受原生标记时机、队列/出口范围及阈值区间内抽样影响。不能只凭全局队列峰值反推每包ECN。
2. 深并发持续受到反馈影响。pmax1时有1,630个反馈后数据包，pmax0.01时1,399个；它们的实际TX速率均低于100G。AI5→40使深条件完成时间分别缩短140,883ns和123,986ns。固定深度/pmax时，两种AI的CNP生成、实际TX和RX按流时间序列完全一致，差异发生在接收相同反馈后的恢复响应；没有把“CNP数相同”当作足够证据。
3. pmax降到0.01并非全局只有1%包会被标记。固定原生实现中，只有kmin和kmax之间采用比例概率，超过kmax仍100%标记。深条件队列峰值294,948B高于kmax，因此CE由1,906降到1,622、CNP由61降到54并不矛盾；不能预期100倍减少。这里说明行为规则与观测相容，没有逐跳抓包逐个归因每次标记。pmax生效依据是哈希固定的公开协议和Fabric直接赋值源码，不冒充硬件读回。

## 12个单因素配对，保留零效果

差值为右侧条件减左侧条件，负数表示完成更早。pmax方向为0.01→1，避免和执行顺序混淆。

| 因素 | 左侧条件 | 右侧条件 | 业务ACK差 ns | CNP差 | 源发包时间线 |
|---|---|---|---:|---:|---|
{effects}

所有条件数据及必要ACK的最忙物理出口服务下界均为443,791.36ns。这个强基线仍成立；它只使用工作量、路径和带宽，不能区分本轮时间状态。完成时间减去下界的差值不等于可消除的优化收益。

## 必须保留的引文勘误

核读DCQCN 2015原文后发现：§5.2中“110µs（默认的两倍）对收敛影响较小”讨论的是alpha衰减更新周期τ′，对应公式(2)；不是速率恢复计时器T。上一轮将其作为恢复110us的文献依据不准确。上一轮110us条件仍是有效的自定恢复敏感性，但不能称作该文结论的复现。

原执行前设计逐字保留，仅在末尾追加标记清楚的事后勘误；报告与生成器同步更正。旧四个数值结果、分析摘要和313项归档不变。旧归档中的报告快照保留历史状态，其旧源码一致性收据描述当时快照；本轮erratum.json列出旧/新文档hash和不变的旧包hash，避免把后来更正写成原先即正确。旧4条件的修改后oracle核验与原verification.json逐字节相同；oracle只参数化AI/HAI，其他假设仍固定，不是任意控制器的通用验证器。

## 对学术目标的判断和下一步门槛

当前增加的是一个可证伪判断：在这个已声明的小模型和有限参数范围，反转不依赖唯一AI/pmax设置。没有对多随机种子做统计估计；同一seed下闭环改变随机数调用顺序，低概率实验不能解释为逐包相同随机抽样的配对。四请求早已被查看，没有独立留出，不能声称泛化。

HOTI 2022已经研究ASTRA-sim与Alibaba ns-3的incast、分块All-to-All和拥塞控制影响；“CC影响集合通信”本身不是本工作的创新。候选贡献应继续接受强基线检验：独立请求/不同阶段是否保留并发决策差异，实际通信库的发布与完成语义是否允许该现象，匹配NIC测量是否支持参考反馈行为，然后才谈能超过已有调度/源节流方法的新机制。multi-rail/HPN、全规模完成和现代通信库可见性未在本轮验证。

不继续搜参数保住现象，不把更快的AI设置包装为新算法。现阶段论文主贡献仍未成立，A类论文目标保持进行中；这份报告完成的是有限机制审查。

## 核验、复现和保存

独立checker重读四个原始JSON，重建180份写；核对21,600个数据包、21,600个ACK和230个CNP，共43,430个端点UID关联、320个物理端口条件、1,440份工作回调/信用、31,162个RP状态事件。按实际目的CE重算NP通知，按事件重放RP速率状态，独立重算ECMP路径、物理端口字节及服务下界。端点包捕获加路径重建不是交换机逐跳抓包，功能核验不是硬件准确性证据。

运行命令、退出状态、输入/二进制hash、复用来源和受监控资源记录均保存。方法与入口见experiments/paper_readiness/README.md。无平台/依赖改动、构建、安装、下载、网络失败或重试。每阶段60秒/RSS1GiB/地址2GiB，仿真5ms；整轮128MiB/300秒，归档成员96MiB/压缩48MiB。原始件与检查点仅在/dev/shm/moe-feedback-parameters-20261022，易失且没有持久备份；ELF保留原RPATH，归档不是可迁移运行镜像。本地提交，不push。

一手来源：DCQCN 2015 https://conferences.sigcomm.org/sigcomm/2015/pdf/papers/p523.pdf ；RoCE拥塞控制集合通信研究 HOTI 2022 https://arxiv.org/pdf/2207.10898 。PDF与源码证据随归档保存，不需为核验重新下载。
'''
    (out / 'report.md').write_text(text)
    parts, in_table = [], False
    for line in text.splitlines():
        row = line.startswith('|')
        if row and not in_table:
            parts.append('<div class="scroll"><table>')
            in_table = True
        elif in_table and not row:
            parts.append('</table></div>')
            in_table = False
        if line.startswith('|---'):
            continue
        if row:
            parts.append('<tr>' + ''.join('<td>' + html.escape(x.strip()) + '</td>' for x in line.strip('|').split('|')) + '</tr>')
        elif line.startswith('## '):
            parts.append('<h2>' + html.escape(line[3:]) + '</h2>')
        elif line.startswith('# '):
            parts.append('<h1>' + html.escape(line[2:]) + '</h1>')
        elif line.strip():
            parts.append('<p>' + html.escape(line) + '</p>')
    if in_table:
        parts.append('</table></div>')
    (out / 'report.html').write_text('''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MoE Scale-Out并发排序与参数依赖</title><style>body{font:17px/1.85 system-ui,sans-serif;max-width:1200px;margin:40px auto;padding:0 22px;color:#23334b;background:#f4f7fb}h1{font-size:30px;line-height:1.5}h2{font-size:23px;margin-top:36px;color:#164b7d}p{background:#fff;padding:14px 18px;border-radius:8px}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;background:#fff;font-size:14px}td{border:1px solid #d3deeb;padding:8px;text-align:right;white-space:nowrap}td:first-child{text-align:left}tr:first-child{background:#e0eafa;font-weight:bold}a{color:#1462a1}@media print{body{background:white;max-width:none;font-size:11px}table{font-size:9px}td{white-space:normal}}</style></head><body>''' + ''.join(parts) + '''<p><a href="summary.json">全部指标与配对</a> · <a href="verification.json">独立包级核验</a> · <a href="protocol.md">执行前方案</a> · <a href="erratum.json">历史引文勘误</a> · <a href="archive.json">归档收据</a> · <a href="final-check.json">最终检查</a></p></body></html>''')
    print(json.dumps(dict(report=True,conditions=8,new_network_runs=6,reused=2,output=str(out))))


if __name__ == '__main__':
    main()
