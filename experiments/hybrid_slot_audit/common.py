"""Observed declarations and bounded schedules, without truth inputs."""
from collections import deque
from itertools import product
import hashlib
import json

COMMIT='f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
COUNTS=(6,90,24,6,20,4)
POLICIES=('ascending-round','descending-round','ascending-drain')


def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def encoded(v):return (json.dumps(v,sort_keys=True,separators=(',',':'))+'\n').encode()
def digest(v):return hashlib.sha256(encoded(v)).hexdigest()
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(encoded(v))


def key(ingress,channel):return str(ingress)+':'+str(channel)


def streams(plan):
    result={}
    for ingress in range(plan['ranks_per_domain']):
        for channel in range(plan['channels_per_rank']):
            sources=sorted((c for c in plan['channels'] if c['target']==ingress and c['channel']==channel),
                           key=lambda c:c['source_domain'])
            assert len(sources)==plan['domains']
            queues=[]
            for c in sources:
                chunks=[c['transfers'][start:start+6] for start in range(0,len(c['transfers']),6)]
                if len(c['transfers'])%6==0:chunks.append([])  # explicit independent finish turn
                queues.append(deque(chunks))
            word=[];last=channel%plan['domains']
            while any(queues):
                peer=next((last+j)%len(queues) for j in range(1,len(queues)+1) if queues[(last+j)%len(queues)])
                chunk=queues[peer].popleft();last=peer
                for i,name in enumerate(chunk):word.append(dict(dispatch=name,last_in_chunk=i+1==len(chunk)))
            result[key(ingress,channel)]=word
    return result


def shuffles(lengths,prefix=()):
    if not any(lengths):yield prefix;return
    for i,n in enumerate(lengths):
        if n:
            updated=list(lengths);updated[i]-=1
            yield from shuffles(tuple(updated),prefix+(i,))


def schedules(plan,declared,synthetic):
    per_ingress=[]
    for ingress in range(plan['ranks_per_domain']):
        keys=[key(ingress,c) for c in range(plan['channels_per_rank'])]
        lengths=[len(declared[k]) for k in keys]
        if synthetic:
            per_ingress.append([[keys[i] for i in sequence] for sequence in shuffles(tuple(lengths))])
        else:per_ingress.append((keys,lengths))
    if synthetic:
        for i,parts in enumerate(product(*per_ingress)):
            assert i<10000
            yield str(i).zfill(4),[k for part in parts for k in part]
    else:
        for policy in POLICIES:
            sequence=[]
            for keys,lengths in per_ingress:
                positions=list(reversed(range(len(keys)))) if policy=='descending-round' else list(range(len(keys)))
                if policy=='ascending-drain':sequence.extend(k for k,n in zip(keys,lengths) for _ in range(n))
                else:
                    for step in range(max(lengths,default=0)):
                        sequence.extend(keys[i] for i in positions if step<lengths[i])
            yield policy,sequence


def config(scaleup,experts,topk,hidden,channels,tokens,name):
    return dict(schema_version='moe-hybrid-logical-input/1',
        model=dict(experts=experts,top_k=topk,hidden_size=hidden,precision='bf16'),
        deployment=dict(domains=[dict(name='server-'+str(i),ranks=list(range(i*scaleup,(i+1)*scaleup))) for i in range(2)],
                        rails=[dict(name='rail-'+str(i),ranks=[i,scaleup+i]) for i in range(scaleup)]),
        execution=dict(mode='hybrid_fresh',num_sms=1,scaleout_warps=channels,max_tokens_per_rank=16,
                       expanded_layout=True,allow_multiple_reduction=True),
        calls=[dict(name=name,tokens=tokens)],limits=dict(max_rows=16384,max_input_bytes=1048576,max_output_bytes=16777216))
