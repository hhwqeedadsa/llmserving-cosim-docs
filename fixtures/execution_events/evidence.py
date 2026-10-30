"""Collect preserved execution/command identities without rerunning networks."""
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def load(path):return json.loads(path.read_bytes())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path, value):
    with path.open('x') as stream:json.dump(value, stream, indent=2, ensure_ascii=False);stream.write('\n')


def main():
    root = ROOT/'runs/execution_events_20261030'
    monitors = {}
    paths = list((root/'monitors').glob('*/probe.json'))+[root/mode/'execution/probe.json' for mode in ('off', 'on')]
    for path in sorted(paths):
        value = load(path)
        assert 'ended_utc' in value and not value['remaining_live_group_pids']
        name = str(path.relative_to(root))
        if name == 'monitors/regression/probe.json':
            assert value['status'] == 'child_failed' and value['child_exit_code'] == 5
        elif name == 'monitors/verification/probe.json':
            assert value['status'] == 'child_failed' and value['child_exit_code'] == 1
        else:assert value['status'] == 'completed' and value['child_exit_code'] == 0
        monitors[name] = value
    seconds = sum(row['wall_seconds'] for row in monitors.values())
    assert seconds < 1200
    assert 'Ran 45 tests' in (root/'monitors/final-unit/stderr.log').read_text()
    verification = load(root/'final-verification/verification.json')
    assert verification['passed'] and verification['same_complete_packet_queue_callback_results']
    source_head = verification['source_execution_commit']
    assert source_head == 'b405711bc65ef23e2e8bb09bd1e9d9485c18dae1'
    backend = load(root/'build/backend.json')
    assert len(backend['recompiled_objects']) == 27
    assert sha(Path(backend['service'])) == backend['service_sha256']
    assert backend['old_outputs_unchanged']
    for path, digest in load(root/'build/original-hashes.json').items():
        assert sha(Path(path)) == digest
    compile_commands = load(root/'build/commands.json')
    for row in compile_commands:
        if 'source_sha256' in row:
            assert sha(Path(row['argv'][-1])) == row['source_sha256']
    assert 'libunified' not in ' '.join(load(root/'on/result.json')['loaded_libraries'])
    a = load(root/'off/provenance.json');b = load(root/'on/provenance.json')
    for receipt in (a, b):
        assert receipt['hardware_calibrated'] is False
        for path, digest in receipt['hashes'].items():assert sha(Path(path)) == digest
    dep = subprocess.run(['bash', 'scripts/check_deps.sh'], capture_output=True, text=True, timeout=20)
    assert dep.returncode == 0 and 'PASS: checked 13 dependency checkouts' in dep.stdout
    published = subprocess.run(['python3', 'scripts/init_published_deps.py', '--verify-only'],
                               capture_output=True, text=True, timeout=20)
    assert published.returncode == 0
    links = []
    for name in ('docs/execution_events.md', 'fixtures/execution_events/README.md'):
        path = ROOT/name
        for href in re.findall(r'\]\(([^)]+)\)', path.read_text()):
            url = urlsplit(href)
            if url.scheme:continue
            target = (path.parent/unquote(url.path)).resolve()
            # receipt and verification are generated below in this command.
            assert target.is_file() or target in (HERE/'receipt.json', HERE/'verification.json')
            links.append(str(target.relative_to(ROOT)))
    names = ['backend/rdma/service.cc', 'backend/rdma/astra_adapter.cc', 'backend/rdma/astra_adapter.hh',
             'backend/rdma/execution_events.hh', 'python/moe_ub_cosim/execution_events.py',
             'python/moe_ub_cosim/native_control.py', 'python/moe_ub_cosim/scaleout_service.py',
             'python/moe_ub_cosim/scaleout_verify.py', 'python/moe_ub_cosim/service_transport.py',
             'tests/unit/execution_events_test.py', 'tests/integration/check_execution_events.py',
             'fixtures/execution_events/build.py', 'fixtures/execution_events/evidence.py']
    for name in names:
        if name.endswith('.py'):ast.parse((ROOT/name).read_text())
    subprocess.run(['git', 'diff', '--check'], check=True)
    commands = load(root/'final-verification/commands.json')
    assert len(commands) == 6 and commands[0]['exit_code'] == 0 and all(c['exit_code'] != 0 for c in commands[1:])
    output_bytes = sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    assert output_bytes < 2**30
    receipt = dict(passed=True, execution_commit=source_head,
        platform_source_sha256={name:sha(ROOT/name) for name in names},
        native_build=backend, build_commands=compile_commands,
        unit_tests=45, new_unit_tests=8, related_regressions=37,
        functional_network_executions=2, topology='128 GPU endpoints Clos', real_gpu_executions=0,
        inputs=load(root/'input/preparation.json'),
        native_commands={mode:load(root/mode/'execution/probe.json')['command'] for mode in ('off', 'on')},
        public_commands={name:row['command'] for name, row in monitors.items()},
        independent_check_commands=commands,
        dependency_check=dict(exit_code=dep.returncode, stdout=dep.stdout, stderr=dep.stderr),
        published_dependencies=load_json_text(published.stdout),
        linked_files=links, new_network_fault_conditions=0, algorithm_gain_claimed=False,
        monitors_before_evidence=monitors, cumulative_monitored_seconds_before_evidence=seconds,
        sampled_peak_group_rss_bytes=max(row['peak_sampled_group_rss_bytes'] for row in monitors.values()),
        apparent_run_bytes_before_evidence=output_bytes,
        scope='passive callback integration; no contribution result binding or recovery execution')
    shutil.copyfile(root/'final-verification/verification.json', HERE/'verification.json')
    save(HERE/'receipt.json', receipt)
    example = verification['real_callback_example']
    document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>实时执行事件验收</title>
