"""Independent complete reachability comparison and counterexample refusals."""
import argparse
from collections import deque
from copy import deepcopy
import hashlib
from pathlib import Path

from moe_ub_cosim.modern_deepep_plan import compile_plan
from moe_ub_cosim.modern_deepep_runtime import ModernDeepEPRuntime
from experiments.input_graph_baseline.analysis.study import load,save,digest,guard,metrics,sha


def closure(parents):
    n=len(parents);children=[[] for _ in parents];degree=[]
    for child,ps in enumerate(parents):
        assert len(ps)==len(set(ps))
        degree.append(len(ps))
        for parent in ps:
            assert type(parent) is int and 0<=parent<n and parent!=child
            children[parent].append(child)
    ready=deque(i for i,k in enumerate(degree) if k==0);anc=[0]*n;visited=0
    while ready:
        parent=ready.popleft();visited+=1
        for child in children[parent]:
            anc[child]|=anc[parent]|(1<<parent);degree[child]-=1
            if degree[child]==0:ready.append(child)
    assert visited==n,'cycle'
    return anc


def expand(value):
    n=len(value['base']);joins=value['joins'];out=[]
    for ps in value['base']:
        result=[]
        for p in ps:
            if p<n:result.append(p)
            else:
                assert 0<=p-n<len(joins)
                assert all(type(q) is int and 0<=q<n for q in joins[p-n])
                result.extend(joins[p-n])
        assert len(result)==len(set(result));out.append(sorted(result))
    return out


def checked_case(stage,name,mutate=None):
    config=load(stage/'observed'/(name+'.json'));base=stage/'computed'/name
    ranks=config['execution']['ranks'];batch=int(name.rsplit('-b',1)[1]);selected=[]
    for source in load(stage/'observed/sources.json')['files']:
        document=load(stage/'observed/routes'/source['name'])
        for i in range(1,batch//8+1):
            choices,=document[i]['3'];selected.append(choices)
    assert len(selected)==batch
    assert config['calls']==[dict(name='real-layer3',tokens=[selected[r::ranks] for r in range(ranks)])]
    assert config['model']==dict(experts=256,top_k=8,hidden_size=7168,precision='bf16')
    original=load(base/'original.json');reduced=load(base/'reduced.json');factored=load(base/'factored.json')
    guards=load(base/'guards.json');names=load(base/'names.json');m=load(base/'metrics.json')
    if mutate:mutate(original,reduced,factored,guards)
    assert len(original)==len(reduced)==len(names)==m['nodes'] and len(set(names))==len(names)
    expected=ModernDeepEPRuntime(config).pending_dependencies();index={v:i for i,v in enumerate(names)}
    assert names==sorted(expected)
    assert original==[sorted(index[p] for p in expected[name]) for name in names]
    assert all(set(r)<=set(o) for o,r in zip(original,reduced))
    before,after=closure(original),closure(reduced);assert before==after,'reachability changed'
    assert expand(factored)==reduced
    full=closure(factored['base']+factored['joins'])
    mask=(1<<len(names))-1
    assert [v&mask for v in full[:len(names)]]==before
    # Independently enumerate demand rather than trusting the plan's counts.
    epr=256//ranks
    destinations=sum(len({e//epr for e in token}) for call in config['calls'] for row in call['tokens'] for token in row)
    assert m['operations']==6*ranks*ranks+2*destinations
    assert m['slots']==destinations
    expected_guard=guard(compile_plan(config));assert guards==expected_guard
    assert digest(guards)==m['guards_sha256']
    assert m['original_edges']==sum(map(len,original)) and m['reduced_edges']==sum(map(len,reduced))
    assert m['join_nodes']==len(factored['joins'])
    assert m['factored_edges']==sum(map(len,factored['base']))+sum(map(len,factored['joins']))
    for key,value in [('original',original),('reduced',reduced),('factored',factored)]:assert m[key]==metrics(value)
    return dict(name=name,nodes=len(names),original_ancestor_pairs=sum(v.bit_count() for v in before),
                reachable_relations_equal=True,guard_equal=True,operations_independently_counted=m['operations'])


def execute(stage,output):
    state=load(stage/'state.json');assert state['status']=='completed' and state['completed']==state['planned']
    for row in load(stage/'observed/sources.json')['files']:assert sha(stage/'observed/routes'/row['name'])==row['sha256']
    records=[checked_case(stage,name) for name in state['completed']]
    name=state['completed'][0];base=stage/'computed'/name
    reduced=load(base/'reduced.json');child=next(i for i,ps in enumerate(reduced) if ps);parent=reduced[child][0]
    factored=load(base/'factored.json');assert factored['joins']
    def delete(o,r,f,g):r[child].remove(parent)
    def reverse(o,r,f,g):r[parent].append(child)
    def join(o,r,f,g):f['joins'][0]=[]
    def dynamic(o,r,f,g):g['deferred_constraints']=[]
    rejected=[]
    for label,fn in [('necessary_edge_removed',delete),('back_edge',reverse),('shared_join_changed',join),('dynamic_constraints_removed',dynamic)]:
        try:checked_case(stage,name,fn)
        except (AssertionError,ValueError):rejected.append(label)
        else:raise AssertionError('tamper accepted: '+label)
    save(output,dict(passed=True,cases=records,tamper_rejected=rejected,network_runs=0,
        input_only_static_graph=True,source_selection_checked=True,complete_collective_equivalence_proven=False,
        performance_speedup_measured=False,new_innovations_established=0))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('stage','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();execute(a.stage.resolve(),a.output.resolve())
