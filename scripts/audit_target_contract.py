"""Offline R1 storage arithmetic and current implementation boundaries.

Reads pinned, already acquired files; never downloads or runs target code.
Exit success means the audit completed, not that the target can be simulated.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import yaml

from moe_ub_cosim.model_capacity import audit_capacity
from moe_ub_cosim.observation import validate_observation
from moe_ub_cosim.plan import MAX_TRANSFERS, build_plan

ROOT=Path(__file__).resolve().parents[1]


def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def align(n,a):return (n+a-1)//a*a


def audit(reference, source_dir):
    assert reference['schema_version']=='moe-ub-target-contract-audit/1'
    assert reference['runtime_configuration'] is reference['formal_run_ready'] is False
    assert reference['reference_policy_id']=='r1_tp1_ep128_row_int8_compressed_mla_bf16_v1'
    assert sha(ROOT/reference['scenario_path'])==reference['scenario_sha256']
    scene=yaml.safe_load((ROOT/reference['scenario_path']).read_text())
    spec=reference['structural_config'];assert sha(ROOT/spec['path'])==spec['sha256']
    model=read(ROOT/spec['path'])
    sources=[]
    for source in reference['structural_implementation_sources']:
        p=source_dir/source['path'];assert p.stat().st_size==source['bytes'] and sha(p)==source['sha256']
        sources.append(source)
    assert scene['ns3_backend']['transaction_profile_for_bringup']==reference['transactions']['profile']=='urma_write_rtp_reference'
    roles=reference['transactions']['roles'];assert set(roles)=={'dispatch_data','dispatch_count_status','combine_data','combine_completion_status'}
    assert all(r==dict(programming_model='URMA',operation='WRITE',transport='RTP',initiator='data_owner',
        payload_direction='initiator_to_target',receiver_ready='RemoteWriteCommitted',sender_release='transaction_done') for r in roles.values())
    assert reference['transactions']['native_MTE_LDST_equivalence'] is False
    h,v=model['hidden_size'],model['vocab_size'];n=model['num_hidden_layers'];dense=model['first_k_dense_replace'];moe=n-dense
    heads,qr,kr=model['num_attention_heads'],model['q_lora_rank'],model['kv_lora_rank']
    nope,rope,vd=model['qk_nope_head_dim'],model['qk_rope_head_dim'],model['v_head_dim']
    experts,topk,f,fd=model['n_routed_experts'],model['num_experts_per_tok'],model['moe_intermediate_size'],model['intermediate_size']
    dep=reference['deployment'];ep=dep['ep'];local=dep['local_routed_experts'];shared=dep['local_shared_experts']
    assert dep==dict(ep=128,tp=1,pp=1,dies_per_chip=2,local_routed_experts=2,local_shared_experts=1)
    assert (h,v,n,dense,experts,topk)==(7168,129280,61,3,256,8)
    assert experts==ep*local and model['n_shared_experts']==shared and not model['tie_word_embeddings']
    assert scene['model']['mla']==dict(num_attention_heads=heads,q_lora_rank=qr,kv_lora_rank=kr,
        qk_nope_head_dim=nope,qk_rope_head_dim=rope,v_head_dim=vd)
    policy=reference['weight_storage']
    assert (policy['matrix_dtype'],policy['scale_dtype'],policy['scale_axis'],policy['symmetric'],
        policy['zero_point_bytes'],policy['matrix_storage_padding_bytes'])==('int8','float32','output_channel',True,0,0)
    assert [policy[k] for k in ('embedding_dtype','lm_head_dtype','norm_dtype','router_dtype','routing_correction_dtype')]==['bfloat16']*3+['float32']*2
    assert not policy['tied_embedding'] and not policy['mtp_included']
    tensors=[]
    def tensor(name,rows,cols,count,dtype):
        params=rows*cols*count;weight=params*{'int8':1,'bfloat16':2,'float32':4}[dtype]
        scales=rows*count*4 if dtype=='int8' else 0
        tensors.append(dict(component=name,shape=[rows,cols],copies_per_rank=count,dtype=dtype,
            parameters=params,weight_bytes=weight,scale_bytes=scales,total_bytes=weight+scales))
    tensor('embedding',v,h,1,'bfloat16');tensor('lm_head',v,h,1,'bfloat16');tensor('final_norm',h,1,1,'bfloat16')
    for name,rows,cols in [('mla_q_a',qr,h),('mla_q_b',heads*(nope+rope),qr),('mla_kv_a',kr+rope,h),
                           ('mla_kv_b',heads*(nope+vd),kr),('mla_o',h,heads*vd)]:tensor(name,rows,cols,n,'int8')
    tensor('block_norms',h,1,2*n,'bfloat16');tensor('mla_q_norm',qr,1,n,'bfloat16');tensor('mla_kv_norm',kr,1,n,'bfloat16')
    for kind,size,count in [('dense_ffn',fd,dense),('routed_expert_ffn',f,local*moe),('shared_expert_ffn',f,shared*moe)]:
        tensor(kind+'_gate_up',size,h,2*count,'int8');tensor(kind+'_down',h,size,count,'int8')
    tensor('router',experts,h,moe,'float32');tensor('routing_correction',experts,1,moe,'float32')
    parameters=sum(t['parameters'] for t in tensors)
    global_parameters=parameters+(experts-local)*moe*3*h*f
    weight_bytes=sum(t['weight_bytes'] for t in tensors);scale_bytes=sum(t['scale_bytes'] for t in tensors)
    kv=reference['kv'];assert (kv['layout'],kv['latent_dtype'],kv['rope_dtype'],kv['quantization'],kv['alignment_padding_bytes'])==(
        'absorbed_latent_plus_shared_rope','bfloat16','bfloat16','none',0)
    block=kv['block_tokens'];assert block==scene['compute_and_memory']['kv_cache']['allocation_block_tokens']['value']==16
    bpt=n*(kr+rope)*2;block_bytes=bpt*block
    assert bpt==70272 and block_bytes==1124352
    memory=reference['memory'];bs=scene['workload']['profiles']['d128_b8_window64']['max_active_requests_per_rank']
    minimum_window=2*bs*(experts*align(align(2*h,32)+44,512)+topk*align(2*h,512))
    window=memory['cann_data_window_bytes_per_layer'];state=memory['cann_state_window_bytes_per_layer'];lanes=memory['cann_reference_lanes']
    assert window>=minimum_window and state==1048576 and lanes==2
    staging=2*lanes*align(align(h,32)+44,32);communication=moe*(window+state+staging)
    reserved=memory['kernel_and_runtime_reserve_bytes_per_rank'];available=memory['device_bytes_per_rank']-weight_bytes-scale_bytes-communication-reserved
    assert available>=block_bytes
    capacity_blocks=available//block_bytes
    # Status writes alone prove the present graph budget is insufficient; no
    # target graph is materialized and no target-scale simulation is launched.
    status_nonself=ep*(experts-local);status_local=ep*local*(dep['dies_per_chip']-1)
    status_fabric=status_nonself-status_local
    assert status_nonself==32512 and status_fabric==32256 and status_nonself>MAX_TRANSFERS
    files=[];manifest_ref=reference['route_manifest'];assert sha(ROOT/manifest_ref['path'])==manifest_ref['sha256']
    manifest=read(ROOT/manifest_ref['path'])
    for file in manifest['files']:
        item=file['file'];assert sha(ROOT/item['path'])==item['sha256'];data=read(ROOT/item['path'])
        assert isinstance(data,list) and len(data)>1
        prompt=len(data[0]['3']);records=0
        for step,entry in enumerate(data):
            assert set(entry)==set(map(str,range(n)))
            assert all(entry[str(i)] is None for i in range(dense))
            for i in range(dense,n):
                selections=entry[str(i)];assert len(selections)==(prompt if step==0 else 1)
                for selected in selections:assert len(selected)==topk and len(set(selected))==topk and all(type(e)is int and 0<=e<experts for e in selected)
                if step:records+=1
        reservation=align(prompt+64,block)*bpt
        files.append(dict(file_id=file['file_id'],sha256=item['sha256'],prompt_tokens=prompt,
            available_decode_steps=len(data)-1,decode_layer_records=records,eligible_window64=len(data)-1>=64,
            window64_reserved_tokens=align(prompt+64,block),window64_kv_bytes=reservation))
    # Exercise actual rejection paths on small fixtures without disguising R1
    # as a supported GQA runtime or creating a full-size target execution.
    rejections={};small=read(ROOT/'fixtures/m10_cann/service.json');layer=read(ROOT/'fixtures/m10_cann/layers.json')['groups'][0]['layers'][0]
    model0=small['groups'][0]['model_spec'];memory0=small['groups'][0]['memory']
    def rejected(name,fragment,call):
        try:call()
        except ValueError as exc:
            assert fragment in str(exc),(name,str(exc));rejections[name]=str(exc)
        else:raise AssertionError(name+' unexpectedly accepted')
    m=deepcopy(model0);m['architecture']='bias_free_mla_rmsnorm_gated_ffn'
    rejected('mla_architecture','Unsupported model architecture',lambda:audit_capacity(m,memory0,layer))
    m=deepcopy(model0);m['dtypes']['weights']='int8'
    rejected('quantized_weights','Unsupported model dtype',lambda:audit_capacity(m,memory0,layer))
    m=deepcopy(model0);m['layers'][layer['layer_id']].update(shared_experts=1,shared_intermediate_size=256)
    rejected('selected_shared_expert','shared-expert execution is not implemented',lambda:audit_capacity(m,memory0,layer))
    l=deepcopy(layer);l['layer_id']=0
    rejected('dense_execution','Selected execution layer must be MoE',lambda:audit_capacity(model0,memory0,l))
    l=read(ROOT/'fixtures/m10_cann/layer.json');l['chunk_bytes']=1;l['tokens'][0]['expert_ids']=[4,5]
    rejected('small_graph_budget','4096 transfer budget',lambda:build_plan(l))
    obs=dict(clocks=[dict(device_type=t,device_id=i,offset_ns=0,drift_ppb=0) for t,i in [('rank',2),('switch',8)]],
        switches=[dict(switch_id=8,port_ids=list(range(17)),start_local_ns=0,end_local_ns=1000,period_ns=1000)],record_provenance=True)
    rejected('observed_ports','Invalid observed ports',lambda:validate_observation(obs,[2],1000))
    obs['switches'][0].update(port_ids=[4],end_local_ns=4097000)
    rejected('observed_windows','Too many observation windows',lambda:validate_observation(obs,[2],4097000))
    return dict(passed=True,audit_only=True,formal_run_ready=False,reference_policy_id=reference['reference_policy_id'],
        model=dict(hidden=h,layers=n,dense_layers=dense,moe_layers=moe,routed_experts=experts,shared_experts=shared,
            parameters_per_rank=parameters,unique_parameters_excluding_mtp=global_parameters,tensors=tensors,
            weight_bytes_per_rank=weight_bytes,scale_bytes_per_rank=scale_bytes,total_weight_bytes_per_rank=weight_bytes+scale_bytes),
        memory=dict(kv_bytes_per_token=bpt,kv_block_bytes=block_bytes,cann_data_window_minimum_bytes=minimum_window,
            cann_staging_bytes_per_layer=staging,communication_buffer_bytes_per_rank=communication,
            conditional_available_kv_bytes=available,conditional_kv_capacity_blocks=capacity_blocks,
            conditional_uniform_prompt_limit_at_8_requests=capacity_blocks//8*block-64,
            conditional_uniform_prompt_limit_at_16_requests_window128=capacity_blocks//16*block-128,
            qualification='Conditional arithmetic only; measured HBM/runtime allocation and complete target admission unverified'),
        status_only_lower_bound=dict(nonself_transfers_per_layer=status_nonself,same_chip_transfers_per_layer=status_local,
            fabric_writes_per_layer=status_fabric,fabric_payload_bytes_per_layer=status_fabric*32,
            graph_transfer_limit=MAX_TRANSFERS,all_58_layers_transfers=status_nonself*moe),
        routes=dict(checked_manifest_files=files,eligible_window64=sum(f['eligible_window64'] for f in files),
            required_unique_window64=1024,required_unique_refill=2048,scope='only manifest files, not an exhaustive dataset scan'),
        actual_rejections=rejections,source_evidence=sources,
        source_hashes={str(p.relative_to(ROOT)):sha(p) for folder in ('python/moe_ub_cosim','backend')
            for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in ('.py','.cc','.hh','.txt','.cmake')})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',type=Path,default=ROOT/'fixtures/m10_target_contract/reference.json')
    parser.add_argument('--source-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();out=args.output_dir;out.mkdir(parents=True,exist_ok=False)
    result=audit(read(args.reference),args.source_dir)
    result['reference_sha256']=sha(args.reference);result['audit_script_sha256']=sha(Path(__file__))
    (out/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS: pinned structural sources, conditional R1 inventory, MLA KV, route availability and seven current rejection paths')
    print('Target execution ready: false; no full-target simulation performed')


if __name__=='__main__':main()
