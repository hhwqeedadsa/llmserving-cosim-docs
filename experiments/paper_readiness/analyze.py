"""Independently evaluate sensitivity; do not assign publication scores."""
import argparse
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    source, out = args.input.resolve(), args.output.resolve()
    out.mkdir(exist_ok=False)
    exp = read(source / 'experiment.json')
    root = Path(__file__).resolve().parents[2]
    for name, checksum in exp['source_hashes'].items():
        assert sha(root / name) == checksum
    assert len(exp['cases']) == 16
    cases, raw, graphs, paths = {}, {}, {}, {}
    for name, case in exp['cases'].items():
        run = source / case['run']
        result = read(run / 'result.json')
        frontend = read(run / 'frontend.json')
        diagnosis = read(run.parent / 'diagnosis.json')
        protocol = read(run / 'rdma.json')
        batch, mode, fault = name.split('-')
        batch = int(batch[1:])
        assert result['passed'] and diagnosis['passed']
        assert protocol['admission'] == exp['modes'][mode]
        assert 'history' not in protocol['recovery'] and 'per_destination' not in protocol['admission']
        assert len(frontend['history']) == 4 and all(h['finished_ns'] is not None for h in frontend['history'])
        assert diagnosis['admission_resources']['reconstructed']
        finish = max(h['finished_ns'] for h in frontend['history'])
        m = case['metrics']
        assert finish == m['frontend_complete_ns'] and result['finish_ns'] == m['network_finish_or_stop_ns']
        assert sha(run / 'result.json') == m['result_sha256']
        assert sum(f['source_data_payload_bytes'] for f in result['messages']) == m['source_payload_bytes']
        assert sum(s['timeout_retransmissions'] for s in result['recovery']['states']) == (0 if fault == 'none' else 1)
        for q in (run / 'graphs').glob('*/wave.json'):
            key = batch, q.parent.name
            assert graphs.setdefault(key, sha(q)) == sha(q)
        target = next(f for f in result['messages'] if f['id'] == exp['targets'][str(batch)]['id'])
        held = target['ack_ns'] - target['receive_ready_ns']
        waiting = [f['admission_granted_ns'] - f['ready_ns'] for f in result['messages']]
        assert min(waiting) >= 0
        cases[name] = dict(batch=batch, mode=mode, fault=fault, frontend_complete_ns=finish,
                           network_finish_ns=result['finish_ns'], source_payload_bytes=m['source_payload_bytes'],
                           cumulative_admission_wait_ns=sum(waiting), max_admission_wait_ns=max(waiting),
                           target=target, target_data_ready_to_ack_ns=held,
                           waves=frontend['history'], result_sha256=sha(run / 'result.json'))
        raw[name] = result
        paths[name] = {f['id']: [(h['node'], h['port']) for h in f['first_packet_path']] for f in result['messages']}
    equivalence = {}
    path_changes = {}
    for batch in (1, 4):
        for fault in ('none', 'single'):
            for a, b in [('small', 'slots_only'), ('nonbinding', 'passthrough')]:
                left, right = f'b{batch}-{a}-{fault}', f'b{batch}-{b}-{fault}'
                # Policy/config snapshots differ; actual execution evidence must agree.
                keys = ('messages', 'ports', 'service', 'queue_events', 'recovery')
                equivalent = {key: raw[left][key] == raw[right][key] for key in keys}
                changed = [mid for mid in {f['id'] for f in raw[left]['messages']}
                           if next(f for f in raw[left]['messages'] if f['id'] == mid) != next(f for f in raw[right]['messages'] if f['id'] == mid)]
                equivalence[left + ':' + right] = dict(fields=equivalent, changed_message_ids=sorted(changed),
                    frontend_completion_equal=cases[left]['frontend_complete_ns'] == cases[right]['frontend_complete_ns'])
            reference = paths[f'b{batch}-small-{fault}']
            for mode in exp['modes']:
                name = f'b{batch}-{mode}-{fault}'
                path_changes[name] = [mid for mid, path in paths[name].items() if path != reference[mid]]
    penalties = {}
    for batch in (1, 4):
        penalties[str(batch)] = {}
        for mode in exp['modes']:
            normal, fault = cases[f'b{batch}-{mode}-none'], cases[f'b{batch}-{mode}-single']
            penalties[str(batch)][mode] = dict(fault_penalty_ns=fault['frontend_complete_ns'] - normal['frontend_complete_ns'],
                target_ack_penalty_ns=fault['target']['ack_ns'] - normal['target']['ack_ns'])
    summary = dict(passed=True, platform_commit=exp['platform_commit'], cases=cases,
                   execution_equivalence=equivalence, path_changes=path_changes, fault_penalties=penalties,
                   common_graphs_identical_within_batch=True, analysis_sha256=sha(Path(__file__)),
                   conclusion='Removing byte cap preserves aggregate completion but changes some batch4 message ordering; nonbinding software credits remove admission wait; ACK fault still delays execution.',
                   limitations=['synthetic finite decode', 'no calibrated NIC/QP/GPU buffer capacity',
                                'no new algorithm or hardware speedup', 'cumulative waits overlap and are not request loss',
                                'no real service p99 or inferential statistics'])
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    md = ['# 源额度强基线：16条件判别结果', '',
          '目的：检查先前的资源传播解释是否依赖外加软件约束，不为某个算法寻找有利配置。固定8GPU、合成MoE层段、正常40μs初值，无恢复历史或目的端份额。', '',
          '| batch | 模式 | 故障 | 业务完成 ns | 累计准入等待 ns | 最大单消息等待 ns |',
          '|---|---|---|---:|---:|---:|']
    for name, c in sorted(cases.items()):
        md.append('| ' + ' | '.join(str(c[k]) for k in ('batch', 'mode', 'fault', 'frontend_complete_ns',
                   'cumulative_admission_wait_ns', 'max_admission_wait_ns')) + ' |')
    md += ['', 'small=3槽/16384B；slots_only=3槽/无字节上限；nonbinding=64槽/无字节上限；passthrough=软件透传。累计等待可重叠，不能加总为业务损失；无限制指额外软件准入，底层网络仍有链路、网卡及协议行为。', '',
           '## 对研究主张的影响', '',
           '1. small与slots_only在两个batch、两种故障下，整体完成时间、总payload及累计准入等待相同。batch1执行证据相同；batch4有5条消息记录变化，其中40/41交换准入次序。字节额度会改变局部时序，但本矩阵中未改变整体完成；不能把聚合相等误写成执行相等，也不能把字节与槽的作用混为一谈。', '',
           '2. nonbinding与passthrough的上述执行证据也一致，累计软件准入等待均为0。small下无故障业务为53093/63545ns，透传为43611/54362ns，分别低17.86%/14.45%。这说明外加约束影响模型结果，不是算法优化或真实硬件加速。', '',
           '3. 单最终ACK丢失仍使透传业务延迟：batch1增加41477ns，batch4增加38871ns。不能把整个故障影响归给源额度；当前前端图完成还等待源SEND确认，后续波次受其影响，其对应真实通信库的边界需要校准。', '',
           '4. 故障惩罚在透传条件比small更大（batch1：37013→41477ns；batch4：34561→38871ns），所以不能从无故障更快或累计等待消失，推出确认丢失的边际代价一定减少。应分别报告绝对完成时间和相对各自基线的故障惩罚。', '',
           '5. 下一步首先校验真实库的QP复用、异步完成、缓冲复用和轮次边界，取得代表性正常工作量及强基线证据；不把3槽设置、故障注入或简单缓存本身写成A类论文的新机制。', '',
           '共同已提交任务图在相同batch内逐字节一致，首包路径差异单独列入summary；本轮没有用未来真值驱动策略。这里只支持16个有限合成条件的机制结论，不证明生产普遍性。', '']
    (out / 'results.md').write_text('\n'.join(md))
    print(json.dumps(dict(passed=True, cases=16, equivalence_pairs=len(equivalence),
                          path_changed_cases=sum(bool(v) for v in path_changes.values()))))


if __name__ == '__main__':
    main()
