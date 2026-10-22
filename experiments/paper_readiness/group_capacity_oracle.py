"""Exact conditional bounds for static pairs of eight logical groups; evaluation oracle only."""
import argparse
from collections import Counter
import hashlib
from itertools import combinations, permutations
import json
from pathlib import Path
import resource
import shutil
import sys
import time


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,separators=(',',':'))+'\n')


def matchings(items):
    if not items:
        yield ()
    else:
        first=items[0]
        for other in items[1:]:
            rest=tuple(x for x in items if x not in (first,other))
            for tail in matchings(rest):yield ((first,other),)+tail


def enumerate_candidates(a):
    prepared=a.prepared;obs=read(prepared/'observed/frequencies.json');future=read(prepared/'truth/decode.json')['selections']
    assert sha(a.reference_placements)=='5b32f1977a694d2a382d2f1e0e9f7dca4ed8b6edaefc0da603ce51b153ad38dd'
    reference=read(a.reference_placements);assert reference['observed_sha256']==sha(prepared/'observed/frequencies.json')
    assert obs['experts']==256 and obs['ranks']==4 and obs['source_layers']==list(range(3,61))
    parts=list(matchings(tuple(range(8))));assert len(parts)==len(set(parts))==105
    pair_list=list(combinations(range(8),2));pair_index={p:i for i,p in enumerate(pair_list)}
    masks=[(1<<x)|(1<<y) for x,y in pair_list];perms=list(permutations(range(4)));layers=[];bounds=[]
    for li in range(58):
        group_counts=[[0]*8 for _ in range(128)];token_masks=[[] for _ in range(4)]
        for src in range(4):
            for step,token in enumerate(future[li][src]):
                assert len(token)==len(set(token))==8
                mask=0
                for e in token:group_counts[step][e//32]+=1;mask|=1<<(e//32)
                assert mask.bit_count()<=4;token_masks[src].append(mask)
        # Coverage counts depend on source and group pair, not rank labels.
        covered=[[sum(bool(mask&pm) for mask in src) for pm in masks] for src in token_masks]
        pair_compute=[[row[x]+row[y] for row in group_counts] for x,y in pair_list]
        pre={view:[sum(w[g*32:g*32+32]) for g in range(8)] for view in ('all','tail10') for w in [obs['counts'][view][li]]}
        candidates=[]
        for pid,part in enumerate(parts):
            ids=[pair_index[pair] for pair in part];coverage=[[covered[s][j] for j in ids] for s in range(4)]
            total=sum(sum(row) for row in coverage)
            cost,perm=min((total-sum(coverage[perm[j]][j] for j in range(4)),perm) for perm in perms)
            hist=Counter(max(pair_compute[j][step] for j in ids) for step in range(128))
            candidates.append(dict(partition=pid,min_copies=cost,rank_for_pair=list(perm),
                destination_copies=total,source_pair_coverage=coverage,
                compute_histogram=[hist[i] for i in range(33)],
                prefill_max={view:max(pre[view][x]+pre[view][y] for x,y in part) for view in pre}))
        layers.append(dict(layer=li+3,candidates=candidates))
        entry=dict(layer=li+3,views={})
        for view in pre:
            owners=reference['placements']['eplb_hier_'+view+'_adapter'][li]
            load=[sum(w for e,w in enumerate(obs['counts'][view][li]) if owners[e]==rank) for rank in range(4)]
            optimum=min(c['prefill_max'][view] for c in candidates)
            entry['views'][view]=dict(greedy_max=max(load),exact_minimax=optimum,greedy_is_optimal=max(load)==optimum)
        bounds.append(entry)
    a.output.mkdir()
    save(a.output/'candidates.json',dict(schema_version='moe-static-group-capacity/1',partitions=parts,layers=layers,
        observed_sha256=sha(prepared/'observed/frequencies.json'),decode_sha256=sha(prepared/'truth/decode.json'),
        reference_placements_sha256=sha(a.reference_placements),reads_future_decode=True,deployable=False))
    save(a.output/'prefill_bounds.json',dict(by_layer=bounds,
        optimal_layers={view:sum(r['views'][view]['greedy_is_optimal'] for r in bounds) for view in pre},
        scope='Maximum aggregate Prefill count only, not Decode p95 or latency'))
    shutil.copy2(a.reference_placements,a.output/'reference_placements.json')
    print(json.dumps(dict(partitions=105,layers=58,candidates=6090,labelings=146160,
        greedy_optimal_layers={view:sum(r['views'][view]['greedy_is_optimal'] for r in bounds) for view in pre})))


def optimize(a):
    data=read(a.candidates/'candidates.json');layers=data['layers'];limit=371;budget=69409;inf=10**12
    assert len(layers)==58 and len(data['partitions'])==105
    def solve(threshold,witness=False):
        dp=[inf]*(limit+1);dp[0]=0;history=[];transitions=0
        for layer in layers:
            # For equal exceedance count only the least-copy candidate matters.
            choices={}
            for c in layer['candidates']:
                exp=sum(c['compute_histogram'][threshold+1:]);key=(c['min_copies'],c['partition'])
                if exp not in choices or key<choices[exp]:choices[exp]=key
            nxt=[inf]*(limit+1);back=[None]*(limit+1)
            for exp,cost in enumerate(dp):
                if cost==inf:continue
                for add,(extra,pid) in sorted(choices.items()):
                    total=exp+add
                    if total<=limit:
                        transitions+=1
                        if cost+extra<nxt[total]:nxt[total]=cost+extra;back[total]=(exp,pid)
            dp=nxt
            if witness:history.append(back)
            if min(dp)==inf:return dict(threshold=threshold,feasible=False,minimum_copies=None,transitions=transitions)
        cost,exp=min((cost,exp) for exp,cost in enumerate(dp))
        result=dict(threshold=threshold,feasible=cost<=budget,minimum_copies=cost,exceedances=exp,transitions=transitions)
        if witness:
            ids=[]
            for back in reversed(history):exp,pid=back[exp];ids.append(pid)
            ids.reverse();hist=[0]*33;selected=[];copies=0
            for layer,pid in zip(layers,ids):
                c=layer['candidates'][pid];assert c['partition']==pid;copies+=c['min_copies']
                hist=[x+y for x,y in zip(hist,c['compute_histogram'])]
                selected.append(dict(layer=layer['layer'],partition=pid,rank_for_pair=c['rank_for_pair'],copies=c['min_copies']))
            assert sum(hist)==7424 and copies==cost
            p95=next(t for t in range(33) if sum(hist[:t+1])>=7053)
            result.update(selected=selected,compute_histogram=hist,p95=p95,reads_future_decode=True,deployable=False)
        return result
    cache={}
    def probe(t):
        if t not in cache:cache[t]=solve(t)
        return cache[t]
    assert probe(32)['feasible']
    lo,hi=8,32
    while lo<hi:
        mid=(lo+hi)//2
        if probe(mid)['feasible']:hi=mid
        else:lo=mid+1
    constrained=solve(lo,witness=True);assert constrained['feasible'] and constrained['p95']==lo
    if lo>8:assert not probe(lo-1)['feasible']
    unconstrained=next(t for t in range(8,33) if sum(min(sum(c['compute_histogram'][t+1:]) for c in layer['candidates']) for layer in layers)<=limit)
    a.output.mkdir()
    save(a.output/'oracle.json',dict(schema_version='moe-static-group-oracle/1',communication_budget=budget,
        windows=7424,percentile_rank=7053,allowed_exceedances=limit,
        best_p95_without_communication_budget=unconstrained,best_p95_with_communication_budget=lo,
        probes=[cache[t] for t in sorted(cache)],witness=constrained,candidates_sha256=sha(a.candidates/'candidates.json'),
        reads_future_decode=True,deployable=False,network_executed=False,latency_predicted=False))
    print(json.dumps(dict(best_p95_unconstrained=unconstrained,best_p95_with_copy_budget=lo,
        witness_copies=constrained['minimum_copies'],witness_exceedances=constrained['exceedances'],thresholds=sorted(cache))))


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('enumerate');q.add_argument('--prepared',type=Path,required=True);q.add_argument('--reference-placements',type=Path,required=True)
    r=sub.add_parser('optimize');r.add_argument('--candidates',type=Path,required=True)
    for s in (q,r):s.add_argument('--output',type=Path,required=True)
    a=p.parse_args();start=time.monotonic();resource.setrlimit(resource.RLIMIT_AS,(2*2**30,2*2**30));resource.setrlimit(resource.RLIMIT_CPU,(60,60))
    assert not a.output.exists() and shutil.disk_usage(a.output.parent).free>=8*2**30+32*2**20
    (enumerate_candidates if a.command=='enumerate' else optimize)(a)
    assert sum(f.stat().st_size for f in a.output.iterdir())<=32*2**20
    save(a.output/'execution.json',dict(argv=sys.argv,cwd=str(Path.cwd()),script_sha256=sha(Path(__file__)),
        wall_seconds=time.monotonic()-start,peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))


if __name__=='__main__':main()
