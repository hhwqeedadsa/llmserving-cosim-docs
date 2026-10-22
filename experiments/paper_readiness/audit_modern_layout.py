"""Fixed-source layout and dependency audit; no CUDA compilation or timing."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import shutil

ROOT = Path(__file__).resolve().parents[2]
COMMIT = 'f6aa38fe06cd4a0489e7730365d4e0114970a2f3'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    resource.setrlimit(resource.RLIMIT_AS, (2*2**30, 2*2**30))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-dir', required=True, type=Path)
    p.add_argument('--base-source-dir', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    out = a.output.resolve()
    assert not out.exists() and shutil.disk_usage(out.parent).free >= 8*2**30+2*2**20
    identity = json.loads((a.source_dir/'tree-source.json').read_text())
    assert identity['commit'] == COMMIT and identity['status'] == 'read'
    tree_raw = (a.source_dir/'tree.json').read_bytes()
    assert sha(tree_raw) == identity['requests'][1]['sha256']
    tree = json.loads(tree_raw)
    assert tree['sha'] == COMMIT and not tree['truncated']
    entries = {r['path']: r for r in tree['tree'] if r['type'] == 'blob'}
    records, texts = [], {}
    supplemental = json.loads((a.source_dir/'file-sources.json').read_text())
    assert len(supplemental['sources']) == 6
    base = json.loads((ROOT/'docs/review_artifacts/deepep_modern_20261022/sources.json').read_text())
    assert base['commit'] == COMMIT
    wanted = ['deep_ep/include/deep_ep/layout/ep/token.cuh',
              'deep_ep/include/deep_ep/impls/ep/dispatch.cuh',
              'deep_ep/include/deep_ep/impls/ep/combine.cuh',
              'deep_ep/include/deep_ep/comm/barrier.cuh', 'csrc/buffers/ep.hpp']
    pairs = [(a.source_dir, r) for r in supplemental['sources']]
    pairs += [(a.base_source_dir, next(r for r in base['sources'] if r['path'] == name)) for name in wanted]
    for folder, record in pairs:
        name = record['path'];raw = (folder/'sources'/name).read_bytes()
        assert record['status'] == 'read' and record['commit'] == COMMIT
        assert sha(raw) == record['sha256'] and len(raw) == entries[name]['size']
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() == entries[name]['sha']
        records.append(record);texts[name] = raw.decode()
    anchors = [
        ('deep_ep/include/deep_ep/common/compiled.cuh', 'constexpr int kNumTMAAlignmentBytes = 32;', 4),
        ('deep_ep/include/deep_ep/common/compiled.cuh', 'constexpr int kDefaultQPDepth = 1024;', 1),
        ('deep_ep/include/deep_ep/layout/ep/token.cuh', 'num_metadata_bytes(num_topk *', 3),
        ('deep_ep/include/deep_ep/layout/ep/token.cuh', 'const auto num_data_bytes = math::align<int>(', 6),
        ('deep_ep/include/deep_ep/impls/ep/dispatch.cuh', 'stored_dst_slot_idx = atomicAdd(', 1),
        ('deep_ep/include/deep_ep/impls/ep/dispatch.cuh', 'const auto token_start = dispatch_warp_idx', 2),
        ('deep_ep/include/deep_ep/impls/ep/dispatch.cuh', 'gin.put_value<team_t>(dst_rank_counter', 2),
        ('deep_ep/include/deep_ep/impls/ep/combine.cuh', 'int num_tokens_per_warp = math::ceil_div(', 3),
        ('deep_ep/include/deep_ep/impls/ep/combine.cuh', 'const int src_token_idx = __ldg(', 4),
        ('deep_ep/include/deep_ep/impls/ep/combine_utils.cuh', 'constexpr bool use_rank_layout()', 5),
        ('deep_ep/include/deep_ep/common/math.cuh', 'return -value - static_cast<dtype_t>(1);', 1),
    ]
    excerpts = []
    for name, needle, length in anchors:
        lines = texts[name].splitlines();positions = [i for i,s in enumerate(lines) if needle in s]
        assert len(positions) == 1, (name, needle, positions)
        i = positions[0]
        excerpts.append(dict(path=name, first_line=i+1, excerpt='\n'.join(lines[i:i+length])))
    # Integer layout formulas vs an independent 32-byte-block packing oracle.
    comparisons = 0
    for hidden in (256, 1024, 2048, 7168, 8192):
        for topk in range(1, 33):
            for metadata in (False, True):
                nmeta = 8*topk + (4+4*topk if metadata else 0)
                exact = ((2*hidden+((nmeta+31)//32)*32+63)//64)*64
                blocks = 2*hidden//32 + sum(1 for _ in range(0, nmeta, 32))
                packed = (blocks + blocks%2)*32
                assert exact == packed;comparisons += 1
    def size(h, k, metadata):
        nmeta = 8*k+(4+4*k if metadata else 0)
        return ((2*h+((nmeta+31)//32)*32+63)//64)*64
    evidence = dict(commit=COMMIT, sources=records, excerpts=excerpts,
        collection=supplemental, cached_tree_origin=json.loads((a.source_dir/'cache-origin.json').read_text()),
        layout_comparisons=comparisons,
        r1_bf16_top8=dict(hidden_bytes=14336, dispatch_metadata_bytes=100,
            dispatch_put_bytes=size(7168,8,True), combine_metadata_bytes=64,
            combine_put_bytes=size(7168,8,False)),
        kernel_execution=False, packet_execution=False, measured=False,
        conclusions=['Dispatch source-local per-destination slot allocation is concurrent, not token order',
            'Combine channel assignment depends on Dispatch slot order and received rank prefix sums',
            'Top-k input may be 64-bit but communication TokenLayout indices are 32-bit',
            'Default QP depth 1024 is source configuration, not measured runtime depth'])
    out.mkdir()
    (out/'sources.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    for folder, record in pairs:
        dest = out/'snapshot'/record['path'];dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(folder/'sources'/record['path'], dest)
    assert sum(p.stat().st_size for p in out.rglob('*') if p.is_file()) <= 2*2**20
    print(json.dumps(dict(source_files=len(records),layout_comparisons=comparisons,
                         r1_bf16_top8=evidence['r1_bf16_top8'],measured=False)))


if __name__ == '__main__':
    main()
