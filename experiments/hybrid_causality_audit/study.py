"""Frozen logical causality study. Predictions never read truth/ results."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from .models import publications, initial, cache_raw_step, static_dag, dag_attempt, roots, explore, hypothetical_control

COMMIT='f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
SYNTHETIC=((0,6),(6,0),(6,6),(7,6),(12,13),
           (0,6,7),(6,0,7),(7,6,0),(12,13,6),
           (0,1,6,7),(6,7,0,1),(13,12,7,6),(0,0,0,0))
POLICIES=('publish-first','reverse-drain','alternating-once')


def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def config(domains,scaleup,experts,topk,hidden,tokens,name):
    ranks=domains*scaleup
    return dict(schema_version='moe-hybrid-logical-input/1',model=dict(experts=experts,top_k=topk,hidden_size=hidden,precision='bf16'),
        deployment=dict(domains=[dict(name='server-'+str(i),ranks=list(range(i*scaleup,(i+1)*scaleup))) for i in range(domains)],
                        rails=[dict(name='rail-'+str(i),ranks=list(range(i,ranks,scaleup))) for i in range(scaleup)]),
        execution=dict(mode='hybrid_fresh',num_sms=1,scaleout_warps=1,max_tokens_per_rank=16,
                       expanded_layout=True,allow_multiple_reduction=True),
        calls=[dict(name=name,tokens=tokens)],limits=dict(max_rows=16384,max_input_bytes=1048576,max_output_bytes=16777216))


def prepare(repo,run):
    observed=run/'observed';assert not observed.exists();observed.mkdir()
    old=repo/'runs/hybrid_path_audit_20261027/observed';routes=load(old/'routes.json')
    assert len(routes['files'])==15 and routes['excluded']==['899.json','1479.json']
    (observed/'routes').mkdir();source_tokens=[]
    for row in routes['files']:
        source=old/'routes'/row['name'];assert sha(source)==row['sha256'] and source.stat().st_size==row['bytes']
        shutil.copyfile(source,observed/'routes'/row['name'])
        raw=load(source);choices=raw[0]['3'][:16]
        assert choices and all(len(x)==len(set(x))==8 and all(type(e)is int and 0<=e<256 for e in x) for x in choices)
        source_tokens.append(deepcopy(choices))
    save(observed/'routes.json',routes)
    save(observed/'route-provenance.json',dict(dataset='core12345/MoE_expert_selection_trace',
        directory='cognitivecomputations/DeepSeek-R1-AWQ/mmlu',
        origin='User confirmed the four original and thirteen additional local files came from this directory',
        upstream_revision_independently_verified=False,arrival_timestamps_available=False))
    library=load(old/'library.json');assert library['commit']==COMMIT
    original=repo/'data/research/deepep_modern_20261022';tree=load(original/'tree.json')
    assert tree['sha']==COMMIT and not tree['truncated'];blobs={r['path']:r['sha'] for r in tree['tree'] if r['type']=='blob'}
    for name,row in library['files'].items():
        path=old/'library'/name;raw=path.read_bytes()
        assert sha(path)==row['sha256'] and hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==row['git_blob_sha']==blobs[name]
        dst=observed/'library'/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,dst)
    save(observed/'library.json',library);save(observed/'tree.json',tree)
    cases=[]
    for i,sizes in enumerate(SYNTHETIC):
        domains=len(sizes);tokens=[]
        for count in sizes:tokens.extend([[[0] for _ in range(count)],[]])
        name='synthetic-'+str(i).zfill(2);value=config(domains,2,2*domains,1,256,tokens,name)
        save(observed/'inputs'/(name+'.json'),value);cases.append(dict(name=name,sizes=sizes,source='synthetic'))
    tokens=source_tokens+[[]]
    value=config(2,8,256,8,7168,tokens,'real-prefill-prefix')
    save(observed/'inputs/real-prefill-prefix.json',value)
    # For ingress0/channel0 only rank0 and rank8 share its same-position rail.
    sizes=[sum(any(e<128 for e in token) for token in tokens[rank]) for rank in (0,8)]
    cases.append(dict(name='real-prefill-prefix',sizes=sizes,source='15_fixed_real_files_entry0_layer3_first16'))
    save(observed/'cases.json',cases)
    platform={}
    for name in ('hybrid_deepep_plan.py','hybrid_deepep_progress.py'):
        path=repo/'python/moe_ub_cosim'/name
        old_raw=subprocess.check_output(['git','show','19670ba:python/moe_ub_cosim/'+name],cwd=repo)
        assert path.read_bytes()==old_raw;platform[name]=sha(path)
    save(observed/'platform.json',dict(commit='19670ba',files=platform))
    save(run/'input-identity.json',{str(p.relative_to(run)):sha(p) for p in sorted(observed.rglob('*')) if p.is_file()})
    save(run/'preparation.json',dict(passed=True,cases=len(cases),policies=POLICIES,public_plans=14,public_replays=42,
        raw_route_files=15,fixed_library_files=len(library['files']),new_networks=0,gpu_executed=False))


def environment(plan,policy):
    channels=sorted((c for c in plan['channels'] if c['call']==0 and c['target']==0 and c['channel']==0),key=lambda c:c['source_domain'])
    domains=plan['domains'];assert len(channels)==domains
    sizes=tuple(len(c['transfers']) for c in channels);state=initial(domains,0);pub=[-1]*domains
    actions,macros,visible=[],[],[0]*domains
    def publish(peer):
        index=pub[peer]+1;value=channels[peer]['publications'][index]
        for name in channels[peer]['transfers'][visible[peer]:value['tail']]:actions.append(dict(kind='data_visible',transfer=name))
        visible[peer]=value['tail'];actions.append(dict(kind='tail_visible',channel=channels[peer]['id'],publication=index));pub[peer]=index
        macros.append(dict(kind='publish',source=peer,publication=index,tail=value['tail'],finished=value['finished'],action_end=len(actions)))
    def attempt():
        nonlocal state
        begin=len(actions)
        for _ in range(domains+1):
            state,value=cache_raw_step(state,pub,sizes)
            if value[0]!='done':actions.append(dict(kind='forward_step',call=0,ingress=0,channel=0))
            if value[0]!='empty':break
        else:raise AssertionError('too many empty turns')
        macros.append(dict(kind='attempt',action_begin=begin,action_end=len(actions)))
        return value[0]
    def drain():
        for _ in range(sum(sizes)+domains+1):
            status=attempt()
            if status!='data':return status
        raise AssertionError('not drained')
    if policy=='publish-first':
        for peer,c in enumerate(channels):
            for _ in c['publications']:publish(peer)
    elif policy=='reverse-drain':
        for peer in reversed(range(domains)):
            for _ in channels[peer]['publications']:publish(peer);drain()
    else:
        assert policy=='alternating-once'
        while any(pub[p]+1<len(channels[p]['publications']) for p in range(domains)):
            for peer in range(domains):
                if pub[peer]+1<len(channels[peer]['publications']):publish(peer);attempt()
    assert drain()=='done'
    return dict(schema_version='moe-hybrid-progress-actions/1',actions=actions),dict(sizes=sizes,seed=0,policy=policy,macros=macros)


def make_environments(run):
    identities={}
    for case in load(run/'observed/cases.json'):
        plan=load(run/'observed/plans'/case['name']/'plan.json')
        for policy in POLICIES:
            actions,environment_record=environment(plan,policy);assert list(environment_record['sizes'])==case['sizes']
            for suffix,value in (('actions',actions),('environment',environment_record)):
                path=run/'observed/environments'/case['name']/(policy+'-'+suffix+'.json');save(path,value);identities[str(path.relative_to(run))]=sha(path)
    save(run/'environment-identity.json',identities)


def predict(run,output):
    predictions={}
    for case in load(run/'observed/cases.json'):
        for policy in POLICIES:
            environment_record=load(run/'observed/environments'/case['name']/(policy+'-environment.json'))
            sizes=environment_record['sizes'];word=static_dag(sizes,0);cursor=0;pub=[-1]*len(sizes);answers=[]
            for macro in environment_record['macros']:
                if macro['kind']=='publish':pub[macro['source']]=macro['publication']
                else:
                    cursor,value=dag_attempt(word,cursor,pub);answers.append(value)
            assert answers[-1]==('done',)
            predictions[case['name']+'/'+policy]=dict(sizes=sizes,dag=list(word),macro_results=answers)
    save(output,dict(schema_version='hybrid-causal-predictions/1',reads_truth=False,predictions=predictions,
                     hypothetical_parameter_control=hypothetical_control(),new_networks=0))


def exhaustive(run,domains):
    previous=[load(p) for p in sorted((run/'analysis').glob('exhaustive-*.json'))]
    states=sum(p['states'] for p in previous);edges=sum(p['edges'] for p in previous)
    assert all(p['status']=='equivalent' for p in previous)
    results=[];status='equivalent'
    for sizes,seed in roots():
        if len(sizes)!=domains:continue
        row=explore(sizes,seed,2000000-states,5000000-edges);results.append(row);states+=row['states'];edges+=row['edges']
        if row['status']!='equivalent':status=row['status'];break
    save(run/'analysis'/('exhaustive-'+str(domains)+'.json'),dict(status=status,domains=domains,roots=len(results),
        states=sum(r['states'] for r in results),edges=sum(r['edges'] for r in results),results=results,
        cumulative_states=states,cumulative_edges=edges))
    print(json.dumps(dict(status=status,domains=domains,roots=len(results),cumulative_states=states,cumulative_edges=edges)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('prepare','environments','predict','exhaustive'))
    p.add_argument('--repository',type=Path);p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path);p.add_argument('--domains',type=int)
    a=p.parse_args();run=a.run.resolve()
    if a.action=='prepare':prepare(a.repository.resolve(),run)
    elif a.action=='environments':make_environments(run)
    elif a.action=='predict':predict(run,a.output.resolve())
    else:exhaustive(run,a.domains)
