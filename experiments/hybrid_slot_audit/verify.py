"""Independent dense-partition, inverse-list and original-token association checks."""
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
import hashlib
from math import factorial
from pathlib import Path

from .common import COMMIT,COUNTS,POLICIES,load,save,sha,digest


def check_trace(config,plan,declaration,reference,trace):
    scaleup=plan['ranks_per_domain'];channels=plan['channels_per_rank'];domains=plan['domains']
    maximum=config['execution']['max_tokens_per_rank'];capacity=maximum*domains;topk=config['model']['top_k']
    epr=config['model']['experts']//plan['ranks'];stride=next(iter(reference['allocations'].values()))['operand_bytes']
    assert trace['stride_bytes']==stride and trace['capacity_per_sender_partition']==capacity
    assert trace['network_executed'] is False and trace['gpu_executed'] is False
    expected=reference['allocations'];allocations=trace['allocations']
    assert len(allocations)==len(expected) and {a['id'] for a in allocations}==set(expected)
    by_id={a['id']:a for a in allocations};scopes=defaultdict(list);addresses=set()
    ordinal={};used=defaultdict(int)
    for forwarder,entries in declaration['streams'].items():
        ingress,channel=map(int,forwarder.split(':'))
        for entry in entries:
            for target in range(scaleup):
                name=entry['dispatch']+'.gpu'+str(target)
                if name in expected:ordinal[name]=used[ingress,channel,target];used[ingress,channel,target]+=1
    for a in allocations:
        r=expected[a['id']];assert all(a[k]==v for k,v in r.items())
        assert a['atomic_owner']==a['partition']==a['ingress']
        assert a['linked_ordinal']==ordinal[a['id']]
        assert type(a['slot']) is int and 0<=a['slot']<capacity
        assert a['byte_offset']==(a['partition']*capacity+a['slot'])*stride
        address=(a['target'],a['byte_offset']);assert address not in addresses;addresses.add(address)
        assert a['slot']==len(scopes[a['ingress'],a['target']])
        scopes[a['ingress'],a['target']].append(a['slot'])
    assert all(sorted(slots)==list(range(len(slots))) for slots in scopes.values())
    # Reconstruct independent copy-epilogue row numbers from partition counts.
    expected_rows={};row_by_id={}
    for target in range(scaleup):
        prefix=0
        for ingress in range(scaleup):
            for a in allocations:
                if (a['target'],a['ingress'])==(target,ingress):
                    i=prefix+a['slot'];expected_rows[target,i]=a;row_by_id[a['id']]=i
            prefix+=len(scopes[ingress,target])
    assert len(trace['epilogue'])==len(expected_rows)
    seen_rows=set()
    for r in trace['epilogue']:
        identity=(r['target'],r['row']);assert identity not in seen_rows;seen_rows.add(identity)
        a=expected_rows[identity]
        assert all(r[k]==a[k] for k in ('dispatch','ingress','slot','source_global_token'))
        assert r['src_metadata_slot_topk']==a['slot']*topk+a['master_topk']
    assert seen_rows==set(expected_rows)
    links={}
    for r in trace['linked_list']:
        identity=(r['target'],r['channel'],r['ordinal'],r['ingress']);assert identity not in links
        links[identity]=r['row']
    expected_links={}
    for a in allocations:expected_links[a['target'],a['channel'],a['linked_ordinal'],a['ingress']]=row_by_id[a['id']]
    for target in range(scaleup):
        for channel in range(channels):
            for ingress in range(scaleup):expected_links[target,channel,used[ingress,channel,target],ingress]=-1
    assert links==expected_links
    dispatch={d['id']:d for d in plan['dispatch']}
    assert set(trace['forward_metadata'])==set(declaration['streams'])
    for forwarder,entries in declaration['streams'].items():
        ingress,channel=map(int,forwarder.split(':'));rows=trace['forward_metadata'][forwarder]
        assert len(rows)==len(entries)
        for row,entry in zip(rows,entries):
            name=entry['dispatch'];d=dispatch[name];choices=config['calls'][0]['tokens'][d['source']][d['token']]
            lanes=[e//epr if e//epr<scaleup else -1 for e in choices]
            slots=[-1]*topk;ll=[-1]*topk
            for target in set(lanes)-{-1}:
                a=by_id[name+'.gpu'+str(target)];members=[k for k,gpu in enumerate(lanes) if gpu==target]
                slots[members[0]]=a['slot']
                absolute=(channel*(domains*((maximum+channels-1)//channels)+1)+a['linked_ordinal'])*scaleup+ingress
                for k in members:ll[k]=absolute
                # Following the actual inverse list must retrieve this original token.
                i=links[target,channel,a['linked_ordinal'],ingress]
                assert expected_rows[target,i]['dispatch']==name
            assert row==dict(dispatch=name,source_global_token=d['source']*maximum+d['token'],
                last_in_chunk=entry['last_in_chunk'],target_lanes=lanes,slot_lanes=slots,link_lanes=ll)
    assert trace['inverse_projection']==reference['projection']
    return dict(allocations=len(allocations),sender_target_scopes=sum(bool(v) for v in scopes.values()),
        linked_entries=len(links),projection_sha256=digest(trace['inverse_projection']),
        layout_sha256=digest(sorted((a['id'],a['slot'],a['byte_offset']) for a in allocations)))


def verify(run,output):
    assert not output.exists()
    for filename in ('input-identity.json','declaration-identity.json'):
        for name,digest_value in load(run/filename).items():assert sha(run/name)==digest_value,name
    observed=run/'observed';routes=load(observed/'routes.json')
    assert len(routes['files'])==15 and routes['excluded']==['899.json','1479.json']
    originals=[]
    for row in routes['files']:
        p=observed/'routes'/row['name'];assert sha(p)==row['sha256'] and p.stat().st_size==row['bytes']
        originals.append(load(p)[0]['3'][:16])
    library=load(observed/'library.json');assert library['commit']==COMMIT and len(library['files'])==7
    blobs={}
    for p in observed.glob('*-tree.json'):
        tree=load(p);assert tree['sha']==COMMIT and tree['truncated'] is False
        blobs.update({r['path']:r['sha'] for r in tree['tree'] if r['type']=='blob'})
    for name,row in library['files'].items():
        p=observed/'library'/name;raw=p.read_bytes()
        assert sha(p)==row['sha256'] and hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==row['git_blob_sha']==blobs[name]
    for name,anchors in {
        'deep_ep/include/deep_ep/layout/ep/workspace.cuh':('return signals.scaleup_atomic_sender_count;','int scaleup_atomic_sender_count[kNumMaxRanks];'),
        'deep_ep/include/deep_ep/impls/ep/hybrid_dispatch.cuh':('scaleup_buffer = scaleup_buffer.get_rank_buffer(scaleup_rank_idx);','atomicAdd(workspace_layout.get_scaleup_atomic_sender_counter() + stored_dst_scaleup_rank_idx, 1)'),
        'deep_ep/include/deep_ep/impls/ep/dispatch_copy_epilogue.cuh':('channel_linked_list[tma_buffer.get_linked_list_idx_ptr()[master_src_topk_idx]] = i;','(i - current_rank_start) * kNumTopk + master_src_topk_idx'),
        'deep_ep/include/deep_ep/impls/ep/hybrid_combine.cuh':('stored_ll_idx[i] * kNumScaleupRanks + j','const auto src_slot_idx = __ldg(src_metadata + token_idx * kMetadataStride + 1) / kNumTopk;'),
        'deep_ep/include/deep_ep/common/compiled.cuh':('kNumTMAAlignmentBytes = 32;','kNumRDMAAlignmentBytes = 64;')}.items():
        text=(observed/'library'/name).read_text();assert all(anchor in text for anchor in anchors),name
    reference=load(run/'analysis/reference.json');assert reference['reads_truth'] is False
    truth_index=load(run/'truth/index.json');cases=load(observed/'cases.json');assert len(cases)==len(reference['cases'])==8
    results={};total=0;synthetic=0;witness=None;wrong_counter_args=None
    for case in cases:
        name=case['name'];root=observed/'plans'/name
        config=load(root/'input.json');plan=load(root/'plan.json');manifest=load(root/'manifest.json')
        assert manifest['sha256']=={'input.json':sha(root/'input.json'),'plan.json':sha(root/'plan.json')}
        assert (root/'input.json').read_bytes()==(observed/'inputs'/(name+'.json')).read_bytes()
        assert plan['combine_branch']=='domain_reduction' and plan['progress_interval']==6
        if not case['synthetic']:assert config['calls'][0]['tokens']==originals+[[]]
        declaration=load(observed/'declarations'/(name+'.json'));r=reference['cases'][name]
        scaleup=plan['ranks_per_domain'];epr=config['model']['experts']//plan['ranks']
        raw_allocations={}
        for source,tokens in enumerate(config['calls'][0]['tokens']):
            for token,choices in enumerate(tokens):
                assert len(choices)==len(set(choices))==config['model']['top_k']
                identity=f'h0.s{source}.t{token}.d0';ingress=source%scaleup
                hidden=2*config['model']['hidden_size'];metadata=12*len(choices)+4
                stride=64*((hidden+32*((metadata+31)//32)+63)//64)
                for target in sorted({expert//epr for expert in choices if expert//epr<scaleup}):
                    positions=[k for k,e in enumerate(choices) if e//epr==target];op=identity+'.gpu'+str(target)
                    raw_allocations[op]=dict(id=op,dispatch=identity,ingress=ingress,target=target,
                        channel=token%plan['channels_per_rank'],topk_positions=positions,operand_bytes=stride,
                        path='ingress_self_copy' if ingress==target else 'intra_domain_forward',
                        source_global_token=source*config['execution']['max_tokens_per_rank']+token,master_topk=positions[0])
        assert raw_allocations==r['allocations']
        # Independently sort chunks by round and cyclic source distance.
        for forwarder,entries in declaration['streams'].items():
            ingress,channel=map(int,forwarder.split(':'));chunks=[]
            for source in range(ingress,plan['ranks'],scaleup):
                tokens=config['calls'][0]['tokens'][source]
                transfers=[f'h0.s{source}.t{i}.d0' for i,choices in enumerate(tokens)
                           if i%plan['channels_per_rank']==channel and any(e//epr<scaleup for e in choices)]
                for j in range(0,len(transfers),6):
                    chunk=transfers[j:j+6]
                    chunks.append((j//6,(source//scaleup-channel%plan['domains']-1)%plan['domains'],chunk))
            expected=[dict(dispatch=token,last_in_chunk=i+1==len(chunk)) for _,_,chunk in sorted(chunks) for i,token in enumerate(chunk)]
            assert entries==expected
        declarations=declaration['schedules'];assert len(declarations)==case['declared_schedules']
        assert len({tuple(s['sequence']) for s in declarations})==len(declarations)
        if case['synthetic']:
            expected_count=1
            for ingress in range(plan['ranks_per_domain']):
                sizes=[len(v) for k,v in declaration['streams'].items() if int(k.split(':')[0])==ingress]
                expected_count*=factorial(sum(sizes))//__import__('math').prod(factorial(n) for n in sizes)
            assert expected_count==COUNTS[int(name[-1])]==len(declarations);synthetic+=len(declarations)
        else:assert [s['name'] for s in declarations]==list(POLICIES)
        for s in declarations:
            assert Counter(s['sequence'])==Counter({k:len(v) for k,v in declaration['streams'].items() if v})
        rows=[];layouts={};projection_hashes=set()
        assert [row['name'] for row in truth_index[name]]==[s['name'] for s in declarations]
        for schedule,index in zip(declarations,truth_index[name]):
            p=run/'truth'/name/(schedule['name']+'.json');assert sha(p)==index['trace_sha256']
            trace=load(p);positions=defaultdict(int);ordered=[]
            for forwarder in schedule['sequence']:
                token=declaration['streams'][forwarder][positions[forwarder]]['dispatch'];positions[forwarder]+=1
                ordered.extend(op for op in sorted(r['allocations'],key=lambda op:r['allocations'][op]['target'])
                               if r['allocations'][op]['dispatch']==token)
            assert [a['id'] for a in trace['allocations']]==ordered
            checks=check_trace(config,plan,declaration,r,trace)
            assert all(index[k]==checks[k] for k in ('layout_sha256','projection_sha256'))
            layouts.setdefault(checks['layout_sha256'],schedule['name']);projection_hashes.add(checks['projection_sha256'])
            rows.append(dict(schedule=schedule['name'],**checks));total+=1
            if name=='synthetic-0':
                if witness is None:witness=(config,plan,declaration,r,trace)
                elif check_trace(*witness)['layout_sha256']!=checks['layout_sha256'] and isinstance(witness,tuple):
                    alternate=trace
            if name=='synthetic-5' and wrong_counter_args is None:wrong_counter_args=(config,plan,declaration,schedule)
        assert projection_hashes=={r['projection_sha256']}
        results[name]=dict(schedules=len(rows),distinct_layouts=len(layouts),distinct_token_associations=len(projection_hashes),
            first_distinct_layout_schedules=list(layouts.values())[:2],checks=rows)
    assert total==156 and synthetic==150
    rejections={}
    for kind in ('slot_collision','counter_owner','duplicate_gpu_allocation','inverse_link','finish_marker','source_metadata'):
        args=deepcopy(witness);trace=args[-1]
        if kind=='slot_collision':
            trace['allocations'][1]['slot']=trace['allocations'][0]['slot'];trace['allocations'][1]['byte_offset']=trace['allocations'][0]['byte_offset']
        elif kind=='counter_owner':trace['allocations'][0]['atomic_owner']+=1
        elif kind=='duplicate_gpu_allocation':next(rows[0] for rows in trace['forward_metadata'].values() if rows)['slot_lanes'][1]=0
        elif kind=='inverse_link':next(r for r in trace['linked_list'] if r['row']>=0)['row']+=1
        elif kind=='finish_marker':next(r for r in trace['linked_list'] if r['row']==-1)['row']=0
        else:trace['epilogue'][0]['source_global_token']+=1
        try:check_trace(*args)
        except (AssertionError,KeyError,IndexError,ValueError):rejections[kind]='rejected'
        else:raise AssertionError('accepted corruption '+kind)
    # Two mechanism controls, never benefit baselines.
    from .model import interpret
    config,plan,declaration,schedule=wrong_counter_args
    try:interpret(config,plan,declaration['streams'],schedule['sequence'],wrong_global_counter=True)
    except KeyError:wrong_counter='rejected_missing_dense_sender_partition'
    else:raise AssertionError('global target counter unexpectedly accepted')
    stale=deepcopy(witness);stale[-1]['epilogue']=alternate['epilogue'];stale[-1]['linked_list']=alternate['linked_list']
    try:check_trace(*stale)
    except (AssertionError,KeyError):stale_result='rejected'
    else:raise AssertionError('stale inverse mapping accepted')
    save(output,dict(passed=True,raw_route_files=15,library_files=7,public_plans=8,synthetic_schedules=150,
        real_declared_schedules=6,cases=results,corruption_rejections=rejections,
        controls=dict(global_target_counter=wrong_counter,stale_inverse_mapping=stale_result),
        scope='declared token-step allocation and inverse contribution association; no timing/transport/complete Combine',
        new_networks=0,gpu_executed=False,paper_innovations_established=0))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();verify(a.run.resolve(),a.output.resolve())
