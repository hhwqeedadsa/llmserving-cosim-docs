"""Independent cyclic-shift expectation, preserving each request's rank sets."""
import argparse
from collections import Counter
from itertools import combinations
import json
from pathlib import Path

from experiments.ep_network_projection.study import EPS, destinations, load, save, sha


def analyze(run, output):
    assert not output.exists(); output.mkdir()
    source=load(run/'observed/source.json'); originals=[]
    for row in source['files']:
        p=run/'observed/routes'/row['name']; assert sha(p)==row['sha256']
        originals.append(dict(name=row['name'],raw=load(p)))
    rows=[]
    for batch in (4,8):
        groups=[originals[i:i+batch] for i in range(0,len(originals)-batch+1,batch)]
        for ep in EPS:
            expected=0.; actual=0; layer_rows=[]
            for layer in range(3,61):
                layer_expected=0.;layer_actual=0
                for group in groups:
                    steps=min(len(r['raw'])-1 for r in group)
                    streams=[[destinations(r['raw'][s][str(layer)][0],ep)-{j%4}
                              for s in range(1,steps+1)] for j,r in enumerate(group)]
                    frequency=[Counter(d for ds in stream for d in ds) for stream in streams]
                    for i,j in combinations(range(batch),2):
                        layer_expected+=sum(n*frequency[j][d] for d,n in frequency[i].items())/steps
                        layer_actual+=sum(len(a&b) for a,b in zip(streams[i],streams[j]))
                expected+=layer_expected;actual+=layer_actual
                layer_rows.append(dict(layer=layer,observed_collisions=layer_actual,
                    independent_cyclic_shift_expected_collisions=layer_expected,
                    observed_to_shift_expectation=layer_actual/layer_expected if layer_expected else None))
            rows.append(dict(batch=batch,ep=ep,observed_collisions=actual,
                independent_cyclic_shift_expected_collisions=expected,
                observed_to_shift_expectation=actual/expected if expected else None,layers=layer_rows))
    save(output/'summary.json',dict(schema_version='ep-network-marginals/1',rows=rows,
        reference='independent_uniform_cyclic_shifts_of_each_request_within_common_prefix',
        marginal_rank_popularity_preserved=True,cyclic_within_request_order_preserved=True,
        routes_changed_or_duplicated=False,future_prediction_claimed=False,new_networks=0,
        significance_test=False))
    print('Computed a stronger rank-popularity-preserving expectation; no network execution.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();analyze(a.run.resolve(),a.output.resolve())
