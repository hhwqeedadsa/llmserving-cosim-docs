"""Observed preparation, public-plan declarations and symbolic strong reference."""
import argparse
from copy import deepcopy
import hashlib
from pathlib import Path
import shutil
import subprocess

from .common import COMMIT,COUNTS,config,load,save,sha,digest,streams,schedules

PATHS={
 'deepep_modern_20261022':('deep_ep/include/deep_ep/impls/ep/hybrid_dispatch.cuh',
                         'deep_ep/include/deep_ep/impls/ep/hybrid_combine.cuh',
                         'deep_ep/include/deep_ep/layout/ep/token.cuh','csrc/buffers/ep.hpp'),
 'deepep_layout_20261022':('deep_ep/include/deep_ep/layout/ep/workspace.cuh',
                         'deep_ep/include/deep_ep/impls/ep/dispatch_copy_epilogue.cuh',
                         'deep_ep/include/deep_ep/common/compiled.cuh')}


def prepare(repo,run):
    observed=run/'observed';assert not observed.exists();observed.mkdir()
    old=repo/'runs/hybrid_path_audit_20261027/observed';routes=load(old/'routes.json')
    assert len(routes['files'])==15 and routes['excluded']==['899.json','1479.json']
    tokens=[]
    for row in routes['files']:
        source=old/'routes'/row['name'];assert sha(source)==row['sha256'] and source.stat().st_size==row['bytes']
        destination=observed/'routes'/row['name'];destination.parent.mkdir(exist_ok=True);shutil.copyfile(source,destination)
        tokens.append(deepcopy(load(source)[0]['3'][:16]))
    assert [r['name'] for r in routes['files']]==sorted((r['name'] for r in routes['files']),key=lambda n:int(n[:-5]))
    save(observed/'routes.json',routes)
    save(observed/'provenance.json',dict(dataset='core12345/MoE_expert_selection_trace',directory='cognitivecomputations/DeepSeek-R1-AWQ/mmlu',
         user_confirmed_original4_and_added13=True,remote_revision_independently_verified=False,real_arrival_timestamps=False))
    library={}
    for cache,names in PATHS.items():
        root=repo/'data/research'/cache;tree=load(root/'tree.json')
        assert tree['sha']==COMMIT and not tree['truncated']
        blobs={r['path']:r['sha'] for r in tree['tree'] if r['type']=='blob'}
        descriptions={}
        for filename in ('file-sources.json','file-sources-completion.json'):
            if (root/filename).exists():descriptions.update({r['path']:r for r in load(root/filename)['sources']})
        for name in names:
            row=descriptions[name];source=root/'sources'/name;raw=source.read_bytes()
            assert row['commit']==COMMIT and sha(source)==row['sha256']
            assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==row['git_blob_sha']==blobs[name]
            destination=observed/'library'/name;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,destination)
            library[name]={k:row[k] for k in ('sha256','git_blob_sha','public_url')}
        save(observed/(cache+'-tree.json'),tree)
    save(observed/'library.json',dict(commit=COMMIT,files=library))
    cases=[]
    for i,(channels,count,choices) in enumerate(((2,4,[2,3]),(3,6,[2,3]),(4,4,[2,3]),(2,4,[0,2]),(2,4,[2,3]),(2,2,[2,3]))):
        rows=[deepcopy([choices]*count),[],[],[]]
        if i==4:rows[2]=[[2,3],[2,3]]
        if i==5:rows[1]=[[2,3],[2,3]]
        name='synthetic-'+str(i);save(observed/'inputs'/(name+'.json'),config(2,8,2,256,channels,rows,name))
        cases.append(dict(name=name,synthetic=True,declared_schedules=COUNTS[i]))
    for channels in (2,4):
        name='real-c'+str(channels)
        save(observed/'inputs'/(name+'.json'),config(8,256,8,7168,channels,tokens+[[]],name))
        cases.append(dict(name=name,synthetic=False,declared_schedules=3))
    save(observed/'cases.json',cases)
    platform={}
    for filename in ('hybrid_deepep_plan.py','hybrid_deepep_progress.py'):
        path=repo/'python/moe_ub_cosim'/filename
        assert path.read_bytes()==subprocess.check_output(['git','show','19670ba:python/moe_ub_cosim/'+filename],cwd=repo)
        platform[filename]=sha(path)
    save(observed/'platform.json',dict(commit='19670ba',files=platform))
    save(run/'input-identity.json',{str(p.relative_to(run)):sha(p) for p in sorted(observed.rglob('*')) if p.is_file()})


