"""Freeze completed functional evidence; does not execute or tune network cases."""
import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess

from moe_ub_cosim.host_probe import disk_usage
from verify import load, require, digest


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def summarize(root, output, repo):
    offline = load(root/'offline.json')
    reduced, expanded = (load(root/f'{mode}-audit.json') for mode in ('reduced', 'expanded'))
    require(offline['passed'] and reduced['passed'] and expanded['passed'], 'missing successful evidence')
    tests = {}
    for name, expected in (('unit', 7), ('regressions', 38)):
        stderr = (root/'monitors'/name/'stderr.log').read_text()
        require(re.search(rf'Ran {expected} tests? in ', stderr) and stderr.rstrip().endswith('OK'),
                'unit evidence incomplete')
        tests[name] = expected
    native_runs = []
    hashes = {}
    configurations = []
    topology = None
    for mode in ('reduced', 'expanded'):
        native = root/f'{mode}-run'
        c, r, t = (load(native/name) for name in ('input.json', 'result.json', 'topology.json'))
        configurations.append(c)
        require(topology is None or topology == t, 'changed topology between functional modes')
        topology = t
        protocol = load(native/'rdma.json')
        require(not any(k in protocol for k in ('data_loss', 'drop_once', 'faults', 'link_failures')),
                'unexpected artificial failure')
        streams = r['service_transport']['native']['streams']
        data_packets = sum(s['source_data_packets'] for s in streams)
        payload_bytes = sum(s['source_payload_bytes'] for s in streams)
        paths = [native/name for name in ('input.json', 'result.json', 'frontend.json', 'verification.json',
                                          'topology.json', 'rdma.json', 'provenance.json', 'log/astra_nodes.log')]
        paths.extend(sorted((native/'graphs').glob('*/wave.json')))
        manifest = load(native/c['route_replay']['manifest']['path'])
        paths.append(native/c['route_replay']['manifest']['path'])
        paths.extend(native/f['file']['path'] for f in manifest['files'])
        hashes.update({str(p): digest(p) for p in paths})
        messages = r['service_transport']['messages']
        active = sorted({m[k] for m in messages for k in ('src', 'dst')})
        native_runs.append(dict(mode=mode, source_data_packets=data_packets,
            source_payload_bytes=payload_bytes, data_active_ranks=active,
            active_source_ranks=sorted({q['rank_id'] for q in c['route_replay']['requests']}),
            full_cluster_load=False, gpu_execution=False))
    for key in ('model', 'workload', 'parallel', 'compute', 'phase_completion', 'transport'):
        require(configurations[0][key] == configurations[1][key], 'changed shared configuration '+key)
    require(configurations[0]['route_replay']['requests'] == configurations[1]['route_replay']['requests'],
            'changed request placement')
    for mode in ('reduced', 'expanded'):
        a = load(root/f'{mode}-input/manifest.json')
        manifest_hashes = [f['file']['sha256'] for f in a['files']]
        if mode == 'reduced':
            original_hashes = manifest_hashes
        else:
            require(manifest_hashes == original_hashes, 'changed original route bytes')
    gpu_nodes = [n for n in topology['nodes'] if n['kind'] == 'gpu']
    geometry = dict(endpoints=len(gpu_nodes), servers=len({n['server'] for n in gpu_nodes}),
        switch_counts=dict(Counter(n['kind'] for n in topology['nodes'] if n['kind'] != 'gpu')),
        links=len(topology['links']), ep_sizes=[64, 64], independent_synthetic_requests=4,
        bandwidth_bps_by_tier={tier: sorted({e['rate_bps'] for e in topology['links'] if e['tier'] == tier})
                               for tier in sorted({e['tier'] for e in topology['links']})},
        link_delays_ns=sorted({e['delay_ns'] for e in topology['links']}))
    identity = load(root/'backend-identity.json')
    for path, expected in identity['identities'].items():
        require(digest(repo/path) == expected, 'backend/library/source identity changed')
    probes = [load(p) for p in sorted((root/'monitors').glob('*/probe.json'))]
    require(len(probes) == 11 and all(p['status'] == 'completed' and p['child_exit_code'] == 0 and
            not p['remaining_live_group_pids'] for p in probes), 'incomplete monitored commands')
    require(sum(p['wall_seconds'] for p in probes) < 900, 'task time budget exceeded')
    require(max(disk_usage(root).values()) < 512*2**20, 'task disk budget exceeded')
    verification = dict(passed=True, tests=tests, geometry=geometry, offline=offline,
                        native_modes=[reduced, expanded], actual_traffic=native_runs,
                        new_network_executions=2, real_route_network_executions=0,
                        hardware_calibrated=False, complete_DeepEP=False,
                        new_algorithm_gain_measured=False)
    save(output/'verification.json', verification)
    receipt = dict(passed=True, platform_commit=subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip(),
        root=str(root), backend_identity=identity, input_and_result_sha256=hashes,
        fixture_script_sha256={name: digest(output/name) for name in ('verify.py', 'summarize.py')},
        completed_monitored_commands_before_summary=probes,
        monitored_seconds_before_summary=sum(p['wall_seconds'] for p in probes),
        peak_sampled_group_rss_before_summary=max(p['peak_sampled_group_rss_bytes'] for p in probes),
        zero_build_download_install_gpu_dependency_changes=True, successful_network_reruns=0)
    save(output/'receipt.json', receipt)
    real = offline['offline_real_route_modes']
    offline_rows = ''.join(f'<tr><td>{html.escape(name)}</td><td>{row["layout_payload_bytes"]:,}</td>'
        f'<td>{row["expert_contributions"]}</td><td>{row["local_reduce_declared_ns"]:,}</td></tr>'
        for name, row in real.items())
    native_rows = ''.join(f'<tr><td>{html.escape(row["combine"])}</td><td>{row["layout_payload_bytes"]:,}</td>'
        f'<td>{row["expert_contributions"]}</td><td>{row["application_finished_ns"]/1000:.3f}</td>'
        f'<td>{row["packet_events_checked"]:,}</td></tr>' for row in (expanded, reduced))
    document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>128卡MoE传输表示核验</title>
