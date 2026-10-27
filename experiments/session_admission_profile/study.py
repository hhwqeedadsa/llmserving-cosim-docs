"""Offline profiling of the public complete transaction validator on real prefixes."""
import argparse
from collections import Counter
from copy import deepcopy
import cProfile
import hashlib
from html import escape
import json
import os
from pathlib import Path
import pstats
import shutil
import subprocess
import sys
import time

from moe_ub_cosim.scaleout_transactions import validate
from moe_ub_cosim.host_timing import read_trace

CASES = ('batch4-modern', 'batch4-generic', 'batch8-modern', 'batch8-generic')


def load(p): return json.loads(p.read_bytes())
def save(p, v): p.write_text(json.dumps(v, ensure_ascii=False, indent=2)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def digest(v): return hashlib.sha256(json.dumps(v, separators=(',', ':'), sort_keys=True).encode()).hexdigest()


def prepare(repo, run):
    observed = run/'observed'
    observed.mkdir()
    old = repo/'runs/optimized_host_cost_20261027/stage/truth'
    validator = repo/'python/moe_ub_cosim/scaleout_transactions.py'
    historical = subprocess.check_output(['git', 'show', 'd3e639f:python/moe_ub_cosim/scaleout_transactions.py'], cwd=repo)
    assert historical == validator.read_bytes(), 'main validation source changed from recorded O3 phase'
    for case in CASES:
        batch, mode = case.split('-')
        source, target = old/batch/mode, observed/case
        target.mkdir()
        native = load(source/'native-command.json')
        for name, index in (('topology.json', 1), ('protocol.json', 2)):
            path = Path(native['command'][index])
            assert sha(path) == native['input_hashes'][str(path)]
            shutil.copyfile(path, target/name)
        for name in ('initial.json', 'control.jsonl', 'worker_host.jsonl', 'native_host.jsonl', 'native-command.json', 'equivalence.json'):
            shutil.copyfile(source/name, target/name)
        assert load(target/'equivalence.json')['passed']
        assert load(target/'initial.json')['operations'] == []
    shutil.copyfile(repo/'experiments/optimized_host_cost/results/20261027/summary.json', observed/'old-summary.json')
    save(run/'inputs.json', dict(files={str(p.relative_to(run)): sha(p) for p in observed.rglob('*') if p.is_file()},
        source_files={str(p.relative_to(repo)): sha(p) for p in (repo/'python/moe_ub_cosim').glob('*.py')},
        validator_matches_old_commit='d3e639f', source_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip(),
        new_networks=0, hardware_calibrated=False))


def measure(repo, run, case):
    assert all(sha(run/n) == d for n, d in load(run/'inputs.json')['files'].items())
    root = run/'observed'/case
    config, topology, protocol = load(root/'initial.json'), load(root/'topology.json'), load(root/'protocol.json')
    validate(topology, protocol, config, allow_no_remote=True)
    profiler, prefixes = cProfile.Profile(), []
    for line in (root/'control.jsonl').read_text().splitlines():
        record = json.loads(line)
        if record['direction'] != 'frontend':
            continue
        command = record['message']
        start, cpu = time.perf_counter_ns(), time.process_time_ns()
        candidate = deepcopy(config)
        candidate['operations'].extend(deepcopy(command['submissions']))
        copy_cpu, copy_wall = time.process_time_ns()-cpu, time.perf_counter_ns()-start
        start, cpu = time.perf_counter_ns(), time.process_time_ns()
        profiler.runcall(validate, topology, protocol, candidate, allow_no_remote=True)
        validate_cpu, validate_wall = time.process_time_ns()-cpu, time.perf_counter_ns()-start
        prefixes.append(dict(decision_id=command['decision_id'], nodes=len(candidate['operations']),
            added=len(command['submissions']), candidate_sha256=digest(candidate),
            copy_cpu_ns=copy_cpu, copy_wall_ns=copy_wall,
            profiled_validation_cpu_ns=validate_cpu, profiled_validation_wall_ns=validate_wall))
        config = candidate
    rows = []
    def key(value):
        file, line, name = value
        path = Path(file)
        return dict(path=str(path.relative_to(repo)) if path.is_absolute() and path.is_relative_to(repo) else file,
                    line=line, function=name)
    for function, (primitive, calls, exclusive, inclusive, callers) in pstats.Stats(profiler).stats.items():
        rows.append(dict(**key(function), primitive_calls=primitive, calls=calls,
            exclusive_wall_seconds=exclusive, cumulative_wall_seconds=inclusive,
            callers=[dict(**key(caller), values=list(values)) for caller, values in sorted(callers.items())]))
    rows.sort(key=lambda r: (r['path'], r['line'], r['function']))
    save(run/'profiles'/(case+'.json'), dict(case=case, prefixes=prefixes, functions=rows,
        final_candidate_sha256=digest(config), profiling_overhead_included=True,
        profile_clock='cProfile default monotonic wall timer', new_networks=0))


def prefix_summary(root):
    config = load(root/'initial.json')
    static_hash = digest({k: v for k, v in config.items() if k != 'operations'})
    counters = Counter()
    rows = []
    for line in (root/'control.jsonl').read_text().splitlines():
        record = json.loads(line)
        if record['direction'] != 'frontend':
            continue
        command = record['message']
        config['operations'] += command['submissions']
        operations = config['operations']
        remote = sum(op['kind'] == 'remote' for op in operations)
        edges = sum(len(op['after']) for op in operations)
        full = len(json.dumps(config, separators=(',', ':')).encode())
        delta = len(json.dumps(command['submissions'], separators=(',', ':')).encode())
        counters.update(candidate_nodes=len(operations), new_nodes=len(command['submissions']),
            candidate_dependencies=edges, candidate_remote_nodes=remote,
            per_history_loop_visits=remote*(remote-1)//2,
            full_candidate_json_bytes=full, appended_operations_json_bytes=delta,
            empty_commands=int(not command['submissions']))
        assert digest({k: v for k, v in config.items() if k != 'operations'}) == static_hash
        rows.append(dict(decision_id=command['decision_id'], nodes=len(operations), added=len(command['submissions']),
                         candidate_sha256=digest(config)))
    return dict(commands=len(rows), final_nodes=len(config['operations']), **counters,
        prefix_node_revisit_factor=counters['candidate_nodes']/len(config['operations']),
        final_candidate_sha256=digest(config), static_configuration_unchanged=True, prefixes=rows)


def analyze(run, output):
    output.mkdir()
    inputs, old = load(run/'inputs.json'), load(run/'observed/old-summary.json')
    assert all(sha(run/n) == d for n, d in inputs['files'].items())
    cases = {}
    for case in CASES:
        profile = load(run/'profiles'/(case+'.json'))
        counts = prefix_summary(run/'observed'/case)
        assert [{k: row[k] for k in ('decision_id', 'nodes', 'added', 'candidate_sha256')} for row in profile['prefixes']] == counts['prefixes']
        functions = profile['functions']
        main, = [r for r in functions if r['path'] == 'python/moe_ub_cosim/scaleout_transactions.py' and r['function'] == 'validate']
        streams, = [r for r in functions if r['path'] == 'python/moe_ub_cosim/scaleout_streams.py' and r['function'] == 'validate']
        assert main['calls'] == streams['calls'] == counts['commands']
        # streams.validate is a disjoint child on each complete-validator call.
        static_fraction = streams['cumulative_wall_seconds']/main['cumulative_wall_seconds']
        mode = case.split('-')[1]
        traces = {name: read_trace(run/'observed'/case/(name+'_host.jsonl')) for name in ('worker', 'native')}
        assert all(t['trace_complete'] for t in traces.values())
        worker, native = traces['worker']['phases'], traces['native']['phases']
        total = worker[mode.replace('modern', 'modern').replace('generic', 'generic')+'_worker']['inclusive_cpu_ns']+native['native_total']['inclusive_cpu_ns']
        assert total == old['cases'][case]['optimized_O3']['observed_CPU_ns']
        candidate_ns = sum(worker[n]['exclusive_cpu_ns'] for n in ('python_candidate_copy', 'python_candidate_validate'))
        candidate_ns += sum(native[n]['exclusive_cpu_ns'] for n in ('session_candidate_copy', 'session_candidate_validate'))
        assert all(worker[n]['count'] == counts['commands'] for n in ('python_candidate_copy', 'python_candidate_validate'))
        top = sorted(functions, key=lambda r: -r['exclusive_wall_seconds'])[:12]
        cases[case] = dict(counts={k: v for k, v in counts.items() if k != 'prefixes'},
            profiled_validation_wall_seconds=main['cumulative_wall_seconds'],
            static_streams_validation_fraction=static_fraction, prioritize_static_candidate=static_fraction >= .10,
            copy_cpu_ns=sum(r['copy_cpu_ns'] for r in profile['prefixes']),
            profiled_validation_cpu_ns=sum(r['profiled_validation_cpu_ns'] for r in profile['prefixes']),
            original_total_cpu_ns=total, original_candidate_cpu_ns=candidate_ns,
            original_candidate_cpu_fraction=candidate_ns/total,
            optimistic_all_candidate_free_cpu_factor=total/(total-candidate_ns),
            top_exclusive_functions=[{k: v for k, v in r.items() if k != 'callers'} for r in top])
    summary = dict(passed=True, cases=cases, new_networks=0, profile_sweeps=4, measured_speedup=False,
        profiling_overhead_included=True, CPU_free_factor_is_not_achievable_speedup=True,
        ordinary_caching_is_not_new_contribution=True)
    save(output/'summary.json', summary)
    lines = ['# 完整候选准入的重复处理', '',
        '本轮只离线执行公开完整校验，不重新运行网络。输入为四份O3实际完成调用的DECIDE/COMMIT；原CPU来自原完整时钟，cProfile仅用于定位当前校验热点，二者不能直接换算。', '',
        '| 条件 | 实际命令 | 最终节点 | 累计节点检查 | 重访倍数 | 原候选复制/校验占CPU | 全部候选处理免费CPU倍数 | 固定streams子校验占剖析 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    cells = []
    for case, row in cases.items():
        c = row['counts']
        values = (case, str(c['commands']), str(c['final_nodes']), str(c['candidate_nodes']),
            f"{c['prefix_node_revisit_factor']:.2f}", f"{100*row['original_candidate_cpu_fraction']:.2f}%",
            f"{row['optimistic_all_candidate_free_cpu_factor']:.3f}", f"{100*row['static_streams_validation_fraction']:.2f}%")
        lines.append('| '+' | '.join(values)+' |')
        cells.append('<tr>'+''.join('<td>'+escape(v)+'</td>' for v in values)+'</tr>')
    lines += ['', '免费倍数把所有Python/C++候选复制和校验工作都当可消除，并保持其他CPU不变；只是旧记录中的宽松CPU工作量界，不是可达、墙钟或千卡速度。剖析中的固定streams子调用含物理连接、拓扑路径和协议验证，不包含全部静态校验；其比例包含剖析开销，不能乘原CPU来承诺收益。', '',
        '## 应优先改什么', '']
    for case, row in cases.items():
        lines.append(case+'：'+('达到事前10%门槛，可进一步比较类型安全静态缓存及其键构建成本。' if row['prioritize_static_candidate'] else
            '固定streams子校验未达到事前10%门槛，不优先为这部分添加缓存。'))
        lines.append('独占热点：'+'；'.join(r['function']+' '+f"{r['exclusive_wall_seconds']:.5f}s" for r in row['top_exclusive_functions'][:6])+'。')
    lines += ['', '候选反复包含旧操作，准入会重算依赖祖先、同连接效果传播和重叠写顺序。两个远端历史循环每候选各遍历R(R−1)/2对；这是源代码循环上界内的实际迭代组合数，不等于冲突数或CPU。复制JSON逻辑体积也不等于真实堆分配。', '',
        '## 改进必须保留的约束', '',
        '静态缓存必须区分bool/int/float和容器类型，验证拓扑/协议/绑定确实未变，并在拒绝后保留旧状态；Python普通相等或JSON序列化可能掩盖输入类型差异。增量准入必须保留旧节点身份、事件类型、祖先和跨连接远端效果、写区间、窗口/注册权限及应用窗口寿命，新增节点仍要与旧历史比较。不能只校验新节点、跳过native检查、删依赖或把实际完成换成预置时间。', '',
        '这些属于正确实现和强基线建设，不单独算论文创新。本轮没有实现优化、测得提速或验证大EP；两项候选仍须证明特定语义带来的必要性及相对强实现的实际成本/误差优势。']
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    header = ''.join('<th>'+escape(v.strip())+'</th>' for v in lines[4].strip('|').split('|'))
    html = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>完整候选准入成本</title><style>body{font:16px/1.8 system-ui;max-width:1150px;margin:auto;padding:28px;background:#f4f7fa;color:#193047}.scroll{overflow:auto}table{border-collapse:collapse;background:white;white-space:nowrap}td,th{padding:10px;border-bottom:1px solid #dbe4ed;text-align:left}</style><h1>完整候选准入：重复工作在哪里</h1><p>四份已完成真实调用的离线剖析，零新网络。原完整CPU、当前剖析比例和循环计数分开解释。</p><div class="scroll"><table><tr>'+header+'</tr>'+''.join(cells)+'</table></div><p>“全部候选免费”只是宽松CPU工作量界。剖析含额外开销，不能与旧CPU比例相乘，更不是已实现的速度。</p><h2>下一步的条件</h2><p>先按热点决定实现对象。缓存和增量检查需保留输入类型、因果关系、内存写顺序和失败原子性；常规缓存本身不作为新贡献。</p><p><a href="report.md">详细解释</a> · <a href="summary.json">完整指标</a></p></html>'
    (output/'report.html').write_text(html+'\n')


def execute(repo, run):
    run.mkdir(); (run/'monitor').mkdir(); (run/'profiles').mkdir(); (run/'empty-cwd').mkdir()
    env = os.environ | {'PYTHONPATH': str(repo/'python')+':'+str(repo), 'PYTHONDONTWRITEBYTECODE': '1'}
    ledger = dict(state='started', closed=False, records=[], profile_limit=4, new_networks=0)
    try:
        for action, case in [('prepare', None), *[('measure', c) for c in CASES]]:
            assert sum(load(p)['wall_seconds'] for p in run.rglob('probe.json'))+65 < 600
            assert sum(p.stat().st_size for p in run.rglob('*') if p.is_file())+32*2**20 < 256*2**20
            label = action+('-'+case if case else '')
            argv = [sys.executable, '-B', '-m', 'experiments.session_admission_profile.study', action,
                '--repository', str(repo), '--run', str(run), *(['--case', case] if case else [])]
            row = dict(label=label, command=argv, cwd=str(run/'empty-cwd'), state='started')
            ledger['records'].append(row); save(run/'execution.json', ledger)
            command = [sys.executable, '-B', '-m', 'moe_ub_cosim.host_probe', '--output-dir', str(run/'monitor'/label),
                '--rss-bytes', str(2**30), '--address-bytes', str(2**31), '--artifact-bytes', str(256*2**20),
                '--artifact-root', str(run), '--wall-seconds', '60', '--min-free-bytes', str(128*2**20), '--', *argv]
            result = subprocess.run(command, cwd=run/'empty-cwd', env=env, timeout=65)
            row.update(state='terminal', exit_code=result.returncode); save(run/'execution.json', ledger)
            assert result.returncode == 0, 'preserve failure; stop remaining profiles'
            print(label, 'passed', flush=True)
        ledger.update(state='completed', closed=True)
    except BaseException as error:
        ledger.update(state='failure_preserved', closed=True, error=str(error)); raise
    finally:
        save(run/'execution.json', ledger)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('prepare', 'measure', 'analyze', 'run'))
    for name in ('repository', 'run', 'output'):
        parser.add_argument('--'+name, type=Path)
    parser.add_argument('--case', choices=CASES)
    args = parser.parse_args()
    if args.action == 'prepare': prepare(args.repository.resolve(), args.run.resolve())
    elif args.action == 'measure': measure(args.repository.resolve(), args.run.resolve(), args.case)
    elif args.action == 'analyze': analyze(args.run.resolve(), args.output.resolve())
    else: execute(args.repository.resolve(), args.run.resolve())
