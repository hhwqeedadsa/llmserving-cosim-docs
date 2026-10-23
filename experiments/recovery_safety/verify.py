"""Independent order-count and byte-witness audit of saved finite-model results."""
import argparse
from functools import lru_cache
import hashlib
import json
from pathlib import Path


def topological_count(events,edges):
    index={name:i for i,name in enumerate(events)};parents=[0]*len(events)
    for a,b in edges:parents[index[b]]|=1<<index[a]
    full=(1<<len(events))-1
    @lru_cache(None)
    def count(mask):
        if mask==full:return 1
        return sum(count(mask|1<<i) for i in range(len(events)) if not mask>>i&1 and parents[i]&mask==parents[i])
    return count(0)


def inspect_witness(row):
    trace=row['counterexample']['trace'];params=row['parameters'];slot=params['slot'];generation=params['generation']
    positions={event:trace.index(event) for event in row['events']}
    assert len(trace)==len(row['events'])+1 and trace.count('consume')==1
    assert all(positions[a]<positions[b] for a,b in row['edges'])
    consume=trace.index('consume');reuse=positions['reuse']
    assert reuse<consume
    assert positions['new_notice']<consume or (not params['tagged'] and reuse<positions['old_notice']<consume)
    # Independently collect target writes, then select the last write before
    # consumption. No production replay function is called.
    writes=[(reuse,slot,None),(positions['new_data'],slot,generation)]
    if params['revoke']:
        if positions['old_validate']<positions['revoke']:writes.append((positions['old_dma'],0,0))
    else:writes.append((positions['old_data'],0,0))
    before=sorted((t,value) for t,target,value in writes if target==slot and t<consume)
    observed=before[-1][1]
    expected=[]
    if observed!=generation:expected.append('wrong_generation_at_consume')
    if any(t>consume and target==slot and value==0 for t,target,value in writes):expected.append('old_write_after_consume')
    assert sorted(expected)==row['counterexample']['failures'] and expected
    assert observed==row['counterexample']['observed']


def audit(result):
    total=0;witnesses=0
    for row in result['cases']:
        n=topological_count(row['events'],row['edges']);assert n==row['counts']['network_orders'];total+=n
        if row['counterexample']:inspect_witness(row);witnesses+=1
    assert witnesses==4 and total==415
    assert not result['production_vulnerability_demonstrated']
    return dict(passed=True,network_orders=total,independently_replayed_counterexamples=witnesses,
        method='subset-DP order count and last-write byte oracle; not an exhaustive implementation proof')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--summary',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    assert not a.output.exists();result=json.loads(a.summary.read_text());summary=audit(result)
    # A changed event relation must change the combinatorial count, and a
    # changed witness value must be rejected by this separate last-write oracle.
    from copy import deepcopy
    mutations=[]
    for name in ('removed-order','changed-consumed-value'):
        d=deepcopy(result)
        if name=='removed-order':d['cases'][0]['edges'].remove(['timeout','old_ack'])
        else:d['cases'][0]['counterexample']['observed']=99
        try:audit(d)
        except AssertionError:mutations.append(name)
        else:raise AssertionError('independent oracle accepted mutation '+name)
    summary['rejected_mutations']=mutations
    summary['input_sha256']=hashlib.sha256(a.summary.read_bytes()).hexdigest()
    a.output.write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))
if __name__=='__main__':main()
