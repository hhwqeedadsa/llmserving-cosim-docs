"""Recompute selected matrices, windows and full plan counts from source JSON."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random

from moe_ub_cosim.route_evidence import iter_sources,validate_manifest

if not __debug__:raise RuntimeError('Do not run verification with -O')
ROOT=Path(__file__).resolve().parents[2]
load=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()


def verify_temporal(rows,observed,width):
    old=None;js=[];cs=[]
    assert observed['width_steps']==width and len(observed['windows'])==64//width
    for i,window in enumerate(observed['windows']):
        counts=Counter(e for choices in rows[i*width:(i+1)*width] for token in choices for e in token)
        top=sorted(counts,key=lambda e:(-counts[e],e))[:8]
        assert window['first_source_entry']==i*width+1 and window['steps']==width
        assert window['top_experts']==top
        assert window['top8_cutoff_count']==counts[top[-1]]
        assert window['experts_tied_at_cutoff']==sum(v==counts[top[-1]] for v in counts.values())
        assert window['top8_share']==sum(counts[e] for e in top)/sum(counts.values())
        if old is not None:
            j=len(set(old)&set(top))/len(set(old)|set(top));c=sum(counts[e] for e in old)/sum(counts.values())
            assert window['previous_top_jaccard']==j and window['previous_top8_next_selection_coverage']==c
            js.append(j);cs.append(c)
        else:assert window['previous_top_jaccard'] is None and window['previous_top8_next_selection_coverage'] is None
        old=top
    assert observed['comparisons']==len(js)
    assert observed['mean_previous_top_jaccard']==(sum(js)/len(js) if js else None)
    assert observed['mean_previous_top8_next_selection_coverage']==(sum(cs)/len(cs) if cs else None)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,required=True)
    p.add_argument('--analysis',type=Path,required=True);p.add_argument('--admission',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();assert not a.output.exists()
    frozen=load(a.study/'frozen.json');revision=load(a.study/'frozen-v2.json')
    assert sha(a.study/'frozen.json')==revision['original_frozen_sha256']
    assert sha(Path(__file__).with_name('study.py'))==revision['preparation_sha256']
    assert sha(Path(__file__).with_name('README.md'))==frozen['method_sha256']
    for name,digest in frozen['input_sha256'].items():assert sha(a.study/name)==digest
    manifest=load(a.study/'source-manifest.json');validate_manifest(manifest)
    analysis=load(a.analysis/'analysis.json');permutations=load(a.analysis/'permutations.json')
    assert analysis['passed'] and not analysis['network_executed']
    assert sha(a.analysis/'waves.jsonl')==analysis['waves_sha256']
    policy=analysis['policy'];source={};lengths=[]
    for file_id,raw in iter_sources(manifest):
        lengths.append(raw['available_decode_records'])
        for layer in policy['layers']:
            source[file_id,layer]=[raw['raw'][i][str(layer)][0] for i in range(1,65)]
    assert sum(lengths)*58==analysis['normalized_records'] and len(lengths)==15 and min(lengths)>=64
    wave_rows={}
    with (a.analysis/'waves.jsonl').open() as stream:
        for line in stream:
            row=json.loads(line);key=row['group'],row['layer'],row['source_entry'];assert key not in wave_rows;wave_rows[key]=row
    assert len(wave_rows)==2*3*64
    matrices_checked=windows_checked=0
    for sample in analysis['samples']:
        group,layer=sample['group'],sample['layer'];first,last=((0,8),(8,15))[group]
        ids=policy['source_order'][first:last]
        allrows=[[source[file,layer][i] for file in ids] for i in range(64)]
        total=Counter(e for row in allrows for token in row for e in token)
        assert sample['observed']['experts']['counts']==[total[e] for e in range(256)]
        rank_counts=[sum(total[e] for e in range(8*r,8*r+8)) for r in range(32)]
        assert sample['observed']['rank_load']['counts']==rank_counts
        assert sample['observed']['experts']['max_to_mean']==max(total.values())/(sum(total.values())/256)
        assert sample['observed']['rank_load']['max_to_mean']==max(rank_counts)/(sum(rank_counts)/32)
        for step,tokens in enumerate(allrows,1):
            row=wave_rows[group,layer,step]
            dense=[[0]*32 for _ in range(32)];unique=[[0]*32 for _ in range(32)];local=0
            ec=Counter(e for choices in tokens for e in choices)
            for src,choices in enumerate(tokens):
                for dst in range(32):
                    n=sum(e//8==dst for e in choices)
                    if dst==src:local+=n
                    else:dense[src][dst]=n;unique[src][dst]=int(n>0)
            expected=[[s,d,dense[s][d],unique[s][d]] for s in range(32) for d in range(32) if dense[s][d]]
            remote=sum(map(sum,dense));dedup=sum(map(sum,unique))
            assert row['expert_counts']==[ec[e] for e in range(256)] and row['local_expert_choices']==local
            assert row['flows']==expected and row['remote_expert_contributions']==remote and row['remote_rank_vectors']==dedup
            assert row['dispatch_activation_bytes']==row['combine_activation_bytes']==[remote*14336,dedup*14336]
            assert row['legacy_dispatch_layout_bytes']==remote*14352 and row['legacy_combine_layout_bytes']==remote*14336
            assert row['direct_dispatch_layout_bytes']==dedup*14464 and row['direct_combine_layout_bytes']==dedup*14400
            for matrix,name in [(dense,'contribution_access_bound'),(unique,'deduplicated_access_bound')]:
                maximum=14336*max([sum(r) for r in matrix]+[sum(r[d] for r in matrix) for d in range(32)])
                assert row[name]['max_access_direction_activation_bytes']==maximum
                assert row[name]['access_service_lower_bound_ns']==(maximum+49)//50
            assert row['old_complete_operations']==2*32*256+2*len(tokens)*8
            assert row['old_complete_events']==4*row['old_complete_operations']+11*32
            assert row['old_remote_carrier_work']==4*31*256+2*remote
            matrices_checked+=2
        for width in policy['windows']:
            verify_temporal(allrows,sample['observed']['temporal'][str(width)],width);windows_checked+=64//width
        for seed in policy['shuffle_seeds']:
            shuffled={}
            for file in ids:
                indices=permutations[f'{seed}/{file}/{layer}'];assert sorted(indices)==list(range(64))
                expected=list(range(64));namespace=int.from_bytes(hashlib.sha256(f'{seed}\n{file}\n{layer}'.encode()).digest(),'big')
                random.Random(namespace).shuffle(expected);assert indices==expected
                shuffled[file]=[source[file,layer][i] for i in indices]
            controls=[[shuffled[file][i] for file in ids] for i in range(64)]
            assert Counter(e for row in controls for token in row for e in token)==total
            for width in policy['windows']:
                verify_temporal(controls,sample['shuffled'][str(seed)][str(width)],width);windows_checked+=64//width
    for row in analysis['capacity']:
        waves=[wave_rows[row['group'],row['layer'],i] for i in range(1,row['steps']+1)]
        for name,key in [('complete_operations','old_complete_operations'),('complete_events','old_complete_events'),('carrier_work','old_remote_carrier_work')]:
            assert row[name]==sum(w[key] for w in waves)
    admission=load(a.admission/'admission-summary.json')
    assert admission['passed'] and not admission['public_precheck_accepted'] and not admission['native_network_executed']
    assert admission['rejection']=='complete plan exceeds native lifetime budget'
    for group in (0,1):
        folder=a.admission/f'group-{group}'/'plan';meta=load(folder/'manifest.json')
        source_input=load(folder/'input.json');actual=Counter();n=0;notifications=0
        for call,config in enumerate(source_input['calls']):
            for rank,token in enumerate(config['tokens']):
                if token:
                    file=policy['source_order'][rank+(0 if group==0 else 8)]
                    assert token==[source[file,3][call]]
        for name,record in meta['files'].items():assert sha(folder/name)==record['sha256']
        with (folder/'operations.jsonl').open() as stream:
            for line in stream:
                op=json.loads(line);n+=1
                if op[3]=='data':actual[op[1],op[2],op[4],op[6]]+=1
                else:notifications+=1
        assert notifications==2*16384 and n==admission['per_group_operations'][group]==meta['operation_count']
        assert n==next(r['complete_operations'] for r in analysis['capacity'] if r['group']==group and r['layer']==3 and r['steps']==2)
        assert meta['event_count']==next(r['complete_events'] for r in analysis['capacity'] if r['group']==group and r['layer']==3 and r['steps']==2)
        # Indexed columns retain phase strings; data must occur in both phases.
        for call in (0,1):
            for rank,file in enumerate(policy['source_order'][(0 if group==0 else 8):(8 if group==0 else 15)]):
                for expert in source[file,3][call]:
                    assert actual[call,'dispatch',rank,expert]==1
                    assert actual[call,'combine',expert//8,expert]==sum(expert in source[f,3][call] for f in policy['source_order'][(0 if group==0 else 8):(8 if group==0 else 15)])
    result=dict(passed=True,raw_source_files=15,full_decode_layer_rows=sum(lengths)*58,
        selected_token_layer_rows=15*3*64,selected_expert_choices=15*3*64*8,
        independently_recomputed_matrices=matrices_checked,independently_recomputed_windows=windows_checked,
        full_two_call_plan_counts_verified=True,expected_native_lifetime_rejection_verified=True,
        input_revision_lineage_verified=True,new_network_runs=0,hardware_calibrated=False)
    a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
