"""Pinned invocation/readiness audit and conditional channel accounting, no GPU/network."""
import argparse
import ast
from collections import Counter
from itertools import product
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[2]
DEEP='f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
NCCL='12df1a11afad322be5a204a2db890161cbf8131d'


def load(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def align(v,a):return (v+a-1)//a*a


def prepare(a):
    a.sources=a.sources.resolve()
    assert not a.output.exists();a.output.mkdir();obs=a.output/'observed';obs.mkdir();(a.output/'truth').mkdir()
    deep_tree=ROOT/'data/research/deepep_modern_20261022/tree.json'
    nccl_tree=ROOT/'data/research/nccl_gin_20261022/tree.json'
    trees={DEEP:load(deep_tree),NCCL:load(nccl_tree)}
    assert trees[DEEP]['sha']==DEEP and not any(t['truncated'] for t in trees.values())
    nccl_commit=load(nccl_tree.with_name('commit.json'))
    assert nccl_commit['sha']==NCCL and nccl_commit['tree']['sha']==trees[NCCL]['sha']
    new=load(a.sources/'sources.json');assert new['complete'] and new['commit']==DEEP and new['executed_downloaded_code'] is False
    selections=[(a.sources,DEEP,r['path']) for r in new['sources']]
    selections += [(ROOT/'data/research/deepep_modern_20261022',DEEP,p) for p in (
        'csrc/buffers/ep.hpp','csrc/kernels/comm/context.cpp','deep_ep/include/deep_ep/comm/handle.cuh',
        'deep_ep/include/deep_ep/comm/barrier.cuh','deep_ep/include/deep_ep/impls/ep/dispatch.cuh',
        'deep_ep/include/deep_ep/impls/ep/combine.cuh','deep_ep/include/deep_ep/layout/ep/token.cuh','README.md')]
    selections += [(ROOT/'data/research/deepep_layout_20261022',DEEP,'deep_ep/include/deep_ep/common/compiled.cuh')]
    selections += [(ROOT/'data/research/nccl_gin_20261022',NCCL,p) for p in (
        'src/gin/gin_host.cc','src/transport/net_ib/gdaki/gin_host_gdaki.cc',
        'src/include/nccl_device/impl/gin__funcs.h','src/include/nccl_device/gin/gdaki/gin_gdaki.h')]
    records=[];texts={}
    for cache,commit,name in selections:
        path=cache/'sources'/name;raw=path.read_bytes()
        blob=next(r for r in trees[commit]['tree'] if r['path']==name)
        identity=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        assert identity==blob['sha'] and len(raw)==blob['size']
        dest=obs/'sources'/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw);texts[name]=raw.decode()
        records.append(dict(path=name,cache_path=str(path.relative_to(ROOT)),commit=commit,git_blob_sha=identity,sha256=sha(path),bytes=len(raw),
            public_url=f'https://github.com/{"deepseek-ai/DeepEP" if commit==DEEP else "NVIDIA/nccl"}/blob/{commit}/{name}'))
    anchors=[
        ('deep_ep/buffers/ep.py','if num_allocated_qps == 0:',9),
        ('deep_ep/buffers/ep.py','num_qps = min(num_sms, 8 + 1)',8),
        ('deep_ep/buffers/ep.py','num_sms = handle.num_sms if num_sms == 0',3),
        ('deep_ep/buffers/ep.py','if self.num_scaleout_ranks > 1 and (rdma_traffic / rdma_gbs)',18),
        ('deep_ep/buffers/ep.py','self.num_scaleout_ranks, self.num_scaleup_ranks =',6),
        ('csrc/buffers/ep.hpp','context = std::make_shared<comm::Context>',5),
        ('csrc/buffers/ep.hpp','if (previous_event.has_value()) {',5),
        ('csrc/buffers/ep.hpp','if (async_with_compute_stream) {',15),
        ('csrc/kernels/comm/context.cpp','reqs.ginContextCount = num_allocated_qps;',5),
        ('csrc/kernels/comm/context.cpp','void Context::finalize()',28),
        ('csrc/kernels/ep/dispatch.hpp','constexpr int kNumNotifyWarps = 4;',5),
        ('csrc/kernels/ep/dispatch.hpp','num_dispatch_warps = std::min<int>(',3),
        ('csrc/comm/stream.hpp','static void stream_wait(const at::cuda::CUDAStream& stream, const at::cuda::CUDAStream& event_stream)',5),
        ('csrc/utils/event.hpp','void current_stream_wait() const',1),
        ('deep_ep/utils/event.py','def current_stream_wait(self, release_handle',24),
        ('deep_ep/include/deep_ep/comm/barrier.cuh','// Only one QP',5),
        ('deep_ep/include/deep_ep/comm/barrier.cuh','// The notify warp always use 1 SM and 1 QP',4),
        ('deep_ep/include/deep_ep/comm/barrier.cuh','// All SMs share all QPs',4),
        ('deep_ep/include/deep_ep/comm/barrier.cuh','// Flush all QPs by all SMs',17),
        ('deep_ep/include/deep_ep/comm/barrier.cuh','// Use QP 0 to do barrier',7),
        ('deep_ep/include/deep_ep/impls/ep/dispatch.cuh','const auto token_start = dispatch_warp_idx',3),
        ('deep_ep/include/deep_ep/impls/ep/dispatch.cuh','// Barrier to ensure data arrival',5),
        ('deep_ep/include/deep_ep/impls/ep/combine.cuh','// Final barrier to ensure data arrival',5),
        ('src/include/nccl_device/impl/gin__funcs.h','gin->nConnections = comm.ginConnectionCount;',15),
        ('src/gin/gin_host.cc','nContextsTotal = ROUNDUP(nContextsTotal, backend->ginCommCount);',4),
        ('src/transport/net_ib/gdaki/gin_host_gdaki.cc','const int nqps_per_rank = ncontexts;',3),
        ('src/include/nccl_device/gin/gdaki/gin_gdaki.h','uint64_t ticket = doca_gpu_dev_verbs_atomic_read',10),
        ('README.md','Training, inference prefilling, and inference decoding use the same',1),
        ('README.md','if _buffer is not None and _buffer.group == group',3),
    ]
    excerpts=[]
    for name,needle,length in anchors:
        lines=texts[name].splitlines();positions=[i for i,line in enumerate(lines) if needle in line]
        assert len(positions)==1,(name,needle,positions)
        i=positions[0];excerpts.append(dict(path=name,first_line=i+1,text='\n'.join(lines[i:i+length])))
    node=ast.parse(texts['deep_ep/buffers/ep.py']);cls=next(c for c in node.body if isinstance(c,ast.ClassDef) and c.name=='EPBuffer')
    defaults={}
    for method in cls.body:
        if isinstance(method,ast.FunctionDef) and method.name in ('__init__','dispatch','combine'):
            pairs=zip(method.args.args[-len(method.args.defaults):],method.args.defaults)
            defaults[method.name]={k.arg:ast.literal_eval(v) for k,v in pairs if k.arg in (
                'num_allocated_qps','allow_hybrid_mode','prefer_overlap_with_compute','num_sms','num_qps','async_with_compute_stream')}
    save(obs/'sources.json',dict(sources=records,excerpts=excerpts,api_defaults=defaults,downloaded_code_executed=False))
    routes=[];manifest=load(a.manifest)
    for src,item in enumerate(manifest['files']):
        path=ROOT/item['file']['path'];assert sha(path)==item['file']['sha256']
        choices=load(path)[0]['4'][:32];routes.append(dict(src=src,choices=choices))
    assert [len(r['choices']) for r in routes]==[32,28,10,6]
    save(obs/'routes.json',dict(routes=routes,entry=0,layer=4,token_cap=32,independent_requests=False))
    save(a.output/'truth/manifest.json',manifest)
    save(obs/'conditions.json',dict(cases=[dict(num_sms=sm,shared_memory_bytes=mem) for sm,mem in product((4,8,16),(65536,131072))],
        connections=[1,2,4],logical_scaleout_ranks=1,physical_nvlink_ranks=1,physical_rdma_ranks=4,
        hidden=7168,topk=8,experts=256,fresh=True,allow_hybrid_mode=False,synthetic_shared_memory_budget=True))
    shutil.copyfile(Path(__file__).with_name('invocation_semantics_protocol.md'),a.output/'protocol.md')
    print(json.dumps(dict(prepared=True,sources=len(records),excerpts=len(excerpts),api_defaults=defaults)))


