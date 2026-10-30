"""Collect identities/resource receipts from completed atomic32 checks; no runs."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); run = args.run.resolve(); out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    assert not (out/'verification.json').exists() and not (out/'report.html').exists()
    load = lambda name: json.loads((run/name).read_bytes())
    build = load('build/backend.json'); provenance = load('build/provenance.json')
    assert build['passed'] and build['native_commit'] == '69cb14764c0c5de2c7ad31599ee5b3999dddc154'
    for name, digest in provenance['sources'].items():
        assert sha(ROOT/name) == digest, 'Built source changed: '+name
    for path in build['linked_ns3_libraries']:
        assert sha(Path(path)) == build['library_hashes'][Path(path).name]
    audit = load('audit-final/verification.json'); cpu = load('cpu/verification.json')
    admission = load('admission/verification.json')
    assert audit['passed'] and cpu['passed'] and admission['passed']
    observed = {}
    for name in ('normal', 'recovery', 'session-recovery'):
        result = load('cases/'+name+'/run/result.json')
        libraries = {str(Path(p).resolve()) for p in result['loaded_libraries'] if Path(p).name.startswith('libns3')}
        assert libraries == {str(Path(p).resolve()) for p in build['linked_ns3_libraries']}
        command = load('cases/'+name+'/command.json'); assert command['exit_code'] == 0
        for path, digest in command['sha256'].items():
            assert sha(Path(path)) == digest, 'Executed input/executable changed: '+path
        observed[name] = dict(result_sha256=sha(run/'cases'/name/'run/result.json'),
                              input_identity=command['sha256'], executable=command['argv'][-3])
    monitors = {}
    for file in sorted((run/'monitors').glob('*/probe.json')):
        probe = json.loads(file.read_bytes())
        assert probe['status'] == 'completed' and probe['child_exit_code'] == 0
        assert not probe['remaining_live_group_pids']
        monitors[file.parent.name] = dict(wall_seconds=probe['wall_seconds'],
            peak_rss_bytes=probe['peak_sampled_group_rss_bytes'],
            peak_artifact_bytes=probe['peak_sampled_artifact_bytes'], sha256=sha(file))
    total_seconds = sum(p['wall_seconds'] for p in monitors.values())
    assert total_seconds < 1800
    artifact_bytes = sum(p.stat().st_size for p in run.rglob('*') if p.is_file())
    assert artifact_bytes < 512*2**20
    python_methods = {}
    for name in ('atomic32_input_test.py', 'alltransactions_test.py', 'allsessionalltest.py', 'allapplication_windowalltest.py'):
        content = (run/'cpu'/(name+'.stderr')).read_text()
        count = re.search(r'Ran (\d+) tests?', content)
        assert count and int(count[1]) > 0 and content.rstrip().endswith('OK')
        python_methods[name] = int(count[1])
    modified = subprocess.check_output(['git', 'ls-files', '-m', '-o', '--exclude-standard'], cwd=ROOT, text=True).splitlines()
    sources = {name: sha(ROOT/name) for name in sorted(set(modified))
               if (ROOT/name).is_file() and not name.startswith('fixtures/atomic32/')}
    sources.update({name: sha(ROOT/name) for name in (
        'examples/scaleout_clos128/clos-128.json', 'examples/scaleout_clos128/rdma.json',
        'examples/scaleout_rdma_transactions/selective-chain.json')})
    dependencies = (run/'dependencies.stdout').read_text()
    assert '13' in dependencies and 'PASS' in dependencies
    summary = dict(passed=True, scope='functional 32-bit notifications on sparse 128-endpoint Clos',
        overall_goal='active', cpu=cpu['cores'], python_unit_methods=python_methods,
        network=audit, admission=admission, build=dict(native_commit=build['native_commit'],
            translation_units=build['compiler_options']['translation_units'],
            built_source_sha256=provenance['sources'], linked_library_sha256=build['library_hashes']),
        executed=observed, source_sha256=sources, monitors=monitors,
        completed_monitored_wall_seconds_at_receipt=total_seconds,
        peak_sampled_group_rss_bytes=max(p['peak_rss_bytes'] for p in monitors.values()),
        artifact_apparent_bytes_at_receipt=artifact_bytes, raw_run=str(run),
        limitations=['artificial data, four active ranks, no real routing input',
                     'MRT1 framing and reliable response streams, not RoCE Atomic ACK',
                     'no GIN/provider/GPU timing or visibility calibration',
                     'complete legacy MoE plan network binding remains incomplete'],
        review_notes=['Initial read-only audit used queue column 10 as payload length; corrected to column-name lookup.',
                      'Existing normal network reused for auditor refinements; each declared network executed once.',
                      'No dependency/source upgrade, package install, cleanup or physical GPU execution.'])
    (out/'verification.json').write_text(json.dumps(summary, indent=2)+'\n')
    rows = ''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in (
        name, case['request_frames'], case['reply_frames'], case['logical_transport_bytes'],
        case['transmitted_payload_bytes'], case['retransmitted_payload_bytes']))+'</tr>'
        for name, case in audit['cases'].items())
    report = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>32位通知：功能验收</title><style>
body{max-width:1000px;margin:40px auto;padding:0 22px;font:17px/1.75 system-ui;color:#17283b;background:#f8fafc}
article{background:white;padding:28px;border-radius:16px}table{border-collapse:collapse;width:100%;font-size:15px}
th,td{padding:10px;border-bottom:1px solid #d7e0ea;text-align:left}code{background:#edf2f7;padding:2px 5px}
.note{border-left:4px solid #4279ba;padding:10px 18px;background:#eef5ff}a{color:#2163aa}</style>
<article><h1>4字节通知已能经过实际网络推进</h1>
<p>旧低延迟通信需要4字节计数器；原事务接口只支持8字节。本次新增显式32位原子和条件等待，保持原64位默认行为。</p>
<p class="note">128端点Clos，只有0、1、16、17四个rank活动。人工功能事务；没有运行真实模型或GPU。本页展示正确性，不展示研究加速收益。</p>
<h2>实际执行</h2><p>两次写入、六次32位原子、一次64位原子和两个本地复制节点，共用一张原生ns-3网络和同一时钟。</p>
<table><thead><tr><th>条件</th><th>请求帧</th><th>回复帧</th><th>去重传输B</th><th>实际发送B</th><th>重复发送B</th></tr></thead><tbody>'''+rows+'''</tbody></table>
<p>恢复条件各丢失一个请求包和一个回复包。三条件最终窗口逐字节相同，邻居哨兵不变；目标仅应用9个请求，所有源保护、队列和物理包排空。</p>
<h2>为什么额外发送184字节</h2><p>请求流重发36B；回复流快速重传发送76B、随后超时重传发送72B。反馈尚未覆盖的在途片段也可能再次发送。不能把源重传包数当作目标应用次数。</p>
<h2>怎样核查</h2><p>独立脚本读取实际source TX与payload_delivered字节，还原MRT1请求/回复；按事件顺序重放32/64位加法、本地条件清零和复制，并比较完整最终窗口。9类非法输入在Python与两个原生入口均拒绝，6类篡改结果也被检出。原子退休仍等待请求ACK、实际旧值回复及前序退休。</p>
<h2>交付边界与下一步</h2><p>这是接入完整旧低延迟MoE计划的前置能力。仍需将计划操作绑定到持久连接、注册窗口、通知及发送发布；随后才能做真实专家流量下的完整比较。MRT1不是RoCE线格式，反向可靠流不是Atomic ACK；本地耗时没有GPU校准。</p>
<p><a href="verification.json">完整核验、源码身份与资源摘要</a> · <a href="../../examples/scaleout_atomic32/README.md">用户运行说明</a></p></article></html>'''
    (out/'report.html').write_text(report)
    print(json.dumps(dict(passed=True, output=str(out), monitored_wall_seconds=total_seconds,
                          artifact_apparent_bytes=artifact_bytes)))


if __name__ == '__main__':
    main()
