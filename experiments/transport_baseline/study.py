"""Frozen four-case study using public preparation, execution and diagnosis."""
import argparse
from copy import deepcopy
import hashlib
import html
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CASES = ('base-normal', 'base-local', 'short-normal', 'short-local')


def load(path):
    return json.loads(Path(path).read_bytes())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def files(path):
    return {str(p.relative_to(path)): sha(p) for p in sorted(path.rglob('*')) if p.is_file()}


def public(command, receipt):
    save(receipt, dict(argv=command, cwd=str(ROOT), status='started'))
    with receipt.with_suffix('.stdout.log').open('x') as out, receipt.with_suffix('.stderr.log').open('x') as err:
        result = subprocess.run(command, cwd=ROOT, stdout=out, stderr=err)
    record = load(receipt)
    record.update(exit_code=result.returncode, status='completed' if result.returncode == 0 else 'failed')
    receipt.write_text(json.dumps(record, indent=2) + '\n')
    if result.returncode:
        raise SystemExit(f'Public command failed ({result.returncode}); retained: {receipt}')


def cli(*args):
    return [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', *map(str, args)]


def prepare(a):
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    build = a.build.resolve()
    provenance = load(build / 'provenance.json')
    for path, digest in provenance['sources'].items():
        assert sha(ROOT / path) == digest, ('compiled source drift', path)
    backend = build / 'output/moe-rdma/ns3.42-moe-rdma-service'
    libraries = sorted((build / 'output/lib').glob('libns3*.so'))
    assert len(libraries) == 8
    prepared = out / 'input'
    public([sys.executable, '-B', str(ROOT / 'examples/scaleout_message_buffers/prepare.py'),
            '--source-manifest', str(a.source_manifest.resolve()), '--source-base', str(a.source_base.resolve()),
            '--output', str(prepared)], out / 'prepare-command.json')
    c = load(prepared / 'service-buffers.json')
    c['inflight_helpers']['policy'] = 'observe_only/1'
    assert c['transport']['transport_recovery']['initial_rto_ns'] == 80000
    assert list(map(len, c['parallel']['groups'])) == [32] * 4
    save(prepared / 'service-base.json', c)
    p = load(prepared / 'rdma-buffers.json')
    assert p['data_loss'] == dict(kind='periodic_destination_data/1', numerator=1, denominator=1,
                                 offset=0, start_ns=0, end_ns=20000, nodes=[32])
    normal = deepcopy(p)
    del normal['data_loss']
    save(prepared / 'rdma-normal.json', normal)
    # Pin implementation, dependencies, source lineage and every prepared file.
    sources = [*sorted((ROOT / 'python/moe_ub_cosim').glob('*.py')),
               *sorted((ROOT / 'examples').glob('*/prepare.py')), *sorted(Path(__file__).parent.glob('*.py')),
               Path(__file__).with_name('README.md')]
    manifest = load(prepared / 'buffer-source-manifest.json')
    route_hashes = {f['file']['path']: f['file']['sha256'] for f in manifest['files']}
    assert len(route_hashes) == 4 and len(set(route_hashes.values())) == 4
    assert all(sha(p) == h for p, h in route_hashes.items())
    frozen = dict(schema_version='moe-transport-baseline/1', network_executed=False,
                  platform_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  prepared=str(prepared), prepared_sha256=files(prepared),
                  backend=str(backend), backend_sha256=sha(backend),
                  libraries_sha256={str(p): sha(p) for p in libraries},
                  build_provenance_sha256=sha(build / 'provenance.json'),
                  compiled_sources_sha256=provenance['sources'],
                  source_sha256={str(p.relative_to(ROOT)): sha(p) for p in sources},
                  routes_sha256=route_hashes, cases=list(CASES), fault=p['data_loss'],
                  limits=dict(new_networks=4, monitored_seconds=1200, artifact_bytes=2**30, min_free_bytes=8*2**30))
    save(out / 'frozen.json', frozen)
    print(json.dumps(dict(prepared=True, network_executed=False, routes=4, endpoints=128, cases=list(CASES))))


def checked(study):
    f = load(study / 'frozen.json')
    assert sha(f['backend']) == f['backend_sha256']
    for group in ('source_sha256', 'compiled_sources_sha256'):
        for path, digest in f[group].items():
            assert sha(ROOT / path) == digest, path
    for group in ('libraries_sha256', 'routes_sha256'):
        for path, digest in f[group].items():
            assert sha(path) == digest, path
    current = files(Path(f['prepared']))
    # Calibrated configuration is the only later prepared file.
    current.pop('service-short.json', None)
    assert current == f['prepared_sha256']
    return f


def run(a):
    study = a.study.resolve()
    f = checked(study)
    if a.case != 'base-normal':
        calibration = load(study / 'calibration.json')
        assert sha(study / 'base-normal/result.json') == calibration['normal_result_sha256']
        assert sha(Path(f['prepared']) / 'service-short.json') == calibration['short_config_sha256']
    p = Path(f['prepared'])
    output = study / a.case
    assert not output.exists(), 'each case runs once; never overwrite'
    cmd = cli('rdma-run', '--config', p / ('service-short.json' if a.case.startswith('short-') else 'service-base.json'),
              '--protocol', p / ('rdma-normal.json' if a.case.endswith('-normal') else 'rdma-buffers.json'),
              '--topology', p / 'topology', '--backend', f['backend'], '--protoc', a.protoc.resolve(), '--output', output)
    public(cmd, study / (a.case + '-command.json'))
    print(json.dumps(dict(case=a.case, completed=True, result_sha256=sha(output / 'result.json'))))


def calibrate(a):
    study = a.study.resolve()
    f = checked(study)
    assert not any((study / name).exists() for name in CASES[1:]), 'calibrate before the other networks'
    directory = study / 'base-normal'
    public(cli('rdma-check', '--run', directory), study / 'calibration-check.json')
    r = load(directory / 'result.json')
    messages = r['service_transport']['messages']
    assert r['passed'] and r['drained'] and r['fault_drops'] == 0
    assert not r['service']['inflight_helpers']['attempts']
    intervals = {str(m['id']): dict(ready_ns=m['ready_ns'], ack_ns=m['ack_ns']) for m in messages}
    longest = max(m['ack_ns'] - m['ready_ns'] for m in messages)
    timeout = (longest // 1000 + 1) * 1000
    p = Path(f['prepared'])
    c = load(p / 'service-base.json')
    c['transport']['transport_recovery']['initial_rto_ns'] = timeout
    assert 0 < timeout <= c['transport']['transport_recovery']['max_rto_ns']
    save(p / 'service-short.json', c)
    save(study / 'calibration.json', dict(rule='next_strict_1000ns_above_all_normal_ready_to_ack_max/1',
         normal_result_sha256=sha(directory / 'result.json'), normal_max_ready_ack_ns=longest,
         initial_rto_ns=timeout, messages=intervals, short_config_sha256=sha(p / 'service-short.json'),
         independent_holdout=False, normal_samples=1, RTT_estimate=False, fault_used_for_calibration=False))
    print(json.dumps(dict(calibrated=True, messages=len(messages), longest_ns=longest, initial_rto_ns=timeout)))


def summarize(directory, diagnosis):
    r = load(directory / 'result.json')
    n = r['service_transport']['native']
    assert r['passed'] and r['drained'] and r['gpu_count'] == 128 and r['switch_count'] == 20
    assert n['closed'] and n['physical_drained'] and n['inflight_packets'] == 0
    assert not r['service']['inflight_helpers']['attempts']
    assert r['service_transport']['message_buffers']['used_bytes'] == n['message_payload']['retained_bytes'] == 0
    assert n['recovery']['complete'] and not n['recovery']['failed']
    assert r['packet_capture']['complete'] and r['packet_capture']['omitted_events'] == 0
    cols = n['source_packet_columns']
    packets = [dict(zip(cols, row)) for row in n['source_packets']]
    logical = sum(m['bytes'] for m in r['service_transport']['messages'])
    tx = sum(p['payload_bytes'] for p in packets)
    assert tx == sum(s['source_payload_bytes'] for s in n['streams']) >= logical
    histories = load(directory / 'frontend.json')['history']
    groups = {str(i): max(h['finished_ns'] for h in histories if h['group_index'] == i) for i in range(4)}
    gates = {g['graph_id'] + '.' + g['id']: g['released_ns'] for g in r['service']['phase_completion']['gates']}
    recovery = n['recovery']
    rc = recovery['columns']
    timeouts = [dict(zip(rc, row)) for row in recovery['events']
                if row[rc.index('event')] == recovery['event_codes']['timeout_retransmit']]
    drops = [dict(zip(r['packet_capture']['columns'], row)) for row in r['packet_capture']['events']
             if row[r['packet_capture']['columns'].index('event')] == r['packet_capture']['event_codes']['drop']]
    return dict(result_sha256=sha(directory / 'result.json'), group_complete_ns=groups,
                drain_ns=r['finish_ns'], logical_bytes=logical, source_tx_payload_bytes=tx,
                retransmitted_payload_bytes=tx-logical, fault_drops=r['fault_drops'], packet_drops=drops,
                initial_rto_ns=recovery['config']['initial_rto_ns'], timeout_events=timeouts,
                gates_ns=gates, message_count=len(r['service_transport']['messages']),
                buffer_peak_bytes=r['service_transport']['message_buffers']['peak_bytes'], final_buffers_bytes=0,
                diagnosis=load(diagnosis / 'summary.json'))


def analyze(a):
    study = a.study.resolve()
    f = checked(study)
    out = study / 'delivery'
    out.mkdir(exist_ok=False)
    results = {}
    baseline_waves = None
    for name in CASES:
        directory = study / name
        diagnosis = study / ('diagnosis-' + name)
        public(cli('contribution-frontier', '--run', directory, '--output', diagnosis), study / (name + '-diagnosis.json'))
        results[name] = summarize(directory, diagnosis)
        waves = {str(p.relative_to(directory / 'graphs')): sha(p) for p in (directory / 'graphs').rglob('*') if p.is_file()}
        if baseline_waves is None:
            baseline_waves = waves
        assert waves == baseline_waves, 'original generated graphs differ'
    normalized = []
    for name in CASES:
        c = load(study / name / 'input.json')
        c['transport']['transport_recovery']['initial_rto_ns'] = 80000
        normalized.append(c)
    assert all(c == normalized[0] for c in normalized)
    for policy in ('base', 'short'):
        normal = load(study / (policy + '-normal') / 'rdma.json')
        local = load(study / (policy + '-local') / 'rdma.json')
        assert local.pop('data_loss') == f['fault'] and local == normal
    effects = {}
    for condition in ('normal', 'local'):
        b, s = results['base-' + condition], results['short-' + condition]
        effects[condition] = dict(target_saved_ns=b['group_complete_ns']['0']-s['group_complete_ns']['0'],
            other_group_delay_ns={k:s['group_complete_ns'][k]-b['group_complete_ns'][k] for k in ('1', '2', '3')},
            drain_saved_ns=b['drain_ns']-s['drain_ns'], extra_tx_payload_bytes=s['source_tx_payload_bytes']-b['source_tx_payload_bytes'],
            first_dispatch_saved_ns=b['gates_ns']['g0.w0.L0.dispatch']-s['gates_ns']['g0.w0.L0.dispatch'])
    penalty = results['base-local']['group_complete_ns']['0']-results['base-normal']['group_complete_ns']['0']
    evidence = dict(schema_version='moe-transport-baseline-results/1', cases=results, effects=effects,
                    calibration=load(study / 'calibration.json'), base_fault_penalty_ns=penalty,
                    fault_penalty_recovered_fraction=effects['local']['target_saved_ns']/penalty if penalty>0 else None,
                    method_sha256=f['source_sha256']['experiments/transport_baseline/README.md'],
                    source_sha256=f['source_sha256'], frozen_sha256=sha(study/'frozen.json'),
                    same_original_graphs=True, same_config_except_initial_rto=True, new_networks=4, builds=0,
                    hardware_calibrated=False, complete_model=False)
    save(out / 'analysis.json', evidence)
    render(out, evidence)
    print(json.dumps(dict(completed=True, effects=effects, fault_penalty_ns=penalty)))


def render(out, evidence):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    results = evidence['cases']
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    for ax, condition in zip(axes, ('normal', 'local')):
        for offset, policy, color in ((-.18, 'base', '#9ba9b8'), (.18, 'short', '#2472a8')):
            row = results[policy+'-'+condition]
            ax.bar([x+offset for x in range(4)], [row['group_complete_ns'][str(x)]/1000 for x in range(4)],
                   width=.34, label=f'{policy}: RTO {row["initial_rto_ns"]/1000:g} us', color=color)
        ax.set_xticks(range(4), ['Target EP', 'Other EP1', 'Other EP2', 'Other EP3'])
        ax.set_title(condition);ax.set_ylabel('Two-step fragment completion (us)')
        ax.legend();ax.grid(axis='y', alpha=.2);ax.set_axisbelow(True)
    fig.suptitle('128-endpoint Clos: mature selective-repeat timeout baseline')
    fig.tight_layout()
    for ext in ('png', 'svg'):
        fig.savefig(out / ('completion.'+ext), dpi=160, bbox_inches='tight')
    plt.close(fig)
    rows = ''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in
             [name, r['initial_rto_ns']/1000, r['group_complete_ns']['0']/1000,
              max(r['group_complete_ns'][k] for k in ('1','2','3'))/1000,
              r['drain_ns']/1000, r['fault_drops'], len(r['timeout_events']),
              r['retransmitted_payload_bytes'], r['buffer_peak_bytes']])+'</tr>' for name,r in results.items())
    effect = evidence['effects']; short = evidence['calibration']['initial_rto_ns']/1000
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>原始通信恢复的收益与代价</title><style>body{font:16px/1.8 system-ui;max-width:1150px;margin:30px auto;padding:0 20px;background:#f4f7fb;color:#183149}section{background:white;padding:22px;margin:20px 0;border-radius:12px}h1{font-size:28px}table{border-collapse:collapse;display:block;overflow:auto;font-size:14px}td,th{padding:9px;border:1px solid #d9e3ed;white-space:nowrap;text-align:left}img{width:100%}a{color:#1261a2}</style>
<h1>先把成熟恢复基线做强，再判断创新空间</h1>
<p>128端点Clos，四组EP32，四份真实R1专家选择，两层两个Decode步。声明计算、标记字节，不是完整模型或硬件实测。四次网络各执行一次；全部保留原门限与源ACK，均未启动备用。</p>
<section><h2>本轮改变了什么？</h2><p>normal不额外注入故障；local只在前20微秒丢弃GPU32的DATA，ACK等控制包保留。先运行80微秒超时的正常条件，用全部消息从ready到ACK的最长跨度的下一个整微秒，固定较短超时为'''+str(short)+'''微秒。这个值包含排队，不是RTT测量；仅一个正常样本，无独立验证集。</p></section>
<section><h2>实际结果</h2><img src="completion.png" alt="四组EP在正常和局部故障下的完成时间对比">
<table><tr><th>条件</th><th>初始RTO µs</th><th>目标完成µs</th><th>其他组最晚µs</th><th>排空µs</th><th>故障丢包</th><th>超时次数</th><th>重传载荷B</th><th>缓冲峰值B</th></tr>'''+rows+'''</table>
<p>目标故障条件完成提前'''+str(effect['local']['target_saved_ns']/1000)+'''微秒，首Dispatch门限提前'''+str(effect['local']['first_dispatch_saved_ns']/1000)+'''微秒；正常目标提前'''+str(effect['normal']['target_saved_ns']/1000)+'''微秒（负值表示变慢）。故障条件额外发送'''+str(effect['local']['extra_tx_payload_bytes'])+'''字节，正常额外发送'''+str(effect['normal']['extra_tx_payload_bytes'])+'''字节。各其他组变化和实际丢包时刻在派生证据中逐项保留。</p></section>
<section><h2>这说明什么？</h2><p>这项对照检验直接推进原始通信确认能收回多少等待，属于成熟重传基线，不是新算法。收益须归给实际推进的原门限；不能把单份备用提前量、阶段提前量与任务提前量相加。若较短RTO已消除主要等待，就不能继续用80微秒基线宣称同等规模的创新收益。</p>
<p>四份请求是稀疏负载。正常样本校准不保证新负载下没有误重传；局部短时丢包也不覆盖链路断开、持续拥塞或ACK丢失。原正常/故障差距是本场景参考，不是理论可优化上限。完整推理、生产吞吐、经济收益尚未验证。</p></section>
<section><h2>证据</h2><p>四份原图完全相同，配置仅初始RTO随策略改变；各次公开核验、贡献诊断和最终缓冲排空通过。没有构建或修改网络后端。<a href="analysis.json">派生证据</a> · <a href="completion.svg">矢量图</a></p></section></html>'''
    (out/'report.html').write_text(page)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    q = sub.add_parser('prepare')
    for name in ('source-manifest', 'source-base', 'build', 'output'):
        q.add_argument('--'+name, type=Path, required=True)
    for action in ('run', 'calibrate', 'analyze'):
        q = sub.add_parser(action); q.add_argument('--study', type=Path, required=True)
        if action == 'run':
            q.add_argument('--case', choices=CASES, required=True)
            q.add_argument('--protoc', type=Path, required=True)
    a = p.parse_args()
    globals()[a.action](a)


if __name__ == '__main__':
    main()
