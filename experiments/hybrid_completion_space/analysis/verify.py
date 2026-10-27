"""Independent bitset accounting of both directions and ordinary baselines."""
import argparse
from copy import deepcopy
import hashlib
from pathlib import Path

from experiments.hybrid_completion_space.analysis.study import load,save,sha


def expected(documents,ep,channels,binding,phase,layer):
    domains=ep//8;forward=[{} for _ in range(ep)];backward=[{} for _ in range(ep)]
    count={};last={};periodic=0;vectors=0;tokens=0
    for i,document in enumerate(documents):
        source=i if binding=='contiguous' else i*ep//15
        for token,choices in enumerate(document[0 if phase=='prefill' else 1][str(layer)]):
            tokens+=1;mask=0;channel=token%channels
            for expert in choices:mask|=1<<((expert//(256//ep))//8)
            mask&=~(1<<(source//8))
            for peer in range(domains):
                if not (mask>>peer)&1:continue
                vectors+=1
                forward[source][peer]=forward[source].get(peer,0)|(1<<channel)
                reverse_source=peer*8+source%8;reverse_peer=source//8
                backward[reverse_source][reverse_peer]=backward[reverse_source].get(reverse_peer,0)|(1<<channel)
                key=(source,peer,channel);count[key]=count.get(key,0)+1
                if count[key]-last.get(key,0)>=6:periodic+=1;last[key]=count[key]
    def describe(rows):
        exact=factorized=source_only=peer_only=peers=0
        for mapping in rows:
            union=0
            for bits in mapping.values():exact+=bits.bit_count();union|=bits
            factorized+=union.bit_count()*len(mapping)
            source_only+=union.bit_count()*(domains-1);peer_only+=channels*len(mapping);peers+=len(mapping)
        return dict(dense=ep*(domains-1)*channels,source_channel_only=source_only,peer_only=peer_only,
                    factorized=factorized,exact=exact,active_peer_pairs=peers,hidden_vectors=vectors)
    d,c=describe(forward),describe(backward);pairs=ep*(domains-1)
    actual=4*pairs+d['dense']+c['dense']+periodic
    factored=4*pairs+d['factorized']+c['factorized']+periodic
    exact=4*pairs+d['exact']+c['exact']+periodic
    return dict(ep=ep,channels=channels,binding=binding,phase=phase,layer=layer,tokens=tokens,
        dispatch=d,combine=c,entry_barrier_signal_calls=2*pairs,dispatch_count_put_calls=2*pairs,
        dispatch_periodic_tail_calls=periodic,dispatch_count_operand_bytes=pairs*4*(8+256//domains),
        control_api_calls=actual,after_factorized_api_calls=factored,after_exact_api_calls=exact,
        residual_calls=factored-exact,bidirectional_hidden_bytes=vectors*2*14336,
        dense_bitmap_bits=2*pairs*channels,dense_bitmap_aligned_bytes=2*pairs*4*((channels+31)//32),
        active_bitmap_bits=(d['active_peer_pairs']+c['active_peer_pairs'])*channels)


def compare(row,reference):
    assert all(row[key]==value for key,value in reference.items()),'accounting mismatch'


def sources_check(stage):
    s=load(stage/'observed/sources.json');documents=[]
    assert len(s['routes'])==15 and {'899.json','1479.json'}.isdisjoint(r['name'] for r in s['routes'])
    for row in s['routes']:
        p=stage/'observed/routes'/row['name'];assert sha(p)==row['sha256'];documents.append(load(p))
    for name,row in s['library'].items():
        p=stage/'observed/library'/name;body=p.read_bytes()
        assert sha(p)==row['sha256'] and hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()==row['git_blob_sha']
    paths={Path(name).name:stage/'observed/library'/name for name in s['library']}
    anchors={
        'hybrid_dispatch.cuh':['int kScaleoutUpdateInterval = 6,','update_scaleout_tail(true);',
            'comm::kHybridDispatchTag0, false, false, true>','/* do not scale-out */ false, true'],
        'hybrid_combine.cuh':['// Replay the dispatch','gin.flush<ncclCoopWarp>();',
            'const auto expected_signal = math::pack2<int, int64_t>(1, 0);','// No barrier at epilogue',
            'comm::kHybridCombineTag0, false, true, true>'],
        'barrier.cuh':['barrier_gin.signal<team_t>','ncclTeamTagRail, kTag, kFlushStores>'],
        'handle.cuh':['void red_add_rel(dtype_t* sym_ptr']}
    locations=[]
    for file,needles in anchors.items():
        lines=paths[file].read_text().splitlines()
        for needle in needles:
            hits=[i+1 for i,line in enumerate(lines) if needle in line];assert len(hits)==1,(file,needle,hits)
            locations.append(dict(file=file,anchor=needle,line=hits[0]))
    return documents,locations


def execute(stage,output):
    state=load(stage/'state.json');assert state['status']=='completed' and state['completed']==36
    documents,locations=sources_check(stage);rows=load(stage/'analysis/layers.json');assert len(rows)==2088
    keys=set();expected_rows={}
    for row in rows:
        key=tuple(row[k] for k in ('ep','channels','binding','phase','layer'));assert key not in keys;keys.add(key)
        reference=expected(documents,*key);compare(row,reference);expected_rows[key]=reference
    wanted={(ep,c,b,p,l) for ep in (64,128,256) for c in (4,8,16)
            for b in ('contiguous','spread') for p in ('prefill','decode') for l in range(3,61)}
    assert keys==wanted
    summary=load(stage/'analysis/summary.json');assert len(summary['cases'])==36;passed=[]
    for aggregate in summary['cases']:
        key=tuple(aggregate[k] for k in ('ep','channels','binding','phase'))
        subset=[expected_rows[(*key,l)] for l in range(3,61)];assert aggregate['layers']==58
        for name,value in subset[0].items():
            if name in ('ep','channels','binding','phase','layer'):continue
            target={k:sum(r[name][k] for r in subset) for k in value} if isinstance(value,dict) else sum(r[name] for r in subset)
            assert aggregate[name]==target,(key,name)
        a,b=aggregate['residual_calls'],aggregate['after_factorized_api_calls']
        bits,hidden=aggregate['dense_bitmap_aligned_bytes'],aggregate['bidirectional_hidden_bytes']
        assert abs(aggregate['residual_percent_after_factorized']-100*a/b)<1e-12
        assert abs(aggregate['optimistic_bitmap_percent_hidden']-100*bits/hidden)<1e-12
        gate=key[-1]=='prefill' and 10*a>=b and 100*bits<hidden
        assert gate==aggregate['screening_gate_passed']
        if gate:passed.append({k:aggregate[k] for k in ('ep','channels','binding','phase')})
    assert passed==summary['screening_passed']
    public=load(stage/'public-check.json');cfg=public['config'];assert cfg['calls'][0]['tokens']==[d[0]['3'][:16] for d in documents[:4]]
    vectors=0
    for src,tokens in enumerate(cfg['calls'][0]['tokens']):
        for choices in tokens:vectors+=len({(e//64)//2 for e in choices}-{src//2})
    assert public['counts']==dict(finish=16,rank_count=4,expert_count=4,token_data=vectors)
    tested=[]
    candidates=[
        ('empty_finishes_omitted',next(r for r in rows if r['dispatch']['dense']>r['dispatch']['exact']),lambda r:r['dispatch'].update(dense=r['dispatch']['exact'])),
        ('combine_not_transposed',next(r for r in rows if r['dispatch']!=r['combine']),lambda r:r.update(combine=deepcopy(r['dispatch']))),
        ('periodic_removed',next(r for r in rows if r['dispatch_periodic_tail_calls']),lambda r:r.update(dispatch_periodic_tail_calls=0)),
        ('baseline_weakened',next(r for r in rows if r['dispatch']['factorized']!=r['dispatch']['peer_only']),lambda r:r['dispatch'].update(factorized=r['dispatch']['peer_only']))]
    for label,row,mutation in candidates:
        altered=deepcopy(row);mutation(altered);key=tuple(row[k] for k in ('ep','channels','binding','phase','layer'))
        try:compare(altered,expected_rows[key])
        except AssertionError:tested.append(label)
        else:raise AssertionError('tamper accepted: '+label)
    save(output,dict(passed=True,layer_conditions=2088,aggregate_conditions=36,source_files=15,
        source_anchors=locations,bitset_directions_verified=True,public_small_dispatch_check=True,
        tamper_rejected=tested,screening_gate_passed_count=len(passed),network_runs=0,GPU=0,
        safe_protocol_implementation_verified=False,latency_benefit_measured=False,new_innovations_established=0))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('stage','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();execute(a.stage.resolve(),a.output.resolve())
