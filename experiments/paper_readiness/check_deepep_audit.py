"""Independent source/expert enumeration and evidence integrity for the G1 audit."""
import argparse
from collections import Counter
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from audit_deepep import account

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        self.links += [v for k, v in attrs if k in ('href', 'src')]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audit', required=True, type=Path)
    p.add_argument('--repeat', required=True, type=Path)
    p.add_argument('--html', required=True, type=Path)
    p.add_argument('--graphs', type=Path, help='Existing public output or relocated saved_run snapshot')
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    assert not a.output.exists()
    audit = json.loads(a.audit.read_text())
    assert a.audit.read_bytes() == a.repeat.read_bytes(), 'Non-deterministic audit'
    counterexample = account([[2,3],[0,1],[6,7],[4,5]], 8, 128)
    assert counterexample['offrank_contributions'] == 8
    assert counterexample['rank_pair_messages_two_legs'] == 8
    assert counterexample['offrank_payload_put_calls_two_legs'] == 16
    assert counterexample['count_atomic_calls'] == 24 and counterexample['zero_token_count_atomic_calls'] == 16
    assert counterexample['dispatch_metadata_bytes'] == 128
    local = account([[0,1],[2,3],[4,5],[6,7]], 8, 128)
    assert local['rank_pair_messages_two_legs'] == 0 and local['count_atomic_calls'] == local['zero_token_count_atomic_calls'] == 24
    texts = {}
    for source in audit['sources']:
        path = ROOT / source['local_snapshot']
        assert digest(path) == source['sha256']
        raw = path.read_bytes()
        assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == source['git_blob_sha']
        texts[source['path']] = raw.decode().splitlines()
    excerpts = 0
    for finding in audit['findings']:
        for item in finding['evidence']:
            start, end = item['first_line'], item['last_line']
            assert 1 <= start <= end <= len(texts[item['path']])
            assert item['excerpt'] == '\n'.join(texts[item['path']][start-1:end])
            excerpts += 1
    for item in audit['platform_sources']:
        assert digest(ROOT / item['path']) == item['sha256']
    claims_path = ROOT / 'docs/review_artifacts/paper_readiness_20261022/claims.json'
    claims = json.loads(claims_path.read_text())
    for claim in claims['claims']:
        for item in claim['evidence']:
            assert digest(ROOT / item['path']) == item['sha256']
    accounting = audit['accounting']
    ref = accounting['manifest']
    assert digest(ROOT / ref['path']) == ref['sha256']
    manifest = json.loads((ROOT / ref['path']).read_text())
    raw = []
    for item, identity in zip(manifest['files'], accounting['files']):
        path = ROOT / item['file']['path']
        assert item['file_id'] == identity['file_id'] and digest(path) == identity['sha256'] == item['file']['sha256']
        raw.append(json.loads(path.read_text()))
    total = Counter()
    windows = 0
    for lid in range(3, 61):
        layer = Counter()
        for entry in range(1, 129):
            selected = [set(r[entry][str(lid)][0]) for r in raw]
            puts = notifications = zero = 0
            pairs = set()
            # Enumerate zero and nonzero entries independently of the audit's
            # direct (G-1)*E formula and contribution-to-matrix implementation.
            for src in range(4):
                for e in range(256):
                    dst = e//64
                    if src == dst:
                        continue
                    notifications += 1
                    if e in selected[src]:
                        puts += 1
                        pairs.add((src, dst))
                    else:
                        zero += 1
            assert notifications == puts+zero
            layer.update(dict(offrank_contributions=puts, rank_pair_messages_two_legs=len(pairs)*2,
                offrank_payload_put_calls_two_legs=puts*2,
                count_atomic_calls=notifications, combine_flag_atomic_calls=notifications,
                zero_token_count_atomic_calls=zero, zero_token_combine_flag_atomic_calls=zero,
                zero_payload_rank_pairs_per_leg=12-len(pairs),
                abstract_two_leg_bf16_operand_bytes=puts*28672,
                deepep_two_leg_bf16_data_operand_bytes=puts*28688,
                deepep_dispatch_fp8_data_operand_bytes=puts*7408,
                dispatch_metadata_bytes=puts*16))
            windows += 1
        row = accounting['by_layer'][lid-3]
        assert row['source_layer_id'] == lid and row['synchronized_windows'] == 128
        assert dict(layer) == {k:v for k,v in row.items() if k not in ('source_layer_id', 'synchronized_windows')}
        total.update(layer)
    assert dict(total) == accounting['totals'] and windows == accounting['synchronized_layer_windows'] == 7424
    comparison = audit.get('saved_graph_comparison')
    if comparison:
        assert a.graphs and digest(a.graphs / 'input.json') == comparison['input_sha256']
        for wave in comparison['waves']:
            path = Path(wave['path'])
            assert not path.is_absolute() and '..' not in path.parts
            assert digest(a.graphs / path) == wave['sha256']
    parser = Links()
    parser.feed(a.html.read_text())
    local_links = 0
    for target in parser.links:
        url = urlsplit(target)
        if not url.scheme and url.path:
            path = (a.html.parent / unquote(url.path)).resolve()
            assert path.exists() or path == a.output.resolve(), target
            local_links += 1
    result = dict(source_hashes_verified=len(audit['sources']), exact_excerpts_verified=excerpts,
                  raw_files_verified=len(raw), independently_enumerated_windows=windows,
                  independently_enumerated_source_expert_entries=windows*4*256,
                  deterministic_offline_repeat=True, local_html_links=local_links,
                  saved_wave_hashes_verified=len(comparison['waves']) if comparison else 0,
                  same_rank_two_expert_counterexample=True, zero_payload_notification_counterexample=True,
                  simulated_new_packets=False, hardware_calibrated=False,
                  browser_visual_acceptance=False, overall_paper_ready=False,
                  audit_sha256=digest(a.audit), html_sha256=digest(a.html))
    result['paper_claims_verified'] = len(claims['claims'])
    result['paper_claims_sha256'] = digest(claims_path)
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
