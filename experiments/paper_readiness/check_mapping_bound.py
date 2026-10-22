"""Check the post-result coverage bound with raw requests and subset assignment DP."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('prepared','evaluation','bound','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();sources=load(a.prepared/'truth/sources.json');data=load(a.prepared/'observed/inputs.json')
    bound=load(a.bound/'bound.json');summary=load(a.evaluation/'summary.json');checked=token_checks=0
    assert bound['summary_sha256']==sha(a.evaluation/'summary.json')
    assert bound['observed_sha256']==sha(a.prepared/'observed/inputs.json') and bound['decode_sha256']==sha(a.prepared/'truth/decode.json')
    assert bound['oracle_reads_future'] and not bound['oracle_is_deployable'] and bound['post_result_mechanism_check']
    for ci,group in enumerate(sources['groups']):
        raws=[]
        for row in group:
            file=Path(row['path']);assert sha(file)==row['sha256'];raws.append(load(file))
        for bi in range(3):
            saved=bound['results'][ci*3+bi];hist=Counter();layers=[]
            for li,layer in enumerate(range(3,61)):
                owners=data['cohorts'][ci]['base_maps'][bi][li];counts=[[0]*4 for _ in range(4)];k_sum=0
                for src,raw in enumerate(raws):
                    for entry in raw[1:]:
                        token=entry[str(layer)][0]
                        hit=[any(owners[e]==group for e in token) for group in range(4)]
                        k=sum(hit);hist[k]+=1;k_sum+=k;token_checks+=1
                        for group,has in enumerate(hit):counts[src][group]+=has
                # dp[mask] assigns the first popcount(mask) source ranks to
                # distinct groups; no permutation enumeration is shared.
                dp={0:0}
                for mask in range(16):
                    if mask not in dp:continue
                    src=mask.bit_count()
                    if src==4:continue
                    for group in range(4):
                        if not (mask>>group)&1:
                            new=mask|(1<<group);dp[new]=max(dp.get(new,-1),dp[mask]+counts[src][group])
                row=dict(layer=layer,total_group_coverage=k_sum,token_count=512,
                    universal_per_token_lower_bound=k_sum-512,best_future_fixed_permutation_copies=k_sum-dp[15],
                    identity_copies=k_sum-sum(counts[r][r] for r in range(4)),coverage_counts_by_source_group=counts)
                assert row==saved['by_layer'][li];layers.append(row);checked+=1
            for key in ('identity_copies','total_group_coverage','token_count','universal_per_token_lower_bound','best_future_fixed_permutation_copies'):
                assert saved[key]==sum(row[key] for row in layers)
            assert saved['coverage_k_histogram']=={str(k):v for k,v in sorted(hist.items())}
            assert saved['identity_copies']==next(r['copies'] for r in summary['conditions'] if (r['cohort'],r['base'],r['policy'])==(ci,bi,0))
            assert saved['universal_per_token_lower_bound']<=saved['best_future_fixed_permutation_copies']<=saved['identity_copies']
    a.output.mkdir();(a.output/'verification.json').write_text(json.dumps(dict(passed=True,layer_assignment_dp_checks=checked,
        raw_token_coverage_checks=token_checks,bound_sha256=sha(a.bound/'bound.json'),
        summary_sha256=sha(a.evaluation/'summary.json'),oracle_is_deployable=False,goal_complete=False),indent=2)+'\n')
    print(json.dumps(dict(passed=True,layer_assignment_dp_checks=checked,raw_token_coverage_checks=token_checks)))


if __name__=='__main__':main()
