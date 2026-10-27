"""O3 observations on fixed inputs; platform use stays on public entry points."""
import argparse
from collections import Counter
from copy import deepcopy
import html
import json
import os
from pathlib import Path
import subprocess
import sys
from experiments.shared_uplink_choice.prepare import admit, load, save, sha
from experiments.shared_host_cost.analyze import metrics
from experiments.shared_host_cost.verify import sweep
from moe_ub_cosim.native_compilation import inspect_commands

OLD_SHA = '042e8c258b7f1e193fcfa68e20ba8f7da5ad6156fef29f01f23418509492b5cf'
NEW_SHA = '2b93cc921820cc72cdc66b34596b30b4ca742622a497045a2600c64aa3513d99'


def prepare(repo, stage):
    blobs, manifest = admit(repo/'runs/shared_host_cost_20261027/checkpoint.tar.gz', OLD_SHA, 'shared-host-cost-checkpoint/1')
    copied = {}
    for name, raw in blobs.items():
        for old, new in (('run/stage/observed/', 'observed/'), ('run/stage/frozen/', 'frozen/'),
                         ('run/stage/truth/', 'reference/')):
            if name.startswith(old):
                relative = new+name.removeprefix(old); path = stage/relative
                path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
                copied[relative] = sha(path)
    wrapper = blobs['run/stage/runtime/run-transaction-session']
    del blobs
    blobs, manifest = admit(repo/'runs/native_release_build_20261027/checkpoint.tar.gz', NEW_SHA, 'native-release-build-checkpoint/1')
    runtime = stage/'runtime'; runtime.mkdir(); files = {}
    prefix = 'evidence/build/output/'
    for name, raw in blobs.items():
        if name.startswith(prefix+'lib/') or name == prefix+'moe-rdma/ns3.42-moe-rdma-transaction-session':
            tail = name.removeprefix(prefix)
            path = runtime/('transaction-session' if tail.startswith('moe-rdma/') else tail)
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw); path.chmod(manifest['files'][name]['mode'])
            files[str(path.relative_to(runtime))] = sha(path)
    path = runtime/'run-transaction-session'; path.write_bytes(wrapper); path.chmod(0o755); files[path.name] = sha(path)
    compiler = stage/'compiler'; compiler.mkdir()
    for name in ('provenance.json', 'compiler-options.json', 'cmake/compile_commands.json'):
        path = compiler/Path(name).name; path.write_bytes(blobs['evidence/build/'+name]); copied[str(path.relative_to(stage))] = sha(path)
    provenance = load(compiler/'provenance.json')
    assert len(files) == 10 and inspect_commands(compiler/'compile_commands.json') == load(compiler/'compiler-options.json')
    for name, digest in provenance['sources'].items(): assert sha(repo/name) == digest
    save(stage/'runtime-identity.json', dict(files=files, sources=provenance['sources']))
    save(stage/'source.json', dict(old_archive=OLD_SHA, optimized_archive=NEW_SHA, copied=copied,
        new_builds=0, hardware_calibrated=False, platform_head='d3e639f'))


def compare(stage, batch, mode):
    old, new = stage/f'reference/batch{batch}/{mode}', stage/f'truth/batch{batch}/{mode}'
    identity = load(stage/'runtime-identity.json')
    for name, digest in identity['files'].items(): assert sha(stage/'runtime'/name) == digest
    expected = {Path(n).name: d for n, d in identity['files'].items() if n.startswith('lib/')}
    def project(value, live):
        value = deepcopy(value); names = []
        for name in value['loaded_libraries']:
            base = Path(name).name
            if base.startswith('libns3.'):
                assert base in expected
                if live: assert sha(Path(name)) == expected[base]
                names.append(base)
            else: names.append(name)
        assert {n for n in names if n.startswith('libns3.')} == set(expected)
        value['loaded_libraries'] = sorted(names)
        return value
    assert project(load(old/'result.json'), False) == project(load(new/'result.json'), True), 'native behavior changed beyond declared loader metadata'
    snapshot = 'modern-execution.json' if mode == 'modern' else 'event-execution.json'
    assert load(old/snapshot) == load(new/snapshot) and (old/'control.jsonl').read_bytes() == (new/'control.jsonl').read_bytes()
    result = load(new/'result.json')
    assert result['passed'] and result['session']['finished'] and not result['packet_capture']['omitted_events']
    save(new/'equivalence.json', dict(passed=True, full_state_and_control=True,
        loader_paths_and_order_preregistered=True, live_native_library_hashes_checked=True))


