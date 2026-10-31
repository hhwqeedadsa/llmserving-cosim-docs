"""Independent set/byte arithmetic for the frozen single-token launch shape."""
import argparse
from collections import Counter
import hashlib
import json
from math import ceil
from pathlib import Path


def require(value,message):
    if not value:raise ValueError(message)


def expected(cfg):
    x,m=cfg['execution'],cfg['model'];n=x['ranks'];local=m['experts']//n
    require(n==32 and x['max_tokens_per_rank']==1,'verifier is restricted to the declared EP32 single-token shape')
    require((x['num_sms'],x['dispatch_warps'],x['combine_warps'],x['call_contexts'],x['connections'])==(2,2,2,5,1),
            'verifier requires the fixed public launch shape')
    require((m['experts'],m['hidden_size'],m['top_k'],m['precision'])==(256,7168,8,'bf16'),
            'verifier requires the fixed R1 model shape')
    # Each control send uses context zero, including self. Data from token 0
    # uses Dispatch context one. Return receive indices divide into four
    # contiguous ceil(received/4) groups, mapping directly to contexts 0..3.
    keys={(s,0,d) for s in range(n) for d in range(n)}
    remote=local_work=tokens=destinations=local_pairs=0
    segments=[len(cfg['calls'])]*n
    init=[8*(n+m['experts'])*len(cfg['calls']) for _ in range(n)]
    for call in cfg['calls']:
        pairs=[];active=0
        for src,row in enumerate(call['tokens']):
            require(len(row)<=1,'future token was combined with the current call')
            for choices in row:
                active+=1
                for dst in sorted({e//local for e in choices}):pairs.append((src,dst))
        same=sum(s==d for s,d in pairs);cross=len(pairs)-same
        remote+=6*n*n-n+2*cross
        local_work+=8*n+active+len(pairs)+2*same
        tokens+=active;destinations+=len(pairs);local_pairs+=same
        received=Counter(d for s,d in pairs)
        for src,dst in pairs:
            if src!=dst:
                keys.add((src,1,dst))
                index=sum(1 for a,b in pairs if b==dst and a<src)
                keys.add((dst,index//ceil(received[dst]/4),src))
        for src,row in enumerate(call['tokens']):
            if row:
                segments[src]+=1;init[src]+=32
    for call in cfg['calls']:
        for src,row in enumerate(call['tokens']):
            for choices in row:
                for dst in sorted({e//local for e in choices}):segments[dst]+=1;init[dst]+=32
    return dict(remote=remote,local=local_work,keys=len(keys),self_connections=n,
        calls=len(cfg['calls']),tokens=tokens,destinations=destinations,local_pairs=local_pairs,
        modern_operations=6*n*n*len(cfg['calls'])+2*destinations,segments=segments,initialized_bytes=init)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    frozen=json.loads((a.run/'frozen.json').read_text());records=[]
    for source in frozen['inputs']:
        folder=a.run/f"group-{source['group']}";path=folder/'input.json'
        require(hashlib.sha256(path.read_bytes()).hexdigest()==source['sha256'],'input hash differs')
        cfg=json.loads(path.read_text());e=expected(cfg);r=json.loads((folder/'capacity.json').read_text());c=r['counts']
        require((e['remote'],e['local'])==(c['native_nodes']['remote'],c['native_nodes']['local']),'native work counts differ')
        require(e['keys']==c['prospective_connections'] and e['self_connections']==c['self_connections'],'connection count differs')
        require(e['modern_operations']==c['modern_operations'],'modern operation count differs')
        require(e['segments']==[x['initialized_segments'] for x in c['immutable_operands']],'segments differ')
        require(e['initialized_bytes']==[x['initialized_bytes'] for x in c['immutable_operands']],'initialized bytes differ')
        require(not r['runtime_admitted'] and not r['network_executed'],'static evidence mislabeled')
        require(not r['passed'] and len(r['failures'])>=6,'expected structural failures absent')
        records.append(dict(group=source['group'],independent=e,failures=r['failures']))
    result=dict(passed=True,groups=records,network_runs=0,platform_imports_used=False,
                scope='Independent complete control/data work and connection accounting; no runtime or physical deployment admission')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps(result))


if __name__=='__main__':main()
