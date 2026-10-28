"""Derive Qwen3-235B inputs from hash-checked upstream configuration, without execution."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from moe_ub_cosim.scaleout_llm import capacity, validate_llm


def read(p): return json.loads(p.read_bytes())
def save(p, value): p.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def prepare(sources, base, output):
    assert not output.exists(); output.mkdir(parents=True)
    manifest=read(sources/'manifest.json')
    for entry in manifest:
        raw=(sources/entry['file']).read_bytes()
        assert len(raw)==entry['bytes']
        assert hashlib.sha256(raw).hexdigest()==entry['sha256']
        if 'git_blob_sha1' in entry:
            assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==entry['git_blob_sha1']
    q=read(sources/'qwen235.json')
    assert q['model_type']=='qwen3_moe' and q['decoder_sparse_step']==1 and not q['mlp_only_layers']
    assert not q['attention_bias'] and not q['tie_word_embeddings']
    template=read(base); m=template['model']; llm=m['llm']
    m.update(name='Qwen3-235B-A22B-shape-BF16-declared-compute',hidden_size=q['hidden_size'],
             ffn_hidden_size=q['moe_intermediate_size'],experts=q['num_experts'],
             top_k=q['num_experts_per_tok'],layers=q['num_hidden_layers'])
    llm.update(attention_heads=q['num_attention_heads'],kv_heads=q['num_key_value_heads'],
               head_dim=q['head_dim'],vocab_size=q['vocab_size'],
               tied_embeddings=q['tie_word_embeddings'],weight_bytes=2)
    validate_llm(m,template['compute'])
    records=[]
    for size in (320,512):
        config=deepcopy(template);replicas=size//64
        # Spread each EP64 group across Pods, rather than confining it to one Pod.
        groups=[list(range(i,size,replicas)) for i in range(replicas)]
        config['parallel']['groups']=groups
        assert all(len(g)==64 for g in groups)
        assert sorted(rank for g in groups for rank in g)==list(range(size))
        cap=capacity(config,64)
        # Independently count projections, norms, embeddings and routed experts.
        h=q['hidden_size']; l=q['num_hidden_layers']; hd=q['head_dim']
        ex=3*l*h*q['moe_intermediate_size']*q['num_experts']
        replicated=2*q['vocab_size']*h+l*(2*h*hd*(q['num_attention_heads']+q['num_key_value_heads'])+
                      2*hd+2*h+h*q['num_experts'])+h
        assert cap['total_parameters']==ex+replicated
        assert cap['weight_bytes_per_rank']==2*(replicated+ex//64)
        filename=f'qwen235-{size}.json';save(output/filename,config)
        records.append(dict(file=filename,gpus=size,ep=64,replica_groups=replicas,experts_per_rank_per_layer=2,
                            batch_per_rank=config['workload']['batch_per_rank'],
                            global_batch=size*config['workload']['batch_per_rank'],**cap,
                            routing='synthetic; no R1 route relabeling',compute='declared_reference',
                            network_executed=False,model_shape_source='qwen235.json'))
    exact=[]
    for size in (300,500):
        assert __import__('math').gcd(size,q['num_experts'])==4
        try: capacity(template,4)
        except ValueError as error: reason=str(error)
        else: raise AssertionError('expected EP4 BF16 memory rejection')
        exact.append(dict(gpus=size,max_uniform_ep=4,experts_per_rank_per_layer=32,
                          bf16_weight_bytes_per_rank=2*(replicated+ex//4),
                          runnable_model_config_provided=False,reason=reason))
    result=dict(models=records,exact_size_capacity=exact,sources_verified=len(manifest),
                calibration='shape declaration only; no numerical kernels, routing measurement or GPU timing',
                memory_scope='weights and GQA KV only; excludes activation, workspace, allocator and communication buffers',
                full_r1_supported=False,large_network_executions=0)
    save(output/'summary.json',result);print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',type=Path,required=True);p.add_argument('--base',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    prepare(a.sources,a.base,a.output)
