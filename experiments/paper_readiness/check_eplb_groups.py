"""Check the group adapter from raw routes, source blobs, greedy inequalities and all CSV cells."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import resource
import shutil

ROOT=Path(__file__).resolve().parents[2]


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def distribution(values):
    ordered=sorted(values);n=len(ordered)
    return dict(count=n,minimum=ordered[0],p50=ordered[(n+1)//2-1],p95=ordered[(95*n+99)//100-1],maximum=ordered[-1])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for flag in ('work','source-commits','source-trees','source-code','output'):
        p.add_argument('--'+flag,type=Path,required=True)
    a=p.parse_args();resource.setrlimit(resource.RLIMIT_AS,(2*2**30,2*2**30))
    assert not a.output.exists() and shutil.disk_usage(a.output.parent).free>=8*2**30+32*2**20
    # Check all fetched code against exact commit -> tree -> blob identities, without execution.
    identities=[]
    for stem,commit,files in (
        ('eplb','d52c72d5b2f2fb4c41afbf8eb21366820239913d',{'eplb.py':'eplb.py','eplb-README.md':'README.md'}),
        ('ta-moe','b3aff038bba430f34790e1cd196821d37da7226a',{'ta-gshard-gate.py':'examples/language_model/moe/dygraph/moe/gate/gshard_gate.py'})):
        metadata=read(a.source_commits/(stem+'-commit.json'));assert len(metadata)==1 and metadata[0]['sha']==commit
        tree=read(a.source_trees/(stem+'-tree.json'));assert not tree['truncated'] and tree['sha']==metadata[0]['commit']['tree']['sha']
        by_path={r['path']:r for r in tree['tree']}
        for local,path in files.items():
            file=a.source_code/local;data=file.read_bytes();row=by_path[path]
            assert row['type']=='blob' and len(data)==row['size']
            assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==row['sha']
            identities.append(dict(repository=stem,commit=commit,path=path,blob=row['sha'],sha256=sha(file)))
    work=a.work;manifest=read(work/'prepared/truth/manifest.json');observed=read(work/'prepared/observed/frequencies.json')
    counts={view:[[0]*256 for _ in range(58)] for view in ('all','tail10')};raw={};source_shas=[]
    for s,item in enumerate(manifest['files']):
        file=ROOT/item['file']['path'];assert sha(file)==item['file']['sha256'];source_shas.append(sha(file));doc=read(file)
        for l in range(3,61):
            for view,rows in (('all',doc[0][str(l)]),('tail10',doc[0][str(l)][-10:])):
                for token in rows:
                    for expert in token:counts[view][l-3][expert]+=1
            for t in range(128):raw[l,s,t]=doc[t+1][str(l)][0]
    assert counts==observed['counts']
    planned=read(work/'planned/placements.json');packing=read(work/'planned/packing.json');maps=planned['placements']
    assert planned['observed_sha256']==sha(work/'prepared/observed/frequencies.json')
    assert planned['source_sha256']==sha(a.source_code/'eplb.py')
    reference=read(ROOT/'docs/review_artifacts/prefill_placement_20261022/placements.json')
    for name,owners in reference['placements'].items():assert maps[name]==owners
    decisions=0
    for view in ('all','tail10'):
        name='eplb_hier_'+view+'_adapter';tied=[]
        for l in range(3,61):
            owners=maps[name][l-3];assert Counter(owners)=={0:64,1:64,2:64,3:64}
            groups=[sum(counts[view][l-3][g*32:g*32+32]) for g in range(8)]
            if len(set(groups))<8:tied.append(l)
            rec=packing['records'][name][l-3];order=rec['order'];assert sorted(order)==list(range(8))
            assert all((-groups[x],x)<=(-groups[y],y) for x,y in zip(order,order[1:]))
            loads=[0]*4;used=[0]*4
            for g in order:
                destinations=set(owners[g*32:g*32+32]);assert len(destinations)==1
                node=destinations.pop();assert used[node]<2
                assert all((loads[node],node)<=(loads[n],n) for n in range(4) if used[n]<2)
                assert rec['group_owner'][g]==node
                loads[node]+=groups[g];used[node]+=1;decisions+=1
            assert used==[2]*4 and rec['node_load']==loads and rec['group_weights']==groups
        assert tied==packing['group_weight_tie_layers'][name]
    for name in ('placements.json','packing.json'):
        assert (work/'planned'/name).read_bytes()==(work/'repeat-planned'/name).read_bytes()
    assert sorted(f.name for f in (work/'relocated-observed').iterdir())==['frequencies.json']
    assert sha(work/'relocated-observed/frequencies.json')==sha(work/'prepared/observed/frequencies.json')
    summary=read(work/'evaluated/summary.json');assert summary['placements_sha256']==sha(work/'planned/placements.json')
    assert summary['window_csv_sha256']==sha(work/'evaluated/windows.csv')
    futures=read(work/'prepared/truth/decode.json')['selections']
    assert all(futures[l-3][s][t]==row for (l,s,t),row in raw.items())
    values={};windows=0
    with (work/'evaluated/windows.csv').open(newline='') as f:
        rows=csv.DictReader(f)
        for result in summary['conditions']:
            name=result['condition'];copies=[];maxcompute=[];incoming=[];remote=0;layers=[];values[name]=[]
            for l in range(3,61):
                lc=[];lcomp=[];le=0
                for t in range(128):
                    mat=[[0]*4 for _ in range(4)];compute=[0]*4;ne=0
                    for s in range(4):
                        token=raw[l,s,t];assert len(token)==len(set(token))==8
                        for expert in token:
                            d=maps[name][l-3][expert];compute[d]+=1;ne+=d!=s
                            if d!=s:mat[s][d]=1
                    bitstring=''.join(str(n) for row in mat for n in row);nc=bitstring.count('1')
                    inc=max(sum(mat[s][d] for s in range(4)) for d in range(4));mc=max(compute)
                    assert next(rows)==dict(condition=name,layer=str(l),entry=str(t+1),direct_copies=str(nc),
                        remote_expert_contributions=str(ne),incoming_max=str(inc),compute_max=str(mc),
                        matrix_bits=bitstring,compute_by_rank=':'.join(map(str,compute)))
                    assert sum(compute)==32;copies.append(nc);maxcompute.append(mc);incoming.append(inc)
                    lc.append(nc);lcomp.append(mc);le+=ne;remote+=ne;values[name].append((nc,inc,mc));windows+=1
                layers.append(dict(layer=l,direct_copies=sum(lc),remote_expert_contributions=le,compute_max=distribution(lcomp)))
            assert result['direct_copies']==sum(copies) and result['remote_expert_contributions']==remote
            assert result['incoming_max']==distribution(incoming) and result['compute_max']==distribution(maxcompute)
            assert result['by_layer']==layers
            for j,metric in enumerate(('direct_copies','incoming_max','compute_max')):
                actual=Counter('lower' if x[j]<y[j] else 'higher' if x[j]>y[j] else 'equal' for x,y in zip(values[name],values['contiguous']))
                assert result['paired_with_contiguous'][metric]=={key:actual[key] for key in ('lower','equal','higher')}
        assert next(rows,None) is None
    pairs=[]
    for view in ('all','tail10'):
        before=values['prefill_'+view+'_lpt'];after=values['eplb_hier_'+view+'_adapter'];metrics={}
        for j,metric in enumerate(('direct_copies','incoming_max','compute_max')):
            hist=Counter('lower' if x[j]<y[j] else 'higher' if x[j]>y[j] else 'equal' for x,y in zip(after,before))
            metrics[metric]={key:hist[key] for key in ('lower','equal','higher')}
        pairs.append(dict(view=view,adapter_vs_flat_lpt=metrics))
    a.output.mkdir();save(a.output/'paired.json',dict(pairs=pairs))
    save(a.output/'source_identities.json',dict(identities=identities,downloaded_code_executed=False))
    save(a.output/'verification.json',dict(completed=True,conditions=5,windows=windows,matrix_entries=16*windows,
        raw_token_selections=4*windows,source_hashes=source_shas,greedy_decisions_checked=decisions,
        summary_sha256=sha(work/'evaluated/summary.json'),csv_sha256=sha(work/'evaluated/windows.csv'),
        observed_sha256=sha(work/'prepared/observed/frequencies.json'),placements_sha256=sha(work/'planned/placements.json'),
        checker_sha256=sha(Path(__file__)),repeat_files=2,upstream_eplb_runtime_executed=False,
        network_executed=False,gpu_executed=False,hardware_calibrated=False))
    print(json.dumps(dict(completed=True,windows=windows,decisions=decisions,paired=pairs)))


if __name__=='__main__':main()
