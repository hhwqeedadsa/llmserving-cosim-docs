"""GQA MoE architecture and capacity declarations for the scaled RDMA service.

No tensor execution or hardware calibration is implied by these exact shapes.
"""
from .scaleout_topology import fields, integer, require

EXTRA_COSTS = ('embedding norm residual lm_head sampling attention_context').split()


def validate_llm(model, compute):
    spec=model['llm']
    fields(spec,'attention_heads kv_heads head_dim vocab_size tied_embeddings weight_bytes device_bytes', 'GQA LLM')
    for key in ('attention_heads','kv_heads','head_dim','vocab_size'):
        integer(spec[key],key,1,1000000)
    require(spec['attention_heads']%spec['kv_heads']==0,'GQA heads must divide KV groups')
    require(type(spec['tied_embeddings']) is bool,'invalid embedding tie flag')
    integer(spec['weight_bytes'],'weight element bytes',1,4)
    integer(spec['device_bytes'],'device memory bytes',1073741824,1099511627776)
    for key in EXTRA_COSTS:
        integer(compute[key+'_ns_per_token'],key+' cost',0,1000000)


def capacity(config, group_size, contexts=None):
    m,w=config['model'],config['workload'];h,f,e,l=m['hidden_size'],m['ffn_hidden_size'],m['experts'],m['layers']
    expert=3*l*h*f*e
    if config['schema_version']=='moe-scaleout-service/1':
        return dict(expert_parameters=expert,replicated_parameters=0,
                    weight_bytes_per_rank=expert//group_size*2,
                    kv_bytes_per_token=2*l*h*m['activation_bytes'],device_bytes=None)
    s=m['llm'];q=s['attention_heads']*s['head_dim'];kv=s['kv_heads']*s['head_dim']
    # Q/K/V/O projections, Q/K head norm, two layer norms, router; final norm.
    replicated=s['vocab_size']*h*(1 if s['tied_embeddings'] else 2)+l*(2*h*q+2*h*kv+2*s['head_dim']+2*h+h*e)+h
    weight=(replicated+expert//group_size)*s['weight_bytes']
    kv_token=2*l*kv*m['activation_bytes']
    contexts=contexts if contexts is not None else [w['context_tokens']]*w['batch_per_rank']
    sparse=config.get('route_replay',{}).get('request_layout')=='sparse_explicit/1'
    require((0<=len(contexts)<=w['batch_per_rank']) if sparse else len(contexts)==w['batch_per_rank'],
            'KV context vector differs from batch')
    kv_blocks=sum((context+w['decode_steps']+15)//16 for context in contexts)
    kv_reserved=kv_blocks*16*kv_token
    require(weight+kv_reserved <= s['device_bytes'],'weights plus declared KV exceed GPU device memory')
    return dict(expert_parameters=expert,replicated_parameters=replicated,total_parameters=expert+replicated,
                weight_bytes_per_rank=weight,kv_bytes_per_token=kv_token,kv_reserved_bytes_per_rank=kv_reserved,
                device_bytes=s['device_bytes'],other_workspace_bytes=0,
                scope='BF16/declared weights + GQA KV; no activation/workspace/allocator overhead calibration')
