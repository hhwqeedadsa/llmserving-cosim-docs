"""Review the fixed completion-event study and verify a bounded local checkpoint."""
import argparse
from collections import Counter
import hashlib
from html import escape
from html.parser import HTMLParser
from itertools import combinations
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    'completion_semantics_protocol.md', 'prepare_completion_semantics.py',
    'run_dispatch_validation.py', 'analyze_dispatch_network.py',
    'check_dispatch_network.py', 'analyze_completion_semantics.py',
    'package_completion_semantics.py', 'README.md',
)


def load(path):
    return json.loads(path.read_text())


def sha(path):
    with path.open('rb') as stream:
        return stream_sha(stream)


def stream_sha(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(block)
    return digest.hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def audit_metrics(task, baseline, comparison):
    """Recompute simple reported metrics from raw results, without the analyzer."""
    rows = []
    assert len(comparison['cases']) == 18
    assert (task/'compared/comparison.json').read_bytes() == (task/'compared-portable/comparison.json').read_bytes()
    assert (task/'compared/comparison.json').read_bytes() == (task/'compared-relocated/comparison.json').read_bytes()
    assert load(task/'baseline-rechecked/verification.json')['dependency_event'] == 'acked'
    assert load(task/'checked/verification.json')['dependency_event'] == 'accepted'
    for case in comparison['cases']:
        counts = []
        for root, mode in ((baseline, 'baseline'), (task, 'candidate')):
            result = load(root/'run'/case['case']/'result.json')
            works = result['persistent_transport']['work']
            assert result['drained'] and result['passed']
            assert max(w['acked_ns'] for w in works) == case[mode]['acked_ns'] == result['finish_ns']
            assert max(w['accepted_ns'] for w in works) == case[mode]['accepted_ns']
            waits = [w['published_ns']-w['ready_ns'] for w in works if w['published_ns'] > w['ready_ns']]
            assert len(waits) == case[mode]['publication_wait_work_count']
            assert max(waits, default=0) == case[mode]['max_publication_wait_ns']
            intervals = []
            for step in range(4):
                selected = [w for w in works if w['id']//16 == step]
                ready = {w['ready_ns'] for w in selected}
                assert len(ready) == 1
                intervals.append(max(w['acked_ns'] for w in selected)-ready.pop())
            assert sum(intervals) == case[mode]['window_ack_duration_sum_ns']
            counts.append(len(works))
        assert counts[0] == counts[1]
        old, new = case['baseline']['acked_ns'], case['candidate']['acked_ns']
        assert old-new == case['ack_difference_ns']
        assert (old-new)/old == case['relative_ack_difference']
        assert case['candidate']['window_ack_duration_sum_ns']-new == case['acknowledged_intervals_overlap_amount_ns']
        rows.append(dict(case=case['case'], network=case['network'], condition=case['condition'],
            work_count=counts[0], acked_baseline_ns=old, acked_candidate_ns=new,
            relative_difference=(old-new)/old, **{key:case['candidate'][key] for key in
            ('publication_wait_work_count', 'max_publication_wait_ns')},
            overlapping_interval_overcount_ns=case['acknowledged_intervals_overlap_amount_ns']))
    rank_checks = 0
    for network in sorted({r['network'] for r in rows}):
        for left, right in combinations([r for r in rows if r['network'] == network], 2):
            def order(key):
                delta = left[key]-right[key]
                return (delta > 0)-(delta < 0)
            assert order('acked_baseline_ns') == order('acked_candidate_ns')
            rank_checks += 1
    assert rank_checks == len(comparison['comparisons']) == 18 and comparison['ranking_changes'] == 0
    assert sum(r['publication_wait_work_count'] for r in rows) == comparison['publication_wait_work_count']
    return rows


def render(comparison, metrics, monitors):
    rows = ''.join('<tr><td>'+escape(r['case'])+'</td>'
        f'<td>{r["work_count"]}</td><td>{r["acked_baseline_ns"]/1000:.3f}</td>'
        f'<td>{r["acked_candidate_ns"]/1000:.3f}</td><td>{100*r["relative_difference"]:.3f}%</td>'
        f'<td>{r["publication_wait_work_count"]}</td><td>{r["max_publication_wait_ns"]/1000:.3f}</td></tr>'
        for r in metrics)
    example = next(r for r in comparison['cases'] if r['case'] == 's11-all100g-contiguous')
    component_names = dict(source_queue_credit_wait_ns='源队列槽等待（旧ACK释放）',
        source_tail_after_publication_ns='发布至所选数据尾包首次发送',
        data_serialization_ns='数据序列化', data_propagation_ns='数据传播',
        data_switch_wait_ns='数据交换机等待', ack_generation_gap_ns='ACK生成间隔',
        ack_serialization_ns='ACK序列化', ack_propagation_ns='ACK传播', ack_switch_wait_ns='ACK交换机等待')
    component_rows = ''.join(f'<tr><td>{name}</td><td>{example["baseline"]["chain_components"].get(key,0):,}</td>'
        f'<td>{example["candidate"]["chain_components"].get(key,0):,}</td></tr>'
        for key, name in component_names.items())
    window_rows = ''.join(f'<tr><td>{w["step"]}</td><td>{w["ready_ns"]:,}</td>'
        f'<td>{w["published_first_ns"]:,}—{w["published_last_ns"]:,}</td><td>{w["accepted_ns"]:,}</td>'
        f'<td>{w["acked_ns"]:,}</td><td>{w["prior_window_unacked_at_ready"]}</td></tr>'
        for w in example['candidate']['windows'])
    run_monitor = next(m for m in monitors if m['command'][1].endswith('/run_dispatch_validation.py'))
    low = 100*min(r['relative_difference'] for r in metrics)
    high = 100*max(r['relative_difference'] for r in metrics)
    over = [r['overlapping_interval_overcount_ns'] for r in metrics]
    witness = []
    for mode, label in (('baseline','全ACK依赖'), ('candidate','连续接收依赖')):
        chain = ' → '.join(f'工作{s["work"]}/{s["event"]} [{s["start_ns"]:,}, {s["end_ns"]:,}]'
            for s in example[mode]['application_predecessor_witness'])
        witness.append(f'<p><b>{label}：</b>{escape(chain)}</p>')
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Scale-out窗口完成语义：网络实验复核</title>
<style>body{{font:16px/1.8 system-ui,sans-serif;max-width:1180px;margin:36px auto;padding:0 22px;color:#1c3045;background:#fbfcfe}}
h1,h2{{line-height:1.4}}h2{{margin-top:32px}}aside{{padding:18px;background:#eaf3fb;border-left:4px solid #246392}}
table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:9px 10px;border-bottom:1px solid #d3deea;text-align:left}}
th{{background:#edf2f7}}.scroll{{overflow:auto}}a{{color:#125b99}}code{{background:#edf1f5;padding:2px 4px}}nav{{line-height:2.2}}
</style></head><body><h1>相同数据、相同路径，完成条件会怎样影响网络结果？</h1>
<p>2026-10-22 · Scale-out原生RDMA ns-3 · 固定18组配对 · 平台起点a6229b8</p>
<aside><b>结果：</b>最终ACK时间缩短{low:.3f}%—{high:.3f}%，三种专家放置的18次两两排序全部保持。
数据包形状、路径和端口总发送量一致。因此，时序依赖影响绝对时间，但本轮未推翻之前的放置排序。
这是模型假设敏感性，不能当成真实推理加速或已成立的论文贡献。</aside>
<nav><a href="comparison.json">完整配对与等待前驱</a> · <a href="metrics.json">18组指标</a> ·
<a href="packet-verification.json">新条件逐包核验</a> · <a href="baseline-verification.json">旧条件重新核验</a> ·
<a href="maintenance.json">命令、资源与版本</a> · <a href="protocol.md">执行前固定方法</a> · <a href="archive.json">本机检查点</a></nav>
<h2>实际做了什么</h2><p>沿用四个R1真实路由请求，取(层4,入口5/6)、(层32,入口5/6)四个窗口。
EP4逻辑rank映射到四台服务器、两个Pod的GPU 0/2/4/6；物理拓扑含8个GPU、4个ToR、4个汇聚、2个核心交换机，
四个GPU实际承载任务。接入100Gb/s，上层100或25Gb/s，每链路传播1μs，ECMP种子11/29/47。
每份Dispatch数据14,464B，12条持久连接，每连接队列深度1。</p>
<p>比较连续放置、按全部Prefill统计分配、按尾部10个Prefill token统计分配三种既有方案。
后两者使用LPT（优先分配较重专家的负载均衡规则）。本轮没有改变放置，只将后续窗口的应用依赖从前窗全部
<code>acked</code>改为全部<code>accepted</code>。原18组网络结果只读保留，新增18组真实网络执行，全部窗口共享同一网络与仿真时钟。</p>
<h2>三个时刻必须分开</h2><table><tr><th>时刻</th><th>本模型的含义</th><th>对后续传输的作用</th></tr>
<tr><td>accepted：连续接收</td><td>目标传输层已连续收到工作对应的字节</td><td>新条件用它满足下一窗口的应用依赖</td></tr>
<tr><td>ready：应用就绪</td><td>声明的前驱事件都已发生</td><td>不保证本连接还有可用发送槽</td></tr>
<tr><td>acked：发送方确认</td><td>发送方收到覆盖相应字节的实际ACK</td><td>两组都用它释放原队列槽；深度仍为1</td></tr></table>
<p>每个工作实际发布时间都满足：<code>published = max(应用就绪时间, 同连接上一工作ACK时间)</code>。
新条件中共有{comparison['publication_wait_work_count']}个工作已就绪却等槽，最长{comparison['max_publication_wait_ns']:,}ns。
例如seed11/上层25G/连续放置中，工作29在21,551ns就绪，仍等工作13的ACK到27,781ns才发布，等待6,230ns。</p>
<p><b>适用边界：</b>这里是独立字节写入，不复用真实张量源缓冲。accepted不代表GPU可见、计算可启动或源buffer安全复用；
未执行计算、Combine、完整现代DeepEP/GIN或真实GPU。协议ACK也不是现代通信库的完整完成语义。</p>
<h2>18组实际结果</h2><p>时间单位μs；差异百分比=(原最终ACK−新最终ACK)/原最终ACK。表示模型条件变化，不是可部署优化的收益。</p>
<div class="scroll"><table><tr><th>种子/带宽/放置</th><th>传输份数</th><th>原全ACK依赖</th><th>新连续接收依赖</th><th>时间缩短</th><th>等槽工作数</th><th>最长等槽μs</th></tr>{rows}</table></div>
<p>每个网络配置内三种放置两两比较，共18次，排序变化0次。连续放置全部最快，尾部Prefill相对全量Prefill仍5/6更慢。
这些是固定小样本的逐条件结果，没有总体显著性或独立留出集含义。</p>
<h2>为什么不能再把各窗口耗时相加</h2><p>新条件让前一窗口ACK尚未返回时，下一窗口已就绪；以下是seed11/100G/连续放置的真实时间：</p>
<div class="scroll"><table><tr><th>窗口</th><th>就绪ns</th><th>发布范围ns</th><th>全连续接收ns</th><th>全ACK ns</th><th>就绪时前窗未ACK工作数</th></tr>{window_rows}</table></div>
<p>不同窗口的“就绪到全ACK”区间发生重叠。18组中，直接相加会比整段最终ACK多算{min(over):,}—{max(over):,}ns。
旧实验按串行ACK窗口求和的链路服务量指标不能直接沿用于本条件的整体下界。本轮仅将整段单个物理出口的累计模型字节×8/速率取最大值，
作为不含传播、源信用和依赖的服务量下界；没有把它当作完成时间。</p>
<h2>时间差能追到哪些真实事件</h2><p>同一例的最终ACK从64,071ns变为56,912ns。以下各自选一条应用前驱见证；并列前驱保存在JSON中。</p>
{''.join(witness)}<div class="scroll"><table><tr><th>所选见证的时间组成（ns）</th><th>全ACK依赖</th><th>连续接收依赖</th></tr>
{component_rows}<tr><th>合计</th><th>{example['baseline']['acked_ns']:,}</th><th>{example['candidate']['acked_ns']:,}</th></tr></table></div>
<p>每段都由真实数据/ACK时间与队列记录分解，连接起来恰好覆盖整段时间。这只是应用依赖的一条见证，
源队列等待另记录其占槽旧工作；不是展开全部资源竞争的完整因果图。发布至尾包发送也包含传输发包过程，不能全部叫交换机拥塞或可消除损失。</p>
<h2>验证到了哪里</h2><p>配对核对22,140条数据/ACK包的形状与物理路径、720个物理端口计数，全部一致（仅排除时间戳与包UID）。
新条件独立检查116,280次物理发送、94,140次ECMP选择、738个工作回调、576个交换出口队列积分、72个窗口。
两次只读包分析逐字节一致；修订后的比较器按内容hash识别旧结果，再分析所得JSON也逐字节一致。
另建真实根目录、以目录符号链接指向未改变的原输入，迁移根目录后的比较结果也逐字节一致；未复制大文件或重跑网络。
原ACK条件用修改后的检查器重新核验通过，没有重跑旧网络。全部无ECN、PFC或丢包，所以不能据此证明拥塞控制长尾。</p>
<p>18个新增网络条件及6次拓扑CLI共耗宿主{run_monitor['wall_seconds']:.2f}秒，进程树采样峰值RSS
{run_monitor['peak_sampled_group_rss_bytes']/2**20:.2f}MiB。这是仿真器宿主资源，不是GPU显存或推理耗时。
无安装、下载、依赖变化或平台构建。准备阶段补拷贝未变化truth供独立核验，记录已保存，发生在网络执行前。</p>
<h2>对论文方向的实际影响与下一步</h2><p>这轮排除了“此前放置排序仅由全ACK窗口造成”的解释，但也说明只有流量和路径计数不足以精确确定完成时间。
尚未证明新的网络机制或算法；之前的简单链路基线很强，下一步应先针对它解释不了的真实在线依赖研究，避免用更多参数组合替代贡献。</p>
<ol><li>先完成现代通信库完成事件、可见性、源缓冲寿命与队列信用的逐项对应及最小验收；本轮accepted配置不能替代这一工作。</li>
<li>补独立请求与可匹配的GPU/RDMA实测，对真实并发、共享链路和热点变化进行校准；当前四请求不足以证明泛化。</li>
<li>固定强链路基线与现有拓扑感知方法，只有出现不能由简单机制解释且有实际影响的问题，才进入新策略和留出评价。</li>
<li>千卡证据仍仅有此前1层×2轮、256个EP4副本的人工短任务；完整48层真实推理和EP1024均未验收。</li></ol>
<p>论文研究目标保持进行中。本地检查点包含本轮全部原始记录与上轮已验证检查点，逐文件核hash；不是远端备份或可直接迁移的运行镜像。</p>
</body></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('task', 'baseline', 'output', 'checkpoint', 'prior_archive', 'prior_members'):
        parser.add_argument('--'+name.replace('_','-'), type=Path, required=True)
    parser.add_argument('--monitors', type=Path, nargs='+', required=True)
    args = parser.parse_args()
    task, baseline, out, checkpoint = (p.resolve() for p in (args.task,args.baseline,args.output,args.checkpoint))
    assert out.is_relative_to(ROOT) and checkpoint.is_relative_to(ROOT)
    assert not out.exists() and not checkpoint.exists()
    assert shutil.disk_usage(out.parent).free >= 8*2**30 + 32*2**20
    prior_identity = load(ROOT/'docs/review_artifacts/dispatch_validation_20261022/archive.json')
    assert sha(args.prior_archive) == prior_identity['sha256']
    prior_members = load(args.prior_members)
    with tarfile.open(args.prior_archive, 'r:gz') as archive:
        assert len(archive.getnames()) == len(prior_members) == prior_identity['files']
        assert set(archive.getnames()) == set(prior_members)
        for member in archive:
            assert member.isfile() and member.size == prior_members[member.name]['bytes']
            assert stream_sha(archive.extractfile(member)) == prior_members[member.name]['sha256']
    comparison = load(task/'compared-portable/comparison.json')
    metrics = audit_metrics(task, baseline, comparison)
    monitors = [load(p/'probe.json') for p in args.monitors]
    assert len(monitors) >= 9
    assert all(m['status'] == 'completed' and m['child_exit_code'] == 0 and not m['remaining_live_group_pids'] for m in monitors)
    assert sum(m['wall_seconds'] for m in monitors) < 240  # leave 60s for this command
    out.mkdir()
    for source, name in ((task/'compared-portable/comparison.json','comparison.json'),
        (task/'checked/verification.json','packet-verification.json'),
        (task/'baseline-rechecked/verification.json','baseline-verification.json'),
        (ROOT/'experiments/paper_readiness/completion_semantics_protocol.md','protocol.md')):
        shutil.copyfile(source, out/name)
    save(out/'metrics.json', dict(passed=True, cases=metrics, ranking_comparisons=18, ranking_changes=0,
        comparison_repeated_byte_identical=True, network_repeated=False, goal_complete=False))
    save(out/'maintenance.json', dict(monitors=monitors, execution=load(task/'run/execution.json'),
        package_start_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        sources_sha256={name:sha(ROOT/'experiments/paper_readiness'/name) for name in SOURCES},
        baseline_identity=load(task/'baseline.json'), preparation_note=load(task/'preparation-maintenance.json'),
        native_or_dependencies_changed=False, original_network_repeated=False,
        only_readonly_analysis_repeated=True, calibration=False, modern_GIN=False,
        portability='Comparison uses content hashes, not the saved baseline directory. Raw CLI logs retain original paths. Full packet checker still needs saved runtime paths or explicit restoration.',
        package_probe='Sibling package-monitor recorded separately; excluded while running to avoid self-reference.'))
    (out/'report.html').write_text(render(comparison,metrics,monitors))
    files = {}
    def tree(root, prefix):
        for path in sorted(root.rglob('*')):
            if path.is_file():
                assert not path.is_symlink()
                files[prefix+'/'+str(path.relative_to(root))] = path
    tree(task, 'raw')
    for directory in args.monitors:
        tree(directory, 'monitors/'+directory.name)
    for name in SOURCES:
        files['source/'+name] = ROOT/'experiments/paper_readiness'/name
    files['source/current_task.md'] = ROOT/'docs/tasks/current.md'
    files['source/deps.lock.json'] = ROOT/'deps.lock.json'
    files['baseline/checkpoint.tar.gz'] = args.prior_archive
    files['baseline/members.json'] = args.prior_members
    tree(out, 'review')
    identities = {name:dict(bytes=path.stat().st_size,sha256=sha(path)) for name,path in sorted(files.items())}
    assert sum(i['bytes'] for i in identities.values()) <= 96*2**20
    checkpoint.mkdir()
    target = checkpoint/'checkpoint.tar.gz'
    with tarfile.open(target, 'w:gz') as archive:
        for name,path in sorted(files.items()):
            archive.add(path, arcname=name, recursive=False)
    assert target.stat().st_size <= 32*2**20
    with tarfile.open(target, 'r:gz') as archive:
        assert len(archive.getnames()) == len(identities) and set(archive.getnames()) == set(identities)
        for member in archive:
            assert member.isfile() and member.size == identities[member.name]['bytes']
            assert stream_sha(archive.extractfile(member)) == identities[member.name]['sha256']
    save(checkpoint/'members.json', identities)
    save(out/'archive.json', dict(path=str(target),sha256=sha(target),bytes=target.stat().st_size,
        files=len(files),all_member_hashes_verified=True,storage='local_persistent_disk_not_remote_backup',
        prior_archive_embedded=True,prior_archive_sha256=prior_identity['sha256'],
        prior_archive_all_members_verified=True, original_requests_and_native_runtime_in_prior_archive=True,
        includes_self_archive_manifest=False,includes_running_packaging_probe=False,
        restore='Extract this archive to a new directory; extract baseline/checkpoint.tar.gz into another new directory. Compare its raw/ against this archive raw/ using saved sources. Native RPATH and raw input paths remain unchanged; a portable comparison is not a portable runtime image.'))
    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.count = 0
        def handle_starttag(self,tag,attrs):
            if tag == 'a':
                for key,value in attrs:
                    if key == 'href':
                        assert (out/value).is_file(), value
                        self.count += 1
    links = Links()
    links.feed((out/'report.html').read_text())
    save(out/'review-check.json', dict(passed=True,local_html_links_checked=links.count,
        source_and_raw_metrics_checked=True,archive_files_hash_verified=len(files),
        prior_archive_files_hash_verified=len(prior_members),browser_visual_acceptance=False,goal_complete=False))
    assert shutil.disk_usage(out).free >= 8*2**30
    print(json.dumps(dict(passed=True,files=len(files),archive_bytes=target.stat().st_size,html=str(out/'report.html'))))


if __name__ == '__main__':
    main()
