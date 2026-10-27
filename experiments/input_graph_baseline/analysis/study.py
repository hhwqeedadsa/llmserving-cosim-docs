"""Offline ordinary graph baselines, built from route inputs rather than bindings."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import gzip
import hashlib
import heapq
import json
from pathlib import Path
import shutil
import time

from moe_ub_cosim.modern_deepep_plan import compile_plan
from moe_ub_cosim.modern_deepep_runtime import ModernDeepEPRuntime

NAMES = ('900', '901', '905', '1003', '1004', '1007', '1008', '1011')
CASES = tuple((r,b) for r in (4,8,16) for b in (8,16))


def raw(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def digest(value):return hashlib.sha256(raw(value)).hexdigest()
def load(path):return json.loads(path.read_bytes())
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n')


def prepare(repo,stage):
    assert not stage.exists();stage.mkdir()
    intake_path=repo/'experiments/route_coverage/results/local_additions_20261027/intake.json'
    intake=load(intake_path);observed=stage/'observed';observed.mkdir()
    save(observed/'intake.json',intake)
    rows={r['name']:r for r in intake['files']};source=[];tokens=[]
    for name in NAMES:
        path=repo/'data/moe-trace-small'/(name+'.json');row=rows[path.name]
        assert row['eligible_offline'] and not row['duplicate_of']
        assert sha(path)==row['sha256'] and path.stat().st_size<=8*2**20
        dest=observed/'routes'/path.name;dest.parent.mkdir(exist_ok=True);shutil.copyfile(path,dest)
        document=load(dest);entries=[]
        for i in (1,2):
            choices,=document[i]['3']
            assert len(choices)==len(set(choices))==8 and all(type(e) is int and 0<=e<256 for e in choices)
            entries.append(choices)
        tokens.append(entries);source.append(dict(name=path.name,sha256=sha(dest)))
    assert len({r['sha256'] for r in source})==8
    template=load(repo/'examples/modern_deepep_native/input-selective.json')['modern']
    for ranks,batch in CASES:
        config=deepcopy(template)
        config['model'].update(experts=256,top_k=8,hidden_size=7168,precision='bf16')
        config['execution'].update(ranks=ranks,max_tokens_per_rank=16,local_devices=[[0,1] for _ in range(ranks)])
        config['limits'].update(max_events=65536,max_operations=16384,max_input_bytes=2**20,max_output_bytes=16*2**20)
        selected=[entry for entries in tokens for entry in entries[:batch//8]]
        config['calls']=[dict(name='real-layer3',tokens=[selected[r::ranks] for r in range(ranks)])]
        save(observed/f'ep{ranks}-b{batch}.json',config)
    save(observed/'sources.json',dict(files=source,source=intake['source'],
        remote_revision_independently_verified=False,selection='file order then entry1/1..2 at layer3, round-robin ranks',
        source_base='157aed4',protocol_commit='1c2fa7f',network_runs=0))
    save(stage/'state.json',dict(status='prepared',planned=[f'ep{r}-b{b}' for r,b in CASES],started=[],completed=[]))


def reduce_graph(parents):
    """Exact DAG transitive reduction with a topological ancestor bitset."""
    successors=defaultdict(list);left=[len(p) for p in parents]
    for v,ps in enumerate(parents):
        for p in ps:successors[p].append(v)
    ready=[v for v,n in enumerate(left) if not n];heapq.heapify(ready);order=[]
    while ready:
        v=heapq.heappop(ready);order.append(v)
        for child in successors[v]:
            left[child]-=1
            if not left[child]:heapq.heappush(ready,child)
    assert len(order)==len(parents)
    position={v:i for i,v in enumerate(order)};ancestors=[0]*len(parents);result=[[] for _ in parents]
    for v in order:
        covered=0;kept=[]
        for p in sorted(parents[v],key=position.__getitem__,reverse=True):
            if not (covered>>p)&1:
                kept.append(p);covered|=ancestors[p]|(1<<p)
        result[v]=sorted(kept);ancestors[v]=covered
    return result


def factor(parents):
    groups=Counter(tuple(p) for p in parents);joins=[];index={}
    for ps,count in sorted(groups.items()):
        if len(ps)>1 and count*len(ps)>len(ps)+count:
            index[ps]=len(parents)+len(joins);joins.append(list(ps))
    consumers=[[index[tuple(ps)]] if tuple(ps) in index else list(ps) for ps in parents]
    return dict(base=consumers,joins=joins)


def guard(plan):
    return {k:plan[k] for k in ('operations','slots','deferred_constraints')}


def metrics(value):
    data=raw(value)
    return dict(canonical_json_bytes=len(data),gzip_bytes=len(gzip.compress(data,mtime=0)))


def execute(stage):
    state=load(stage/'state.json');assert state['status']=='prepared'
    for name in state['planned']:
        assert name not in state['started'];state['started'].append(name);state['status']='running';save(stage/'state.json',state)
        start=time.monotonic();cpu=time.process_time()
        try:
            config=load(stage/'observed'/(name+'.json'));plan=compile_plan(config)
            runtime=ModernDeepEPRuntime(config);pending=runtime.pending_dependencies()
            names=sorted(pending);index={name:i for i,name in enumerate(names)}
            parents=[sorted(index[p] for p in pending[name]) for name in names]
            static=list(parents);reduced=reduce_graph(parents);factored=factor(reduced)
            before=digest(guard(plan));base=stage/'computed'/name
            save(base/'names.json',names);save(base/'original.json',static)
            save(base/'reduced.json',reduced);save(base/'factored.json',factored);save(base/'guards.json',guard(plan))
            assert digest(guard(plan))==before
            result=dict(name=name,nodes=len(names),operations=len(plan['operations']),slots=len(plan['slots']),
                original_edges=sum(map(len,parents)),reduced_edges=sum(map(len,reduced)),
                join_nodes=len(factored['joins']),factored_edges=sum(map(len,factored['base']))+sum(map(len,factored['joins'])),
                original=metrics(static),reduced=metrics(reduced),factored=metrics(factored),
                guards_sha256=before,network_runs=0,runtime_fire_calls=0,
                cpu_seconds=time.process_time()-cpu,wall_seconds=time.monotonic()-start)
            save(base/'metrics.json',result);state['completed'].append(name);save(stage/'state.json',state)
        except BaseException as error:
            state.update(status='stopped_after_failure',failed_case=name,error=repr(error));save(stage/'state.json',state);raise
    state['status']='completed';save(stage/'state.json',state)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','execute'])
    p.add_argument('--stage',type=Path,required=True);p.add_argument('--repository',type=Path)
    a=p.parse_args()
    if a.action=='prepare':prepare(a.repository.resolve(),a.stage.resolve())
    else:execute(a.stage.resolve())
