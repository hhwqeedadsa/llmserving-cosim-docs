"""Original four requests -> public modern CLI -> independent operand audit.

No fixture, tests, private graph generator, GPU or network backend is used.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def limits():
    resource.setrlimit(resource.RLIMIT_AS,(2*2**30,2*2**30))
    resource.setrlimit(resource.RLIMIT_CPU,(60,60))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();out=a.output.resolve();started=time.monotonic()
    assert not out.exists() and shutil.disk_usage(out.parent).free>=8*2**30+16*2**20
    out.mkdir();limits()
    commands=[]
    def cli(name,argv):
        remaining=299-(time.monotonic()-started);assert remaining>0
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,argv)]
        before=time.monotonic()
        with (out/(name+'.stdout')).open('w') as stdout,(out/(name+'.stderr')).open('w') as stderr:
            result=subprocess.run(command,cwd=ROOT,env=dict(os.environ,PYTHONPATH=str(ROOT/'python')),
                stdout=stdout,stderr=stderr,timeout=min(60,remaining),preexec_fn=limits)
        commands.append(dict(argv=command,cwd=str(ROOT),exitcode=result.returncode,
                             wall_seconds=time.monotonic()-before))
        save(out/'commands.json',commands)
        assert result.returncode==0,name
    try:
        path=a.manifest.resolve();assert path.stat().st_size<=2**20
        manifest=json.loads(path.read_text())
        assert manifest['source']['dataset_revision']=='27febb7b2d24169560a9f6f83d38eb53f94916cf'
        assert manifest['model']['hidden_dim']==7168 and len(manifest['files'])==4
        raw=[];identities=[];input_bytes=0
        for item in manifest['files']:
            source=ROOT/item['file']['path'];input_bytes+=source.stat().st_size
            assert input_bytes<=16*2**20 and sha(source)==item['file']['sha256']
            raw.append(json.loads(source.read_text()));identities.append(item['file'])
        calls=[];selected=[]
        for entry in (1,2):
            for layer in (3,4):
                assert manifest['layers'][layer]['num_experts']==256
                assert manifest['layers'][layer]['top_k']==8
                tokens=[]
                for rank,record in enumerate(raw):
                    rows=record[entry][str(layer)]
                    assert len(rows)==1 and len(set(rows[0]))==len(rows[0])==8
                    assert all(type(e) is int and 0<=e<256 for e in rows[0])
                    tokens.append(rows)
                    selected.append(dict(call=len(calls),rank=rank,source_entry=entry,source_layer=layer,
                                         source_sha256=identities[rank]['sha256'],choices=rows[0]))
                calls.append(dict(name=f'entry{entry}.layer{layer}',tokens=tokens))
        config=dict(schema_version='moe-modern-deepep-input/1',model=dict(experts=256,top_k=8,hidden_size=7168,precision='bf16'),
            execution=dict(ranks=4,max_tokens_per_rank=1,mode='direct_rdma_only_fresh',
                stream='compute_wait_no_deferred_epilogue',expanded_layout=True,allow_multiple_reduction=True,
                expert_alignment=1,num_sms=2,dispatch_warps=2,combine_warps=2,call_contexts=5,
                requested_contexts=5,connections=2,local_devices=[[0,1] for _ in range(4)]),calls=calls,
            limits=dict(max_events=65536,max_operations=16384,max_input_bytes=2**20,max_output_bytes=16*2**20))
        save(out/'input.json',config)
        save(out/'provenance.json',dict(manifest_sha256=sha(path),source=manifest['source'],files=identities,selections=selected,
            deployment='hypothetical EP4, one original request/rank, contiguous experts, RDMA-only FULL declared',
            launch='SM/warp/contexts and two local NIC identifiers declared; not hardware configuration',
            clock='selected entry/layer order only; no observed arrivals or launch times',full_r1_deployment_verified=False))
        cli('plan',['modern-deepep-plan','--config',out/'input.json','--output',out/'plan'])
        plan=json.loads((out/'plan/plan.json').read_text())
        assert plan['schema_version']=='moe-modern-deepep-partial-order/2'
        expected=[];pair_counts=Counter();expert_counts=Counter()
        for ci,call in enumerate(calls):
            for src,rows in enumerate(call['tokens']):
                for token,choices in enumerate(rows):
                    for dst in range(4):
                        positions=tuple(i for i,e in enumerate(choices) if 64*dst<=e<64*(dst+1))
                        if positions:
                            pair_counts[ci,src,dst]+=1
                            expected.extend([(ci,'dispatch','token_data',src,dst,token,14464,positions),
                                             (ci,'combine','token_data',dst,src,token,14400,positions)])
                    for expert in choices:expert_counts[ci,src,expert]+=1
                for dst in range(4):
                    expected.extend([(ci,'dispatch','rank_count',src,dst,None,8,()),
                                     (ci,'dispatch','expert_count_block',src,dst,None,512,())])
                    for phase in ('dispatch','combine'):
                        for which in ('entry','exit'):
                            expected.append((ci,phase,'barrier_signal',src,dst,None,8,()))
        actual=[(o['call'],o['phase'],o['kind'],o['src'],o['dst'],o.get('token'),o['operand_bytes'],
                 tuple(o.get('topk_positions',[]))) for o in plan['operations']]
        assert Counter(actual)==Counter(expected)
        # Independent API branch accounting. Endpoint equality alone cannot
        # classify self-peer signal or bulk expert blocks as a local bypass.
        expected_paths=Counter()
        for ci,phase,kind,src,dst,token,nbytes,positions in expected:
            path=('rdma' if src!=dst else 'gin_self' if kind in ('barrier_signal','expert_count_block') else 'local')
            expected_paths[path]+=1
        assert Counter(o['path'] for o in plan['operations'])==expected_paths
        zero=[];self_zero=[];dispatch_connections=Counter();combine_connections=Counter()
        provider_connections=Counter();self_connections=Counter()
        for op in plan['operations']:
            ci,src,dst=op['call'],op['src'],op['dst']
            if op['kind']=='rank_count':assert op['value']==-pair_counts[ci,src,dst]-1
            if op['kind']=='expert_count_block':
                assert op['values']==[-expert_counts[ci,src,e]-1 for e in range(64*dst,64*(dst+1))]
                assert op['source']==dict(region='workspace_send_expert_counts',element_offset=64*dst,element_bytes=8,epoch=2*ci)
                if src!=dst and all(v==-1 for v in op['values']):zero.append(op)
                if src==dst and all(v==-1 for v in op['values']):self_zero.append(op)
            if op['path']=='gin_self':
                assert src==dst and op['verb'] in ('gin_signal_inc64','gin_put') and op['binding']['context_index']==0
                self_connections[op['binding']['connection']]+=1
            if op['path']!='local' and not (op['phase']=='combine' and op['kind']=='token_data'):
                provider_connections[op['binding']['connection']]+=1
            if src!=dst and op['kind']=='token_data':
                if op['phase']=='dispatch':
                    assert op['binding']['context_index']==1 and op['binding']['connection']==1
                    dispatch_connections[1]+=1
                else:
                    binding=op['binding']
                    assert binding['source_to_owner_count']==1
                    index=sum(pair_counts[ci,s,src] for s in range(dst))
                    assert binding['source_rank_prefix']==index
                    assert binding['owner_received_tokens']==sum(pair_counts[ci,s,src] for s in range(4))
                    # With one token/source and <=4 incoming ranks, chunk=1.
                    # SM=index%2, rotated warp=index//2 -> context=index.
                    assert 0<=index<4
                    combine_connections[index%2]+=1
        provider_connections.update(combine_connections)
        assert expected_paths['gin_self']==len(calls)*5*4
        assert sum(provider_connections.values())==expected_paths['gin_self']+expected_paths['rdma']
        assert all(slot['group_size']==1 for slot in plan['slots'])
        remote=[o for o in plan['operations'] if o['src']!=o['dst']]
        first=next(o for o in remote if o['kind']=='token_data' and o['phase']=='dispatch')
        probes=dict(normal=None,local_completion=first['events']['local_complete'],
                    target_visibility=first['events']['visible'],final_signal_tail='m3.combine.exit.s0.d1.local_complete')
        if zero:probes['zero_block']=zero[0]['events']['visible']
        probes['self_signal_credit']='m0.dispatch.entry.s0.d0.local_complete'
        if self_zero:probes['self_zero_block']=self_zero[0]['events']['visible']
        results={}
        for name,held in probes.items():
            args=['modern-deepep-check','--plan',out/'plan','--output',out/(name+'.json')]
            if held:args+=['--withhold',held]
            cli(name,args);results[name]=json.loads((out/(name+'.json')).read_text())
        assert results['normal']['all_completions_reachable'] and results['final_signal_tail']['all_completions_reachable']
        for name in ('local_completion','target_visibility'):
            assert all(f'm3.combine.r{r}.complete' in results[name]['blocked_completions'] for r in range(4))
        cli('repeat',['modern-deepep-plan','--config',out/'input.json','--output',out/'repeat'])
        for name in ('input.json','manifest.json','plan.json'):
            assert (out/'plan'/name).read_bytes()==(out/'repeat'/name).read_bytes()
        summary=dict(platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            events=len(plan['events']),operations=len(actual),symbolic_slots=len(plan['slots']),
            source_files=4,source_rows=len(selected),independently_enumerated_operations=len(expected),
            remote_operation_counts=dict(Counter((o['phase']+'.'+o['kind']) for o in remote)),
            operation_paths=dict(expected_paths),
            gin_self_by_kind=dict(Counter(o['kind'] for o in plan['operations'] if o['path']=='gin_self')),
            gin_self_operand_bytes=sum(o['operand_bytes'] for o in plan['operations'] if o['path']=='gin_self'),
            gin_provider_requests=expected_paths['gin_self']+expected_paths['rdma'],
            provider_requests_by_connection_given_unique_slots=dict(provider_connections),
            self_peer_requests_by_connection=dict(self_connections),
            all_zero_self_expert_blocks=len(self_zero),
            remote_operand_bytes=dict((phase,sum(o['operand_bytes'] for o in remote if o['phase']==phase and o['kind']=='token_data'))
                                      for phase in ('dispatch','combine')),
            all_zero_remote_expert_blocks=len(zero),dispatch_data_by_declared_connection=dict(dispatch_connections),
            combine_data_by_connection_given_unique_slots=dict(combine_connections),
            slot_order_ambiguity='none within each source/destination in this batch1 selection; not true for general batch',
            probes={k:dict(withheld=v['withheld'],blocked=len(v['blocked_completions'])) for k,v in results.items()},
            repeat_outputs_byte_identical=3,network_execution=False,latency_predicted=False,hardware_calibrated=False,
            scope='source-level API request counts, including self-peer GIN; not WQE, packet or timing counts')
        save(out/'summary.json',summary)
        assert sum(p.stat().st_size for p in out.rglob('*') if p.is_file())<=16*2**20
        assert resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss<=2**20
        save(out/'execution.json',dict(state='passed',wall_seconds=time.monotonic()-started,
            peak_child_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
            commands=len(commands),cwd=str(ROOT),argv=sys.argv,input_bytes=input_bytes))
        print(json.dumps(summary,ensure_ascii=False))
    except Exception as error:
        save(out/'execution.json',dict(state='failed',reason=str(error),argv=sys.argv,commands=len(commands)))
        raise


if __name__=='__main__':
    main()
