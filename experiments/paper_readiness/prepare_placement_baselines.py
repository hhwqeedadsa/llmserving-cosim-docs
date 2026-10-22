"""Split observed Prefill frequencies from future Decode evaluation routes."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import resource
import shutil

ROOT = Path(__file__).resolve().parents[2]


def save(p, v): p.write_text(json.dumps(v, ensure_ascii=False, separators=(',', ':')) + '\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--source-root', type=Path, default=ROOT)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); out = a.output.resolve()
    resource.setrlimit(resource.RLIMIT_AS, (2 * 2**30, 2 * 2**30))
    assert not out.exists() and out.parent.is_dir() and shutil.disk_usage(out.parent).free >= 8 * 2**30 + 16 * 2**20
    manifest = json.loads(a.manifest.read_text())
    assert manifest['source']['dataset_revision'] == '27febb7b2d24169560a9f6f83d38eb53f94916cf'
    assert len(manifest['files']) == 4
    counts = {name: [[0] * 256 for _ in range(58)] for name in ('all', 'tail10')}
    decode = [[None] * 4 for _ in range(58)]; lengths = []; total = 0
    for src, item in enumerate(manifest['files']):
        source = a.source_root.resolve() / item['file']['path']; total += source.stat().st_size
        assert total < 16 * 2**20 and sha(source) == item['file']['sha256']
        raw = json.loads(source.read_text()); assert len(raw) == 129
        n = len(raw[0]['3']); lengths.append(n)
        for li, layer in enumerate(range(3, 61)):
            assert manifest['layers'][layer]['num_experts'] == 256 and manifest['layers'][layer]['top_k'] == 8
            prefill = raw[0][str(layer)]; assert len(prefill) == n and n > 0
            for rows in (prefill, *(entry[str(layer)] for entry in raw[1:])):
                for selected in rows:
                    assert len(selected) == len(set(selected)) == 8
                    assert all(type(e) is int and 0 <= e < 256 for e in selected)
            for kind, rows in (('all', prefill), ('tail10', prefill[-10:])):
                c = Counter(e for row in rows for e in row)
                counts[kind][li] = [counts[kind][li][e] + c[e] for e in range(256)]
            assert all(len(entry[str(layer)]) == 1 for entry in raw[1:])
            decode[li][src] = [entry[str(layer)][0] for entry in raw[1:]]
    assert lengths == [105, 28, 10, 6]
    out.mkdir(); (out / 'observed').mkdir(); (out / 'truth').mkdir()
    save(out / 'observed/frequencies.json', dict(schema_version='moe-prefill-counts/1',
        experts=256, ranks=4, source_layers=list(range(3, 61)), counts=counts,
        semantics='Raw activation counts across four Prefill requests; no future Decode fields'))
    save(out / 'truth/decode.json', dict(selections=decode))
    shutil.copy2(a.manifest, out / 'truth/manifest.json')
    save(out / 'truth/provenance.json', dict(manifest_sha256=sha(a.manifest), prefill_lengths=lengths,
        original_bytes=total, observed_sha256=sha(out / 'observed/frequencies.json'),
        decode_sha256=sha(out / 'truth/decode.json'), preparation_script_sha256=sha(Path(__file__)),
        source_rank_assignment='manifest file order', synchronized_launch_observed=False,
        full_r1_ep4_deployment_verified=False, network_executed=False))
    assert sum(f.stat().st_size for f in out.rglob('*') if f.is_file()) < 16 * 2**20
    print(json.dumps(dict(prepared=True, prefill_lengths=lengths, decode_token_layer_rows=29696)))


if __name__ == '__main__': main()
