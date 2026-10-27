"""Source-bound Hybrid completion API counts; never a network timing model."""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import shutil

COMMIT='f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
SOURCES=('deep_ep/include/deep_ep/impls/ep/hybrid_dispatch.cuh',
         'deep_ep/include/deep_ep/impls/ep/hybrid_combine.cuh',
         'deep_ep/include/deep_ep/comm/barrier.cuh','deep_ep/include/deep_ep/comm/handle.cuh')
EPS=(64,128,256)
CHANNELS=(4,8,16)
BINDINGS=('contiguous','spread')
HIDDEN_BYTES=14336


def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,value):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n')


def prepare(repo,stage):
    assert not stage.exists();stage.mkdir();observed=stage/'observed';observed.mkdir()
    intake=load(repo/'experiments/route_coverage/results/local_additions_20261027/intake.json')
    save(observed/'intake.json',intake)
    rows=sorted((r for r in intake['files'] if r['eligible_offline']),key=lambda r:int(r['name'][:-5]))
    assert len(rows)==15 and {'899.json','1479.json'}.isdisjoint(r['name'] for r in rows)
    assert sum((repo/'data/moe-trace-small'/r['name']).stat().st_size for r in rows)<40*2**20
    files=[]
    for row in rows:
        source=repo/'data/moe-trace-small'/row['name'];assert sha(source)==row['sha256']
        dest=observed/'routes'/row['name'];dest.parent.mkdir(exist_ok=True);shutil.copyfile(source,dest)
        document=load(dest);assert len(document)>=2
        for entry in (0,1):
            for layer in range(3,61):
                tokens=document[entry][str(layer)];assert tokens and (entry==0 or len(tokens)==1)
                for choices in tokens:assert len(choices)==len(set(choices))==8 and all(type(e) is int and 0<=e<256 for e in choices)
        files.append(dict(name=row['name'],sha256=sha(dest),bytes=dest.stat().st_size))
    cached=repo/'data/research/deepep_modern_20261022';tree=load(cached/'tree.json');assert not tree['truncated']
    blobs={r['path']:r['sha'] for r in tree['tree'] if r['type']=='blob'}
    catalog={r['path']:r for file in ('file-sources.json','file-sources-completion.json') for r in load(cached/file)['sources']}
    sources={}
    for name in SOURCES:
        p=cached/'sources'/name;body=p.read_bytes();row=catalog[name]
        assert row['commit']==COMMIT and sha(p)==row['sha256']
        assert hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()==row['git_blob_sha']==blobs[name]
        dest=observed/'library'/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(body)
        sources[name]=dict(sha256=sha(dest),git_blob_sha=blobs[name],commit=COMMIT,public_url=row['public_url'])
    save(observed/'sources.json',dict(routes=files,source=intake['source'],library=sources,
        identity_scope='cached pinned-ref/tree-entry/blob; no new remote root verification',
        original_commit='dd265cd',protocol_commit='45baae7',synchronous_requests_assumed=True,
        formal_remote_route_revision_verified=False))
    save(stage/'state.json',dict(status='prepared',planned=36,completed=0))


