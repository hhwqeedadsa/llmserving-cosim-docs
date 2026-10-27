"""Independent bitmask/expanded-expert accounting and negative evidence checks."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
from pathlib import Path

from .study import load, save, sha


def valid_choices(es):
    assert isinstance(es,list) and len(es)==8 and len(set(es))==8
    assert all(type(e) is int and 0<=e<256 for e in es)


def identity_check(run, identities):
    assert all(sha(run/name)==digest for name,digest in identities.items())


def expected_case(raws, ep, binding):
    accum={p:Counter() for p in ('prefill','decode')}
    calls={p:set() for p in accum}
    channel_counts=Counter()
    domains=ep//8
    for file_index,raw in enumerate(raws):
        owner=file_index if binding=='contiguous' else file_index*ep//15
        for entry,entry_rows in enumerate(raw):
            phase='prefill' if entry==0 else 'decode'
            m=accum[phase]
            for layer in range(3,61):
                calls[phase].add((entry,layer))
                for token,es in enumerate(entry_rows[str(layer)]):
                    valid_choices(es)
                    gpu_mask=domain_mask=0
                    expert_remote=0
                    for e in es:
                        gpu=e*ep//256
                        gpu_mask |= 1<<gpu
                        domain_mask |= 1<<(gpu//8)
                        expert_remote += (gpu//8 != owner//8)
                    own_domain_mask=((1<<8)-1)<<(8*(owner//8))
                    far_gpu=gpu_mask & ~own_domain_mask
                    far_domain=domain_mask & ~(1<<(owner//8))
                    forward=sum(bool(gpu_mask & (1<<gpu)) for gpu in range(ep) if gpu%8!=owner%8)
                    m.update(tokens=1,expert_contributions=8,destination_gpus=gpu_mask.bit_count(),
                        destination_domains=domain_mask.bit_count(),direct_cross_server_vectors=far_gpu.bit_count(),
                        hybrid_cross_server_vectors=far_domain.bit_count(),
                        hybrid_intra_server_remote_gpu_vectors=forward,
                        hybrid_ingress_self_gpu_copies=gpu_mask.bit_count()-forward,
                        combine_multiple_reduction_vectors=far_domain.bit_count(),
                        combine_no_multiple_unexpanded_vectors=far_gpu.bit_count(),
                        combine_no_multiple_expanded_vectors=expert_remote)
                    while far_domain:
                        bit=far_domain & -far_domain
                        destination=bit.bit_length()-1
                        channel_counts[entry,layer,owner,token%4,destination]+=1
                        far_domain-=bit
    for phase,m in accum.items():
        relevant=[n for k,n in channel_counts.items() if (k[0]==0)==(phase=='prefill')]
        n_calls=len(calls[phase]);finish=n_calls*ep*4*(domains-1)
        periodic=sum(sum(1 for count in range(1,n+1) if count%6==0) for n in relevant)
        m.update(declared_collectives=n_calls,data_carrying_remote_channels=len(relevant),
            dispatch_remote_finish_calls=finish,dispatch_empty_channel_finish_calls=finish-len(relevant),
            dispatch_remote_periodic_tail_calls=periodic,
            dispatch_remote_count_block_put_calls=n_calls*2*ep*(domains-1),
            dispatch_remote_count_operand_bytes=n_calls*ep*(domains-1)*(4*8+4*(256//domains)))
        for a,b in (('direct_cross_server_vectors','dispatch_hidden_bytes_direct'),
                    ('hybrid_cross_server_vectors','dispatch_hidden_bytes_hybrid'),
                    ('hybrid_intra_server_remote_gpu_vectors','hybrid_intra_server_remote_gpu_hidden_bytes')):
            m[b]=m[a]*14336
        assert m['combine_multiple_reduction_vectors']<=m['combine_no_multiple_unexpanded_vectors']<=m['combine_no_multiple_expanded_vectors']
        assert m['hybrid_intra_server_remote_gpu_vectors']+m['hybrid_ingress_self_gpu_copies']==m['destination_gpus']
    return {p:dict(m) for p,m in accum.items()},channel_counts


def check_payload(summary,expected):
    assert len(summary['cases'])==6
    assert [(r['ep'],r['binding']) for r in summary['cases']]==[(ep,b) for ep in (64,128,256) for b in ('contiguous','spread')]
    for row in summary['cases']:
        assert row['phases']==expected[row['ep'],row['binding']]
        assert row['gpus_per_server']==8 and row['servers']==row['ep']//8


def witness_check(w,raws,counts):
    eligible=sorted(k for k,n in counts.items() if k[0]==0 and n>=7)
    assert (w is not None)==bool(eligible)
    if w is None:return 0
    entry,layer,src,channel,dst=eligible[0]
    assert (w['entry'],w['layer'],w['source_rank'],w['channel'],w['destination_domain'])==eligible[0]
    index=w['request_index'];ep=w['ep']
    assert src==(index if w['binding']=='contiguous' else index*ep//15)
    tokens=raws[index][entry][str(layer)]
    selected=[i for i,es in enumerate(tokens) if i%4==channel and any(e*ep//256//8==dst for e in es)]
    assert w['first_seven_token_indices']==selected[:7] and w['stream_tokens']==len(selected)
    assert w['first_seven_choices']==[tokens[i] for i in selected[:7]]
    graph=w['necessary_order_subgraph'];edges=graph['after'];order=graph['linear_extension']
    assert len(order)==len(set(order))==len(edges) and set(order)==set(edges)
    visited=set()
    for node in order:
        assert set(edges[node])<=visited
        visited.add(node)
    n=len(selected)
    assert set(edges['finish.release'])=={f'visible.{i}' for i in range(n)}|{f'tail.{k}.release' for k in range(6,n+1,6)}
    assert set(edges['forward.0'])=={'tail.6.visible'}
    assert set(edges['tail.6.release'])=={f'visible.{i}' for i in range(6)}
    assert order.index('forward.0')<order.index('finish.visible')
    assert graph['scope']=='necessary_single_channel_source_projection_not_full_execution_or_timing'
    return 1


def reject(label,call):
    try:call()
    except (AssertionError,KeyError,ValueError):return label
    raise AssertionError('accepted corruption: '+label)


def execute(run,output):
    identities=load(run/'input-identity.json');identity_check(run,identities)
    source=load(run/'observed/routes.json')
    assert len(source['files'])==15 and source['excluded']==['899.json','1479.json']
    raws=[]
    for row in source['files']:
        path=run/'observed/routes'/row['name']
        assert sha(path)==row['sha256']
        raw=load(path)
        for entry in raw:
            assert set(entry)=={str(i) for i in range(61)}
            assert all(entry[str(i)] is None for i in range(3))
        raws.append(raw)
    library=load(run/'observed/library.json')
    assert library['commit']=='f6aa38fe06cd4a0489e7730365d4e0114970a2f3' and len(library['files'])==6
    for name,row in library['files'].items():
        path=run/'observed/library'/name;raw=path.read_bytes()
        assert sha(path)==row['sha256'] and hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==row['git_blob_sha']
    summary=load(run/'analysis.json');expected={};witnesses=0
    for row in summary['cases']:
        key=row['ep'],row['binding']
        expected[key],counts=expected_case(raws,*key)
        witnesses+=witness_check(row['prefill_overlap_witness'],raws,counts)
    check_payload(summary,expected)
    assert summary['packet_networks']==summary['actual_GPU_or_provider_runs']==0
    assert not summary['new_method_latency_benefit_proven'] and not summary['simulator_speedup_proven']
    for name,anchors in summary['source_evidence'].items():
        path=run/'observed/library'/name
        assert sha(path)==anchors['sha256']
        lines=path.read_text().splitlines()
        for needle,locations in anchors['anchor_lines'].items():
            assert locations==[i for i,line in enumerate(lines,1) if needle in line]
    rejected=[]
    bad=identities.copy();bad[next(iter(bad))]='0'*64
    rejected.append(reject('source content identity',lambda:identity_check(run,bad)))
    bad_choices=raws[0][0]['3'][0].copy();bad_choices[0]=256
    rejected.append(reject('invalid route expert',lambda:valid_choices(bad_choices)))
    for label,field in (
        ('remote domain boundary','hybrid_cross_server_vectors'),
        ('Combine reduction branch','combine_multiple_reduction_vectors'),
        ('zero data finish omission','dispatch_remote_finish_calls'),
    ):
        bad=deepcopy(summary);bad['cases'][0]['phases']['prefill'][field]+=1
        rejected.append(reject(label,lambda:check_payload(bad,expected)))
    save(output,dict(passed=True,cases=6,source_files=15,library_sources=6,
        token_layer_rows=sum(expected[64,'contiguous'][p]['tokens'] for p in ('prefill','decode')),
        bitmask_cases_checked=True,independent_tail_counter_expansion=True,overlap_subgraphs=witnesses,
        rejected_corruptions=rejected,new_networks=0,new_GPU_runs=0,
        full_execution_or_timing_claim=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    execute(a.run.resolve(),a.output.resolve())
