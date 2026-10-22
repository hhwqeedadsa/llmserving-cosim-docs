"""Independent raw recount, canonical-permutation enumeration, assignment and Pareto certificates."""
import argparse
from collections import Counter
import hashlib
from itertools import combinations,permutations
import json
from pathlib import Path
import resource
import shutil

ROOT=Path(__file__).resolve().parents[2]


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work',type=Path,required=True);p.add_argument('--repeat',type=Path)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    resource.setrlimit(resource.RLIMIT_AS,(2*2**30,2*2**30));resource.setrlimit(resource.RLIMIT_CPU,(60,60))
    assert not a.output.exists() and shutil.disk_usage(a.output.parent).free>=8*2**30+32*2**20
    work=a.work;data=read(work/'enumerated/candidates.json');oracle=read(work/'optimized/oracle.json')
    assert oracle['candidates_sha256']==sha(work/'enumerated/candidates.json')
    assert data['observed_sha256']==sha(work/'prepared/observed/frequencies.json')
    assert data['decode_sha256']==sha(work/'prepared/truth/decode.json')
    assert oracle['communication_budget']==69409 and oracle['allowed_exceedances']==371 and oracle['percentile_rank']==7053
    parts=set()
    for sequence in permutations(range(8)):
        parts.add(tuple(sorted(tuple(sorted(sequence[j:j+2])) for j in range(0,8,2))))
    parts=sorted(parts);assert len(parts)==105 and data['partitions']==[[list(pair) for pair in part] for part in parts]
    manifest=read(work/'prepared/truth/manifest.json');sources=[];raw={};prefill={};group_count={};token_groups={}
    for src,item in enumerate(manifest['files']):
        file=ROOT/item['file']['path'];assert sha(file)==item['file']['sha256'];sources.append(sha(file));doc=read(file)
        for l in range(3,61):
            prefill[l,src]=doc[0][str(l)]
            for step in range(128):
                token=doc[step+1][str(l)][0];raw[l,step,src]=token
                assert len(token)==len(set(token))==8 and all(type(e) is int and 0<=e<256 for e in token)
                token_groups[l,step,src]=set(e//32 for e in token)
    counts={view:[[0]*256 for _ in range(58)] for view in ('all','tail10')}
    futures=read(work/'prepared/truth/decode.json')['selections']
    for l in range(3,61):
        for src in range(4):
            for view in counts:
                rows=prefill[l,src] if view=='all' else prefill[l,src][-10:]
                for token in rows:
                    for e in token:counts[view][l-3][e]+=1
            for step in range(128):assert futures[l-3][src][step]==raw[l,step,src]
        for step in range(128):
            group_count[l,step]=Counter(e//32 for src in range(4) for e in raw[l,step,src])
    assert counts==read(work/'prepared/observed/frequencies.json')['counts']
    reference=read(work/'enumerated/reference_placements.json');assert sha(work/'enumerated/reference_placements.json')==data['reference_placements_sha256']
    bounds=read(work/'enumerated/prefill_bounds.json');recomputed=[];checks=0
    for li,l in enumerate(range(3,61)):
        records=data['layers'][li];assert records['layer']==l and len(records['candidates'])==105
        coverage={(src,pair):sum(bool(token_groups[l,t,src].intersection(pair)) for t in range(128))
                  for src in range(4) for pair in combinations(range(8),2)}
        group_weights={view:[sum(counts[view][li][g*32:(g+1)*32]) for g in range(8)] for view in counts}
        layer=[]
        for pid,part in enumerate(parts):
            c=records['candidates'][pid];assert c['partition']==pid
            matrix=[[coverage[src,pair] for pair in part] for src in range(4)];total=sum(map(sum,matrix))
            # Subset DP independently maximizes local coverage, retaining the lexicographic witness.
            assign={0:(0,())}
            for j in range(4):
                nxt={}
                for mask,(neg_score,perm) in assign.items():
                    for rank in range(4):
                        if not mask&(1<<rank):
                            key=mask|(1<<rank);value=(neg_score-matrix[rank][j],perm+(rank,))
                            if key not in nxt or value<nxt[key]:nxt[key]=value
                assign=nxt
            score,permutation=assign[15];copies=total+score
            assert c['min_copies']==copies and c['rank_for_pair']==list(permutation)
            assert c['source_pair_coverage']==matrix and c['destination_copies']==total
            maxima=[max(group_count[l,t][x]+group_count[l,t][y] for x,y in part) for t in range(128)]
            hist=Counter(maxima);h=[hist[v] for v in range(33)];assert c['compute_histogram']==h
            pre_max={view:max(group_weights[view][x]+group_weights[view][y] for x,y in part) for view in counts}
            assert c['prefill_max']==pre_max
            layer.append(dict(copies=copies,hist=h,prefill_max=pre_max));checks+=1
        for view in counts:
            optimum=min(c['prefill_max'][view] for c in layer)
            owners=reference['placements']['eplb_hier_'+view+'_adapter'][li]
            greedy=max(sum(counts[view][li][e] for e in range(256) if owners[e]==rank) for rank in range(4))
            assert bounds['by_layer'][li]['views'][view]==dict(greedy_max=greedy,exact_minimax=optimum,greedy_is_optimal=greedy==optimum)
            ordered=sorted(group_weights[view]);assert optimum==max(ordered[j]+ordered[7-j] for j in range(4))
        recomputed.append(layer)
    def frontier_at(threshold):
        frontier={0:0};peak=1;transitions=0
        for layer in recomputed:
            options={(sum(c['hist'][threshold+1:]),c['copies']) for c in layer};expanded={}
            for exp,cost in frontier.items():
                for more,extra in options:
                    e=exp+more
                    if e<=371:
                        transitions+=1;c=cost+extra
                        if e not in expanded or c<expanded[e]:expanded[e]=c
            # Smaller exceedance with no higher cost dominates a state for every future layer.
            frontier={};best=10**12
            for exp,cost in sorted(expanded.items()):
                if cost<best:frontier[exp]=cost;best=cost
            peak=max(peak,len(frontier))
        if not frontier:return dict(threshold=threshold,minimum_copies=None,feasible=False,peak_states=peak,transitions=transitions)
        cost,exp=min((cost,exp) for exp,cost in frontier.items())
        return dict(threshold=threshold,minimum_copies=cost,feasible=cost<=69409,exceedances=exp,peak_states=peak,transitions=transitions)
    certificates=[]
    for result in oracle['probes']:
        independent=frontier_at(result['threshold'])
        for key in ('threshold','minimum_copies','feasible'):assert independent[key]==result[key]
        certificates.append(independent)
    unconstrained={t:sum(min(sum(c['hist'][t+1:]) for c in layer) for layer in recomputed) for t in range(8,33)}
    assert min(t for t,n in unconstrained.items() if n<=371)==oracle['best_p95_without_communication_budget']
    best=oracle['best_p95_with_communication_budget'];assert frontier_at(best)['feasible']
    if best>8:assert not frontier_at(best-1)['feasible']
    witness=oracle['witness'];assert len(witness['selected'])==58 and witness['reads_future_decode'] and not witness['deployable']
    actual_hist=Counter();baseline_hist=Counter();actual_copies=baseline_copies=0
    for li,selected in enumerate(witness['selected']):
        l=li+3;assert selected['layer']==l;part=parts[selected['partition']];ranks=selected['rank_for_pair']
        assert sorted(ranks)==list(range(4));owners={g:ranks[j] for j,pair in enumerate(part) for g in pair}
        assert Counter(owners.values())=={0:2,1:2,2:2,3:2};layer_copies=0
        for t in range(128):
            dest=[set(owners[e//32] for e in raw[l,t,s]) for s in range(4)]
            base=[set(e//64 for e in raw[l,t,s]) for s in range(4)]
            n=sum(len(ds-{s}) for s,ds in enumerate(dest));layer_copies+=n;actual_copies+=n
            baseline_copies+=sum(len(ds-{s}) for s,ds in enumerate(base))
            compute=Counter(owners[e//32] for s in range(4) for e in raw[l,t,s]);actual_hist[max(compute.values())]+=1
            baseline=Counter(e//64 for s in range(4) for e in raw[l,t,s]);baseline_hist[max(baseline.values())]+=1
        assert selected['copies']==layer_copies
    assert baseline_copies==69409 and [k for k,v in sorted(baseline_hist.items()) for _ in range(v)][7052]==15
    assert actual_copies==witness['minimum_copies']<=69409 and [actual_hist[v] for v in range(33)]==witness['compute_histogram']
    assert sum(v for k,v in actual_hist.items() if k>best)==witness['exceedances']<=371
    assert [k for k,v in sorted(actual_hist.items()) for _ in range(v)][7052]==best==witness['p95']
    repeat_files=0
    if a.repeat:
        assert (a.repeat/'oracle.json').read_bytes()==(work/'optimized/oracle.json').read_bytes();repeat_files=1
    a.output.mkdir()
    save(a.output/'certificates.json',dict(pareto_threshold_checks=certificates,
        minimum_exceedances_without_copy_budget=unconstrained,
        minimum_total_copies_without_compute_constraint=sum(min(c['copies'] for c in layer) for layer in recomputed),
        witness_histogram=dict(sorted(actual_hist.items())),baseline_histogram=dict(sorted(baseline_hist.items()))))
    save(a.output/'verification.json',dict(completed=True,raw_source_hashes=sources,canonical_permutations=40320,
        distinct_pairings=105,candidates_checked=checks,assignment_dp_checks=checks,compute_windows_checked=checks*128,
        witness_windows_checked=7424,baseline_windows_checked=7424,repeat_files=repeat_files,
        oracle_sha256=sha(work/'optimized/oracle.json'),candidates_sha256=sha(work/'enumerated/candidates.json'),
        checker_sha256=sha(Path(__file__)),best_p95=best,witness_copies=actual_copies,
        reads_future_decode=True,deployable=False,network_executed=False,hardware_calibrated=False))
    print(json.dumps(dict(completed=True,candidates=checks,best_p95=best,witness_copies=actual_copies,
        minimum_exceedances_at_previous_threshold=unconstrained.get(best-1),repeat_files=repeat_files)))


if __name__=='__main__':main()
