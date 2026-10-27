"""Preregistered paired public workflows; no platform or historical driver imports."""
import argparse
import html
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys

from moe_ub_cosim.host_probe import disk_usage
from moe_ub_cosim.host_timing import read_trace
from .data import OLD_SHA, CASES, admit, behavior, load, save, sha, runtime_check, schedule


def prepare(repo, stage):
    files = admit(repo/'runs/optimized_host_cost_20261027/checkpoint.tar.gz', OLD_SHA, 'optimized-host-cost-checkpoint/1')
    copied = {}
    for name, (raw, mode) in files.items():
        relative = None
        for prefix, target in (('run/stage/observed/', 'observed/'), ('run/stage/frozen/', 'frozen/'),
                               ('run/stage/runtime/', 'runtime/')):
            if name.startswith(prefix): relative = target+name.removeprefix(prefix)
        if name == 'run/stage/runtime-identity.json': relative = 'runtime-identity.json'
        if name.startswith('run/stage/truth/') and name.endswith((
                '/result.json', '/control.jsonl', '/modern-execution.json', '/event-execution.json')):
            relative = 'reference/'+name.removeprefix('run/stage/truth/')
        if relative is None: continue
        path = stage/relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw); path.chmod(mode)
        copied[relative] = sha(path)
    del files
    runtime_check(stage, repo)
    for batch, _ in CASES:
        frozen = stage/f'frozen/batch{batch}'
        assert all(sha(frozen/n) == h for n, h in load(frozen/'identity.json').items())
    code = {str(p.relative_to(repo)): sha(p) for directory in ('python/moe_ub_cosim', 'experiments/static_validation_cost')
            for p in (repo/directory).glob('*.py')}
    for name in ('experiments/static_dag_transfer/event_graph.py', 'deps.lock.json', 'experiments/static_validation_cost/protocol.md'):
        code[name] = sha(repo/name)
    save(stage/'preparation.json', dict(passed=True, archive_sha256=OLD_SHA, copied=copied, code=code,
        platform_commit='0ab7b50', fixed_schedule=schedule(), new_builds=0, new_downloads=0))
    (stage/'empty-cwd').mkdir(); (stage/'truth').mkdir(); (stage/'measurements').mkdir()


def diagnostics(stage, row):
    root = stage/'truth'/row['label']
    phases = {}
    for writer in ('worker', 'native'):
        trace = read_trace(root/(writer+'_host.jsonl')); assert trace['trace_complete']
        phases[writer] = trace['phases']
    worker = phases['worker']['modern_worker' if row['mode'] == 'modern' else 'generic_worker']
    native = phases['native']['native_total']
    total = worker['inclusive_cpu_ns']+native['inclusive_cpu_ns']
    return dict(worker_phases=phases['worker'], native_phases=phases['native'], observed_root_CPU_ns=total,
        worker_root_CPU_ns=worker['inclusive_cpu_ns'], native_root_CPU_ns=native['inclusive_cpu_ns'],
        native_CPU_fraction=native['inclusive_cpu_ns']/total,
        run_exclusive_fraction=phases['native']['simulator_run']['exclusive_cpu_ns']/total,
        all_native_free_CPU_work_factor=total/worker['inclusive_cpu_ns'], pure_packet_CPU_measured=False)


def analyze(stage):
    prep = load(stage/'preparation.json')
    assert all(sha(stage/n) == h for n, h in prep['copied'].items()); runtime_check(stage)
    ledger = load(stage/'execution.json')
    assert ledger['closed'] and ledger['state'] == 'completed' and ledger['network_attempts'] == 28
    assert [r['case'] for r in ledger['records'] if r['network']] == schedule()
    pairs = {}; diagnostic = {}
    for batch, mode in CASES:
        case = f'batch{batch}-{mode}'; values = []
        for pair in range(3):
            rows = [r for r in schedule() if r['batch'] == batch and r['mode'] == mode and r['pair'] == pair]
            records = {}
            for row in rows:
                behavior(stage, row)
                record = load(stage/'measurements'/(row['label']+'.json'))
                probe = load(stage/'monitor'/row['label']/'probe.json')
                assert record['exit_code'] == 0 and probe['status'] == 'completed'
                record = {name: record[name] for name in ('subtree_CPU_ns', 'elapsed_wall_ns', 'max_single_process_rss_kib')}
                record['sampled_group_RSS_bytes'] = probe['peak_sampled_group_rss_bytes']
                records['cached' if row['cached'] else 'full'] = record
            a, b = records['full'], records['cached']
            values.append(dict(pair=pair, first='cached' if rows[0]['cached'] else 'full', **records,
                CPU_speedup=a['subtree_CPU_ns']/b['subtree_CPU_ns'],
                wall_speedup=a['elapsed_wall_ns']/b['elapsed_wall_ns'],
                group_RSS_ratio=a['sampled_group_RSS_bytes']/b['sampled_group_RSS_bytes']))
        cpus = [p['CPU_speedup'] for p in values]; walls = [p['wall_speedup'] for p in values]
        pairs[case] = dict(pairs=values, median_CPU_speedup=statistics.median(cpus), CPU_speedup_range=[min(cpus), max(cpus)],
            median_wall_speedup=statistics.median(walls), wall_speedup_range=[min(walls), max(walls)],
            engineering_threshold_met=min(cpus)>1.05 and statistics.median(cpus)>=1.10,
            statistical_significance_claimed=False)
        row = next(r for r in schedule() if r['batch'] == batch and r['mode'] == mode and r['timing'])
        behavior(stage, row); diagnostic[case] = diagnostics(stage, row)
    return dict(passed=True, cases=pairs, diagnostics=diagnostic, network_observations=28, paired_networks=24,
        diagnostic_networks=4, coarse_measurement_includes_monitor_and_report_cost=True,
        hardware_calibrated=False, pure_packet_CPU_measured=False, inference_acceleration_measured=False,
        thousand_gpu_generalization=False, novel_algorithm_proven=False)


