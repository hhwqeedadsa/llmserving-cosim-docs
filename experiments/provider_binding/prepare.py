"""Freeze previously acquired fixed sources and original routing selections."""
import argparse
import hashlib
from pathlib import Path
import shutil
from experiments.provider_binding.stage import load,save,sha

NCCL='12df1a11afad322be5a204a2db890161cbf8131d'
DEEPEP='f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
FILES={
 'nccl':['src/transport/net_ib/gdaki/gin_host_gdaki.cc','src/gin/gin_host.cc',
         'src/include/nccl_device/impl/gin__funcs.h','src/include/nccl_device/gin/gdaki/gin_gdaki.h'],
 'deepep':['csrc/kernels/comm/context.cpp','deep_ep/include/deep_ep/comm/barrier.cuh',
           'deep_ep/include/deep_ep/impls/ep/dispatch.cuh','deep_ep/include/deep_ep/impls/ep/combine.cuh']}


def extract(text,marker):
    assert text.count(marker)==1,marker
    start=text.index(marker);end=text.index('\n}',start)+2
    return text[start:end]


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();out=a.output;assert not out.exists();out.mkdir()
    provenance=load(repo/'docs/review_artifacts/modern_operations_20261022/provenance.json')
    save(out/'provenance.json',provenance);raw=[]
    for item in provenance['files']:
        src=repo/item['path'];assert sha(src)==item['sha256'];raw.append(load(src))
        dst=out/'raw'/src.name;dst.parent.mkdir(exist_ok=True);shutil.copyfile(src,dst)
    calls=[]
    for entry in (1,2):
        for layer in (3,4):
            tokens=[r[entry][str(layer)] for r in raw]
            assert all(len(t)==1 and len(t[0])==len(set(t[0]))==8 for t in tokens)
            selections=[x for x in provenance['selections'] if x['call']==len(calls)]
            assert [x['choices'] for x in selections]==[t[0] for t in tokens]
            calls.append(dict(name=f'entry{entry}.layer{layer}',tokens=tokens))
    cfg=dict(schema_version='moe-modern-deepep-input/1',model=dict(experts=256,top_k=8,hidden_size=7168,precision='bf16'),
        execution=dict(ranks=4,max_tokens_per_rank=1,mode='direct_rdma_only_fresh',stream='compute_wait_no_deferred_epilogue',
            expanded_layout=True,allow_multiple_reduction=True,expert_alignment=1,num_sms=2,dispatch_warps=2,
            combine_warps=2,call_contexts=5,requested_contexts=5,connections=2,local_devices=[[0,1] for _ in range(4)]),
        calls=calls,limits=dict(max_events=65536,max_operations=16384,max_input_bytes=2**20,max_output_bytes=16*2**20))
    save(out/'input.json',cfg)
    identities=[]
    for project,stamp,commit in [('nccl','nccl_gin_20261022',NCCL),('deepep','deepep_modern_20261022',DEEPEP)]:
        cache=repo/'data/research'/stamp;tree=load(cache/'tree.json');assert not tree['truncated']
        index={r['path']:r for r in tree['tree']}
        if project=='nccl':
            ident=load(cache/'commit.json');assert ident['sha']==commit and ident['tree']['sha']==tree['sha']
            shutil.copyfile(cache/'commit.json',out/'nccl-commit.json')
        else:
            ident=load(cache/'tree-source.json');assert ident['commit']==commit
            req=[r for r in ident['requests'] if '/git/trees/' in r['url']]
            assert len(req)==1 and req[0]['sha256']==sha(cache/'tree.json')
            shutil.copyfile(cache/'tree-source.json',out/'deepep-tree-source.json')
        shutil.copyfile(cache/'tree.json',out/(project+'-tree.json'))
        for name in FILES[project]:
            src=cache/'sources'/name;data=src.read_bytes()
            blob=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
            assert index[name]['sha']==blob and index[name]['size']==len(data)
            dst=out/'sources'/project/name;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(data)
            identities.append(dict(project=project,commit=commit,path=name,blob=blob,bytes=len(data),sha256=sha(src)))
    save(out/'sources.json',identities)
    barrier=(out/'sources/deepep/deep_ep/include/deep_ep/comm/barrier.cuh').read_text()
    gin=(out/'sources/nccl/src/include/nccl_device/impl/gin__funcs.h').read_text()
    texts=[extract(barrier,'template <int kNumSMs, int kNumQPs, int kNumChannelsPerSM, bool kWithNotifyWarps = false>'),
           extract(gin,'template <typename GinType>\nNCCL_DEVICE_INLINE void ncclGinInitCommon(')]
    save(out/'extractions.json',[dict(bytes=len(t.encode()),sha256=hashlib.sha256(t.encode()).hexdigest()) for t in texts])
    (out/'original_functions.inc').write_text('\n\n'.join(texts)+'\n')
    save(out/'manifest.json',dict(files={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()}))
    print('four original routing files and eight fixed source files verified; no downloads')


if __name__=='__main__':main()