def analyze(a):
    assert not a.output.exists()
    conditions=load(a.observed/'conditions.json');routes=load(a.observed/'routes.json')['routes'];cases=[]
    token_bytes=align(2*7168+align(12*8+4,32),64);shared_token_bytes=token_bytes+32
    notify_bytes=align(4+256,128)*4
    for c in conditions['cases']:
        sm=c['num_sms'];warps=min((c['shared_memory_bytes']-notify_bytes)//shared_token_bytes,28)
        qps=min(sm,9,17);available=qps-1;tasks=[]
        for row in routes:
            src=row['src']
            for ti,chosen in enumerate(row['choices']):
                channel=ti%(sm*warps);si=channel%sm;warp=channel//sm
                if sm<=available:
                    local=available//sm+int(si<available%sm)
                    qp=1+si+(warp%local)*sm
                else:qp=1+(si*warps+warp)%available
                for dst in sorted({e//64 for e in chosen}-{src}):
                    tasks.append(dict(src=src,token=ti,dst=dst,sm=si,warp=warp,context_index=qp,bytes=token_bytes))
        distribution=Counter((t['src'],t['dst'],t['context_index']) for t in tasks)
        assert len(tasks)==180 and sum(t['bytes'] for t in tasks)==2603520
        projections=[]
        for n in conditions['connections']:
            projections.append(dict(connections=n,allocated_contexts=align(17,n),
                active_data_qps=len({(t['src'],t['dst'],t['context_index']%n,t['context_index']//n) for t in tasks}),
                per_connection_writes={str(i):sum(t['context_index']%n==i for t in tasks) for i in range(n)}))
        cases.append(dict(**c,num_notify_warps=4,num_dispatch_warps=warps,requested_contexts=17,call_contexts=qps,
            active_data_context_indices=sorted({t['context_index'] for t in tasks}),
            active_sender_peer_contexts=len(distribution),maximum_writes_per_sender_peer_context=max(distribution.values()),
            writes=180,payload_bytes=2603520,source_tasks=tasks,connection_projections=projections,
            actual_gpu_or_provider_execution=False,token_put_bytes=token_bytes))
    a.output.mkdir();save(a.output/'summary.json',dict(cases=cases,notify_shared_memory_bytes=notify_bytes,
        dispatch_shared_token_bytes=shared_token_bytes,api_defaults=load(a.observed/'sources.json')['api_defaults'],
        direct_automatic_allocated_contexts=17,hybrid_automatic_allocated_contexts=[65,129],
        direct_recommended_call_contexts='min(num_sms,9,num_allocated_qps)',
        automatic_no_nvlink_direct_sm_branch=dict(logical_scaleout_ranks=1,physical_nvlink_ranks=1,
            selected_nvlink_traffic=0,selected_rdma_traffic_used_for_bound=False,
            conditional_result='If bandwidth helpers return valid values, code keeps device SM count (rounded down to even)',
            hardware_value_read=False,source_execution=False),
        network_execution=False,modern_provider_execution=False,hardware_calibrated=False,
        paper_main_contribution_established=False,independent_requests=False))
    print(json.dumps(dict(analyzed=True,cases=len(cases),token_put_bytes=token_bytes)))


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='stage',required=True)
    q=sub.add_parser('prepare')
    for k in ('sources','manifest','output'):q.add_argument('--'+k,type=Path,required=True)
    q=sub.add_parser('analyze')
    for k in ('observed','output'):q.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();globals()[a.stage](a)


if __name__=='__main__':main()
