"""Independent bit-mask, pair-intersection and exact small-reference checks."""
import argparse
from collections import Counter
from fractions import Fraction
import hashlib
from itertools import combinations
import json
from math import isclose, prod
from pathlib import Path


def read(path): return json.loads(path.read_bytes())
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def equal(a,b): assert isclose(a,b,rel_tol=1e-12,abs_tol=1e-10),(a,b)


def mask(choices, ep):
    result=0;shift=8-(ep.bit_length()-1)
    for e in choices:result|=1<<(e>>shift)
    return result


def stats(values):
    values=sorted(values);n=len(values);assert n
    return dict(mean=sum(values)/n,median_lower=values[(n-1)//2],
        p95_nearest_rank=values[(95*n+99)//100-1],minimum=values[0],maximum=values[-1])


def check_row(row, expected):
    for field in ('waves','remote_vectors_sum','pair_collisions_sum','reference_activation_payload_bytes'):
        assert row[field]==expected[field],field
    for field in ('remote_vector_count','pair_collisions','peak_to_mean_including_zero_ranks'):
        for key,value in expected[field].items():equal(row[field][key],value)
    for field in ('uniform_expected_remote_vectors_per_wave','uniform_expected_pair_collisions_per_wave',
                  'observed_collision_to_uniform_expectation'):
        equal(row[field],expected[field])


def verify(run):
    observed=run/'observed';source=read(observed/'source.json');inputs=read(run/'inputs.json')
    assert digest(observed/'source.json')==inputs['source_sha256']
    for name,d in inputs['sources'].items():assert digest(run/'source'/name)==d
    originals=[];routing_rows=0
    for row in source['files']:
        path=observed/'routes'/row['name'];assert digest(path)==row['sha256']
        raw=read(path)
        for index,entry in enumerate(raw):
            assert set(entry)=={str(i) for i in range(61)}
            assert all(entry[str(i)] is None for i in range(3))
            for layer in range(3,61):
                rows=entry[str(layer)];assert len(rows)==(len(raw[0]['3']) if index==0 else 1)
                for choices in rows:
                    assert len(choices)==len(set(choices))==8 and all(type(e)is int and 0<=e<256 for e in choices)
                    routing_rows+=1
        originals.append((row['name'],raw))
    result=read(run/'analysis/summary.json');marginals=read(run/'marginals/summary.json')
    assert len(originals)==15 and result['valid_requests']==15
    for row in result['coverage']:
        ep=row['ep'];bits=[]
        for _,raw in originals:
            for entry in (raw[:1] if row['phase']=='prefill' else raw[1:]):
                for layer in range(3,61):
                    bits.extend(mask(choices,ep) for choices in entry[str(layer)])
        assert row['tokens']==len(bits)
        assert row['distinct_rank_sets']==len(set(bits))
        assert row['destination_count_histogram']=={str(k):v for k,v in sorted(Counter(b.bit_count() for b in bits).items())}
        equal(row['uniform_expected_destinations'],ep*(1-prod((256-256//ep-i)/(256-i) for i in range(8))))
    checked_waves=0;expected_rows={};strong_rows={(r['batch'],r['ep']):r for r in marginals['rows']}
    for row in result['grouped']:
        batch,ep=row['batch'],row['ep'];groups=[originals[i:i+batch] for i in range(0,len(originals)-batch+1,batch)]
        assert row['groups']==[[n for n,_ in group] for group in groups]
        assert row['fixed_source_ranks']==4
        remotes=[];collisions=[];peaks=[];shift_expected=Fraction(0)
        for group in groups:
            steps=min(len(raw)-1 for _,raw in group)
            for layer in range(3,61):
                streams=[[mask(raw[s][str(layer)][0],ep)&~(1<<(j%4)) for s in range(1,steps+1)]
                         for j,(_,raw) in enumerate(group)]
                frequency=[]
                for stream in streams:
                    counts=[0]*ep
                    for bits in stream:
                        while bits:
                            low=bits&-bits;counts[low.bit_length()-1]+=1;bits-=low
                    frequency.append(counts)
                shift_expected+=Fraction(sum(sum(a*b for a,b in zip(frequency[i],frequency[j]))
                    for i,j in combinations(range(batch),2)),steps)
                for s in range(steps):
                    bits=[stream[s] for stream in streams]
                    remote=sum(b.bit_count() for b in bits)
                    collision=sum((bits[i]&bits[j]).bit_count() for i,j in combinations(range(batch),2))
                    counts=Counter()
                    for b in bits:
                        while b:
                            low=b&-b;counts[low.bit_length()-1]+=1;b-=low
                    remotes.append(remote);collisions.append(collision)
                    if remote:peaks.append(max(counts.values())*ep/remote)
        n=len(remotes);checked_waves+=n
        p=1-prod((256-256//ep-i)/(256-i) for i in range(8))
        # Count eligible token PAIRS directly instead of summing destination-bin combinations.
        expected_collision=p*p*sum(ep-len({i%4,j%4}) for i,j in combinations(range(batch),2))
        expected=dict(waves=n,remote_vectors_sum=sum(remotes),pair_collisions_sum=sum(collisions),
            reference_activation_payload_bytes=14336*sum(remotes),remote_vector_count=stats(remotes),
            pair_collisions=stats(collisions),peak_to_mean_including_zero_ranks=stats(peaks),
            uniform_expected_remote_vectors_per_wave=batch*(ep-1)*p,
            uniform_expected_pair_collisions_per_wave=expected_collision,
            observed_collision_to_uniform_expectation=sum(collisions)/(n*expected_collision))
        check_row(row,expected);expected_rows[batch,ep]=expected
        strong=strong_rows[batch,ep]
        assert strong['observed_collisions']==sum(collisions)
        equal(strong['independent_cyclic_shift_expected_collisions'],float(shift_expected))
        equal(strong['observed_to_shift_expectation'],sum(collisions)/float(shift_expected))
    # Exact enumeration of a separate 8-expert/Top2/four-owner two-token universe.
    selections=list(combinations(range(8),2));collision_total=0
    for a in selections:
        for b in selections:collision_total+=len(({e//2 for e in a}-{0})&({e//2 for e in b}-{1}))
    p=1-Fraction(6*5,8*7)
    assert Fraction(collision_total,len(selections)**2)==2*p*p
    # Enumerate all independent circular offsets of two tiny streams.
    left,right=[1,3,2],[2,6,4];total=0
    for i in range(3):
        for j in range(3):total+=sum((left[(s+i)%3]&right[(s+j)%3]).bit_count() for s in range(3))
    exact=sum(sum(bool(b&(1<<r)) for b in left)*sum(bool(b&(1<<r)) for b in right) for r in range(3))
    assert Fraction(total,9)==Fraction(exact,3)
    rejected=[]
    row=next(r for r in result['grouped'] if r['batch']==8 and r['ep']==256)
    for field in ('remote_vectors_sum','pair_collisions_sum','uniform_expected_pair_collisions_per_wave',
                  'reference_activation_payload_bytes'):
        bad=dict(row);bad[field]+=1
        try:check_row(bad,expected_rows[8,256])
        except AssertionError:rejected.append(field)
        else:raise AssertionError('corruption accepted')
    assert len(rejected)==4
    return dict(passed=True,raw_complete_files=15,token_layer_rows=routing_rows,
        expert_selections=routing_rows*8,grouped_ep_waves_checked=checked_waves,
        coverage_rows_checked=len(result['coverage']),independent_bitset_and_pair_checks=True,
        independent_exact_fraction_shift_expectation=True,exact_uniform_reference_pairs=len(selections)**2,
        exact_cyclic_shift_pairs=9,corruption_classes=rejected,
        new_networks=0,large_ep_simulation_proven=False,independent_requests=15)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    assert not a.output.exists();a.output.write_text(json.dumps(verify(a.run),indent=2)+'\n')
    print('Independent EP projection, stronger null and corruption checks passed.')
