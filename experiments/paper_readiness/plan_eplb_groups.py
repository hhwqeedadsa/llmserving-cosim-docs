"""Prefill-count adapter of EPLB's group-to-node packing, EP4/one GPU per node/no replicas."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import resource
import shutil


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,separators=(',',':'))+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for flag in ('observed','reference-placements','reference-source','output'):
        p.add_argument('--'+flag,type=Path,required=True)
    a=p.parse_args();resource.setrlimit(resource.RLIMIT_AS,(2*2**30,2*2**30))
    assert not a.output.exists() and shutil.disk_usage(a.output.parent).free>=8*2**30+16*2**20
    assert sha(a.reference_source)=='25b37ccd83aef2369c68f2b4bb596608a4f289956d97d9a3504bfd3159459e29'
    assert sha(a.reference_placements)=='dcdf9220498839182591250f114d85f75bbe851e376c9bf828a82c384964d3e7'
    data=json.loads((a.observed/'frequencies.json').read_text());reference=json.loads(a.reference_placements.read_text())
    assert reference['observed_sha256']==sha(a.observed/'frequencies.json')
    assert data['schema_version']=='moe-prefill-counts/1' and data['source_layers']==list(range(3,61))
    assert data['experts']==256 and data['ranks']==4
    placements=dict(reference['placements']);traces={};tie_layers={}
    assert list(placements)==['contiguous','prefill_all_lpt','prefill_tail10_lpt']
    for view in ('all','tail10'):
        rows=[];records=[];tied=[]
        for li,weights in enumerate(data['counts'][view]):
            assert len(weights)==256 and all(type(w) is int and w>=0 for w in weights)
            group_weights=[sum(weights[g*32:g*32+32]) for g in range(8)]
            if len(set(group_weights))<8:tied.append(li+3)
            order=sorted(range(8),key=lambda g:(-group_weights[g],g))
            node_load=[0]*4;used=[0]*4;group_owner=[None]*8
            for group in order:
                node=min((n for n in range(4) if used[n]<2),key=lambda n:(node_load[n],n))
                group_owner[group]=node;node_load[node]+=group_weights[group];used[node]+=1
            owner=[group_owner[e//32] for e in range(256)]
            assert used==[2]*4 and Counter(owner)=={0:64,1:64,2:64,3:64}
            rows.append(owner);records.append(dict(layer=li+3,group_weights=group_weights,order=order,
                group_owner=group_owner,node_load=node_load))
        key='eplb_hier_'+view+'_adapter';placements[key]=rows;traces[key]=records;tie_layers[key]=tied
    a.output.mkdir()
    save(a.output/'placements.json',dict(schema_version='moe-prefill-placements/1',
        observed_sha256=sha(a.observed/'frequencies.json'),placements=placements,
        method='Independent count adapter of fixed EPLB group packing; 4 nodes / 4 active GPUs / 256 physical experts',
        reads_decode_or_truth=False,upstream_runtime_executed=False,
        upstream_commit='d52c72d5b2f2fb4c41afbf8eb21366820239913d',source_sha256=sha(a.reference_source),
        tie_break='group weight desc / original group ID asc; minimum node load / node ID asc',
        tie_equivalence_to_torch_claimed=False))
    save(a.output/'packing.json',dict(records=traces,group_weight_tie_layers=tie_layers))
    print(json.dumps(dict(conditions=list(placements),group_weight_tie_layers=tie_layers,upstream_runtime_executed=False)))


if __name__=='__main__':main()
