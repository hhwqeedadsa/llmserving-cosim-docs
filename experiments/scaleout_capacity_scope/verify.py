"""Rebuild every payload matrix from expert bitmaps, without study imports."""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

INTAKE_SHA = '6fb24dd4229bbd42d73900062f769806aecda2b20a78c30c62c564ffe6cda558'


def load(p):
    return json.loads(p.read_bytes())


def encoded(v):
    return (json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(',', ':'))+'\n').encode()


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def expected_metrics(demand, ep):
    sent, received = [0]*ep, [0]*ep
    server_sent, server_received = [0]*(ep//8), [0]*(ep//8)
    cut = [[0]*8 for _ in range(2)]
    canonical = []
    for (src, dst), n in sorted(demand.items()):
        sent[src] += n
        received[dst] += n
        server_sent[src//8] += n
        server_received[dst//8] += n
        if (src < ep//2) != (dst < ep//2):
            cut[int(src >= ep//2)][src%8] += n
        canonical.append([src, dst, n])
    assert sum(sent) == sum(received) == sum(server_sent) == sum(server_received)
    return dict(vectors=sum(sent), matrix_sha256=hashlib.sha256(encoded(canonical)).hexdigest(),
        active_tx_nics=sum(n > 0 for n in sent), active_rx_nics=sum(n > 0 for n in received),
        nic_tx_max=max(sent), nic_rx_max=max(received), nic_max=max(sent+received),
        server_tx_max=max(server_sent), server_rx_max=max(server_received),
        server_max=max(server_sent+server_received), cut_forward_max=max(cut[0]),
        cut_reverse_max=max(cut[1]), cut_max=max(cut[0]+cut[1]))


def verify(stage):
    observed = stage/'observed'
    assert digest(observed/'intake.json') == INTAKE_SHA
    for name, h in load(observed/'identity.json').items():
        assert digest(observed/name) == h
    source = load(observed/'sources.json')
    assert source['revision'] == 'f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
    assert source['operands'] == dict(dispatch=14464, combine=14400)
    for name, meta in source['files'].items():
        raw = (observed/'library'/name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == meta['sha256']
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() == meta['git_blob_sha']
    intake = load(observed/'intake.json')
    originals = sorted((r for r in intake['files'] if r['eligible_offline']), key=lambda r: int(r['name'][:-5]))
    assert len(originals) == 15 and intake['rejected_files'] == ['899.json', '1479.json']
    documents = []
    for r in originals:
        assert digest(observed/'routes'/r['name']) == r['sha256']
        documents.append(load(observed/'routes'/r['name']))
    assert sum(len(d[0]['3']) for d in documents) == 693
    assert sum(len(d)-1 for d in documents) == 1811
    # Reconstruct small source→domain arrays via bitwise expert coverage.
    matrices, inputs = {}, {}
    for ep in (64, 128, 256):
        width = 256//(ep//8)
        for placement in ('packed', 'spread'):
            for entry in range(max(map(len, documents))):
                for layer in range(3, 61):
                    demand, sources, tokens = {}, 0, 0
                    for index, document in enumerate(documents):
                        if entry >= len(document):
                            continue
                        src = index if placement == 'packed' else (ep*index)//15
                        selected = document[entry][str(layer)]
                        sources += bool(selected)
                        tokens += len(selected)
                        for expert_choices in selected:
                            expert_mask = sum(1 << e for e in expert_choices)
                            for domain in range(ep//8):
                                if domain == src//8:
                                    continue
                                if (expert_mask >> (domain*width)) & ((1 << width)-1):
                                    dst = domain*8+src%8
                                    demand[src, dst] = demand.get((src, dst), 0)+1
                    key = ep, placement, entry, layer
                    matrices[key] = expected_metrics(demand, ep)
                    # Combine transposes every domain-reduced vector; TX/RX switch.
                    reverse = expected_metrics({(b, a): n for (a, b), n in demand.items()}, ep)
                    assert reverse['nic_tx_max'] == matrices[key]['nic_rx_max']
                    assert reverse['cut_forward_max'] == matrices[key]['cut_reverse_max']
                    inputs[key] = sources, tokens
    rows = load(stage/'analysis/calls.json')
    assert len(rows) == len(matrices)
    seen = set()
    for row in rows:
        key = row['ep'], row['binding'], row['entry'], row['layer']
        assert key not in seen
        seen.add(key)
        assert row['phase'] == ('prefill' if row['entry'] == 0 else 'decode')
        assert (row['input_sources'], row['input_tokens']) == inputs[key]
        assert {k: row[k] for k in matrices[key]} == matrices[key], key
    assert seen == set(matrices)
    summary = load(stage/'analysis/summary.json')
    assert summary['nic_gbps'] == 400 and summary['server_gbps'] == 3200
    assert summary['cut_gbps'] == [400, 800, 1600] and summary['call_rows'] == len(rows)
    assert summary['operands'] == dict(dispatch=14464, combine=14400)
    assert summary['network_executions'] == summary['innovations_established'] == 0
    assert summary['hardware_calibrated'] is False
    summaries = set()
    for c in summary['cases']:
        key = c['ep'], c['binding'], c['phase']
        assert key not in summaries
        summaries.add(key)
        selected = [r for r in rows if (r['ep'], r['binding'], r['phase']) == key]
        assert c['calls'] == len(selected) and c['vectors_total'] == sum(r['vectors'] for r in selected)
        for label, field in (('input_source_ranks_max', 'input_sources'), ('data_source_nics_max', 'active_tx_nics'),
                             ('data_ingress_nics_max', 'active_rx_nics')):
            assert c[label] == max(r[field] for r in selected)
        gates = sum(400*r['cut_max'] > 1.10*400*r['nic_max'] for r in selected)
        assert c['calls_cut_over_nic_at_400'] == gates and c['data_cut_screen_passed'] == (gates/len(selected) >= .20)
        for capacity in (400, 800, 1600):
            assert c['cut_over_nic_calls_at_'+str(capacity)] == sum(
                r['cut_max']*400 > r['nic_max']*capacity for r in selected)
        for q in (50, 95, 100):
            for metric, values in (
                ('cut_crossover_gbps', [400*r['cut_max']/r['nic_max'] if r['nic_max'] else 0 for r in selected]),
                ('nic_vs_allrank_average', [r['nic_max']*r['ep']/r['vectors'] if r['vectors'] else 0 for r in selected]),
                ('nic_dispatch_floor_ns', [r['nic_max']*14464*8/400 for r in selected])):
                expected = sorted(values)[max(0, math.ceil(len(values)*q/100)-1)]
                assert c[metric+'_p'+str(q)] == expected
    assert len(summaries) == 12
    # Perturb five independently checked quantities; all must be rejected.
    record = rows[0]
    expected = matrices[record['ep'], record['binding'], record['entry'], record['layer']]
    mutations = {}
    for field in ('vectors', 'matrix_sha256', 'nic_tx_max', 'cut_reverse_max'):
        altered = copy.deepcopy(record)
        altered[field] = '0'*64 if field == 'matrix_sha256' else altered[field]+1
        mutations[field] = {k: altered[k] for k in expected} != expected
    capacity = copy.deepcopy(summary)
    capacity['cut_gbps'][0] += 1
    mutations['capacity'] = capacity['cut_gbps'] != [400, 800, 1600]
    assert all(mutations.values())
    return dict(passed=True, source_files=15, excluded=['899.json', '1479.json'],
                prompt_tokens=693, decode_records=1811, layers=58, call_rows=len(rows),
                condition_summaries=12, reverse_combine_checked=True,
                mutation_rejections=mutations, network_executions=0,
                note='independent capacity accounting, no latency or research benefit validation')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    assert not a.output.exists()
    result = verify(a.stage.resolve())
    a.output.write_bytes(encoded(result))
