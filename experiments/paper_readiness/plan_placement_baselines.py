"""Choose three fixed equal-capacity placements from observed Prefill counts only."""
import argparse
import hashlib
import heapq
import json
from pathlib import Path
import resource
import shutil


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--observed', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); resource.setrlimit(resource.RLIMIT_AS, (2 * 2**30, 2 * 2**30))
    assert not a.output.exists() and a.output.parent.is_dir() and shutil.disk_usage(a.output.parent).free >= 8 * 2**30 + 16 * 2**20
    content = (a.observed / 'frequencies.json').read_bytes(); data = json.loads(content)
    assert data['schema_version'] == 'moe-prefill-counts/1'
    assert (data['experts'], data['ranks'], data['source_layers']) == (256, 4, list(range(3, 61)))
    placements = dict(contiguous=[[e // 64 for e in range(256)] for _ in range(58)])
    for mode in ('all', 'tail10'):
        mapping = []
        assert len(data['counts'][mode]) == 58
        for counts in data['counts'][mode]:
            assert len(counts) == 256 and all(type(v) is int and v >= 0 for v in counts)
            order = sorted(range(256), key=lambda e: (-counts[e], e))
            heap = [(0, rank, 0) for rank in range(4)]; owners = [None] * 256
            for expert in order:
                load, rank, assigned = heapq.heappop(heap); owners[expert] = rank
                if assigned + 1 < 64:
                    heapq.heappush(heap, (load + counts[expert], rank, assigned + 1))
            assert [owners.count(r) for r in range(4)] == [64] * 4
            mapping.append(owners)
        placements['prefill_' + mode + '_lpt'] = mapping
    a.output.mkdir()
    (a.output / 'placements.json').write_text(json.dumps(dict(schema_version='moe-prefill-placements/1',
        observed_sha256=hashlib.sha256(content).hexdigest(), placements=placements,
        method='Artifact count-based LPT adapted to declared E256/EP4; not roofline/GPU execution',
        reads_decode_or_truth=False), ensure_ascii=False, separators=(',', ':')) + '\n')
    print(json.dumps(dict(placements=list(placements), reads_decode_or_truth=False)))


if __name__ == '__main__': main()