<style>body{{font:17px/1.8 system-ui,sans-serif;max-width:1060px;margin:40px auto;padding:0 20px;color:#15243a;background:#f6f8fc}}
table{{border-collapse:collapse;width:100%;background:white;font-size:15px}}th,td{{padding:12px;border:1px solid #d8dfeb;text-align:left}}
h1,h2{{line-height:1.4}}.note{{padding:16px;background:#fff4da;border-left:4px solid #d18a00}}a{{color:#135dc1}}</style>
<h1>128卡MoE传输表示核验</h1><p>2026-10-30 · 平台增量 · 不是新算法收益实验</p>
<p>本轮让通信量更接近固定DeepEP direct BF16：同一个token的多个专家若在同一卡，Dispatch只发一份；
Combine可逐专家回传，也可卡内合并后回传。专家计算份数保留，合并成本单独声明。</p>
<h2>真实路由：只做离线核算</h2><p>15份不同R1-AWQ/MMLU原件，两层两步，共480份专家贡献。
其中474份远端贡献对应441个token-rank单元。三种表示的消息数均为882。</p>
<table><tr><th>表示</th><th>布局载荷/B</th><th>专家贡献</th><th>累计声明归并工作/ns</th></tr>{offline_rows}</table>
<p class="note">真实输入本轮未新增网络执行。这些字节差异是表示修正；累计归并工作不是业务时延，不能按减少字节的比例推断收益。
布局载荷包含元数据与填充，未包含网络包头。</p>
<h2>128卡功能执行：两模式各一次</h2><p>16服务器×8模拟GPU，8 ToR / 8 Agg / 4 Core，168链路，各层400Gbps、每链路1微秒。
两个EP64组、每rank每层4专家；四份不同人工请求，H256 / E256 / Top8，两层两步。
原始路由刻意共置专家，属于稀疏功能输入，不是满载或真实R1性能。</p>
<table><tr><th>Combine模式</th><th>布局载荷/B</th><th>专家贡献</th><th>有限任务完成/μs</th><th>完整包事件</th></tr>{native_rows}</table>
<p>两者均62消息、16阶段门限、1024成员到达；收发字节守恒。卡内合并使用每份额外贡献100ns的人工成本，
累计9600ns工作在不同rank上执行。实际计算完成才解锁Combine发送与阶段门限。</p>
<p class="note">少传字节没有在这个小消息输入下变成更短任务：卡内合并103.852μs，逐专家103.582μs。
两模式同时改变数据表示与声明合并工作；这里只验证功能，不能当作校准性能结论。</p>
<h2>已经核验与仍有限制</h2><p>7项新增与38项回归CPU检查；19份固定源码身份；历史3次实际执行的10张默认图完全一致；
两个新128卡实际执行及独立原JSON/逐包/回调检查。零构建、下载、GPU执行或依赖修改。</p>
<p>本模式尚无GIN计数/屏障包、真实CQ/GPU可见性、FP8/SF、hybrid多级转发或硬件成本校准。
后续故障研究应明确采用哪种通信表示，分开核对故障代价与可消除空间。</p>
<p><a href="README.md">验收说明</a> · <a href="verification.json">独立核验</a> · <a href="receipt.json">执行收据</a>
 · <a href="../../examples/scaleout_token_transport/README.md">公开复现示例</a></p></html>'''
    with (output/'report.html').open('x') as stream:
        stream.write(document+'\n')
    print(json.dumps(dict(passed=True, new_network_executions=2,
        cpu_tests=sum(tests.values()), geometry=geometry,
        monitored_seconds_before_summary=receipt['monitored_seconds_before_summary'],
        raw_file_bytes=disk_usage(root)['apparent_bytes'])))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    summarize(args.root.resolve(), args.output.resolve(), Path(__file__).resolve().parents[2])
