"""Verify pinned modern sources and account for selected real-route Dispatch calls.

Counts source-level calls and hidden tensor bytes under an explicit direct/RDMA
interpretation. Does not compile the library, model GIN packets, or predict time.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tarfile

ROOT = Path(__file__).resolve().parents[2]
COMMIT = 'f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
PREFIX = 'deep_ep/include/deep_ep/'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def sources(source):
    identity = json.loads((source/'tree-source.json').read_text())
    assert identity['commit'] == COMMIT and identity['status'] == 'read'
    assert sha(source/'tree.json') == identity['requests'][1]['sha256']
    assert sha(source/'ref.json') == identity['requests'][0]['sha256']
    tree = json.loads((source/'tree.json').read_text())
    assert tree['sha'] == COMMIT and not tree['truncated']
    entries = {i['path']:i for i in tree['tree'] if i['type']=='blob'}
    result = []
    for manifest in sorted(source.glob('file-sources*.json')):
        collection = json.loads(manifest.read_text())
        assert collection['commit'] == COMMIT
        for item in collection['sources']:
            raw = (source/'sources'/item['path']).read_bytes()
            assert item['status'] == 'read' and item['commit'] == COMMIT
            assert len(raw) == item['bytes'] == entries[item['path']]['size']
            assert hashlib.sha256(raw).hexdigest() == item['sha256']
            blob = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
            assert blob == item['git_blob_sha'] == entries[item['path']]['sha']
            result.append(item)
    assert len(result) == len({i['path'] for i in result}) == 12
    excerpts = []
    anchors = [
        ('README.md','- NVIDIA Hopper or newer GPUs',6),
        ('csrc/kernels/comm/context.cpp','std::tuple<int, int> get_logical_domain_size',5),
        ('csrc/kernels/comm/context.cpp','(allow_hybrid_mode ? props.railedGinType',20),
        ('csrc/kernels/comm/context.cpp','// Calculate scaleout/up domain size',10),
        (PREFIX+'comm/handle.cuh','// TODO(NCCL): QP index should just be a hint',10),
        (PREFIX+'comm/handle.cuh','// NOTES: signals are rolling counters',8),
        (PREFIX+'comm/barrier.cuh','std::pair<int, ncclGinResourceSharingMode> get_qp_mode(',33),
        (PREFIX+'comm/barrier.cuh','// Flush all QPs by all SMs',16),
        (PREFIX+'comm/barrier.cuh','// Use QP 0 to do barrier:',6),
        (PREFIX+'impls/ep/dispatch.cuh','// Issue scaleup rank count writes to peers',29),
        (PREFIX+'impls/ep/dispatch.cuh','// Deduplicate ranks and assign slots',18),
        (PREFIX+'impls/ep/dispatch.cuh','// Barrier to ensure data arrival',5),
        (PREFIX+'impls/ep/combine.cuh','// Full barrier to ensure the remote buffer is available',6),
        (PREFIX+'impls/ep/combine.cuh','// Final barrier to ensure data arrival',5),
        (PREFIX+'impls/ep/combine.cuh','// Do local reduction',6),
        (PREFIX+'impls/ep/hybrid_dispatch.cuh','// Issue scale-out tail update',8),
        (PREFIX+'impls/ep/hybrid_dispatch.cuh','// Forward tokens from scale-out ranks',5),
        (PREFIX+'layout/ep/token.cuh','num_metadata_bytes(num_topk *',4),
    ]
    for name, needle, count in anchors:
        lines = (source/'sources'/name).read_text().splitlines()
        positions = [i for i,line in enumerate(lines) if needle in line]
        assert len(positions) == 1, (name, needle)
        begin = positions[0]
        excerpts.append(dict(path=name,first_line=begin+1,last_line=begin+count,
                             excerpt='\n'.join(lines[begin:begin+count])))
    # Kernel-internal barrier inventory; excludes host/epilogue/other kernels.
    for name in ('dispatch','combine'):
        raw = (source/'sources'/PREFIX/f'impls/ep/{name}.cuh').read_text()
        assert raw.count('comm::gpu_barrier<') == 2
    return dict(commit=COMMIT, sources=result, excerpts=excerpts,
                nccl_implementation_audited=False, library_execution=False)


def account():
    base = ROOT/'docs/review_artifacts/deepep_operations_20261022'
    config = json.loads((base/'input.json').read_text())
    provenance = json.loads((base/'provenance.json').read_text())
    previous = json.loads((base/'summary.json').read_text())
    assert sha(base/'input.json') == previous['input_sha256']
    manifest_path = ROOT/provenance['manifest']['path']
    assert sha(manifest_path) == provenance['manifest']['sha256']
    manifest = json.loads(manifest_path.read_text())
    assert manifest['source']['dataset_revision'] == '27febb7b2d24169560a9f6f83d38eb53f94916cf'
    raw_sources = []
    for item in manifest['files']:
        path = ROOT/item['file']['path']
        assert sha(path) == item['file']['sha256']
        raw_sources.append(json.loads(path.read_text()))
    assert len(raw_sources) == 4
    for selection in provenance['selections']:
        choices = raw_sources[selection['rank']][selection['source_entry']][str(selection['source_layer_id'])][0]
        assert choices == selection['expert_ids']
        assert config['calls'][selection['call']]['tokens'][selection['rank']][0] == choices
    ranks = config['execution']['ranks']
    experts, hidden = config['model']['experts'], config['model']['hidden_size']
    assert (ranks, experts, hidden, config['model']['top_k']) == (4,256,7168,8)
    rows, dedup, contributions = [], Counter(), Counter()
    for ci, call in enumerate(config['calls']):
        for src, tokens in enumerate(call['tokens']):
            for token, choices in enumerate(tokens):
                owners = Counter(e//(experts//ranks) for e in choices)
                for dst, count in sorted(owners.items()):
                    if src == dst:
                        continue
                    rows.append(dict(call=ci,src=src,token=token,dst=dst,expert_contributions=count))
                    contributions[ci] += count
                    dedup[ci] += 1
    # Separate enumeration scans the destination expert ranges, not owner sets.
    independent = 0
    for call in config['calls']:
        for dst in range(ranks):
            for src, tokens in enumerate(call['tokens']):
                if dst != src:
                    for choices in tokens:
                        independent += any(dst*(experts//ranks) <= e < (dst+1)*(experts//ranks) for e in choices)
    assert independent == len(rows) == sum(dedup.values())
    assert sum(contributions.values())*2 == previous['remote_operations']['data']
    calls = len(config['calls'])
    off_rank_pairs = calls*ranks*(ranks-1)
    result = dict(
        scope='conditional source-call accounting: fresh routing, direct mode, all off-rank destinations RDMA; no execution',
        source_identity_files_verified=4,selected_raw_rows_verified=len(provenance['selections']),
        invocation_count=calls, ranks=ranks, experts=experts, hidden=hidden,
        remote_expert_contributions=sum(contributions.values()),
        direct_dispatch_distinct_token_destination_puts=len(rows),
        independent_destination_range_count=independent,
        dispatch_hidden_tensor_bytes_per_contribution_model=sum(contributions.values())*hidden*2,
        dispatch_hidden_tensor_bytes_per_deduplicated_model=len(rows)*hidden*2,
        hidden_bytes_scope='BF16 hidden elements only; excludes metadata, alignment, counters, barriers, protocol, local/NVLink traffic',
        per_call=[dict(call=i,remote_contributions=contributions[i],distinct_token_destination_puts=dedup[i]) for i in range(calls)],
        dispatch_off_rank_rank_count_put_value_calls=off_rank_pairs,
        dispatch_off_rank_expert_count_bulk_put_calls=off_rank_pairs,
        dispatch_count_call_operand_bytes=off_rank_pairs*(8+(experts//ranks)*8),
        dispatch_count_scope='rank int64 plus E/ranks int64 expert block; no barrier or GIN aggregation/wire inference',
        selected_core_kernel_barriers=calls*2*2,
        selected_core_kernel_off_rank_signal_api_calls=calls*2*2*ranks*(ranks-1),
        barriers_scope='two internal barriers per direct Dispatch/Combine kernel; not a full host/epilogue/NCCL inventory',
        rows=rows, packets_inferred=False, latency_predicted=False, hardware_compatible_verified=False)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-dir',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--checkpoint',required=True,type=Path)
    a = p.parse_args()
    source, out, checkpoint = a.source_dir.resolve(),a.output.resolve(),a.checkpoint.resolve()
    assert not out.exists() and not checkpoint.exists()
    assert shutil.disk_usage(checkpoint.parent).free >= 8*2**30+4*2**20
    evidence, counts = sources(source),account()
    out.mkdir(parents=True)
    save(out/'sources.json',evidence)
    save(out/'accounting.json',counts)
    save(out/'collection.json',dict(tree=json.loads((source/'tree-source.json').read_text()),
        batches=[json.loads(p.read_text()) for p in sorted(source.glob('file-sources*.json'))]))
    renderer = ROOT/'docs/review_artifacts/paper_readiness_20261022/build_review.py'
    spec = importlib.util.spec_from_file_location('renderer',renderer)
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.HERE = out
    module.render(ROOT/'docs/reviews/现代DeepEP与ScaleOut后端实现约束_20261022.md',out/'report.html',
                  '现代DeepEP与Scale-Out后端实现约束')
    # Existing renderer nav belongs to the previous study; replace explicitly.
    import re
    previous_report = os.path.relpath(ROOT/'docs/review_artifacts/deepep_operations_20261022/report.html',out)
    raw, changed = re.subn(r'<nav>.*?</nav>', '<nav><a href="sources.json">固定源码证据</a> · '
        '<a href="accounting.json">真实选择核算</a> · '
        f'<a href="{previous_report}">旧版操作验证</a></nav>',
        (out/'report.html').read_text(),count=1)
    assert changed == 1
    (out/'report.html').write_text(raw)
    from html.parser import HTMLParser
    from urllib.parse import urlsplit, unquote
    class Links(HTMLParser):
        links = []
        def handle_starttag(self, tag, attrs):
            self.links.extend(v for k,v in attrs if k in ('href','src'))
    parser = Links();parser.feed(raw)
    local_targets = []
    for link in parser.links:
        parsed = urlsplit(link)
        if not parsed.scheme and parsed.path:
            local_targets.append((link,out/unquote(parsed.path)))
    for link,path in local_targets:
        if path not in (out/'verification.json',out/'archive.json'):
            assert path.exists(),link
    save(out/'verification.json',dict(commit=COMMIT,source_blobs_verified=12,excerpts=len(evidence['excerpts']),
        raw_trace_files_verified=4,independent_dispatch_count=counts['independent_destination_range_count'],
        local_html_links=len(local_targets),browser_visual_acceptance=False,packet_execution=False,
        source_call_accounting_only=True,nccl_provider_audited=False,hardware_calibrated=False,
        sources_sha256=sha(out/'sources.json'),accounting_sha256=sha(out/'accounting.json'),
        report_sha256=sha(out/'report.html'),paper_goal_complete=False))
    files = {}
    for folder in (source,out):
        for path in folder.rglob('*'):
            if path.is_file():
                files[str(path.relative_to(ROOT))] = path
    for path in [ROOT/'experiments/paper_readiness/collect_deepep_modern.py',Path(__file__).resolve(),
                 ROOT/'docs/reviews/现代DeepEP与ScaleOut后端实现约束_20261022.md']:
        files[str(path.relative_to(ROOT))] = path
    assert sum(p.stat().st_size for p in files.values()) <= 4*2**20
    checkpoint.mkdir()
    archive = checkpoint/'checkpoint.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for name,path in sorted(files.items()):
            tar.add(path,arcname=name,recursive=False)
    with tarfile.open(archive,'r:gz') as tar:
        assert set(tar.getnames()) == set(files)
        for name,path in files.items():
            assert hashlib.sha256(tar.extractfile(name).read()).hexdigest() == sha(path)
    save(out/'archive.json',dict(path=str(archive.relative_to(ROOT)),bytes=archive.stat().st_size,
        sha256=sha(archive),verified_files=len(files),
        files={n:dict(bytes=p.stat().st_size,sha256=sha(p)) for n,p in sorted(files.items())},
        source_scope='modern fixed source and audit only; original real routes in prior operation checkpoint'))
    # Check self-links only after verification and archive records exist.
    for link,path in local_targets:
        assert path.exists(),link
    print(json.dumps({k:v for k,v in counts.items() if k!='rows'},ensure_ascii=False))


if __name__ == '__main__':
    main()
