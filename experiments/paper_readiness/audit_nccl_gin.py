"""Check fixed GIN/GDAKI sources, bounded mapping arithmetic, and route accounting.

No NCCL/CUDA compilation, physical QP creation, packet injection or timing model.
"""
import argparse
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import shutil
import sys
import tarfile
import time

ROOT=Path(__file__).resolve().parents[2]
COMMIT='12df1a11afad322be5a204a2db890161cbf8131d'
DOCA='src/transport/net_ib/gdaki/doca-gpunetio/include/'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def verify_sources(source):
    identity=json.loads((source/'identity.json').read_text())
    assert identity['commit']==COMMIT and identity['tag']=='v2.32.3-1' and identity['status']=='read'
    for item in identity['requests']:
        assert sha(source/item['file'])==item['sha256']
    commit=json.loads((source/'commit.json').read_text())
    tree=json.loads((source/'tree.json').read_text())
    assert commit['sha']==COMMIT and commit['tree']['sha']==tree['sha']==identity['tree']
    assert not tree['truncated']
    entries={i['path']:i for i in tree['tree'] if i['type']=='blob'}
    records=[]
    for manifest in sorted(source.glob('files-*.json')):
        batch=json.loads(manifest.read_text())
        assert batch['commit']==COMMIT
        for item in batch['sources']:
            raw=(source/'sources'/item['path']).read_bytes()
            assert item['status']=='read' and item['commit']==COMMIT
            assert len(raw)==item['bytes']==entries[item['path']]['size']
            assert hashlib.sha256(raw).hexdigest()==item['sha256']
            assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==item['git_blob_sha']==entries[item['path']]['sha']
            records.append(item)
    assert len(records)==len({r['path'] for r in records})==18
    excerpts=[]
    anchors=[
        ('docs/userguide/source/api/device_gin.rst','Ensures that all the pending transfer operations',4),
        ('docs/userguide/source/api/device_gin.rst','Since NCCL 2.30.5, there are two types of signals:',3),
        ('src/include/nccl_device/host.h','int ginContextCount; // This is a hint',7),
        ('src/include/nccl_device/host.h','true,                                        /* ginStrongSignalsRequired */',2),
        ('src/gin/gin_host.cc','devComm->ginStrongLegacySignals = reqs->ginStrongSignalsRequired;',13),
        ('src/gin/gin_host.cc','backend->ncclGin->listen(backend->ginInstance, localGinDevs[commIdx % nLocalGinDevs]',3),
        ('src/gin/gin_host.cc','if (requestedStride % connectedStride != 0)',6),
        ('src/include/nccl_device/impl/gin__funcs.h','// this->connectionId = contextIndex % comm.ginConnectionCount;',13),
        ('src/include/nccl_device/impl/gin__funcs.h','desc.isStrong = net.comm.ginStrongLegacySignals;',3),
        ('src/include/nccl_device/gin/gdaki/gin_gdaki.h','NCCL_DEVICE_INLINE uint32_t docaOptFlagsFromGinOptFlags',6),
        ('src/include/nccl_device/gin/gdaki/gin_gdaki.h','// GDAKI runs over MLX5 NICs, which flush every previously-received put',6),
        ('src/include/nccl_device/gin/gdaki/gin_gdaki.h','NCCL_DEVICE_INLINE static ncclResult_t flushImplModeCore(ncclGinCtx',46),
        ('src/transport/net_ib/gdaki/gin_host_gdaki.cc','const int nqps_per_rank = ncontexts;',8),
        ('src/transport/net_ib/gdaki/gin_host_gdaki.cc','gdaki_ctx->qp_sq_size = queueDepth > 0 ?',8),
        (DOCA+'device/doca_gpunetio_dev_verbs_qp.cuh','const uint16_t nwqes = doca_gpu_dev_verbs_load_const(&qp->sq_wqe_num);',5),
        (DOCA+'device/doca_gpunetio_dev_verbs_qp.cuh','doca_gpu_dev_verbs_atomic_add<uint64_t, resource_sharing_mode>(&qp->sq_rsvd_index, count);',5),
        (DOCA+'device/doca_gpunetio_dev_verbs_onesided.cuh','/* **************************************** SIGNAL **************************************** */',38),
        (DOCA+'common/doca_gpunetio_verbs_def.h','#define DOCA_GPUNETIO_VERBS_MAX_TRANSFER_SIZE_SHIFT 30',3),
    ]
    # Some descriptors repeat the same assignment for several legacy types.
    # Preserve all occurrences rather than guessing a preferred signal class.
    for name,needle,count in anchors:
        lines=(source/'sources'/name).read_text().splitlines()
        positions=[i for i,line in enumerate(lines) if needle in line]
        expected=4 if needle=='desc.isStrong = net.comm.ginStrongLegacySignals;' else 1
        assert len(positions)==expected,(name,needle,positions)
        for begin in positions:
            excerpts.append(dict(path=name,first_line=begin+1,last_line=begin+count,
                                 excerpt='\n'.join(lines[begin:begin+count])))
    target=json.loads((ROOT/'docs/review_artifacts/deepep_modern_20261022/sources.json').read_text())
    deepep=[]
    for name in ('csrc/kernels/comm/context.cpp','deep_ep/include/deep_ep/comm/barrier.cuh',
                 'deep_ep/include/deep_ep/comm/handle.cuh'):
        item=next(i for i in target['sources'] if i['path']==name)
        path=ROOT/'data/research/deepep_modern_20261022/sources'/name
        assert sha(path)==item['sha256']
        deepep.append(dict(path=str(path.relative_to(ROOT)),sha256=item['sha256'],commit=target['commit']))
    return dict(nccl_commit=COMMIT,nccl_tag='v2.32.3-1',sources=records,deepep_sources=deepep,excerpts=excerpts,
                library_execution=False,hardware_calibrated=False)


