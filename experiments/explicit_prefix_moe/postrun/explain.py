"""Add explicitly post-result mechanism findings to the generated delivery."""
import argparse
import html
import json
from pathlib import Path
import shutil


def load(p):
    return json.loads(Path(p).read_bytes())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', type=Path, required=True)
    p.add_argument('--delivery', type=Path, required=True)
    a = p.parse_args()
    diagnosis = load(a.stage/'path_diagnosis.json')
    assert diagnosis['passed'] and diagnosis['post_result_diagnosis']
    root = Path(__file__).resolve().parents[3]
    import hashlib
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    assert diagnosis['source_sha256'] == digest(Path(__file__).with_name('diagnose_paths.py'))
    for path, h in diagnosis['timer_sources_sha256'].items():
        assert digest(root/path) == h
    analysis = load(a.stage/'analysis.json')
    shutil.copyfile(a.stage/'path_diagnosis.json', a.delivery/'path_diagnosis.json')
    c = diagnosis['cases']
    assert c['local-w20']['timer']['retries'][0]['time_ns'] == 18030
    assert c['prefix-w20']['timer']['progress'][0]['time_ns'] == 12124
    assert c['prefix-w20']['timer']['retries'][0]['time_ns'] == 29124
    assert c['local-w20']['messages']['4000']['message']['ack_ns'] == 26868
    assert c['prefix-w20']['messages']['4000']['message']['ack_ns'] == 37568
    short, long = diagnosis['short_final_same_message'], diagnosis['long_final_same_message']
    # All values here are verified equalities of retained execution events.
    assert short['ready_delta_ns'] == 4560 and short['components_delta_ns']['last_DATA_switch_residence_ns'] == 318
    assert long['ready_delta_ns'] == 9443 and long['components_delta_ns']['last_DATA_switch_residence_ns'] == 2081
    assert long['instant_gate_slack_ns'] == 589
    text = """# 进一步核查：为什么少重传仍会更慢

这是取得四个新网络结果后的诊断，不是事前选定的收益假设；没有改输入或新增网络。原始逐包见证与源码hash见[path_diagnosis.json](path_diagnosis.json)。

## 短故障的退化包含计时器行为

同一消息4000（5→9）的第二段DATA在各条件中都有实际丢弃记录。local-w20没有及时收到第一段的ACK，18.030µs按原期限重试两段，26.868µs全部确认。

prefix-w20保住第一段ACK，12.124µs确认前9,000B。当前后端的发送端计时器会在确认前缀推进后，把剩余数据的超时期限设为“当前时刻+17µs”，因此原18.030µs期限变为29.124µs。它只重试缺失的5,464B，却直到37.568µs才确认全部数据。这个样本中，较早收到部分确认会推迟另一段丢失数据的重试。

不能把这10.700µs全部算成任务退化：旧local首Dispatch还要等其他消息至35.384µs，已有8.516µs余量，所以该集合门限实际只晚2.184µs。最终组1晚4.878µs；与旧全局最晚组2相比，最终最大值只晚1.322µs。

再核算同一最终消息23406：它的ready晚4.560µs，末DATA在交换机中多停留0.318µs，其他分项相同，恰好解释组1的4.878µs差。不能把整段差都写成新绕路包的排队。

## 长故障剩余差距怎样组成

prefix-w60与instant-w60的首Dispatch末前驱都是消息4000。第一段ACK在prefix中晚2.003µs到，实际超时重试也晚2.003µs；补发末DATA的路径耗时相同，最终ACK路径多2.004µs，因此首Dispatch恰晚4.007µs。instant是理想全局改表参考，没有部署检测成本，不能当成可实现最优值。

最终组1的末前驱在prefix中变为消息23531（93→9）。对比instant中的同一消息：ready晚9.443µs，末DATA交换机驻留多2.081µs，其他消息内分项相同；再扣instant门限已有的0.589µs余量，得到组1最终晚10.935µs。全局最晚差8.645µs涉及不同最终组，不能直接混用。

末DATA驻留从0.377增至2.458µs，prefix中最大的等待在节点142端口5，为1.812µs；逐包发送记录表明这些等待区间都由DATA实际占线覆盖，没有未解释空白。这里只证明所选末包的实际等待及占线，不证明都是重传造成，也不证明调整该队列就能消除等量任务时延。

## 下一步真正需要验证的方向

1. 先核对目标RoCE设备如何对部分ACK和缺口计时：当前是项目内QP级计时器，不能把模型行为直接当成所有网卡的缺陷。任何按段计时或已有快速丢包检测都应作为成熟对照，不能直接命名为创新。
2. 在较强恢复基线上，研究能否利用MoE集合完成所缺的贡献，安排有限的恢复发送机会，避免把资源优先给不影响门限的重传。重点验证任务提前和其他组退化，同时限制额外字节；目前尚未实现或验证此策略。
3. 不能承诺收回prefix距normal的全部21.855µs。它混合真实故障损失、计时器、替代路径和后续共享竞争；需要逐项可干预对照。
"""
    (a.delivery/'diagnosis_explanation.md').write_text(text)
    for file in ('README.md', 'report.html'):
        target = a.delivery/file
        body = target.read_text()
        e = analysis['effects']['20']
        old = f"减少{e['saved_ns']/1000:.3f}µs（{e['relative_local_percent']:.2f}%），收回原故障额外等待的{e['fault_penalty_recovered_percent']:.2f}%。"
        new = f"反而延后{-e['saved_ns']/1000:.3f}µs（{-e['relative_local_percent']:.2f}%）；原故障额外等待增加{-e['fault_penalty_recovered_percent']:.2f}%。"
        assert old in body
        body = body.replace(old, new)
        if file.endswith('.html'):
            addition = '<h2>短故障为何反而变慢：逐包核查</h2><p><a href="diagnosis_explanation.md">进一步解释</a> · <a href="path_diagnosis.json">逐包与计时器证据</a></p><pre>'+html.escape(text)+'</pre>'
            body = body.replace('</html>', addition+'</html>')
        else:
            body += '\n\n进一步机制核查见[短故障退化与剩余等待](diagnosis_explanation.md)。\n'
        target.write_text(body)
    with (a.delivery/'postrun_sources.json').open('x') as f:
        json.dump(dict(passed=True, post_result_diagnosis=True, no_new_networks=True,
            sources_sha256={str(p.relative_to(root)): digest(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
            diagnosis_sha256=digest(a.delivery/'path_diagnosis.json')), f, indent=2)
        f.write('\n')
    print(json.dumps(dict(passed=True, post_result_diagnosis=True, network_runs=0)))


if __name__ == '__main__':
    main()
