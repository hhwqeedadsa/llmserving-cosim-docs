"""Recheck the published explanation against raw packet rows and graph gates."""
import argparse
from collections import Counter
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re


def load(p):
    return json.loads(Path(p).read_bytes())


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        self.paths.extend(v for k, v in attrs if (tag == 'img' and k == 'src') or (tag == 'a' and k == 'href'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--study', type=Path, required=True)
    p.add_argument('--delivery', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    output = a.delivery
    analysis, mechanism, raw = (load(output/n) for n in ('analysis.json', 'mechanism.json', 'raw_verification.json'))
    assert raw['passed'] and raw['analysis_sha256'] == sha(output/'analysis.json')
    assert raw['new_networks'] == 5 and raw['messages_checked'] == 4668
    checked = 0
    for name in ('global60', 'local60'):
        entry = mechanism[name]
        r = load(a.study/name/'result.json')
        cap = r['packet_capture']
        idx = {k: i for i, k in enumerate(cap['columns'])}
        original_rows = Counter(tuple(x) for x in cap['events'])
        for path in (entry['data_path'], entry['ack_path']):
            for row in path['events']:
                assert original_rows[tuple(row[c] for c in cap['columns'])] == 1
                checked += 1
            assert path['source_tx_ns'] == path['events'][0]['time_ns']
            assert path['destination_rx_ns'] == path['events'][-1]['time_ns']
        assert entry['message'] in r['service_transport']['messages']
        gate = next(g for g in r['service']['phase_completion']['gates']
                    if g['graph_id'] == 'g2.w1' and g['id'] == 'L1.combine')
        assert gate['released_ns'] == entry['final_combine_gate_ns']
        for node, occupiers in entry['occupiers'].items():
            q = entry['data_path']['switches'][node]
            independent = Counter()
            expected_rows = []
            streams = {s['id']: s for s in r['service_transport']['native']['streams']}
            for row in cap['events']:
                if row[idx['event']] != cap['event_codes']['tx'] or row[idx['node']] != int(node) or row[idx['port']] != q['port']:
                    continue
                # All selected ports belong to400Gb/s physical links; integer ceiling.
                duration = (row[idx['model_bytes']]*8*10**9+400000000000-1)//400000000000
                amount = max(0, min(q['tx_ns'], row[idx['time_ns']]+duration)-max(q['rx_ns'], row[idx['time_ns']]))
                if not amount:
                    continue
                stream = streams.get(row[idx['message_id']])
                group = str(stream['src'] % 4) if stream else 'unmapped'
                independent[group] += amount
                expected_rows.append(tuple(row))
            assert dict(independent) == occupiers['by_source_group_ns']
            assert sum(independent.values()) == q['residence_ns']
            actual_rows = [tuple(w['packet'][c] for c in cap['columns']) for w in occupiers['wire']]
            assert actual_rows == expected_rows
            checked += len(expected_rows)
    base, local = mechanism['global60'], mechanism['local60']
    ready = local['message']['ready_ns']-base['message']['ready_ns']
    data = local['message']['receive_ready_ns']-base['message']['receive_ready_ns']
    ack = local['message']['ack_ns']-base['message']['ack_ns']
    assert ready == 1249 and data == ack == 3014
    assert sum(q['residence_ns'] for q in local['data_path']['switches'].values())-sum(
        q['residence_ns'] for q in base['data_path']['switches'].values()) == 1765
    assert base['final_combine_gate_ns']-base['message']['ack_ns'] == 183
    assert local['final_combine_gate_ns']-base['final_combine_gate_ns'] == 2831
    for e in analysis['effects'].values():
        assert isinstance(e['extra_source_payload_bytes'], int)
    parser = Links()
    parser.feed((output/'report.html').read_text())
    assert len(parser.paths) == 5 and all((output/x).is_file() for x in parser.paths)
    mdlinks = re.findall(r'\]\(([^)]+)\)', (output/'README.md').read_text())
    assert all((output/x).is_file() for x in mdlinks)
    from PIL import Image
    with Image.open(output/'comparison.png') as im:
        assert im.width > 1000 and im.height > 500
    proof = dict(passed=True, raw_packet_rows_checked=checked, exact_group_delay_ns=2831,
        html_resources=len(parser.paths), markdown_links=len(mdlinks), browser_rendering_verified=False,
        files_sha256={x.name: sha(x) for x in output.iterdir() if x.is_file()},
        verifier_sha256=sha(Path(__file__)))
    with a.output.open('x') as s:
        json.dump(proof, s, indent=2)
        s.write('\n')
    print(json.dumps(dict(passed=True, raw_packet_rows_checked=checked, exact_group_delay_ns=2831)))


if __name__ == '__main__':
    main()
