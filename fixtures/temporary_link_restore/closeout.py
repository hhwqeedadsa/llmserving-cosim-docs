"""Save portable acceptance summaries and a readable static result page."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[2]


def load(p):
    return json.loads(p.read_bytes())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, v):
    with p.open('x') as s:
        json.dump(v, s, ensure_ascii=False, indent=2)
        s.write('\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', type=Path, required=True)
    a = p.parse_args()
    out = Path(__file__).resolve().parent
    stage = a.stage.resolve()
    verification = load(stage/'verification-serialization.json')
    tamper = load(stage/'tamper-verification.json')
    native = load(stage/'native-rejects/verification.json')
    assert verification['passed'] and tamper['passed'] and native['passed']
    assert len(native['cases']) == 16 and len(tamper['mutations']) == 6
    prov = load(stage/'build/provenance.json')
    for name, h in prov['sources'].items():
        assert sha(ROOT/name) == h
    probes = [load(x) for x in sorted((stage/'monitors').glob('*/probe.json'))]
    assert all(x['status'] == 'completed' and x['child_exit_code'] == 0 for x in probes)
    seconds = sum(x['wall_seconds'] for x in probes)
    assert seconds < 1800 and max(x['peak_sampled_artifact_bytes'] for x in probes) < 2*2**30
    assert shutil.disk_usage(stage).free > 8*2**30
    unit_count = sum(int(re.search(r'Ran (\d+) tests', (stage/'monitors'/name/'stderr.log').read_text())[1])
                     for name in ('python-inputs', 'affected-unit-checks'))
    assert unit_count == 34
    new_backend = load(stage/'build/backend.json')
    assert len(new_backend['linked_ns3_libraries']) == 8
    for lib in new_backend['linked_ns3_libraries']:
        assert sha(Path(lib)) == new_backend['library_hashes'][Path(lib).name]
    vout = dict(verification, tamper_checks=tamper['mutations'], native_invalid_cases=16,
                unit_checks=unit_count, source_raw_verification_sha256=sha(stage/'verification-serialization.json'))
    save(out/'verification.json', vout)
    network = {path.parent.name: sha(path) for path in (stage/'network').glob('*/result.json')}
    receipt = dict(passed=True, public_full_build=True, native_commit=prov['native_commit'],
        compiled_sources_sha256=prov['sources'], native_binary_sha256=load(stage/'frozen.json')['native_binaries_sha256'],
        library_hashes=new_backend['library_hashes'], native_networks=6, raw_results_sha256=network,
        monitored_seconds=seconds, monitored_stages=len(probes),
        peak_group_rss_bytes=max(x['peak_sampled_group_rss_bytes'] for x in probes),
        peak_task_artifact_bytes=max(x['peak_sampled_artifact_bytes'] for x in probes),
        free_disk_bytes=shutil.disk_usage(stage).free, budget=dict(monitored_seconds=1800, artifact_bytes=2*2**30,
        min_free_bytes=8*2**30), failed_monitored_stages=0, source_inputs_sha256=load(stage/'frozen.json')['input_sha256'],
        source_sha256={str(x.relative_to(ROOT)): sha(x) for x in [
            *sorted(out.glob('*.py')), ROOT/'examples/scaleout_link_restore/prepare.py',
            ROOT/'python/moe_ub_cosim/scaleout_link_failures.py', ROOT/'tests/unit/scaleout_link_failures_test.py']},
        real_route_workload=False, GPU_execution=False, installs=0, downloads=0, dependency_changes=0,
        boundaries=['arrival-state link model; no mid-frame bit/CRC failure', 'declared centralized notification delays',
                    'Clos128 functional bytes, no full-model recovery performance or modern RoCE calibration'])
    save(out/'receipt.json', receipt)
    rows = []
    for name, case in verification['cases'].items():
        rows.append(f"<tr><td>{name}</td><td>{case['physical']['tx']:,}</td><td>{case.get('physical',{}).get('physical_drop',0)}</td><td>{case.get('route_epoch','—')}</td><td>{case.get('last_ack_ns','—')}</td><td>{case.get('final_ns','—')}</td></tr>")
    text = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>临时断链恢复功能验收</title>
<style>body{{font:16px/1.7 system-ui,sans-serif;max-width:1000px;margin:32px auto;padding:0 24px;background:#f5f8fc;color:#153047}}table{{border-collapse:collapse;width:100%;background:white}}td,th{{padding:9px;border-bottom:1px solid #dce4ef;text-align:right}}td:first-child,th:first-child{{text-align:left}}.box{{background:#e3eefb;padding:18px;border-left:5px solid #3279c4}}a{{color:#2465a7}}</style>
<h1>短时断链后，恢复原链路和原连接</h1>
<p>128端点Clos · 20交换机 · 4条人工持久连接 · 选择性重传始终启用 · 没有GPU执行或真实MoE性能结论</p>
<div class="box">已实现物理关闭/上线、独立的路由通知、迟到通知版本保护。六个功能网络通过；正常条件的新旧原始结果除库路径外一致。</div>
<h2>关键情况：恢复先到，旧故障通知晚到</h2>
<table><tr><th>时间</th><th>物理端口</th><th>控制器动作</th></tr><tr><td>4µs</td><td>关闭</td><td>尚未收到断链通知</td></tr><tr><td>24µs</td><td>恢复上线</td><td>收到版本2恢复通知</td></tr><tr><td>110.791µs</td><td>保持上线</td><td>所有工作已实际ACK</td></tr><tr><td>300µs</td><td>保持上线</td><td>忽略迟到的版本1断链通知；处理完未来事件才结束</td></tr></table>
<p>另一条件在10µs物理恢复、20µs才收到断链通知、30µs收到恢复通知；控制面在20–30µs仍应排除该边，不能偷读物理状态。实际包路径已按当时获知的路由独立核对。</p>
<h2>原始验收记录</h2><table><tr><th>条件</th><th>物理发送</th><th>物理丢弃</th><th>路由代次</th><th>最后ACK ns</th><th>网络结束 ns</th></tr>{''.join(rows)}</table>
<p>合计18,556次物理发送逐跳匹配接收/丢弃；9,785次DATA/ACK/NACK实际入队核对独立最短路和原生哈希，事件同ns边界单独保留歧义；源字节、ACK/SACK、队列积分和终态MMU全部核对。34项单位检查、16类非法输入、6类篡改检查通过。</p>
<h2>这次增加的价值</h2><p>此前只能断开链路，现在可以复现短时故障后的恢复过程，并避免迟到通知让控制状态回退。后续真实MoE实验可以分别研究故障持续时间、恢复通知延迟与任务阶段等待。本轮是系统功能建设，不把链路上线或选择性重传称为创新协议。</p>
<p>边界：1ns窗口检查原发送状态未被重置；接收仍按到达瞬间端口状态判断，不模拟发送途中比特中断/CRC或光模块训练。没有真实分布式控制报文、硬件校准、生产收益或完整模型恢复实验。</p>
<p><a href="README.md">验收解释与复算</a> · <a href="../../examples/scaleout_link_restore/README.md">公开运行示例</a> · <a href="verification.json">核验摘要</a> · <a href="receipt.json">版本与资源</a></p></html>"""
    with (out/'report.html').open('x') as s:
        s.write(text+'\n')
    print(json.dumps(dict(passed=True, verification=str(out/'verification.json'), monitored_seconds=seconds,
                         native_networks=6, unit_checks=unit_count)))


if __name__ == '__main__':
    main()
