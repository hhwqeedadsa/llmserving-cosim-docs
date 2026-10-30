"""Export portable summaries and source/command/resource receipts, no raw routes."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def load(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def closeout(root, backend_receipt, output):
    output.mkdir(parents=True, exist_ok=False)
    analysis = load(root / 'analysis/analysis.json')
    mechanism = load(root / 'mechanism/mechanism.json')
    prep = load(root / 'prepared/preparation.json')
    if not analysis['passed'] or not mechanism['passed']:
        raise ValueError('missing analysis/diagnosis acceptance')
    backend = load(backend_receipt)
    if sha(Path(backend['service'])) != backend['service_sha256']:
        raise ValueError('backend identity changed')
    for name, digest in backend['libraries'].items():
        if sha(Path(name)) != digest:
            raise ValueError('library identity changed')
    for folder in ('analysis', 'mechanism'):
        target = output / folder
        target.mkdir()
        for path in (root / folder).iterdir():
            if path.is_file() and path.suffix in ('.json', '.html', '.csv', '.png', '.svg'):
                shutil.copyfile(path, target / path.name)
    monitors = {str(p.relative_to(root)): load(p) for p in (root / 'monitors').glob('*/probe.json')}
    attempts = {case: load(root / 'attempts' / (case + '.json')) for case in ('normal', 'local')}
    seconds = sum(p['wall_seconds'] for p in monitors.values())
    peak = max(p['peak_sampled_group_rss_bytes'] for p in monitors.values())
    if seconds >= 900 or peak >= 2**30:
        raise ValueError('recorded resource budget exceeded')
    receipt = dict(schema_version='moe-clos-transient-receipt/1', gpu_endpoints=128, native_runs=2,
        selected_from_allowed_scales=[128, 256, 512], ep_sizes=[64, 64],
        network_execution_commit=attempts['normal']['execution_commit'],
        normal_and_local_same_commit=attempts['normal']['execution_commit']==attempts['local']['execution_commit'],
        local_raw_root=str(root), preparation_sha256=sha(root / 'prepared/preparation.json'),
        preparation={k: prep[k] for k in ('streams', 'messages', 'logical_bytes', 'counts',
            'max_planned_stream_bytes', 'sr_window_bytes', 'hotspot_rank',
            'original_contributions_with_repeated_destination', 'sources')},
        backend=backend, actual_attempts=attempts, completed_monitors=monitors,
        monitored_seconds_before_closeout=seconds, max_sampled_rss_before_closeout_bytes=peak,
        source_hashes={str(p.relative_to(ROOT)): sha(p) for p in HERE.iterdir() if p.suffix in ('.py', '.md')},
        export_hashes={str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()},
        cold_build_validated=False, gpu_executions=0, dependency_modifications=0,
        network_repetitions=0, measured_novel_gain=False,
        retained_failure='monitors/prepare: old four source confirmations were in original manifest, not new-intake flag; partial inputs retained in prepared-intake-failed',
        post_execution_diagnosis=True)
    (output / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    d = analysis['contrast']
    clean, fault = (analysis['cases'][case] for case in ('normal', 'local'))
    text = f'''# 128卡Clos真实路由局部故障结果

本轮仅选择128卡，未准备或运行256/512。两个EP64组共享同一个网络；15份不同原件、8/7请求、每rank每层4专家，两层两步，882消息、13,590,528有效字节。使用真实LLMServingSim与ASTRA调度，SR与参考DCQCN相同，GPU20在前100us丢弃目的数据。16服务器×8卡、8ToR/8Agg/4Core、168链路，各层400G、每跳1us。

| 条件 | 整段任务 µs | 组0 µs | 组1 µs | 排空 µs |
|---|---:|---:|---:|---:|
| normal | {clean['timing']['application_finished_ns']/1000:.3f} | {clean['groups'][0]['finished_ns']/1000:.3f} | {clean['groups'][1]['finished_ns']/1000:.3f} | {clean['timing']['network_drained_ns']/1000:.3f} |
| local | {fault['timing']['application_finished_ns']/1000:.3f} | {fault['groups'][0]['finished_ns']/1000:.3f} | {fault['groups'][1]['finished_ns']/1000:.3f} | {fault['timing']['network_drained_ns']/1000:.3f} |

**发现一：故障损害了本组，但没有拖慢另一个组。** 整段增加{d['application_extra_ns']/1000:.3f}us（{d['application_extra_percent']:.2f}%），健康组反而提前{-d['group_extra_ns'][1]/1000:.3f}us（{-d['healthy_group_extra_percent']:.2f}%）。两条件均实际共用80个有向交换机出口；健康组ECN标记到达从16包降至1包，逐跳接收至发送的累计停留从483812ns降至280306ns。这支持竞争减少的解释，但累计停留不能直接相加成业务收益，也不代表所有故障场景。

**发现二：主要等待与成熟恢复定时器及阶段依赖一致。** 首Dispatch关键消息由112→20，连接655，14336B。首发1.392/2.692us；81.392/81.573us实际重传仍落在故障窗内；下次241.392/241.573us重传成功，数据248.585us完成，源确认与组内门限254.591us完成。正常首Dispatch门限17.439us。故障100us结束到下次关键发包相隔141.392us，这是现有指数退避的参考行为，不是新发现的协议机制，也不能直接认作可消除空间。组0第二步比正常晚235.437us开始，本步执行反而短4.527us，区分继承等待与本次通信。

**丢包口径：** 局部窗内目标GPU数据丢弃24/24包；目标GPU全程丢弃24/46包（52.17%）；全网端点数据观察丢弃24/1916包（1.253%）。额外实际源载荷172032B、24次额外源数据发送、10次恢复请求；请求数不当作包数。两条件都无自然交换机丢弃或PFC暂停。只影响接收GPU的目的数据，不是物理上行失效。

**尚未证明的收益与下一步：** 本例不支持以“恢复一定干扰其他组”直接立项，也没有实现新算法。故障开销230.910us不是可消除收益。当前图按专家贡献发送，60个token-layer输入中存在33次远端同目的rank重复贡献，尚未实现现代rank去重/组内归并；零协调成本完成门限也不等同完整DeepEP。先补齐这些强基线，再验证故障组的恢复等待是否存在可定位、可消除且有业务价值的剩余空间，不继续重复调RTO制造收益。

硬件边界：H100只是描述标签；使用真实专家选择及声明计算/部署，不运行GPU、完整671B模型或Prefill，无生产到达/时延校准。每连接全部计划字节最大57344B，小于1MiB窗口。原始数据远端revision未知；15份方便样本，不代表全体模型或生产集群。

见[网页](report.html)、[详细曲线与CSV](analysis/report.html)、[完整核验](analysis/analysis.json)、[关键恢复与逐跳解释](mechanism/mechanism.json)、[执行/资源/版本收据](receipt.json)。实际执行固定于`{attempts['normal']['execution_commit']}`；事后解释工具另行保存。原始包和来源快照留在`{root}`，不进入Git。没有重跑成功网络、编译、下载、安装或依赖变更。准备阶段首次旧四份来源判定失败与部分产物保留；修正按原manifest的SHA和路径核对后完成准备，未放宽来源要求。
'''
    (output / 'README.md').write_text(text)
    rows = []
    for case, c in (('normal', clean), ('local', fault)):
        vals = [case, *[f'{t/1000:.3f}' for t in (c['timing']['application_finished_ns'],
            c['groups'][0]['finished_ns'], c['groups'][1]['finished_ns'], c['timing']['network_drained_ns'])]]
        rows.append('<tr>'+''.join('<td>'+html.escape(v)+'</td>' for v in vals)+'</tr>')
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>128卡Clos故障对照结果</title><style>body{max-width:1150px;margin:40px auto;padding:0 20px;font:17px/1.8 sans-serif;color:#18263b}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd5df;padding:10px}img{max-width:100%}a{color:#2463b7}strong{color:#a45218}</style>'
    page += '<h1>128卡Clos：局部故障是否拖慢其他任务？</h1><p>本轮只选择128卡Clos。15份不同R1-AWQ/MMLU真实专家选择、两个EP64组，同一LLMServingSim/ASTRA/native RDMA ns-3执行。每rank每层4专家，各层400G、每跳1us。</p><p><b>normal</b>不注入故障；<b>local</b>只在GPU20的前100us丢弃目的数据。两者均开启选择性重传与相同拥塞控制，其他配置完全相同。</p>'
    page += '<table><tr><th>条件</th><th>整段任务 µs</th><th>故障组0 µs</th><th>健康组1 µs</th><th>网络排空 µs</th></tr>'+''.join(rows)+'</table>'
    page += f'<p><strong>结果：故障组变慢，健康组反而提前4.827us完成。</strong> 整段任务多等230.910us，但健康组提前3.58%。本例不支持“恢复一定拖慢其他任务”的假设。</p><p>两组实际共用80个有向出口。健康组ECN标记16→1包，累计逐跳停留483812→280306ns，支持竞争减轻的解释；累计值不是可相加的业务收益。</p>'
    page += '<h2>故障组为什么等这么久</h2><ol><li>关键消息首次重传在81.392us，仍在丢包窗口内。</li><li>100us后GPU20恢复接收，但指数退避使下一次关键源发包在241.392us。</li><li>数据在248.585us到齐，源确认和本组阶段门限在254.591us完成，随后任务继续。</li></ol><p>这是成熟恢复方法和声明阶段依赖的作用。100us后的141.392us间隔没有被证明可以安全消除；本轮没有测得新算法收益。</p>'
    page += '<h2>丢包的范围</h2><p>目标GPU在故障窗内丢弃24/24个数据包；全程为24/46包。整个集群端点观察为24/1916包（1.253%）。因此100%只描述目标位置及指定窗口。未直接丢包的组1没有丢包，也没有自然交换机丢弃或PFC暂停。</p>'
    page += '<img src="analysis/arrival-curves.png" alt="Cumulative accepted and source-confirmed bytes"><h2>怎样看待收益空间</h2><p>230.910us是本例故障成本，不是承诺的优化收益。本例先否定“健康组一定被恢复拖慢”的解释。下一步先补齐现代通信的rank去重及完成语义，再看强基线下是否仍有可定位的剩余损失。</p><p>模型边界：只有真实专家选择；计算、到达、部署及H100标签为声明参考。没有运行GPU、完整R1或Prefill；零协调成本阶段门限不是完整DeepEP，当前逐专家贡献还未去重。</p>'
    page += '<p><a href="README.md">完整说明</a> · <a href="analysis/report.html">曲线与端点/消息CSV</a> · <a href="analysis/analysis.json">核验结果</a> · <a href="mechanism/mechanism.json">关键恢复时序</a> · <a href="receipt.json">执行与资源收据</a></p></html>'
    (output / 'report.html').write_text(page)
    print(json.dumps(dict(passed=True, output=str(output), monitored_seconds_before_closeout=seconds,
        peak_rss_bytes=peak, network_runs=2)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'backend-receipt', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    closeout(args.root.resolve(), args.backend_receipt.resolve(), args.output.resolve())
