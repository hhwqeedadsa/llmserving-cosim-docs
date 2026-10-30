"""Independent enumeration of source/expert notifications and conditional posting."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(path):
    return json.loads(path.read_bytes())


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def check(value, waves):
    assert len(value['rows']) == 16
    expected, identities, origins = {}, set(), {}
    for row in value['rows']:
        identity = row['graph_id'], row['layer']
        assert identity not in identities
        identities.add(identity)
        wave = next(w for w in waves if w['graph_id'] == row['graph_id'])
        route = next(r for r in wave['routes'] if r['layer'] == row['layer'])
        assert row['group'] == wave['group_index'] and row['step'] == wave['decode_step']
        assert row['ranks'] == wave['rank_ids'] and len(row['ranks']) == 32
        assert row['tokens'] == len(route['tokens'])
        assert row['selections'] == sum(len(t['experts']) for t in route['tokens'])
        for token in route['tokens']:
            key, h = token['source']['request_id'], token['source']['sha256']
            assert key not in origins or origins[key] == h
            origins[key] = h
        for phase in ('dispatch', 'combine'):
            data, zero, markers, direct, pending, nonempty, qps = 0, 0, 0, 0, 0, 0, set()
            for source in row['ranks']:
                for expert in range(256):
                    destination = row['ranks'][expert//8]
                    if source == destination:
                        continue
                    amount = sum(expert in token['experts'] for token in route['tokens'] if token['rank'] == source)
                    markers += 1
                    data += amount
                    zero += amount == 0
                    if amount:
                        nonempty += 1
                        qps.add((source, destination, expert % 8) if phase == 'dispatch' else
                                (destination, source, expert % 8))
                    triggered = [i for i in range(amount) if (i+1) % 4 == 0]
                    direct += len(triggered)
                    pending += amount-(triggered[-1]+1 if triggered else 0)
            reference = dict(data_puts=data, marker_operations=markers, zero_token_markers=zero,
                nonempty_marker_contexts=nonempty, data_active_logical_QPs=len(qps),
                synchronous_data_postsend_calls=direct, puts_in_final_unrung_batch=pending,
                marker_forced_postsend_calls=markers)
            for name, number in reference.items():
                assert row['low_latency_projection'][phase][name] == number, (identity, phase, name)
                key = phase+'_'+name
                expected[key] = expected.get(key, 0)+number
        messages = [m for m in wave['messages'] if m['layer'] == row['layer']]
        assert row['current_direct']['messages'] == len(messages)
        assert row['current_direct']['logical_payload_bytes'] == sum(m['bytes'] for m in messages)
        for name, number in [('tokens', row['tokens']), ('selections', row['selections']),
            ('current_direct_messages', len(messages)), ('current_direct_payload_bytes', sum(m['bytes'] for m in messages))]:
            expected[name] = expected.get(name, 0)+number
    assert value['totals'] == expected
    assert value['distinct_real_requests'] == len(origins) == 15
    assert value['request_sha256'] == {str(k): v for k, v in origins.items()}
    assert len(value['key_examples']) == 3
    for example in value['key_examples']:
        span, size = example['assumed_key_span_bytes'], example['put_bytes']
        cuts = {0, size}
        for offset in (example['local_offset'], example['remote_offset']):
            cuts.update(i for i in range(1, size) if (offset+i) % span == 0)
        ordered = sorted(cuts)
        assert example['WQE_payload_bytes'] == [b-a for a, b in zip(ordered, ordered[1:])]
        assert example['network_packets'] is None and example['ACKs'] is None
    assert [len(e['WQE_payload_bytes']) for e in value['key_examples']] == [1, 2, 3]
    assert value['conditions']['actual_async_mode_known'] is False
    assert value['conditions']['current_direct_is_same_library_mode'] is False
    assert not value['network_executed'] and not value['latency_predicted'] and not value['hardware_calibrated']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('input', 'analysis', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    frozen = load(args.input/'frozen.json')
    for name, h in frozen['raw_sha256'].items():
        assert sha(Path(name)) == h
    assert sha(args.input/'projections.json') == frozen['projections_sha256']
    waves = load(args.input/'projections.json')
    run = next(Path(p).parent for p in frozen['raw_sha256'] if p.endswith('/result.json'))
    for wave in waves:
        original = load(run/'graphs'/wave['graph_id']/'wave.json')
        assert wave == {key: original[key] for key in wave}
    sources = load(args.input/'sources.json')
    for source in sources:
        raw = (ROOT/source['path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == source['sha256']
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() == source['git_blob_sha1']
        lines = raw.decode().splitlines()
        for excerpt in source['excerpts']:
            assert '\n'.join(lines[excerpt['first_line']-1:excerpt['last_line']]) == excerpt['text']
    value = load(args.analysis)
    assert value['frozen_sha256'] == sha(args.input/'frozen.json')
    check(value, waves)
    failures = []
    for name in ('put_count', 'zero_notification', 'posting_call', 'key_split'):
        altered = deepcopy(value)
        if name == 'put_count':
            altered['rows'][0]['low_latency_projection']['dispatch']['data_puts'] += 1
        elif name == 'zero_notification':
            altered['rows'][0]['low_latency_projection']['combine']['zero_token_markers'] += 1
        elif name == 'posting_call':
            altered['rows'][0]['low_latency_projection']['dispatch']['synchronous_data_postsend_calls'] += 1
        else:
            altered['key_examples'][1]['WQE_payload_bytes'].reverse()
        try:
            check(altered, waves)
        except AssertionError:
            failures.append(name)
        else:
            raise AssertionError('tamper accepted: '+name)
    proof = dict(passed=True, analysis_sha256=sha(args.analysis), verifier_sha256=sha(Path(__file__)),
        windows_checked=16, source_expert_items_enumerated=16*32*256*2,
        sources_verified=len(sources), raw_artifacts_verified=len(frozen['raw_sha256']),
        tamper_rejected=failures, new_networks=0, latency_predicted=False)
    with args.output.open('x') as stream:
        json.dump(proof, stream, indent=2)
        stream.write('\n')
    print(json.dumps(proof))


if __name__ == '__main__':
    main()