def report(stage, output):
    assert not output.exists(); output.mkdir(); data = analyze(stage); save(output/'summary.json', data)
    lines = ['# 固定校验复用：完整流程省了多少', '',
        '四个固定条件，每条件三对交错运行。CPU与墙钟覆盖完整命令及其已等待子孙，速度对照关闭细粒度计时日志。默认/复用均逐项保持旧完整网络、控制消息与应用结果。', '',
        '| 条件 | CPU倍率中位数 | 三对CPU范围 | 墙钟倍率中位数 | 三对墙钟范围 | 达到事前工程阈值 |',
        '|---|---:|---|---:|---|---|']
    table = []
    for name, row in data['cases'].items():
        values = [name, f"{row['median_CPU_speedup']:.3f}", '—'.join(f'{v:.3f}' for v in row['CPU_speedup_range']),
            f"{row['median_wall_speedup']:.3f}", '—'.join(f'{v:.3f}' for v in row['wall_speedup_range']),
            '是' if row['engineering_threshold_met'] else '否']
        lines.append('| '+' | '.join(values)+' |'); table.append(values)
    lines += ['', '倍率=默认耗时÷复用耗时。1.10倍约表示耗时减少9.09%，不是减少10%；小于1表示变慢。工程阈值为三对CPU均>1.05且中位数≥1.10；不是统计显著性或论文新颖性门槛。三对不足以给跨机器稳定性能保证，全部原数与RSS见summary.json。', '',
        '完整现代公共CLI包含其内层进程监控，通用执行器使用公开session并由外层监控；每条件内两模式开销策略一致，不能只比较二者CPU绝对值便归因于语义表示。RSS是10Hz采样进程树和，包含测量包装进程；wait4 maxrss是最大单进程峰值，两者口径不同。', '',
        '另四次开启复用并写细粒度日志，仅解释剩余开销，不混入上表速度对照：', '',
        '| 条件 | native占已观测CPU | Run exclusive占已观测CPU | 所有native免费CPU工作量界 |',
        '|---|---:|---:|---:|']
    notes = []
    for name, row in data['diagnostics'].items():
        lines.append(f"| {name} | {100*row['native_CPU_fraction']:.2f}% | {100*row['run_exclusive_fraction']:.2f}% | {row['all_native_free_CPU_work_factor']:.3f} |")
        for writer in ('worker', 'native'):
            top = sorted(row[writer+'_phases'].items(), key=lambda p: -p[1]['exclusive_cpu_ns'])[:4]
            notes.append(name+' / '+writer+'：'+'；'.join(f"{n}={p['exclusive_cpu_ns']/1e9:.5f}s" for n, p in top)+'。')
    lines += ['', *notes]
    lines += ['', 'Run混合网络、内存、传输与事件分派，不是纯包CPU。所有native免费假定Python成本不变且所有原生工作均可消除，只是宽松CPU工作量界，不是墙钟或实际算法收益。', '',
        '输入为八份真实MMLU路由的便利样本、单层EP4、batch4/8及声明计算/provider成本；没有GPU执行、生产硬件校准或千卡外推。固定图来自旧实际绑定，不能称输入独立编译器。普通校验复用属于强基线工程，两项论文创新仍需新的机制和收益证据。']
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    cells = ''.join('<tr>'+''.join('<td>'+html.escape(v)+'</td>' for v in row)+'</tr>' for row in table)
    (output/'report.html').write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>固定校验复用的完整流程收益</title><style>body{font:16px/1.8 system-ui;background:#f4f7fa;color:#17324b}main{max-width:1120px;margin:auto;padding:28px}table{border-collapse:collapse;background:white}td,th{padding:12px;border-bottom:1px solid #dae3ec;white-space:nowrap;text-align:left}.scroll{overflow:auto}a{color:#12618c}</style><main><h1>少做重复校验，完整仿真快了多少</h1><p>每条件三对交错运行，关闭细粒度日志；倍率=默认耗时÷复用耗时。全部网络、控制和应用行为保持。</p><div class="scroll"><table><thead><tr><th>条件</th><th>CPU倍率中位数</th><th>三对CPU范围</th><th>墙钟倍率中位数</th><th>三对墙钟范围</th><th>达到工程阈值</th></tr></thead><tbody>'''+cells+'''</tbody></table></div><p>工程阈值：三对CPU均&gt;1.05且中位数≥1.10。三对不能证明统计显著；倍率不等于时间节省百分比。RSS为采样进程树和，不是缓存键内存；原数与先后顺序完整保留。</p><p>四次带日志诊断只解释剩余成本，未混入速度对照。普通缓存是强基线工程工作，没有证明论文创新、GPU推理加速或千卡泛化。</p><p><a href="report.md">完整解释与剩余阶段</a> · <a href="summary.json">全部配对原数和RSS</a></p></main></html>''')


def execute(repo, root):
    assert not root.exists(); root.mkdir(); stage = root/'stage'; stage.mkdir(); (stage/'monitor').mkdir()
    ledger = dict(state='started', closed=False, network_attempts=0, records=[])
    env = os.environ|{'PYTHONPATH': str(repo/'python')+':'+str(repo), 'PYTHONDONTWRITEBYTECODE': '1'}
    def command(label, argv, row=None):
        assert sum(load(p)['wall_seconds'] for p in root.rglob('probe.json'))+65 < 1800
        assert max(disk_usage(root).values())+64*2**20 < 2*2**30
        assert shutil.disk_usage(root).free >= 8*2**30
        if row is not None:
            assert ledger['network_attempts'] < 28; ledger['network_attempts'] += 1
        record = dict(label=label, network=row is not None, case=row, command=list(map(str, argv)), exit_code=None)
        ledger['records'].append(record); save(stage/'execution.json', ledger)
        wrapped = [sys.executable, '-P', '-B', '-m', 'moe_ub_cosim.host_probe', '--output-dir', stage/'monitor'/label,
            '--rss-bytes', 2**30, '--address-bytes', 2**31, '--artifact-bytes', 2*2**30,
            '--artifact-root', root, '--wall-seconds', 60, '--min-free-bytes', 8*2**30, '--', *argv]
        done = subprocess.run(list(map(str, wrapped)), cwd=repo if label == 'prepare' else stage/'empty-cwd', env=env, timeout=65)
        record['exit_code'] = done.returncode; save(stage/'execution.json', ledger)
        probe = load(stage/'monitor'/label/'probe.json')
        assert done.returncode == 0 and probe['status'] == 'completed' and probe['child_exit_code'] == 0
        assert not probe['remaining_live_group_pids']
        print(label, 'passed', flush=True)
    cli = [sys.executable, '-P', '-B', '-m', 'moe_ub_cosim', 'scaleout']
    module = [sys.executable, '-P', '-B', '-m', 'experiments.static_validation_cost.study']
    try:
        command('prepare', [*module, 'prepare', '--repository', repo, '--stage', stage])
        command('dependencies', ['bash', str(repo/'scripts/check_deps.sh')])
        for row in schedule():
            out = stage/'truth'/row['label']; batch = row['batch']
            if row['mode'] == 'modern':
                argv = [*cli, 'modern-deepep-native', '--topology', stage/'observed/baseline-topology',
                    '--protocol', stage/'observed/baseline-protocol.json', '--config', stage/f'observed/batch{batch}/input.json',
                    '--backend', stage/'runtime/run-transaction-session', '--output', out, '--callback-batches']
                if row['cached']: argv.append('--reuse-static-validation')
            else:
                argv = [sys.executable, '-P', '-B', '-m', 'experiments.static_validation_cost.generic',
                    '--stage', stage, '--batch', batch, '--output', out]
                if row['cached']: argv.append('--cached')
            if row['timing']: argv.append('--host-timing')
            measured = [sys.executable, '-P', '-B', '-m', 'experiments.static_validation_cost.measure',
                        '--output', stage/'measurements'/(row['label']+'.json'), '--', *argv]
            command(row['label'], measured, row)
            save(out/'behavior.json', behavior(stage, row, live=True))
        runtime_check(stage, repo); ledger.update(state='completed', closed=True)
    except BaseException as error:
        ledger.update(state='failure_preserved_no_remaining_networks', closed=True, error=repr(error)); raise
    finally: save(stage/'execution.json', ledger)
    command('report', [*module, 'report', '--stage', stage, '--output', stage/'report'])


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('action', choices=('prepare', 'run', 'report'))
    for name in ('repository', 'stage', 'output'): p.add_argument('--'+name, type=Path)
    a = p.parse_args()
    if a.action == 'prepare': prepare(a.repository.resolve(), a.stage.resolve())
    elif a.action == 'run': execute(a.repository.resolve(), a.output.resolve())
    else: report(a.stage.resolve(), a.output.resolve())
