"""Temporal controls and per-step traffic projection from public normalized rows."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import random

from moe_ub_cosim.modern_deepep_plan import token_bytes

if not __debug__:raise RuntimeError('Do not run analysis with -O')
load=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
save=lambda p,x:p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')


def top8(counts):return sorted((i for i,c in enumerate(counts) if c),key=lambda i:(-counts[i],i))[:8]


def describe(counts):
    total=sum(counts);positive=sum(c>0 for c in counts);top=top8(counts)
    cutoff=counts[top[-1]] if top else 0
    return dict(selections=total,counts=counts,active=len([c for c in counts if c]),top_experts=top,
        max_to_mean=max(counts)/(total/len(counts)) if total else None,
        top8_share=sum(counts[i] for i in top)/total if total else None,
        top8_cutoff_count=cutoff,experts_tied_at_cutoff=sum(c==cutoff for c in counts) if cutoff else 0)


def temporal(step_counts,width):
    rows=[];prior=None;jaccards=[];coverage=[]
    for start in range(0,64,width):
        counts=[sum(row[e] for row in step_counts[start:start+width]) for e in range(256)]
        d=describe(counts);current=set(d['top_experts'])
        if prior is not None:
            overlap=len(prior&current)/len(prior|current)
            captured=sum(counts[e] for e in prior)/sum(counts)
            jaccards.append(overlap);coverage.append(captured)
        else:overlap=captured=None
        # Counts for the full64 prefix are retained separately. Window rows
        # retain exact selected experts and tie metadata without256 counters.
        rows.append(dict(first_source_entry=1+start,steps=width,top_experts=d['top_experts'],
            max_to_mean=d['max_to_mean'],top8_share=d['top8_share'],top8_cutoff_count=d['top8_cutoff_count'],
            experts_tied_at_cutoff=d['experts_tied_at_cutoff'],previous_top_jaccard=overlap,
            previous_top8_next_selection_coverage=captured))
        prior=current
    return dict(width_steps=width,windows=rows,comparisons=len(jaccards),
        mean_previous_top_jaccard=sum(jaccards)/len(jaccards) if jaccards else None,
        mean_previous_top8_next_selection_coverage=sum(coverage)/len(coverage) if coverage else None)


def wave(tokens):
    experts=[0]*256;rank_counts=[0]*32;flows=Counter();dedup=Counter();local=0
    for src,choices in tokens.items():
        owners=set()
        for expert in choices:
            experts[expert]+=1;owner=expert//8;rank_counts[owner]+=1;owners.add(owner)
            if owner==src:local+=1
            else:flows[src,owner]+=1
        for owner in owners:
            if owner!=src:dedup[src,owner]+=1
    assert sum(experts)==8*len(tokens) and sum(rank_counts)==sum(experts)
    vectors=[sum(flows.values()),sum(dedup.values())]
    cuts=[]
    for matrix in (flows,dedup):
        source=[0]*32;destination=[0]*32
        for (s,d),n in matrix.items():source[s]+=n*14336;destination[d]+=n*14336
        maximum=max(source+destination)
        cuts.append(dict(max_access_direction_activation_bytes=maximum,
            access_service_lower_bound_ns=(maximum*8*10**9+400000000000-1)//400000000000))
    return dict(expert_counts=experts,rank_expert_counts=rank_counts,tokens=len(tokens),local_expert_choices=local,
        remote_expert_contributions=vectors[0],remote_rank_vectors=vectors[1],
        flows=[[s,d,flows[s,d],dedup[s,d]] for s,d in sorted(set(flows)|set(dedup))],
        dispatch_activation_bytes=[n*14336 for n in vectors],combine_activation_bytes=[n*14336 for n in vectors],
        legacy_dispatch_layout_bytes=vectors[0]*(14336+16),legacy_combine_layout_bytes=vectors[0]*14336,
        direct_dispatch_layout_bytes=vectors[1]*token_bytes(7168,8,True),
        direct_combine_layout_bytes=vectors[1]*token_bytes(7168,8,False),
        contribution_access_bound=cuts[0],deduplicated_access_bound=cuts[1],
        old_complete_operations=16384+16*len(tokens),old_complete_events=4*(16384+16*len(tokens))+352,
        old_remote_carrier_work=31744+2*vectors[0])


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();study=a.study.resolve()
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    frozen=load(study/'frozen.json')
    for name,digest in frozen['input_sha256'].items():assert sha(study/name)==digest
    policy=load(study/'policy.json');report=load(study/'normalized/report.json');data={};read=0
    with (study/'normalized/normalized_decode.jsonl').open() as stream:
        for line in stream:
            assert len(line)<=8192;read+=1;assert read<=150000
            row=json.loads(line)
            if row['source_entry']<=64 and row['layer_id'] in policy['layers']:
                key=row['file_id'],row['layer_id'],row['source_entry'];assert key not in data
                assert len(row['expert_ids'])==8;data[key]=row['expert_ids']
    assert read==report['records'] and len(data)==15*3*64
    samples=[];permutations={};volume=[]
    with (out/'waves.jsonl').open('x') as log:
        for group,(first,last) in enumerate(((0,8),(8,15))):
            ids=policy['source_order'][first:last]
            for layer in policy['layers']:
                waves=[wave({rank:data[file,layer,entry] for rank,file in enumerate(ids)}) for entry in range(1,65)]
                for i,w in enumerate(waves):log.write(json.dumps(dict(group=group,layer=layer,source_entry=i+1,**w),separators=(',',':'))+'\n')
                counts=[w['expert_counts'] for w in waves]
                total=[sum(w[e] for w in counts) for e in range(256)]
                rank_total=[sum(w['rank_expert_counts'][r] for w in waves) for r in range(32)]
                observed=dict(experts=describe(total),rank_load=describe(rank_total),
                    temporal={str(width):temporal(counts,width) for width in policy['windows']})
                controls={}
                for seed in policy['shuffle_seeds']:
                    shuffled={}
                    for rank,file in enumerate(ids):
                        indices=list(range(64))
                        digest=hashlib.sha256(f'{seed}\n{file}\n{layer}'.encode()).digest()
                        random.Random(int.from_bytes(digest,'big')).shuffle(indices)
                        permutations[f'{seed}/{file}/{layer}']=indices
                        shuffled[rank]=[data[file,layer,i+1] for i in indices]
                    counters=[]
                    for i in range(64):
                        c=[0]*256
                        for rank in shuffled:
                            for expert in shuffled[rank][i]:c[expert]+=1
                        counters.append(c)
                    assert [sum(row[e] for row in counters) for e in range(256)]==total
                    controls[str(seed)]={str(width):temporal(counters,width) for width in policy['windows']}
                samples.append(dict(group=group,layer=layer,requests=len(ids),steps=64,observed=observed,shuffled=controls,
                    dispatch_activation_totals=[sum(w['dispatch_activation_bytes'][j] for w in waves) for j in (0,1)],
                    mean_step_remote_contributions=sum(w['remote_expert_contributions'] for w in waves)/64,
                    mean_step_remote_vectors=sum(w['remote_rank_vectors'] for w in waves)/64,
                    max_contribution_access_bound_ns=max(w['contribution_access_bound']['access_service_lower_bound_ns'] for w in waves),
                    max_deduplicated_access_bound_ns=max(w['deduplicated_access_bound']['access_service_lower_bound_ns'] for w in waves)))
                for steps in (1,2,4,64):
                    prefix=waves[:steps]
                    volume.append(dict(group=group,layer=layer,steps=steps,requests=len(ids),
                        complete_operations=sum(w['old_complete_operations'] for w in prefix),
                        complete_events=sum(w['old_complete_events'] for w in prefix),
                        carrier_work=sum(w['old_remote_carrier_work'] for w in prefix),
                        per_rank_tokens_each_call=1,max_calls_supported_by_indexed_schema=4,
                        native_group_operation_cap=32768,native_group_event_cap=140000,
                        native_group_work_cap=65536,
                        counts_fit_native_group_caps=(sum(w['old_complete_operations'] for w in prefix)<=32768 and
                            sum(w['old_complete_events'] for w in prefix)<=140000 and
                            sum(w['old_remote_carrier_work'] for w in prefix)<=65536),
                        network_execution_proven=False))
    summary=dict(passed=True,policy=policy,normalized_records=read,selected_token_layer_rows=len(data),
        selected_expert_choices=len(data)*8,source_files=15,samples=samples,capacity=volume,
        waves_sha256=sha(out/'waves.jsonl'),source_manifest_sha256=frozen['source_manifest_sha256'],
        frozen_sha256=sha(study/'frozen.json'),network_executed=False,hardware_calibrated=False,
        frontend_scheduler_executed=False,algorithm_innovation_validated=False,
        scope='Real recorded choices with assumed step alignment and fixed deployment; activation/layout projection without packet/control/compute timing')
    save(out/'analysis.json',summary);save(out/'permutations.json',permutations)
    print(json.dumps(dict(passed=True,selected_token_layer_rows=len(data),expert_choices=len(data)*8,samples=len(samples),new_network_runs=0)))


if __name__=='__main__':main()