def observe(run):
    identities={};total=0
    for case in load(run/'observed/cases.json'):
        name=case['name'];plan=load(run/'observed/plans'/name/'plan.json');declared=streams(plan)
        generated=list(schedules(plan,declared,case['synthetic']));assert len(generated)==case['declared_schedules']
        total+=len(generated)
        value=dict(streams=declared,schedules=[dict(name=n,sequence=s) for n,s in generated],
                   inter_ingress_order='concatenated ingress blocks; not all global time interleavings')
        p=run/'observed/declarations'/(name+'.json');assert not p.exists();save(p,value);identities[str(p.relative_to(run))]=sha(p)
    assert total==156;save(run/'declaration-identity.json',identities)


def reference(run,output):
    assert not output.exists();all_cases={}
    for case in load(run/'observed/cases.json'):
        name=case['name'];plan=load(run/'observed/plans'/name/'plan.json');config_value=load(run/'observed/inputs'/(name+'.json'))
        declared=load(run/'observed/declarations'/(name+'.json'))['streams']
        scaleup=plan['ranks_per_domain'];epr=config_value['model']['experts']//plan['ranks']
        dispatch={d['id']:d for d in plan['dispatch']};allocations={};projection=[]
        for forwarder,entries in sorted(declared.items()):
            ingress,channel=map(int,forwarder.split(':'))
            for entry in entries:
                d=dispatch[entry['dispatch']];choices=config_value['calls'][0]['tokens'][d['source']][d['token']]
                groups={target:[k for k,e in enumerate(choices) if e//epr==target]
                        for target in range(scaleup) if any(e//epr==target for e in choices)}
                assert d['ingress']==ingress and d['channel']==channel and d['target_domain']==0
                for target,positions in groups.items():
                    identity=d['id']+'.gpu'+str(target)
                    allocations[identity]=dict(id=identity,dispatch=d['id'],ingress=ingress,target=target,channel=channel,
                        topk_positions=positions,operand_bytes=d['token_operand_bytes'],
                        path='ingress_self_copy' if ingress==target else 'intra_domain_forward',
                        source_global_token=d['source']*config_value['execution']['max_tokens_per_rank']+d['token'],master_topk=positions[0])
                projection.append(dict(forwarder=forwarder,dispatch=d['id'],last_in_chunk=entry['last_in_chunk'],
                    contributors=[dict(target=target,topk_positions=positions) for target,positions in sorted(groups.items())]))
        assert set(allocations)=={f['id'] for f in plan['forwards'] if f['source']<scaleup}
        all_cases[name]=dict(allocations=allocations,projection=projection,
            projection_sha256=digest(projection),symbolic_buffer_identity='original dispatch token plus target GPU; no future slots')
    save(output,dict(reads_truth=False,cases=all_cases,new_networks=0))


def interpret(run,output):
    from .model import interpret as source_interpret
    assert not output.exists();summary={}
    for case in load(run/'observed/cases.json'):
        name=case['name'];p=run/'observed/plans'/name
        plan=load(p/'plan.json');config_value=load(p/'input.json');declaration=load(run/'observed/declarations'/(name+'.json'))
        rows=[]
        for schedule in declaration['schedules']:
            value=source_interpret(config_value,plan,declaration['streams'],schedule['sequence'])
            destination=output/name/(schedule['name']+'.json');save(destination,value)
            rows.append(dict(name=schedule['name'],trace_sha256=sha(destination),
                layout_sha256=digest(sorted((a['id'],a['slot'],a['byte_offset']) for a in value['allocations'])),
                projection_sha256=digest(value['inverse_projection'])))
        summary[name]=rows
    save(output/'index.json',summary)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('prepare','observe','reference','interpret'))
    p.add_argument('--repository',type=Path);p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path)
    a=p.parse_args();run=a.run.resolve()
    if a.action=='prepare':prepare(a.repository.resolve(),run)
    elif a.action=='observe':observe(run)
    elif a.action=='reference':reference(run,a.output.resolve())
    else:interpret(run,a.output.resolve())