def mapping_probe():
    comparisons=0
    for connections in range(1,5):
        for requested in range(1,33):
            allocated=((requested+connections-1)//connections)*connections
            cells=set()
            for index in range(allocated):
                # Transcription of the fixed source's 1/2/3/4 connection arithmetic.
                connection=index%3 if connections==3 else index & (connections-1)
                local=index//3 if connections==3 else index >> (2 if connections==4 else connections-1)
                assert (connection,local)==(index%connections,index//connections)
                cells.add((connection,local));comparisons+=1
            assert len(cells)==allocated
            assert cells=={(c,l) for c in range(connections) for l in range(allocated//connections)}
    # Explicit expression examples, not target hardware configuration or benchmark.
    examples=[]
    sms,warps,active_qps,connections=2,4,5,2
    def qp(sm,warp):
        available=active_qps-1
        count=available//sms+(sm<available%sms)
        return 1+sm+(warp%count)*sms
    for tokens in (1,8):
        channels=[]
        for sm in range(sms):
            for warp in range(warps):
                indices=list(range(warp*sms+sm,tokens,warps*sms))
                if indices:
                    index=qp(sm,warp)
                    channels.append(dict(sm=sm,warp=warp,tokens=indices,gin_context_index=index,
                                         connection=index%connections,local_context=index//connections))
        assert sorted(t for c in channels for t in c['tokens'])==list(range(tokens))
        examples.append(dict(tokens_per_rank=tokens,channels=channels,control_context_index=0,
                             control_connection=0,active_data_connections=sorted({c['connection'] for c in channels})))
    assert examples[0]['active_data_connections']==[1]
    assert examples[1]['active_data_connections']==[0,1]
    return dict(scope='source-expression arithmetic only; no CUDA or GIN execution',
        connection_counts_checked=[1,2,3,4],requested_contexts_checked=[1,32],context_index_comparisons=comparisons,
        examples=examples,example_parameters=dict(sms=sms,dispatch_warps=warps,active_call_qps=active_qps,
            allocated_contexts=6,connections=connections,local_gin_devices=['declared_nic0','declared_nic1']),
        measured=False,packets=False)


def verify_routes(run):
    summary=json.loads((run/'summary.json').read_text())
    execution=json.loads((run/'execution.json').read_text())
    assert sha(run/'summary.json')==execution['summary_sha256']
    assert sha(run/'windows.csv')==execution['windows_sha256']
    with (run/'windows.csv').open(newline='') as f:
        rows=list(csv.DictReader(f))
    assert len(rows)==7424
    assert {(int(r['source_entry']),int(r['source_layer'])) for r in rows}=={(e,l) for e in range(1,129) for l in range(3,61)}
    assert sum(int(r['old_remote_contributions']) for r in rows)==summary['old_per_expert_remote_dispatch_contributions']
    assert sum(int(r['direct_remote_token_copies']) for r in rows)==summary['direct_remote_dispatch_token_copies']
    return dict(window_rows=len(rows),summary_sha256=sha(run/'summary.json'),windows_sha256=sha(run/'windows.csv'),
                scope='CSV/summary integrity and complete entry/layer coverage; per-entry independent matrices checked in producer')


def main():
    resource.setrlimit(resource.RLIMIT_AS,(2*2**30,2*2**30))
    resource.setrlimit(resource.RLIMIT_CPU,(60,60))
    started=time.monotonic()
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-dir',type=Path,required=True)
    p.add_argument('--routes',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--checkpoint',type=Path,required=True)
    a=p.parse_args()
    source,run,out,target=(p.resolve() for p in (a.source_dir,a.routes,a.output,a.checkpoint))
    assert not out.exists() and not target.exists()
    assert shutil.disk_usage(target.parent).free>=8*2**30+8*2**20
    evidence,probe,route_check=verify_sources(source),mapping_probe(),verify_routes(run)
    out.mkdir(parents=True)
    save(out/'sources.json',evidence);save(out/'mapping.json',probe)
    for name in ('summary.json','provenance.json','execution.json','windows.csv'):
        shutil.copy2(run/name,out/('routes-'+name))
    save(out/'collection.json',dict(identity=json.loads((source/'identity.json').read_text()),
        batches=[json.loads(p.read_text()) for p in sorted(source.glob('files-*.json'))]))
    module_path=ROOT/'docs/review_artifacts/paper_readiness_20261022/build_review.py'
    spec=importlib.util.spec_from_file_location('renderer',module_path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module.HERE=out
    doc=ROOT/'docs/reviews/NCCL_GIN资源语义与真实路由网络偏斜_20261022.md'
    module.render(doc,out/'report.html','NCCL GIN资源语义与真实路由网络偏斜')
    previous=os.path.relpath(ROOT/'docs/review_artifacts/deepep_modern_20261022/report.html',out)
    raw,n=re.subn(r'<nav>.*?</nav>','<nav><a href="sources.json">固定源码证据</a> · '
        '<a href="mapping.json">连接映射核查</a> · <a href="routes-summary.json">全解码核算</a> · '
        f'<a href="{previous}">前阶段报告</a></nav>',(out/'report.html').read_text(),count=1)
    assert n==1;(out/'report.html').write_text(raw)
    from html.parser import HTMLParser
    from urllib.parse import urlsplit,unquote
    class Links(HTMLParser):
        def __init__(self):
            super().__init__();self.targets=[]
        def handle_starttag(self,tag,attrs):
            for k,v in attrs:
                if k in ('href','src'):
                    url=urlsplit(v)
                    if not url.scheme and url.path:
                        self.targets.append(out/unquote(url.path))
    links=Links();links.feed(raw)
    for path in links.targets:
        if path not in (out/'verification.json',out/'archive.json'):
            assert path.exists(),path
    save(out/'verification.json',dict(scope='fixed source integrity, expression arithmetic, route operand accounting',
        nccl_source_blobs=18,deepep_source_files=3,source_excerpts=len(evidence['excerpts']),
        context_index_comparisons=probe['context_index_comparisons'],route_check=route_check,
        local_html_links=len(links.targets),source_sha256=sha(out/'sources.json'),mapping_sha256=sha(out/'mapping.json'),
        report_sha256=sha(out/'report.html'),browser_visual_acceptance=False,packet_execution=False,
        measured_hardware=False,paper_goal_complete=False))
    usage=resource.getrusage(resource.RUSAGE_SELF)
    assert usage.ru_maxrss<=2**20 and time.monotonic()-started<60
    save(out/'execution.json',dict(argv=sys.argv,cwd=str(Path.cwd()),state='completed_offline_checks',
        wall_seconds_before_archive=time.monotonic()-started,peak_rss_kib=usage.ru_maxrss,
        budgets=dict(wall_seconds=60,rss_bytes=2**30,address_bytes=2*2**30,archive_raw_bytes=8*2**20),
        prior_preflight=dict(status='failed_then_corrected',
            reason='peer loop locator occurred in more than one function; replaced by unique flushImplModeCore locator',
            network_retry=False,budget_increase=False),network_execution=False))
    files={}
    for folder in (source,out):
        for path in folder.rglob('*'):
            if path.is_file():files[str(path.relative_to(ROOT))]=path
    for item in evidence['deepep_sources']:
        path=ROOT/item['path'];files[item['path']]=path
    for path in (doc,Path(__file__).resolve(),ROOT/'experiments/paper_readiness/collect_nccl_gin.py',
                 ROOT/'experiments/paper_readiness/direct_route_accounting.py',
                 ROOT/'experiments/paper_readiness/modern_backend_contract.md'):
        files[str(path.relative_to(ROOT))]=path
    assert sum(p.stat().st_size for p in files.values())<=8*2**20
    target.mkdir();archive=target/'checkpoint.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for name,path in sorted(files.items()):tar.add(path,arcname=name,recursive=False)
    with tarfile.open(archive,'r:gz') as tar:
        assert set(tar.getnames())==set(files)
        for name,path in files.items():
            assert hashlib.sha256(tar.extractfile(name).read()).hexdigest()==sha(path)
    save(out/'archive.json',dict(path=str(archive.relative_to(ROOT)),sha256=sha(archive),bytes=archive.stat().st_size,
        verified_files=len(files),files={n:dict(bytes=p.stat().st_size,sha256=sha(p)) for n,p in sorted(files.items())},
        scope='fixed source and offline accounting; raw routes remain in prior four-request checkpoint'))
    for path in links.targets:assert path.exists(),path
    print(json.dumps(dict(nccl_source_files=18,source_excerpts=len(evidence['excerpts']),
        mapping_comparisons=probe['context_index_comparisons'],route_windows=route_check['window_rows'],
        checkpoint_bytes=archive.stat().st_size,hardware_measured=False),ensure_ascii=False))


if __name__=='__main__':
    main()
