"""Independent ordinal-block and public-log checks; evaluation may read truth/."""
import argparse
from copy import deepcopy
import hashlib
from itertools import product
from pathlib import Path
import re

from .study import COMMIT, POLICIES, SYNTHETIC, load, save, sha


def ordinal_word(sizes, seed):
    # Sorting by (round, cyclic distance) is independent of the tested queue merge.
    entries=[]
    for source,n in enumerate(sizes):
        for j in range((n+5)//6):
            begin,end=6*j,min(n,6*(j+1))
            entries.append((j,(source-seed-1)%len(sizes),[source,begin,end,(end-1)//6]))
        if n%6==0:
            entries.append((n//6,(source-seed-1)%len(sizes),[source,n,n,n//6]))
    return [entry[2] for entry in sorted(entries)]


def check_public(plan, actions, environment, prediction, result):
    channels=sorted((c for c in plan['channels'] if (c['call'],c['target'],c['channel'])==(0,0,0)),
                    key=lambda c:c['source_domain'])
    sizes=[len(c['transfers']) for c in channels]
    assert sizes==environment['sizes']==prediction['sizes']
    assert environment['seed']==0 and environment['policy'] in POLICIES
    word=ordinal_word(sizes,0);assert word==prediction['dag']
    assert result['actions_are_caller_declarations'] is True
    log=result['log'];assert len(log)==len(actions['actions'])
    published=[-1]*len(sizes);visible=[0]*len(sizes);consumed=[0]*len(sizes)
    cursor=position=attempts=0;admissions=[]
    for macro in environment['macros']:
        end=macro['action_end'];assert position<=end<=len(log)
        rows=log[position:end];raw=actions['actions'][position:end]
        assert all(all(row[k]==v for k,v in action.items()) for row,action in zip(rows,raw))
        if macro['kind']=='publish':
            source=macro['source'];index=published[source]+1;channel=channels[source]
            assert macro['publication']==index
            publication=channel['publications'][index]
            assert macro['tail']==publication['tail'] and macro['finished']==publication['finished']
            expected=[dict(kind='data_visible',transfer=t) for t in channel['transfers'][visible[source]:publication['tail']]]
            expected.append(dict(kind='tail_visible',channel=channel['id'],publication=index))
            assert raw==expected
            assert all(row['status']=='caller_declared_visible' for row in rows)
            assert all(rows[-1][k]==v for k,v in publication.items())
            published[source]=index;visible[source]=publication['tail']
        else:
            assert macro['kind']=='attempt' and macro['action_begin']==position
            while cursor<len(word):
                peer,begin,finish,gate=word[cursor]
                if published[peer]<gate:expected=['blocked',peer];break
                cursor+=1
                if finish>begin:expected=['data',peer,begin,finish];break
            else:expected=['done']
            assert prediction['macro_results'][attempts]==expected;attempts+=1
            actual=['done']
            for i,row in enumerate(rows):
                assert raw[i]==dict(kind='forward_step',call=0,ingress=0,channel=0)
                peer=row['source_domain'];assert row['source_channel']==channels[peer]['id']
                assert 0<=peer<len(sizes)
                if row['status']=='finished_empty_peer':
                    assert row['admitted']==[]
                    assert row['domain_channel_slot_begin']==row['domain_channel_slot_end']==consumed[peer]
                    assert published[peer]==len(channels[peer]['publications'])-1
                elif row['status']=='waiting_for_selected_peer':
                    assert i==len(rows)-1 and row['admitted']==[];actual=['blocked',peer]
                else:
                    assert row['status']=='chunk_admitted' and i==len(rows)-1
                    begin,finish=row['domain_channel_slot_begin'],row['domain_channel_slot_end']
                    assert begin==consumed[peer] and 0<finish-begin<=6
                    assert row['admitted']==channels[peer]['transfers'][begin:finish]
                    assert channels[peer]['publications'][published[peer]]['tail']>=finish
                    consumed[peer]=finish;admissions.extend(row['admitted']);actual=['data',peer,begin,finish]
            assert actual==expected
        position=end
    assert position==len(log) and attempts==len(prediction['macro_results'])
    assert consumed==sizes and expected==['done']
    focus=next(f for f in result['forwarders'] if (f['call'],f['ingress'],f['channel'])==(0,0,0))
    assert focus['consumed']==sizes and focus['admissions']==admissions
    assert focus['admission_complete'] is True and focus['cached_finish']==[True]*len(sizes)
    assert focus['cached_tail']==sizes and focus['selected_source_domain'] is None
    assert result['all_forwarder_admissions_complete'] is False
    return dict(admitted_transfers=len(admissions),macro_attempts=attempts,actions=len(log),focused_complete=True)


def verify(run, output):
    identities=load(run/'input-identity.json')|load(run/'environment-identity.json')
    for name,digest in identities.items():assert sha(run/name)==digest,name
    observed=run/'observed';routes=load(observed/'routes.json');assert len(routes['files'])==15
    assert routes['excluded']==['899.json','1479.json']
    names=[row['name'] for row in routes['files']]
    assert names==sorted(names,key=lambda n:int(n[:-5])) and len(set(names))==15
    real=load(observed/'inputs/real-prefill-prefix.json')
    for i,row in enumerate(routes['files']):
        p=observed/'routes'/row['name'];assert sha(p)==row['sha256'] and p.stat().st_size==row['bytes']
        assert real['calls'][0]['tokens'][i]==load(p)[0]['3'][:16]
    assert real['calls'][0]['tokens'][15]==[]
    library=load(observed/'library.json');tree=load(observed/'tree.json')
    assert library['commit']==tree['sha']==COMMIT and tree['truncated'] is False
    blobs={r['path']:r['sha'] for r in tree['tree'] if r['type']=='blob'}
    for name,row in library['files'].items():
        p=observed/'library'/name;raw=p.read_bytes()
        assert sha(p)==row['sha256'] and hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==row['git_blob_sha']==blobs[name]
    source=(observed/'library/deep_ep/include/deep_ep/impls/ep/hybrid_dispatch.cuh').read_text()
    for anchor in ('kScaleoutUpdateInterval = 6','kNumSlotsPerForwardChunk = kScaleoutUpdateInterval',
                   'int recv_scaleout_rank_idx = channel_idx % kNumScaleoutRanks;',
                   'start_slot_idx + kNumSlotsPerForwardChunk','ptx::ld_acquire_sys<int64_t>'):
        assert anchor in source,anchor
    cases=load(observed/'cases.json');assert len(cases)==14
    assert [c['sizes'] for c in cases[:-1]]==[list(s) for s in SYNTHETIC]
    predictions=load(run/'analysis/predictions.json');assert predictions['reads_truth'] is False
    public={};first=None
    for case in cases:
        root=observed/'plans'/case['name'];plan=load(root/'plan.json');manifest=load(root/'manifest.json')
        config=load(root/'input.json')
        assert (root/'input.json').read_bytes()==(observed/'inputs'/(case['name']+'.json')).read_bytes()
        assert manifest['sha256']=={'input.json':sha(root/'input.json'),'plan.json':sha(root/'plan.json')}
        by_id={t['id']:t for t in plan['dispatch']}
        scaleup=len(config['deployment']['rails']);domain_experts=config['model']['experts']//plan['domains']
        for channel in (c for c in plan['channels'] if (c['call'],c['target'],c['channel'])==(0,0,0)):
            peer=channel['source_domain'];rank=peer*scaleup
            tokens=config['calls'][0]['tokens'][rank]
            original=[i for i,choices in enumerate(tokens) if any(e<domain_experts for e in choices)]
            assert channel['source']==rank and channel['rail']==channel['target_domain']==0
            assert len(channel['transfers'])==len(original)
            for slot,(name,index) in enumerate(zip(channel['transfers'],original)):
                transfer=by_id[name]
                assert (transfer['source'],transfer['token'],transfer['ingress'],transfer['target_domain'],
                        transfer['rail'],transfer['channel'],transfer['domain_channel_slot'])==(rank,index,0,0,0,0,slot)
                assert transfer['channel_id']==channel['id']
                assert transfer['topk_positions']==[k for k,e in enumerate(tokens[index]) if e<domain_experts]
        for policy in POLICIES:
            args=(plan,load(observed/'environments'/case['name']/(policy+'-actions.json')),
                  load(observed/'environments'/case['name']/(policy+'-environment.json')),
                  predictions['predictions'][case['name']+'/'+policy],load(run/'truth'/case['name']/(policy+'.json')))
            public[case['name']+'/'+policy]=check_public(*args)
            if first is None and case['name']=='synthetic-04' and policy=='reverse-drain':first=args
    assert len(public)==len(predictions['predictions'])==42
    states=edges=root_count=0;families={}
    for domains,lengths in ((2,(0,1,5,6,7,12,13,16)),(3,(0,6,7,12,13)),(4,(0,1,6,7))):
        value=load(run/'analysis'/('exhaustive-'+str(domains)+'.json'))
        assert value['status']=='equivalent' and value['domains']==domains
        expected=[(list(s),seed) for s in product(lengths,repeat=domains) for seed in range(domains)]
        assert [(r['sizes'],r['seed']) for r in value['results']]==expected
        for row in value['results']:
            assert row['status']=='equivalent' and row['states']>0 and row['edges']>0
            assert re.fullmatch('[0-9a-f]{64}',row['transition_sha256'])
            word=ordinal_word(row['sizes'],row['seed'])
            assert row['dag_nodes']==len(word) and row['data_nodes']==sum(n[2]>n[1] for n in word)
        assert value['roots']==len(expected)
        assert value['states']==sum(r['states'] for r in value['results'])
        assert value['edges']==sum(r['edges'] for r in value['results'])
        states+=value['states'];edges+=value['edges'];root_count+=value['roots']
        assert value['cumulative_states']==states and value['cumulative_edges']==edges
        assert states<=2000000 and edges<=5000000
        families[str(domains)]={k:value[k] for k in ('roots','states','edges')}
    assert root_count==1527
    control=predictions['hypothetical_parameter_control']
    assert control['early_admissions']==[[1,0,5],[0,0,1],[1,5,10]]
    assert control['late_admissions']==[[1,0,6],[0,0,1],[1,6,10]]
    assert control['deployed_configuration'] is False and control['gpu_executed'] is False
    # Mutate copies only: all six corruptions must be rejected by the same checker.
    corruptions={}
    for kind in ('dag_gate','predicted_source','transfer_identity','source_channel','final_prefix','action_identity'):
        args=deepcopy(first)
        if kind=='dag_gate':args[3]['dag'][0][3]+=1
        elif kind=='predicted_source':next(r for r in args[3]['macro_results'] if r[0]=='data')[1]=99
        elif kind=='transfer_identity':next(r for r in args[4]['log'] if r['status']=='chunk_admitted')['admitted'][0]='wrong'
        elif kind=='source_channel':next(r for r in args[4]['log'] if r['kind']=='forward_step')['source_channel']='wrong'
        elif kind=='final_prefix':next(f for f in args[4]['forwarders'] if f['ingress']==0)['consumed'][0]+=1
        else:args[1]['actions'][0]['kind']='wrong'
        try:check_public(*args)
        except (AssertionError,KeyError,ValueError,IndexError):corruptions[kind]='rejected'
        else:raise AssertionError('accepted corruption: '+kind)
    save(output,dict(passed=True,raw_route_files=15,library_files=len(library['files']),
        exhaustive_roots=root_count,states=states,edges=edges,families=families,public_replays=public,
        corruption_rejections=corruptions,new_networks=0,gpu_executed=False,
        boundary='finite source-model enumeration and caller-declared single-forwarder logical replay only; certificates are deterministic hashes, not formal proofs'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();verify(a.run.resolve(),a.output.resolve())