def report(stage, output):
    output.mkdir(); rows = {}
    for name, digest in load(stage/'source.json')['copied'].items(): assert sha(stage/name) == digest
    for name, digest in load(stage/'runtime-identity.json')['files'].items(): assert sha(stage/'runtime'/name) == digest
    assert inspect_commands(stage/'compiler/compile_commands.json') == load(stage/'compiler/compiler-options.json')
    ledger = load(stage/'execution.json'); assert ledger['closed'] and ledger['state'] == 'completed'
    assert sum(r['network'] for r in ledger['records']) == 4 and all(r['exit_code'] == 0 for r in ledger['records'])
    for batch in (4, 8):
        for mode in ('modern', 'generic'):
            key = f'batch{batch}-{mode}'; directory = stage/f'truth/batch{batch}/{mode}'
            assert load(directory/'equivalence.json')['passed']
            old = metrics(stage/f'reference/batch{batch}/{mode}', mode); new = metrics(directory, mode)
            for writer in ('worker', 'native'):
                records = [json.loads(line) for line in (directory/(writer+'_host.jsonl')).read_text().splitlines()]
                assert sweep(records) == new[writer+'_phases'], 'independent phase sweep differs'
            for field in ('source_packets', 'physical_TX', 'last_native_snapshot'):
                assert old[field] == new[field], 'network workload/counter changed'
            rows[key] = dict(old_O0=old, optimized_O3=new,
                observed_total_CPU_ratio=old['observed_CPU_ns']/new['observed_CPU_ns'],
                stable_speedup_estimated=False)
    save(output/'summary.json', dict(passed=True, cases=rows, network_observations=4,
        observed_once_per_condition=True, record_overhead_included=True, hardware_calibrated=False,
        pure_packet_CPU_measured=False, stable_wall_speedup_proven=False, novel_algorithm_speedup_proven=False))
    lines = ['# 优化后强基线：还能优化哪里', '',
        '同一真实路由输入、共享上联网络和完整通信调用；四次观测的网络、control及应用与旧结果一致，仅事前按库名多重集合对应加载元数据。CPU是宿主工作量，不是GPU或网络仿真时延。', '',
        '| 条件 | 旧native CPU s | O3 native CPU s | O3 Python CPU s | O3 native占比 | O3 Run exclusive占总CPU | 全部native免费CPU工作量倍数 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    cells = []
    for name, row in rows.items():
        old, new = row['old_O0'], row['optimized_O3']
        values = (name, f"{old['native_root']['inclusive_cpu_ns']/1e9:.4f}", f"{new['native_root']['inclusive_cpu_ns']/1e9:.4f}",
            f"{new['worker_root']['inclusive_cpu_ns']/1e9:.4f}", f"{100*new['all_native_CPU_fraction']:.2f}%",
            f"{100*new['run_exclusive_CPU_fraction']:.2f}%", f"{new['zero_native_CPU_work_factor']:.3f}")
        lines.append('| '+' | '.join(values)+' |')
        cells.append('<tr>'+''.join('<td>'+html.escape(v)+'</td>' for v in values)+'</tr>')
    lines += ['', '这些是每条件一次、包含逐记录刷新开销的CPU观测，不能作稳定墙钟速度排名。O0与O3差异来自常规构建修复，不是新算法贡献。全部native免费上限保持Python工作不变、同时免除初始化/校验/报告等所有原生成本；它不是纯包优化或墙钟/GPU加速上限。', '',
        'Run exclusive仍混合网络、传输、内存和事件分派；部分包引发的推进/通知计在其他阶段，因此也不能把该比例当所有包相关优化的严格上限。', '', '主要exclusive阶段（CPU秒）：', '']
    for name, row in rows.items():
        new = row['optimized_O3']
        for writer in ('worker', 'native'):
            phases = sorted(new[writer+'_phases'].items(), key=lambda pair: -pair[1]['exclusive_cpu_ns'])[:4]
            lines.append(name+' / '+writer+'：'+'；'.join(f"{n}={p['exclusive_cpu_ns']/1e9:.5f}" for n, p in phases)+'。')
    lines += ['', '优先级应按优化后的主导阶段和完整流程成本确定。不能继续用旧80%—86%的原生占比承诺包级算法收益；减少包处理还必须证明多跳、排队、反馈和应用完成保持。四活动rank/单层/便利样本和声明成本不提供千卡或现代硬件性能泛化。两个论文候选仍未成立。']
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    (output/'report.html').write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>优化后强基线的成本</title><style>body{font:16px/1.8 system-ui;background:#f4f7fa;color:#183047}main{max-width:1150px;margin:auto;padding:28px}table{border-collapse:collapse;background:white}th,td{padding:12px;border-bottom:1px solid #dae3ed;white-space:nowrap;text-align:left}.scroll{overflow:auto}a{color:#15618d}</style><main><h1>优化后强基线：时间花在哪里</h1><p>四次固定真实路由观测；全部网络、control及应用行为与旧结果一致。表中CPU是宿主运行模拟器的工作量。</p><div class="scroll"><table><thead><tr><th>条件</th><th>旧native秒</th><th>O3 native秒</th><th>O3 Python秒</th><th>native占比</th><th>Run exclusive占比</th><th>全部native免费CPU倍数</th></tr></thead><tbody>'''+''.join(cells)+'''</tbody></table></div><p>常规编译修复不是算法创新。每条件只观测一次，包含计时记录开销，不提供稳定墙钟速度或千卡外推。Run exclusive不是纯包CPU，也不是所有包相关优化的上限；全部native免费只给保持Python不变时的宽松CPU工作量上限。</p><p>下一步应针对优化后真正主导的部分投入，再验证保留多跳排队、反馈和应用完成的处理缩减。两个论文候选仍待证实。</p><p><a href="report.md">详细阶段</a> · <a href="summary.json">数据与边界</a></p></main></html>''')


def execute(repo, output):
    output.mkdir(); stage = output/'stage'; stage.mkdir(); (stage/'empty-cwd').mkdir(); (output/'monitor').mkdir()
    ledger = dict(state='started', closed=False, records=[], network_reruns=0)
    env = os.environ | {'PYTHONPATH': str(repo/'python')+':'+str(repo), 'PYTHONDONTWRITEBYTECODE': '1'}
    def run(label, argv, network=False):
        assert sum(r['network'] for r in ledger['records'])+int(network) <= 4
        assert sum(load(p).get('wall_seconds', 0) for p in output.rglob('probe.json'))+65 < 600
        assert sum(p.stat().st_size for p in output.rglob('*') if p.is_file())+64*2**20 < 512*2**20
        row = dict(label=label, network=network, command=list(map(str, argv)), exit_code=None)
        ledger['records'].append(row); save(stage/'execution.json', ledger)
        command = [sys.executable, '-B', '-m', 'moe_ub_cosim.host_probe', '--output-dir', str(output/'monitor'/label),
            '--rss-bytes', str(2**30), '--address-bytes', str(2**31), '--artifact-bytes', str(512*2**20),
            '--artifact-root', str(output), '--wall-seconds', '60', '--min-free-bytes', str(8*2**30), '--', *row['command']]
        done = subprocess.run(command, cwd=stage/'empty-cwd', env=env, timeout=65)
        row['exit_code'] = done.returncode; save(stage/'execution.json', ledger)
        assert done.returncode == 0, 'preserve failure; stop remaining networks'
        print(label, 'passed', flush=True)
    module = [sys.executable, '-B', '-m', 'experiments.optimized_host_cost.study']
    cli = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout']
    try:
        run('prepare', [*module, 'prepare', '--repository', repo, '--stage', stage]); (stage/'truth').mkdir()
        for batch in (4, 8):
            (stage/'truth'/f'batch{batch}').mkdir()
            for mode in ('modern', 'generic'):
                root = stage/f'truth/batch{batch}/{mode}'
                if mode == 'modern':
                    argv = [*cli, 'modern-deepep-native', '--topology', stage/'observed/baseline-topology',
                        '--protocol', stage/'observed/baseline-protocol.json', '--config', stage/f'observed/batch{batch}/input.json',
                        '--backend', stage/'runtime/run-transaction-session', '--output', root, '--callback-batches', '--host-timing']
                else: argv = [sys.executable, '-B', '-m', 'experiments.shared_host_cost.generic', '--stage', stage, '--batch', batch]
                run(f'batch{batch}-{mode}', argv, True)
                run(f'batch{batch}-{mode}-compare', [*module, 'compare', '--stage', stage, '--batch', batch, '--mode', mode])
        ledger.update(state='completed', closed=True)
    except BaseException as error:
        ledger.update(state='failure_preserved_no_remaining_networks', closed=True, error=str(error)); raise
    finally: save(stage/'execution.json', ledger)


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('action', choices=('prepare', 'compare', 'report', 'run'))
    for n in ('repository', 'stage', 'output'): p.add_argument('--'+n, type=Path)
    p.add_argument('--batch', type=int, choices=(4, 8)); p.add_argument('--mode', choices=('modern', 'generic'))
    a = p.parse_args()
    if a.action == 'prepare': prepare(a.repository.resolve(), a.stage.resolve())
    elif a.action == 'compare': compare(a.stage.resolve(), a.batch, a.mode)
    elif a.action == 'report': report(a.stage.resolve(), a.output.resolve())
    else: execute(a.repository.resolve(), a.output.resolve())
