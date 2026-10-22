"""Post-result accounting of the oracle witness's Prefill tradeoff and full histogram."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--work',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();w=a.work;assert not a.output.exists() and shutil.disk_usage(a.output.parent).free>=8*2**30+2**20
    verified=read(w/'verified/verification.json');assert verified['completed']
    assert verified['oracle_sha256']==sha(w/'optimized/oracle.json') and verified['candidates_sha256']==sha(w/'enumerated/candidates.json')
    data=read(w/'enumerated/candidates.json');oracle=read(w/'optimized/oracle.json');bounds=read(w/'enumerated/prefill_bounds.json')
    tradeoffs=[]
    for view in ('all','tail10'):
        rows=[]
        for selected,layer,bound in zip(oracle['witness']['selected'],data['layers'],bounds['by_layer']):
            value=layer['candidates'][selected['partition']]['prefill_max'][view];optimum=bound['views'][view]['exact_minimax']
            assert value>=optimum
            rows.append(dict(layer=layer['layer'],witness_prefill_max=value,optimal_prefill_max=optimum,increase=value-optimum))
        tradeoffs.append(dict(view=view,worse_layers=sum(r['increase']>0 for r in rows),equal_layers=sum(r['increase']==0 for r in rows),
            maximum_absolute_increase=max(r['increase'] for r in rows),by_layer=rows))
    cert=read(w/'verified/certificates.json');distributions={}
    for name,key in (('oracle_witness','witness_histogram'),('contiguous','baseline_histogram')):
        counts={int(k):v for k,v in cert[key].items()};n=sum(counts.values());values=sorted(counts)
        result=dict(count=n,minimum=min(values),maximum=max(values))
        for label,percent in (('p50',50),('p95',95),('p99',99)):
            target=(n*percent+99)//100;seen=0
            for value in values:
                seen+=counts[value]
                if seen>=target:result[label]=value;break
        distributions[name]=result
    a.output.mkdir();result=dict(post_result_explanation_only=True,extra_optimization_conditions=0,
        oracle_sha256=verified['oracle_sha256'],tradeoffs=tradeoffs,compute_input_distributions=distributions,
        reads_future_decode=True,deployable=False,latency_predicted=False)
    (a.output/'diagnostic.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(tradeoffs=[{k:v for k,v in row.items() if k!='by_layer'} for row in tradeoffs],distributions=distributions)))


if __name__=='__main__':main()