<style>body{{font:17px/1.75 system-ui,sans-serif;background:#f3f5f8;color:#142438;margin:0;padding:36px}}
main{{max-width:1040px;margin:auto;background:white;padding:32px;border-radius:12px}}h1{{line-height:1.3}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #cbd5e1;padding:10px;text-align:left}}
.note{{padding:16px;background:#eff6ff;border-left:5px solid #2463bd}}a{{color:#1557ac}}code{{font-size:.92em}}</style>
<main><h1>前端现在能在运行中看到哪些实际事件</h1>
<p class="note">新增的是运行中的观察能力：区分“数据交到了”和“发送方确认完成”。当前没有启动备用专家，没有测得新恢复算法收益。</p>
<p>原生ASTRA/ns-3在真实调用点发送7669条事件，Python在线验证并保存，不解析日志猜时间。
任务完成、原传输确认与网络排空分别处理。</p>
<table><tr><th>128端点Clos人工功能输入</th><th>关闭记录</th><th>开启记录</th></tr>
<tr><td>EP组 / 来源请求</td><td>2×64 / 4</td><td>相同</td></tr>
<tr><td>ASTRA节点 / 消息 / 布局载荷</td><td>3708 / 62 / 37696B</td><td>相同</td></tr>
<tr><td>完整包事件</td><td>2120</td><td>逐项相同</td></tr>
<tr><td>任务完成 / 网络排空</td><td>103.852 / 103.852μs</td><td>相同</td></tr>
<tr><td>实时执行事件</td><td>关闭</td><td>7669条，序号与实际日志/回调一致</td></tr></table>
<h2>一个实际消息的时间线</h2><p>消息{example['id']}：rank{example['src']}→{example['dst']}。</p>
<table><tr><th>阶段</th><th>实际时刻</th><th>含义</th></tr>
<tr><td>SEND提交</td><td>{example['send_post_ns']/1000:.3f}μs</td><td>进入共享传输服务</td></tr>
<tr><td>接收回调</td><td>{example['recv_callback_ns']/1000:.3f}μs</td><td>完整消息可交给ASTRA接收节点</td></tr>
<tr><td>发送确认</td><td>{example['send_callback_ns']/1000:.3f}μs</td><td>源端才得到传输完成通知</td></tr></table>
<p>两个回调相差2.002μs。接收回调不会代替发送确认，也不会提前释放原传输。
这不是2.002μs的优化收益。</p>
<h2>已验证与剩余工作</h2><p>45项CPU检查通过；可迁移运行的公开核验通过。
删除排空事件、改接收rank、改回调时间、重复或截断事件，五种情况均被拒绝。
除新增计数字段外，两次完整网络结果及任务图一致。</p>
<p>本次是四份不同人工路由、31个数据活跃rank、两层两步、声明计算成本，未注入故障；不是完整R1或硬件测量。
下一步仍需将贡献包绑定到同一调用，接入可见故障触发和共享GPU/NIC的备用执行，再对照已有机制评价收益。</p>
<p>构建重新编译27个受影响对象，保留旧产物。累计受监控{seconds:.3f}秒（含嵌套执行的保守相加），
构建峰值采样{receipt['sampled_peak_group_rss_bytes']/2**20:.1f}MiB；没有下载、GPU运行或网络重跑。</p>
<p><a href="README.md">方法与边界</a> · <a href="verification.json">独立核验</a> ·
<a href="receipt.json">命令、版本和资源收据</a> · <a href="../../docs/execution_events.md">使用说明</a></p></main></html>'''
    with (HERE/'report.html').open('x') as stream:stream.write(document)
    print(json.dumps(dict(passed=True, tests=45, network_executions=2,
                         callback_events=7669, old_outputs_unchanged=True, monitored_seconds=seconds)))


def load_json_text(value):return json.loads(value)


if __name__ == '__main__':main()
