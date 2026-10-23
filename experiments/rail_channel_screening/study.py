"""Source-preserving, offline preparation and byte-microbenchmark planning."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

REV='f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
POLICIES=('identity','rotate','local_lpt')


def load(p): return json.loads(p.read_text())
def save(p,v): p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def prepare(repo,out):
    assert not out.exists() and out.parent.is_dir()
    out.mkdir();obs=out/'observed';truth=out/'truth';obs.mkdir();truth.mkdir()
    manifest=load(repo/'data/moe-trace-small/manifest.json')
    assert manifest['source']['dataset_revision']=='27febb7b2d24169560a9f6f83d38eb53f94916cf'
    assert len(manifest['files'])==4
    rows=[]
    for source,item in enumerate(manifest['files']):
        p=repo/item['file']['path'];assert p.stat().st_size<4*2**20 and sha(p)==item['file']['sha256']
        raw=load(p);assert len(raw)==129
        tokens=raw[0]['4'][:16]
        assert all(len(v)==len(set(v))==8 and all(type(e)is int and 0<=e<256 for e in v) for v in tokens)
        rows.append(tokens);shutil.copyfile(p,truth/p.name)
    assert list(map(len,rows))==[16,16,10,6]
    save(truth/'manifest.json',manifest)
    save(obs/'routes.json',dict(tokens=rows,entry=0,layer=4,prefix_cap=16,
        original_hashes=[r['file']['sha256'] for r in manifest['files']],independent_requests=False))
    sources=[]
    selected=[('deepep_modern_20261022',p) for p in (
        'deep_ep/include/deep_ep/comm/barrier.cuh',
        'deep_ep/include/deep_ep/impls/ep/dispatch.cuh',
        'deep_ep/include/deep_ep/layout/ep/token.cuh')]
    selected += [('invocation_semantics_20261023','csrc/kernels/ep/dispatch.hpp'),
                 ('deepep_layout_20261022','deep_ep/include/deep_ep/common/compiled.cuh'),
                 ('nccl_gin_20261022','src/include/nccl_device/impl/gin__funcs.h')]
    for cache,name in selected:
        tree_cache=repo/'data/research'/('nccl_gin_20261022' if cache=='nccl_gin_20261022' else 'deepep_modern_20261022')
        tree=load(tree_cache/'tree.json');assert not tree['truncated']
        data=(repo/'data/research'/cache/'sources'/name).read_bytes()
        row=next(r for r in tree['tree'] if r['path']==name)
        blob=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        assert blob==row['sha'] and len(data)==row['size']
        target=obs/'sources'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        sources.append(dict(path=name,git_blob_sha=blob,sha256=sha(target),bytes=len(data),
            commit='12df1a11afad322be5a204a2db890161cbf8131d' if cache=='nccl_gin_20261022' else REV))
    save(obs/'sources.json',sources)
    for name in ('deepep_modern_20261022','nccl_gin_20261022'):
        shutil.copyfile(repo/'data/research'/name/'tree.json',truth/(name+'-tree.json'))
    config=dict(sms=8,shared_memory_bytes=65536,dispatch_warps=4,call_contexts=8,
        requested_contexts=17,allocated_contexts=18,connections=2,hidden=7168,topk=8,
        token_bytes=14464,declared_port_binding=[1,2],queue_depth=32,
        policies=list(POLICIES),upper_rates=[100000000000,25000000000])
    # Launcher source assumptions are recorded, not executed as hardware code.
    notify=((4+256+127)//128*128)*4
    token_smem=14464+32
    assert min((65536-notify)//token_smem,28)==config['dispatch_warps']
    save(obs/'config.json',config)
    for rate in config['upper_rates']:
        topology=dict(schema_version='moe-scaleout-topology/1',
            deployment=dict(pods=2,segments_per_pod=1,servers_per_segment=2,gpus_per_server=1,gpu_type='NONE'),
            fabric=dict(rails=1,planes=2,tors_per_segment_per_rail=1,upper_tier_scope='shared',
                aggs_per_pod_per_group=1,cores_per_group=1,tor_uplinks=1,agg_uplinks=1,core_wiring='full_mesh'),
            nic=dict(rails_by_gpu_slot=[[0]],total_rate_bps=200000000000),
            links={tier:dict(rate_bps=100000000000 if tier=='access' else rate,delay_ns=1000)
                   for tier in ('access','tor_agg','agg_core','tor_core')},
            port_limits=dict(gpu=16,tor=128,agg=128,core=128),
            budget=dict(max_nodes=8192,max_links=131072,max_switches=1024))
        save(obs/f'topology-{rate}.json',topology)
    protocol=dict(schema_version='moe-scaleout-rdma/2',cc_mode=1,payload_bytes=1000,
        chunk_bytes=4000,ack_interval_bytes=1,base_rtt_ns=14000,switch_buffer_bytes=8388608,
        ecn_by_rate={str(rate):dict(kmin_bytes=32768,kmax_bytes=131072,pmax=1.0)
                     for rate in config['upper_rates']},seed=11,
        limits=dict(max_gpus=4,max_switches=16,max_links=32,max_messages=8192,
            max_queue_events=200000,queue_capture='events',max_sim_ns=5000000,
            wall_seconds=60,rss_bytes=1073741824,address_bytes=2147483648,
            artifact_bytes=67108864,result_json_bytes=16777216),
        packet_capture=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=100000),queue_metrics=True)
    save(obs/'protocol.json',protocol)
    print(json.dumps(dict(prepared=True,tokens=list(map(len,rows)),sources=len(sources))))


def context(slot,cfg):
    sm=slot%cfg['sms'];warp=slot//cfg['sms']%cfg['dispatch_warps']
    available=cfg['call_contexts']-1
    return 1+(sm*cfg['dispatch_warps']+warp)%available


def permutations(tokens,policy,cfg):
    result=[]
    for src,rows in enumerate(tokens):
        n=len(rows)
        if policy=='identity': order=list(range(n))
        elif policy=='rotate': order=[(i+src)%n for i in range(n)]
        else:
            assert policy=='local_lpt'
            order=[None]*n;free=set(range(n));loads=[0,0]
            weights={i:len({e//64 for e in r}-{src}) for i,r in enumerate(rows)}
            for token in sorted(range(n),key=lambda t:(-weights[t],t)):
                def score(slot):
                    proposed=loads.copy();proposed[context(slot,cfg)%2]+=weights[token]
                    return max(proposed),sum(v*v for v in proposed),slot
                slot=min(free,key=score);order[slot]=token;free.remove(slot)
                loads[context(slot,cfg)%2]+=weights[token]
        result.append(order)
    return result


def plan(obs,out):
    assert not out.exists() and out.parent.is_dir();out.mkdir()
    cfg=load(obs/'config.json');tokens=load(obs/'routes.json')['tokens']
    streams=[]
    for src in range(4):
        for dst in range(4):
            if src==dst:continue
            for ctx in range(8):
                sid=src*32+dst*8+ctx;plane=ctx%2
                streams.append(dict(id=sid,src=src,dst=dst,plane=plane,wire_port=20000+sid,
                    source_nic=1+plane,destination_nic=1+plane,queue_depth=32))
    assert len(streams)==96
    projections=[]
    for policy in cfg['policies']:
        perm=permutations(tokens,policy,cfg);work=[];identities=[]
        for src,order in enumerate(perm):
            for slot,token in enumerate(order):
                ctx=context(slot,cfg)
                for dst in sorted({e//64 for e in tokens[src][token]}-{src}):
                    sid=src*32+dst*8+ctx;wid=len(work)
                    work.append(dict(id=wid,stream=sid,bytes=cfg['token_bytes'],after=[]))
                    identities.append(dict(work=wid,src=src,token=token,slot=slot,dst=dst,
                        context=ctx,plane=ctx%2,stream=sid,bytes=cfg['token_bytes']))
        save(out/(policy+'.json'),dict(schema_version='moe-rdma-stream-work/2',
            limits=dict(max_streams=96,max_work=512),streams=streams,work=work))
        src_load=Counter((x['src'],x['plane']) for x in identities)
        dst_load=Counter((x['dst'],x['plane']) for x in identities)
        projections.append(dict(policy=policy,permutations=perm,identities=identities,
            writes=len(work),operand_bytes=sum(w['bytes'] for w in work),
            source_loads=[dict(gpu=g,plane=p,tokens=src_load[g,p]) for g in range(4) for p in range(2)],
            destination_loads=[dict(gpu=g,plane=p,tokens=dst_load[g,p]) for g in range(4) for p in range(2)],
            maximum_source_tokens=max(src_load.values()),maximum_destination_tokens=max(dst_load.values())))
    # Single-token case has no legal reorder; do not run identical networks.
    prefix=[[r[0]] for r in tokens]
    single={p:permutations(prefix,p,cfg) for p in POLICIES}
    assert all(v==[[0]]*4 for v in single.values())
    save(out/'projection.json',dict(cases=projections,single_token_permutations=single,
        single_token_context=context(0,cfg),hardware_calibrated=False,modern_network_execution=False))
    print(json.dumps({c['policy']:dict(writes=c['writes'],source=c['maximum_source_tokens'],
                                    destination=c['maximum_destination_tokens']) for c in projections}))


def capture_time_basis(repo,out):
    assert not out.exists();out.mkdir()
    native=repo/'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3'
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=native,text=True).strip()
    assert commit=='3964b1d41cb5f7b51004c316b0eae1a3713c2498'
    rows=[]
    for name,needle in (
        ('src/network/utils/data-rate.cc','return Seconds(int64x64_t(bits) / m_bps);'),
        ('src/core/model/nstime.h',': m_data(v.Round())'),
        ('src/point-to-point/model/qbb-net-device.cc','Time txTime = m_bps.CalculateBytesTxTime(p->GetSize());')):
        raw=(native/name).read_bytes()
        pinned=subprocess.check_output(['git','show',commit+':'+name],cwd=native)
        assert raw==pinned and needle in raw.decode()
        dest=out/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
        lines=raw.decode().splitlines();indexes=[i for i,s in enumerate(lines) if needle in s]
        assert len(indexes)==1
        i=indexes[0]
        rows.append(dict(path=name,commit=commit,sha256=sha(dest),bytes=len(raw),
            needle=needle,first_line=max(0,i-3)+1,excerpt=lines[max(0,i-3):i+4]))
    save(out/'sources.json',rows)
    print(json.dumps(dict(time_sources=3,rule='nearest ns, positive times; per-frame serialized service')))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='phase',required=True)
    a=sub.add_parser('prepare');a.add_argument('--repo',type=Path,required=True);a.add_argument('--output',type=Path,required=True)
    a=sub.add_parser('plan');a.add_argument('--observed',type=Path,required=True);a.add_argument('--output',type=Path,required=True)
    a=sub.add_parser('capture-time');a.add_argument('--repo',type=Path,required=True);a.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.phase=='prepare':prepare(a.repo,a.output)
    elif a.phase=='plan':plan(a.observed,a.output)
    else:capture_time_basis(a.repo,a.output)
