"""Prepare observed/truth inputs, plan prefixes, then evaluate future TX."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from experiments.static_dag_transfer.prepare import admit
from experiments.interaction_scope.busy_prefix import PrefixPlanner, identity, require

ARCHIVE_SHA = 'f83752dd954031d2f0016ddfc9ae57820ff946bce22a8594511fd32e2bd41a22'


def load(path):
    return json.loads(path.read_bytes())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def prepare(repo, stage):
    require(not stage.exists(), 'stage already exists')
    blobs = admit(repo/'runs/interaction_scope_20261027/checkpoint.tar.gz', ARCHIVE_SHA)
    stage.mkdir()
    hashes = {}
    for condition in ('baseline', 'bandwidth'):
        for directory in ('observed', 'truth'):
            (stage/directory/condition).mkdir(parents=True)
        for name in ('topology.json', 'protocol.json'):
            path = stage/'observed'/condition/name
            path.write_bytes(blobs[f'evidence/{condition}/{name}'])
        result = json.loads(blobs[f'evidence/{condition}/result.json'])
        require(result['passed'] and result['drained'] and result['packet_capture']['complete'] and
                result['packet_capture']['omitted_events'] == 0, 'incomplete reference')
        require(all(p['pfc_pause'] == p['pfc_resume'] == p['ecn_ce_seen'] == 0 for p in result['ports']),
                'initial study only supports the fixed normal no-feedback references')
        (stage/'truth'/condition/'result.json').write_bytes(blobs[f'evidence/{condition}/result.json'])
        path = stage/'observed'/condition/'records.jsonl'
        path.write_text(''.join(json.dumps(row, separators=(',', ':'))+'\n' for row in result['packet_capture']['events']))
    for path in stage.rglob('*'):
        if path.is_file():
            hashes[str(path.relative_to(stage))] = hashlib.sha256(path.read_bytes()).hexdigest()
    base = 'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3/src/'
    sources = [base+'point-to-point/model/'+name for name in ('qbb-channel.cc', 'qbb-net-device.cc',
               'point-to-point-net-device.cc', 'switch-node.cc', 'switch-mmu.cc')]
    sources += [base+'network/utils/broadcom-egress-queue.cc', 'backend/rdma/fabric.cc']
    save(stage/'source.json', dict(archive_sha256=ARCHIVE_SHA, files=hashes,
        source_hashes={n: hashlib.sha256((repo/n).read_bytes()).hexdigest() for n in sources}, new_networks=0,
        scope='prefix local planner; no native state/event coalescing implementation'))


def predict(observed, output):
    require(not output.exists(), 'prediction output already exists')
    output.mkdir(parents=True)
    for name in ('baseline', 'bandwidth'):
        planner = PrefixPlanner(load(observed/name/'topology.json'), load(observed/name/'protocol.json'))
        with (observed/name/'records.jsonl').open() as stream, (output/(name+'.jsonl')).open('x') as sink:
            for ordinal, line in enumerate(stream):
                for row in planner.observe(json.loads(line), ordinal):
                    sink.write(json.dumps(row, separators=(',', ':'))+'\n')
        require(not planner.inflight and not any(planner.queues.values()), 'input did not finish/drain')


def snapshots(stage):
    """Add current-queue snapshots; existing plan bytes must remain identical."""
    for name in ('baseline', 'bandwidth'):
        planner = PrefixPlanner(load(stage/'observed'/name/'topology.json'), load(stage/'observed'/name/'protocol.json'))
        records = []
        for ordinal, line in enumerate((stage/'observed'/name/'records.jsonl').read_text().splitlines()):
            records.extend(planner.observe(json.loads(line), ordinal))
        raw = ''.join(json.dumps(row, separators=(',', ':'))+'\n' for row in records).encode()
        require(raw == (stage/'predictions'/(name+'.jsonl')).read_bytes(), 'snapshot addition changed prior plans')
        path = stage/'predictions'/(name+'-snapshots.jsonl')
        with path.open('x') as sink:
            for row in planner.snapshots:
                sink.write(json.dumps(row, separators=(',', ':'))+'\n')


def optimal_prefix_upper(capacities):
    """Evaluation-only optimum over recorded FIFO positions, ignoring cuts.

    Any chosen batch must start at the next unserved FIFO position and contain
    only its already-queued prefix. Future snapshots select the best partition;
    this is an upper bound for that fixed-schedule rule class, not online input.
    """
    count = len(capacities)
    best = [count+1]*(count+1)
    best[0] = 0
    for i, capacity in enumerate(capacities):
        require(1 <= capacity <= count-i, 'invalid queued capacity')
        for end in range(i+1, i+capacity+1):
            best[end] = min(best[end], best[i]+1)
    return count-best[-1]


def evaluate(stage):
    for name, digest in load(stage/'source.json')['files'].items():
        require(hashlib.sha256((stage/name).read_bytes()).hexdigest() == digest, 'input identity changed')
    summary = {}
    for condition in ('baseline', 'bandwidth'):
        result = load(stage/'truth'/condition/'result.json')
        nodes = {n['id']: n['kind'] for n in load(stage/'observed'/condition/'topology.json')['nodes']}
        original = result['packet_capture']['events']
        require([json.loads(l) for l in (stage/'observed'/condition/'records.jsonl').read_text().splitlines()] == original,
                'observed projection changed')
        tx, ordered = {}, defaultdict(list)
        enqueues, dequeues, receive = {}, {}, {}
        for ordinal, row in enumerate(original):
            key = row[1], row[2], identity(row)
            if row[3] == 3:
                require(key not in enqueues, 'ambiguous enqueue')
                enqueues[key] = ordinal
            elif row[3] == 4:
                require(key not in dequeues, 'ambiguous dequeue')
                dequeues[key] = ordinal
            elif row[3] == 1:
                require(key not in receive, 'ambiguous receive')
                receive[key] = row[0], ordinal
            if row[3] != 0:
                continue
            require(key not in tx, 'ambiguous repeated TX')
            tx[key] = (row[0], ordinal)
            if nodes[row[1]] != 'gpu':
                ordered[row[1], row[2]].append(key)
        positions = {key: i for keys in ordered.values() for i, key in enumerate(keys)}
        # Independent deadline reconstruction: pair physical receives from truth,
        # but a flight belongs to the prefix only after its TX has been observed.
        topology = load(stage/'observed'/condition/'topology.json')
        peers, delays = {}, defaultdict(list)
        for link in topology['links']:
            for a, b, ap, bp in ((link['src'], link['dst'], link['src_port'], link['dst_port']),
                                 (link['dst'], link['src'], link['dst_port'], link['src_port'])):
                peers[a, ap] = b, bp
                delays[a].append(link['delay_ns'])
        pending, cuts = {}, {}
        for ordinal, row in enumerate(original):
            key = row[1], row[2], identity(row)
            if row[3] == 1:
                require(key in pending and pending[key] == row[0], 'physical receive has no known flight')
                del pending[key]
            elif row[3] == 0:
                dst, dst_port = peers[row[1], row[2]]
                target = dst, dst_port, identity(row)
                require(target not in pending, 'duplicate physical flight')
                pending[target] = receive[target][0]
                if nodes[row[1]] != 'gpu':
                    topology_cut = row[0]+min(delays[row[1]])
                    flight_cut = min((t for (node, _, _), t in pending.items() if node == row[1]), default=None)
                    cuts[key] = topology_cut, flight_cut
        groups = defaultdict(list)
        claimed = defaultdict(set)
        for line in (stage/'predictions'/(condition+'.jsonl')).read_text().splitlines():
            plan = json.loads(line)
            mode, node, port = plan['mode'], plan['node'], plan['port']
            first = node, port, tuple(plan['selected'][0][0])
            require(tx[first] == (plan['now_ns'], plan['ordinal']), 'plan start identity mismatch')
            require(mode in ('conservative', 'topology_only', 'queued_upper'), 'unknown planning rule')
            topo, flight = cuts[first]
            expected_cut = min(topo, flight) if flight is not None else topo
            require(plan['topology_cut_ns'] == topo and plan['earliest_observed_inflight_RX_ns'] == flight and
                    plan['deadline_ns'] == (expected_cut if mode == 'conservative' else topo if mode == 'topology_only' else None),
                    'deadline does not follow prefix-visible inputs')
            expected = []
            for pid, time, size in plan['selected']:
                key = node, port, tuple(pid)
                require(key in tx and key not in claimed[mode] and size == pid[3], 'unknown/repeated selected packet')
                require(enqueues[key] < plan['ordinal'] and
                        (key == first or plan['ordinal'] < dequeues[key]), 'selected packet was not already queued')
                claimed[mode].add(key)
                expected.append(tx[key][0] == time)
            sequence = ordered[node, port][positions[first]:positions[first]+len(plan['selected'])]
            same_order = sequence == [(node, port, tuple(p[0])) for p in plan['selected']]
            valid = all(expected) and same_order
            if mode == 'conservative':
                require(valid, 'conservative local TX plan disagrees with execution')
                if len(plan['selected']) > 1:
                    require(plan['finish_ns'] < plan['deadline_ns'], 'conservative plan crosses deadline')
            groups[mode].append(dict(count=len(plan['selected']), valid=valid, plan=plan))
        counts = dict(switch_transmissions=sum(len(v) for v in ordered.values()),
            all_physical_transmissions=len(tx), captured_events=len(original), modes={})
        switch_keys = {key for keys in ordered.values() for key in keys}
        for mode in ('conservative', 'topology_only', 'queued_upper'):
            rows = groups[mode]
            require(claimed[mode] == switch_keys, 'switch TX coverage mismatch')
            savings = sum(r['count']-1 for r in rows)
            invalid = [r for r in rows if not r['valid']]
            witness = None
            if invalid:
                plan = invalid[0]['plan']
                witness = dict(node=plan['node'], port=plan['port'], start_ns=plan['now_ns'],
                    known_inflight_RX_ns=plan['earliest_observed_inflight_RX_ns'],
                    predicted=[dict(packet=p[0], time_ns=p[1]) for p in plan['selected']],
                    actual_TX=[dict(packet=list(identity(r)), time_ns=r[0]) for r in original if
                        r[3] == 0 and r[1] == plan['node'] and r[2] == plan['port'] and
                        plan['now_ns'] <= r[0] < plan['finish_ns']+1])
            counts['modes'][mode] = dict(groups=len(rows), multi_packet_groups=sum(r['count'] > 1 for r in rows),
                largest_group=max(r['count'] for r in rows), packets_in_multi_groups=sum(r['count'] for r in rows if r['count'] > 1),
                candidate_TransmitComplete_callbacks_saved=savings,
                fraction_of_switch_TX_callbacks=savings/counts['switch_transmissions'],
                fraction_of_all_physical_TX_callbacks=savings/counts['all_physical_transmissions'],
                plans_disagreeing_with_actual_TX=len(invalid),
                first_invalid_plan=invalid[0]['plan'] if invalid else None,
                first_invalid_witness=witness,
                example_largest_plan=max(rows, key=lambda r:r['count'])['plan'])
        fifo, snapshot_map = defaultdict(list), {}
        for line in (stage/'predictions'/(condition+'-snapshots.jsonl')).read_text().splitlines():
            snapshot = json.loads(line)
            key = snapshot['node'], snapshot['port'], tuple(snapshot['packet'])
            require(key in tx and key not in snapshot_map and tx[key][1] == snapshot['ordinal'], 'invalid snapshot identity')
            snapshot_map[key] = snapshot
        require(snapshot_map.keys() == switch_keys, 'snapshot TX coverage mismatch')
        for row in original:
            if row[3] == 0 and nodes[row[1]] != 'gpu':
                fifo[row[1], row[2], 0 if row[6] == 252 else 3].append((row[1], row[2], identity(row)))
        optimum_saved = 0
        for items in fifo.values():
            capacities = []
            for position, key in enumerate(items):
                snapshot = snapshot_map[key]
                prefix = [(key[0], key[1], tuple(pid)) for pid in snapshot['queued_prefix']]
                require(prefix == items[position:position+len(prefix)], 'queued snapshot is not the observed FIFO prefix')
                available = [key]
                for other in items[position+1:]:
                    if enqueues[other] >= snapshot['ordinal']:
                        break
                    available.append(other)
                require(prefix == available, 'snapshot omits queued packets or includes future arrivals')
                capacities.append(len(prefix))
            optimum_saved += optimal_prefix_upper(capacities)
        counts['evaluation_only_fixed_FIFO_prefix_upper'] = dict(callbacks_saved_upper=optimum_saved,
            fraction_of_all_physical_TX_callbacks=optimum_saved/counts['all_physical_transmissions'],
            rule_class='fixed actual TX times; same-priority already-queued prefix only; ignore cut/feedback',
            online_algorithm=False)
        require(counts['modes']['conservative']['candidate_TransmitComplete_callbacks_saved'] <= optimum_saved,
                'candidate exceeds fixed-FIFO prefix bound')
        counts.update(native_events_changed=0, native_speedup_measured=False, all_side_effects_equivalent_proven=False)
        summary[condition] = counts
    return summary


def report(stage, output):
    summary = evaluate(stage)
    output.mkdir(parents=True, exist_ok=False)
    save(output/'summary.json', summary)
    rows = []
    for condition, label in (('baseline', '100G'), ('bandwidth', '200G')):
        for mode, text in (('conservative', '前缀+在途+传播界'), ('topology_only', '仅传播界'), ('queued_upper', '不截断的固定贪心参考')):
            d = summary[condition]['modes'][mode]
            rows.append(f"| {label} | {text} | {d['multi_packet_groups']} | {d['largest_group']} | "
                f"{d['candidate_TransmitComplete_callbacks_saved']} | {100*d['fraction_of_all_physical_TX_callbacks']:.2f}% | {d['plans_disagreeing_with_actual_TX']} |")
    text = f'''# 活跃交换机队列里，局部批处理有多少空间

2026-10-27。只使用已捕获事件的当前前缀，规划“当前已经排队的哪些包可按已知串行时间连续发送”，未来发送记录仅作评价。**本轮交付局部前缀规划器和可核对的潜在回调缩减数量，尚未修改原生仿真器，也没有测得加速。**

两份正常记录各528条完整包路径、2,464次物理发送，其中交换机发送1,936次。候选只针对本地TransmitComplete发送完成调度，不删除包、不删除PHY接收、ACK生成、应用可见或恢复事件；即便局部计划时间一致，也没有证明全部副作用等价。

## 方法和较强基线

规划器逐条读入PHY发送/接收和队列入队/出队。发送开始时，只读当前包和同端口同优先级FIFO已排队的后继；每包保持真实长度、整数纳秒服务和原优先级，不读取未来才入队的包。数据队列剩余字节须不高于该端口ECN kmin。截止为已开始物理传输向本交换机到达的最早时刻，与当前时间加最短相邻传播二者中的较早者；整段发送完成须严格早于截止。尚未发出的包与本批输出都不可能越过这个传播界提前到达相邻节点。

这是普通保守离散事件方法/队列批处理的局部基线，没有新增MoE信息，不作为独立创新。较弱的“仅传播界”忽略已经在途的包；不截断的已排队前缀只给局部乐观空间，不能作为安全方法。三者都固定规则，不按未来结果挑选。

## 实际数量

表中百分比的分母是全部物理发送次数，**不是全部ns-3事件，更不是宿主加速比**。一批k个包仅列出可少调度k−1个本地发送完成回调的候选空间；真实实现仍需保留最后完成边界及全部下游处理。

| 网络 | 规则 | 多包组 | 最长组/包 | 潜在少调度回调 | 占物理发送回调 | 与后续实际TX不符的计划 |
|---|---|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

不截断的固定贪心参考不一定达到全局最优，不能拿它的统计当作数学上界。新增独立的离线动态规划，允许使用未来全部队列快照来选最好分段、忽略输入打断与反馈，才得到“发送时刻不变、每批只处理同优先级已排队前缀”这一明确类别的乐观上界：100G至多少{summary['baseline']['evaluation_only_fixed_FIFO_prefix_upper']['callbacks_saved_upper']}次，200G至多少{summary['bandwidth']['evaluation_only_fixed_FIFO_prefix_upper']['callbacks_saved_upper']}次。它只用于评价，不作为在线算法输入，也不约束所有可能的事件聚合方法。

具体反例：100G的交换机8/端口3在7060ns发送76B数据，另一个76B数据已排队。仅传播界把后者排在7066ns，但在途ACK于7062ns到达，实际先在7066ns发送50B ACK，数据到7070ns才发。200G对应7030ns开始、7031ns到达ACK，数据预测7033ns而实际7035ns。保守规则在此拒绝合并，未来真值没有参与这个拒绝。

若较弱规则的计划不符，应直接展示在途输入造成的打断，不能用后续真值回头缩短批次来伪装在线算法。保守规则的所有选中包须逐项核对身份、发送顺序与时间，一旦不符即停止其时间安全主张。

## 结果能支持什么

当前保守规则只提供20/36次局部回调缩减候选；即使在明确的同FIFO前缀类别内使用未来全知最优，也至多75/76次，约占物理发送回调3.04%/3.08%。这还未扣规划、跨端口状态维护和日志成本，也不是全部事件数。**据此本轮不新增该原生批处理功能，不把它作为主创新。** 若继续加速研究，需检验跨多跳/操作边界的表示是否能保留队列、接收、反馈与应用可见而省掉更多真实处理；不能只扩大前缀或改名。

只证明这两个固定记录中的局部发送计划；没有把包级队列/接收/反馈整个替换成流级模型。正式集成还要独立处理跨端口共享MMU查询、ECN随机数分配、PFC暂停/恢复、故障事件、同刻顺序、日志观测及端点/源buffer寿命。正常场景没有反馈不能推导故障时可省略；MRT1仍是功能事务，非现代RoCE/GIN成本。

即使局部存在空间，也须和普通前缀批处理、已有回调批次及QP复用区分。CLEM已运行真实CCL并有扩规模优化，ASTRA已有依赖图与ns-3接入；本轮没有证明超过它们的独立贡献。优先研究仍为更小表示下的可靠网络决策，以及有实际争用时可验证的事件聚合；两个主创新尚未成立。

范围继续是原四请求、四活动rank、单层/一个条目、2KB分包、两个正常网络，不能外推新增15请求、完整服务或千卡。零新网络/构建/GPU/下载/平台依赖修改/push。事前预算、复算和独立检查见[阶段说明](../../busy_prefix_README.md)。
'''
    (output/'report.md').write_text(text)
    return summary


def main():
    parser = argparse.ArgumentParser()
    actions = parser.add_subparsers(dest='action', required=True)
    p = actions.add_parser('prepare')
    p.add_argument('--repository', type=Path, required=True)
    p.add_argument('--stage', type=Path, required=True)
    p = actions.add_parser('predict')
    p.add_argument('--observed', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p = actions.add_parser('report')
    p.add_argument('--stage', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p = actions.add_parser('snapshots')
    p.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.repository.resolve(), args.stage.resolve())
    elif args.action == 'predict':
        predict(args.observed.resolve(), args.output.resolve())
    elif args.action == 'snapshots':
        snapshots(args.stage.resolve())
    else:
        values = report(args.stage.resolve(), args.output.resolve())
        print(json.dumps({k: {m: d['candidate_TransmitComplete_callbacks_saved'] for m, d in v['modes'].items()}
                          for k, v in values.items()}))


if __name__ == '__main__':
    main()