def reverse(matrix):
    out=Counter()
    for (source,domain,channel),n in matrix.items():out[domain*8+source%8,source//8,channel]+=n
    return out


def describe(matrix,ep,channels):
    peers=defaultdict(set);active=defaultdict(set)
    for source,domain,channel in matrix:
        assert 0<=source<ep and 0<=domain<ep//8 and domain!=source//8 and 0<=channel<channels
        peers[source].add(domain);active[source].add(channel)
    exact=len(matrix);factorized=sum(len(peers[s])*len(active[s]) for s in peers)
    dense=ep*(ep//8-1)*channels
    source_only=sum(map(len,active.values()))*(ep//8-1)
    peer_only=sum(map(len,peers.values()))*channels
    assert exact<=factorized<=min(source_only,peer_only)<=max(source_only,peer_only)<=dense
    return dict(dense=dense,source_channel_only=source_only,peer_only=peer_only,
        factorized=factorized,exact=exact,active_peer_pairs=sum(map(len,peers.values())),
        hidden_vectors=sum(matrix.values()))


def layer_case(documents,ep,channels,binding,phase,layer):
    entry=0 if phase=='prefill' else 1;matrix=Counter();token_count=0
    for index,document in enumerate(documents):
        source=index if binding=='contiguous' else index*ep//len(documents)
        for token,choices in enumerate(document[entry][str(layer)]):
            token_count+=1
            for domain in {e//(256//(ep//8)) for e in choices}-{source//8}:
                matrix[source,domain,token%channels]+=1
    forward=describe(matrix,ep,channels);backward=describe(reverse(matrix),ep,channels)
    pairs=ep*(ep//8-1);periodic=sum(n//6 for n in matrix.values())
    controls=4*pairs+forward['dense']+backward['dense']+periodic
    remaining=4*pairs+forward['factorized']+backward['factorized']+periodic
    exact=4*pairs+forward['exact']+backward['exact']+periodic
    residual=remaining-exact;hidden=(forward['hidden_vectors']+backward['hidden_vectors'])*HIDDEN_BYTES
    return dict(ep=ep,channels=channels,binding=binding,phase=phase,layer=layer,tokens=token_count,
        dispatch=forward,combine=backward,entry_barrier_signal_calls=2*pairs,
        dispatch_count_put_calls=2*pairs,dispatch_periodic_tail_calls=periodic,
        dispatch_count_operand_bytes=pairs*4*(8+256//(ep//8)),
        control_api_calls=controls,after_factorized_api_calls=remaining,after_exact_api_calls=exact,
        residual_calls=residual,bidirectional_hidden_bytes=hidden,
        dense_bitmap_bits=2*pairs*channels,dense_bitmap_aligned_bytes=2*pairs*4*((channels+31)//32),
        active_bitmap_bits=(forward['active_peer_pairs']+backward['active_peer_pairs'])*channels,
        caveat='API calls/hidden vector demand only; metadata/epoch/flush/ACK costs and safe wait rewrite unimplemented')


def aggregate(rows):
    first=rows[0];out={k:first[k] for k in ('ep','channels','binding','phase')};out['layers']=len(rows)
    for key in ('tokens','entry_barrier_signal_calls','dispatch_count_put_calls','dispatch_periodic_tail_calls',
                'dispatch_count_operand_bytes','control_api_calls','after_factorized_api_calls',
                'after_exact_api_calls','residual_calls','bidirectional_hidden_bytes',
                'dense_bitmap_bits','dense_bitmap_aligned_bytes','active_bitmap_bits'):
        out[key]=sum(row[key] for row in rows)
    for direction in ('dispatch','combine'):
        out[direction]={key:sum(row[direction][key] for row in rows) for key in first[direction]}
    out['residual_percent_after_factorized']=100*out['residual_calls']/out['after_factorized_api_calls']
    out['optimistic_bitmap_percent_hidden']=100*out['dense_bitmap_aligned_bytes']/out['bidirectional_hidden_bytes']
    out['screening_gate_passed']=(out['phase']=='prefill' and out['residual_percent_after_factorized']>=10
                                  and out['optimistic_bitmap_percent_hidden']<1)
    return out


def public_dispatch_check(stage,documents):
    from moe_ub_cosim.hybrid_deepep_plan import compile_plan
    from moe_ub_cosim.hybrid_deepep_bindings import compile_bindings
    config=dict(schema_version='moe-hybrid-logical-input/1',
        model=dict(experts=256,top_k=8,hidden_size=7168,precision='bf16'),
        deployment=dict(domains=[dict(name='s'+str(i),ranks=list(range(i*2,i*2+2))) for i in range(2)],
                        rails=[dict(name='p'+str(i),ranks=list(range(i,4,2))) for i in range(2)]),
        execution=dict(mode='hybrid_fresh',num_sms=2,scaleout_warps=2,max_tokens_per_rank=16,
                       expanded_layout=True,allow_multiple_reduction=True),
        calls=[dict(name='four-real-prefill-prefixes',tokens=[d[0]['3'][:16] for d in documents[:4]])],
        limits=dict(max_rows=16384,max_input_bytes=2**20,max_output_bytes=16*2**20))
    provider=dict(schema_version='moe-hybrid-provider-input/1',devcomm='source-check',backend='nccl_gin_gdaki',
        nic_handler='gpu_sm_db',call_contexts=3,requested_contexts=3,connections=2,gin_connection_stride=2,
        local_devices=[[0,1] for _ in range(4)],limits=dict(max_bindings=32768,max_resources=8192,
        max_input_bytes=2**20,max_output_bytes=16*2**20))
    plan=compile_plan(config);bound=compile_bindings(config,provider)
    remote=[b for b in bound['bindings'] if b['source']!=b['target']]
    counts=Counter(b['kind'] for b in remote)
    save(stage/'public-check.json',dict(passed=True,config=config,provider=provider,counts=dict(counts),
        finish_expected=4*1*4,finish_actual=counts['finish'],public_plan_rows=plan['row_count'],
        scope='small public Dispatch subset only; not Combine/full kernel or timing'))
    assert counts['finish']==16 and counts['rank_count']==counts['expert_count']==4


def execute(stage):
    state=load(stage/'state.json');assert state['status']=='prepared'
    try:
        sources=load(stage/'observed/sources.json');documents=[load(stage/'observed/routes'/r['name']) for r in sources['routes']]
        public_dispatch_check(stage,documents);rows=[];aggregates=[]
        for ep in EPS:
            for channels in CHANNELS:
                for binding in BINDINGS:
                    for phase in ('prefill','decode'):
                        selected=[layer_case(documents,ep,channels,binding,phase,layer) for layer in range(3,61)]
                        rows.extend(selected);aggregates.append(aggregate(selected));state['completed']+=1
                        save(stage/'state.json',dict(state,status='running'))
        assert len(rows)==2088 and len(aggregates)==36
        save(stage/'analysis/layers.json',rows);save(stage/'analysis/summary.json',dict(cases=aggregates,
            screening_passed=[{k:r[k] for k in ('ep','channels','binding','phase')} for r in aggregates if r['screening_gate_passed']],
            declared_call_shapes=36,layer_conditions=2088,network_runs=0,GPU=0,new_innovations_established=0,
            note='optimistic control API opportunity after factorized baseline; no measured latency or safe new protocol'))
        state['status']='completed';save(stage/'state.json',state)
    except BaseException as error:
        state.update(status='stopped_after_failure',error=repr(error));save(stage/'state.json',state);raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','execute'])
    p.add_argument('--repository',type=Path);p.add_argument('--stage',type=Path,required=True);a=p.parse_args()
    if a.action=='prepare':prepare(a.repository.resolve(),a.stage.resolve())
    else:execute(a.stage.resolve())
