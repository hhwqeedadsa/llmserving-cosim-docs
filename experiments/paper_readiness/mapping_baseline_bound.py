"""Evaluation-only group-coverage lower bound and future-aware fixed-per-layer oracle."""
import argparse
from collections import Counter
import hashlib
from itertools import permutations
import json
from pathlib import Path


def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('prepared','evaluation','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();data=load(a.prepared/'observed/inputs.json');future=load(a.prepared/'truth/decode.json')['cohorts']
    summary=load(a.evaluation/'summary.json');results=[]
    for ci,cohort in enumerate(data['cohorts']):
        for bi in range(3):
            layers=[];hist=Counter()
            for li in range(58):
                owners=cohort['base_maps'][bi][li];coverage=[[0]*4 for _ in range(4)];group_total=0
                for src in range(4):
                    for token in future[ci][li][src]:
                        groups={owners[e] for e in token};hist[len(groups)]+=1;group_total+=len(groups)
                        for group in groups:coverage[src][group]+=1
                candidates=[(sum(coverage[perm[group]][group] for group in range(4)),perm) for perm in permutations(range(4))]
                best=max(score for score,perm in candidates)
                layers.append(dict(layer=li+3,total_group_coverage=group_total,token_count=512,
                    universal_per_token_lower_bound=group_total-512,
                    best_future_fixed_permutation_copies=group_total-best,
                    identity_copies=group_total-sum(coverage[r][r] for r in range(4)),
                    coverage_counts_by_source_group=coverage))
            identity=next(r['copies'] for r in summary['conditions'] if (r['cohort'],r['base'],r['policy'])==(ci,bi,0))
            assert identity==sum(r['identity_copies'] for r in layers)
            results.append(dict(cohort=ci,base=bi,base_name=data['base_names'][bi],identity_copies=identity,
                total_group_coverage=sum(r['total_group_coverage'] for r in layers),token_count=29696,
                coverage_k_histogram=dict(sorted(hist.items())),
                universal_per_token_lower_bound=sum(r['universal_per_token_lower_bound'] for r in layers),
                best_future_fixed_permutation_copies=sum(r['best_future_fixed_permutation_copies'] for r in layers),by_layer=layers))
    a.output.mkdir();(a.output/'bound.json').write_text(json.dumps(dict(results=results,
        observed_sha256=sha(a.prepared/'observed/inputs.json'),decode_sha256=sha(a.prepared/'truth/decode.json'),
        summary_sha256=sha(a.evaluation/'summary.json'),post_result_mechanism_check=True,
        oracle_reads_future=True,oracle_is_deployable=False,latency_predicted=False,goal_complete=False),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps([{k:v for k,v in r.items() if k!='by_layer'} for r in results]))


if __name__=='__main__':main()
