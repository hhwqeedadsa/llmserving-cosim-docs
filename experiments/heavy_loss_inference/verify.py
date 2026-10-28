"""Validate seven saved service runs and explicit loss denominators, offline."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(root):
    selections = [('A', 'runs', 'baseline'), ('A', 'runs', 'data-57-transient')]
    selections += [('B', 'runs-recovery', name) for name in
                   ('baseline', 'data-57-transient', 'data-86-transient',
                    'data-57-persistent', 'ack-57-transient')]
    evidence, cases = {}, []
    for batch, folder, name in selections:
        stage, result_path = root/folder, root/folder/name/'result.json'
        front_path = stage/name/'frontend.json'
        r, f = load(result_path), load(front_path)
        service = load(stage/'service.json')
        assert service['parallel']['groups'] == [[0, 2, 4, 6], [1, 3, 5, 7]]
        assert service['model']['layers'] == 1 and service['model']['hidden_size'] == 1024
        assert service['model']['experts'] == 16 and service['model']['top_k'] == 2
        assert service['workload']['batch_per_rank'] == 4 and service['workload']['decode_steps'] == 2
        protocol = load(stage/(name+'.json'))
        if batch == 'A':
            assert 'recovery' not in protocol
        else:
            assert protocol['recovery'] == dict(mode='go_back_n', initial_rto_ns=40000,
                                               max_rto_ns=160000, max_retries=3, max_events=100000)
        assert r['native_network_executed'] and r['packet_capture']['complete']
        assert r['packet_capture']['omitted_events'] == 0
        assert 'pinned_LLMServingSim' in f['scheduler_source']
        columns = r['packet_capture']['columns']
        index = {n: i for i, n in enumerate(columns)}
        source, drops, acks, nacks = [], [], [], []
        for row in r['packet_capture']['events']:
            if row[index['message_id']] != 1:
                continue
            item = {key: row[index[key]] for key in ('time_ns', 'node', 'port', 'event', 'ip_protocol',
                                                     'sequence', 'data_payload_bytes')}
            if item['node'] == 0 and item['event'] == 0 and item['ip_protocol'] == 17:
                source.append(item)
            if item['event'] == 2:
                drops.append(item)
            if item['node'] == 4 and item['event'] == 0 and item['ip_protocol'] == 252:
                acks.append(item)
            if item['node'] == 4 and item['event'] == 0 and item['ip_protocol'] == 253:
                nacks.append(item)
        tx_count = Counter(p['sequence'] for p in source)
        assert sorted(tx_count) == list(range(0, 6144, 1000))
        for p in source:
            assert p['data_payload_bytes'] == min(1000, 6144-p['sequence'])
        assert len(set(tx_count.values())) == 1
        attempts = next(iter(tx_count.values()))
        target = next(m for m in r['messages'] if m['id'] == 1)
        assert (target['src'], target['dst'], target['bytes']) == (0, 4, 6144)
        payload = sum(p['data_payload_bytes'] for p in source)
        assert target['source_data_payload_bytes'] == payload == attempts*6144
        data_drops = [p for p in drops if p['ip_protocol'] == 17]
        ack_drops = [p for p in drops if p['ip_protocol'] == 252]
        rules = protocol.get('faults', [])
        matches = []
        for rule in rules:
            code = 17 if rule['kind'] == 'data' else 252
            matching = [p for p in drops if p['sequence'] == rule['sequence'] and
                        p['ip_protocol'] == code and p['node'] == rule['node'] and
                        (rule['port'] == 0 or p['port'] == rule['port'])]
            matches.append(len(matching))
        assert matches == r.get('fault_injection', {}).get('drop_counts', [])
        histories = {str(g): [w for w in f['history'] if w['group_index'] == g] for g in (0, 1)}
        group_done = {g: sum('finished_ns' in w for w in waves) for g, waves in histories.items()}
        group_total = {g: len(waves) for g, waves in histories.items()}
        for graph in (stage/name/'graphs').glob('*/wave.json'):
            baseline = stage/'baseline/graphs'/graph.parent.name/'wave.json'
            if baseline.exists():
                assert sha(graph) == sha(baseline), graph
        if name == 'baseline' or name == 'ack-57-transient':
            assert r['passed'] and r['finish_ns'] == 54362 and target['ack_ns'] == 17673
            assert attempts == 1 and group_done == {'0': 2, '1': 2}
        elif batch == 'A':
            assert not r['passed'] and r['finish_ns'] == 5000000
            assert attempts == 2 and target['receiver_contiguous_bytes'] == 0
            assert len(nacks) == 1 and len(data_drops) == 8
            assert target['receive_ready_ns'] is None and target['ack_ns'] is None
        elif name == 'data-57-persistent':
            assert not r['passed'] and r['finish_ns'] == 444203
            assert r['failure']['reason'] == 'recovery_retry_exhausted'
            assert attempts == 5 and len(data_drops) == 20 and group_done == {'0': 0, '1': 2}
            assert all(w['finished_ns'] < r['finish_ns'] for w in histories['1'])
        else:
            assert r['passed'] and r['finish_ns'] == 91297 and target['ack_ns'] == 57154
            assert attempts == 3 and group_done == {'0': 2, '1': 2}
            assert len(data_drops) == (8 if name == 'data-57-transient' else 12)
        if name == 'ack-57-transient':
            assert len(acks) == 7 and len(ack_drops) == 4
            assert all(p['sequence'] < 6144 for p in ack_drops)
        if r['passed']:
            assert target['receiver_contiguous_bytes'] == 6144
        key = batch+':'+name
        cases.append(dict(case=key, passed=r['passed'], finish_ns=r['finish_ns'],
            target_ack_ns=target.get('ack_ns'), source_data_packets=len(source),
            source_payload_bytes=payload, duplicate_payload_bytes=payload-6144,
            target_data_drops=len(data_drops), target_ack_drops=len(ack_drops),
            target_data_drop_fraction=[len(data_drops), len(source)],
            target_data_drop_payload_bytes=sum(p['data_payload_bytes'] for p in data_drops),
            target_data_dropped_payload_fraction=[sum(p['data_payload_bytes'] for p in data_drops), payload],
            target_ack_drop_fraction=[len(ack_drops), len(acks)], source_attempts=attempts,
            nack_count=len(nacks), drop_rule_matches=matches, completed_waves=group_done,
            total_waves=group_total,
            group1_completion_ns=[w.get('finished_ns') for w in histories['1']],
            capture_complete=True, fault_finish_is_not_completion=not r['passed']))
        for path in (result_path, front_path, stage/'service.json', stage/(name+'.json'), stage/'commands.json'):
            evidence[str(path.relative_to(root))] = dict(sha256=sha(path), bytes=path.stat().st_size)
    committed = load(Path(__file__).resolve().parent/'results/20261028/analysis.json')
    saved = {r['label']: r for r in committed['conditions']}
    for row in cases:
        if row['case'].startswith('B:'):
            label = row['case'][2:]
            prior = saved[label]
            assert prior['passed'] == row['passed'] and prior['finish_ns'] == row['finish_ns']
            assert (prior.get('target') or {}).get('ack_ns') == row['target_ack_ns']
            assert prior['source_payload_bytes_sent'] == row['source_payload_bytes']
            assert prior['duplicate_payload_bytes'] == row['duplicate_payload_bytes']
            assert prior['drop_counts'] == row['drop_rule_matches']
            assert prior['source_transmissions'] == {str(q): row['source_attempts'] for q in range(0, 6144, 1000)}
            assert prior['waves_finished_by_group'] == {str(k): dict(finished=v, total=row['total_waves'][k])
                                                         for k, v in row['completed_waves'].items()}
    batch_a = load(Path(__file__).resolve().parent/'results/20261028/batch-a-preserved.json')['conditions']
    assert batch_a['baseline']['finish_ns'] == cases[0]['finish_ns']
    assert batch_a['data-57-transient']['finish_ns'] == 5000000
    assert batch_a['data-57-transient']['drop_counts'] == [2, 2, 2, 2]
    assert cases[0]['group1_completion_ns'] == cases[2]['group1_completion_ns']
    return dict(passed=True, cases=cases, native_simulations_reexecuted=0,
        committed_batch_b_comparison_passed=True, evidence=evidence,
        loss_scope='target-message packet counts; affected rounds and complete-run denominators differ',
        inference_scope='pinned scheduler methods, project Chakra graphs and ASTRA; synthetic routes and declared GPU compute',
        production_RoCE_calibrated=False)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    assert not a.output.exists()
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(verify(a.input.resolve()), ensure_ascii=False, indent=2)+'\n')
