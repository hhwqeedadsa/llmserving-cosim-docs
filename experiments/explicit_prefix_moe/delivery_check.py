"""Recheck curves with independent byte maps; reject four raw corruptions."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import gc
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile

from verify import check_case, load, sha


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k in ('src', 'href'):
                self.links.append(v)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for k in ('study', 'delivery', 'output'):
        p.add_argument('--'+k, type=Path, required=True)
    a = p.parse_args()
    f = load(a.study/'frozen.json')
    analysis, mechanism = load(a.delivery/'analysis.json'), load(a.delivery/'mechanism.json')
    increments = 0
    for name, diagnosis in mechanism['cases'].items():
        d = Path(analysis['cases'][name]['directory'])
        assert sha(d/'result.json') == diagnosis['result_sha256']
        r = load(d/'result.json')
        ms = diagnosis['collective']['messages']
        masks = {m['id']: bytearray(m['bytes']) for m in ms}
        by_stream = defaultdict(list)
        for m in ms:
            by_stream[m['stream']].append(m)
        cap = r['packet_capture']
        actual, by_time = [], Counter()
        for row in cap['events']:
            x = dict(zip(cap['columns'], row))
            if x['event'] != cap['event_codes']['rx'] or x['ip_protocol'] != 17:
                continue
            for m in by_stream[x['message_id']]:
                if x['node'] != m['dst']:
                    continue
                left, right = max(m['begin'], x['sequence']), min(m['end'], x['sequence']+x['data_payload_bytes'])
                if left >= right:
                    continue
                mask = masks[m['id']]
                start, end = left-m['begin'], right-m['begin']
                new = end-start-sum(mask[start:end])
                mask[start:end] = b'\1'*(end-start)
                if new:
                    actual.append(dict(packet=x, new_payload_bytes=new))
                    by_time[x['time_ns']] += new
        assert all(all(mask) for mask in masks.values())
        assert actual == diagnosis['collective']['actual_RX_increments']
        pts, total = [[0, 0]], 0
        for t, value in sorted(by_time.items()):
            total += value
            pts.append([t, total])
        assert pts == diagnosis['collective']['unique_received_curve_ns_bytes']
        assert total == diagnosis['collective']['payload_bytes']
        increments += len(actual)
        del r
        gc.collect()
    # Recompute hashes for deliberately corrupted copies so rejection must come
    # from physical/ACK/metadata consistency, not only the result hash guard.
    source = Path(analysis['cases']['prefix-w60']['directory'])
    original = load(Path(f['reference'])/'normal/input.json')
    routes = load(Path(f['reference'])/'normal'/original['route_replay']['manifest']['path'])
    for row in routes['files']:
        row['file']['path'] = '<verified contents>'
    rejected = []
    for kind in ('physical_RX_time', 'source_ACK_time', 'wire_encapsulation', 'source_payload'):
        r = load(source/'result.json')
        if kind == 'physical_RX_time':
            cap = r['packet_capture']
            next(row for row in cap['events'] if row[3] == cap['event_codes']['rx'])[0] += 7
        elif kind == 'source_ACK_time':
            r['service_transport']['messages'][0]['ack_ns'] += 1
        elif kind == 'wire_encapsulation':
            next(meta for meta in r['explicit_repair']['packet_metadata'] if meta[0])[0] -= 1
        else:
            n = r['service_transport']['native']
            n['source_packets'][0][n['source_packet_columns'].index('payload_bytes')] += 1
        with tempfile.TemporaryDirectory(prefix='tamper-', dir=a.study.parent) as tmp:
            d = Path(tmp)/'prefix-w60'
            d.mkdir()
            for path in source.iterdir():
                if path.name != 'result.json':
                    (d/path.name).symlink_to(path, target_is_directory=path.is_dir())
            (d/'result.json').write_text(json.dumps(r))
            expected = deepcopy(analysis['cases']['prefix-w60'])
            expected['result_sha256'] = sha(d/'result.json')
            try:
                check_case(d, expected, f, original, routes)
            except AssertionError:
                rejected.append(kind)
            else:
                raise AssertionError('Corruption accepted: '+kind)
        del r
        gc.collect()
    parser = Links()
    parser.feed((a.delivery/'report.html').read_text())
    for link in parser.links:
        assert '://' not in link and (a.delivery/link).is_file(), link
    for file in ('comparison.png', 'arrival_curves.png'):
        assert (a.delivery/file).read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    with a.output.open('x') as s:
        json.dump(dict(passed=True, unique_RX_increments_checked=increments, tamper_checks=rejected,
            static_HTML_links=len(parser.links), browser_rendering_verified=False,
            verifier_sha256=sha(Path(__file__)), delivery_sha256={p.name: sha(p) for p in a.delivery.iterdir() if p.is_file()}), s, indent=2)
        s.write('\n')
    print(json.dumps(dict(passed=True, increments=increments, tamper_checks=rejected)))


if __name__ == '__main__':
    main()
