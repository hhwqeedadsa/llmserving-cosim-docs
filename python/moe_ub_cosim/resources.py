"""Explicit host record budgets, separate from modeled HBM and protocol limits.

Graphs and completed transfer objects still reside in memory. These limits bound
record counts and serialized plan bytes; they are not an RSS prediction.
"""
from collections import Counter

from .plan import integer, require, MAX_TRANSFERS

FIELDS = ('max_graph_transfers', 'max_graph_nodes', 'max_plan_bytes',
          'max_run_graphs', 'max_run_transfers', 'max_run_nodes')


def validate_budget(budget):
    require(isinstance(budget,dict) and set(budget)==set(FIELDS)|{'schema_version'} and
            budget['schema_version']=='moe-ub-graph-resources/1','Invalid graph resource budget')
    for key in FIELDS:integer(budget[key],key,1)
    require(budget['max_graph_transfers']<=budget['max_run_transfers']<499999900 and
            budget['max_graph_nodes']<=budget['max_run_nodes'],'Graph/run resource or comm_tag budget mismatch')
    return budget


def check_graph(usage,budget=None):
    if budget is None:
        require(usage['transfers']<=MAX_TRANSFERS,'Functional graph exceeds 4096 transfers; explicit resource budget required')
    else:
        validate_budget(budget)
        for key in ('transfers','nodes'):
            require(usage[key]<=budget['max_graph_'+key],f'Graph resource budget exceeded: {key}={usage[key]}')
    return usage


def cann_usage(c):
    """Count without constructing status messages, transfer dictionaries or DAGs."""
    if c.get('template')=='cann_a3_v850_store_resources_v1':
        from .a3 import usage
        return usage(c)
    ranks=c['rank_ids'];placement=c['expert_placement'];h=c['hidden_dim'];chunk=c['chunk_bytes']
    chips={r['rank_id']:r['chip_id'] for r in c.get('deployment',{}).get('ranks',[])}
    def locality(src,dst):
        if src==dst:return 'same_rank'
        return 'same_chip' if chips and chips[src]==chips[dst] else 'cross_chip'
    local=Counter(t['src_rank'] for t in c['tokens']);experts=Counter();prep={k:set() for k in ('dispatch','combine')}
    fabric=near=0
    for src in ranks:
        for dst in placement:
            if src!=dst:
                if locality(src,dst)=='same_chip':near+=1
                else:fabric+=1
    for token in c['tokens']:
        src=token['src_rank']
        for expert in token['expert_ids']:
            dst=placement[expert];experts[dst]+=1
            kind=locality(src,dst)
            if kind=='same_chip':
                near+=3;prep['dispatch'].add(src);prep['combine'].add(dst)
            elif kind=='cross_chip':
                dispatch=((h+31)//32*32+44+chunk-1)//chunk
                combine=(2*h+chunk-1)//chunk
                fabric+=dispatch+combine+1
                native=c.get('native_profile',{})
                fabric+=dispatch*int(native.get('dispatch')=='LOAD')+combine*int(native.get('combine')=='LOAD')
    compute=len(ranks)+2*len(local)+2*len(experts)
    if c['compute_profile_ns']['next_layer']:compute+=len(local)
    for kind in prep:
        if c.get('deployment',{}).get('local_prepare_ns_per_vector',{}).get(kind,0):compute+=len(prep[kind])
    return dict(transfers=fabric+near,fabric_transfers=fabric,local_transfers=near,nodes=2*(fabric+near)+compute)


def full_usage(configs,model):
    rows=[cann_usage(c) for c in configs]
    result={key:sum(r[key] for r in rows) for key in rows[0]}
    active=len({t['src_rank'] for t in configs[0]['tokens']})
    outer=4+sum(8 if r['kind']=='dense' else 10 if r['shared_experts'] else 8 for r in model['layers'])
    result['nodes']+=active*outer
    return result


def actual_usage(plan):
    fabric=sum('ub_task_id' in t for t in plan['transfers'])
    return dict(transfers=len(plan['transfers']),fabric_transfers=fabric,
        local_transfers=len(plan['transfers'])-fabric,nodes=sum(len(g['nodes']) for g in plan['rank_graphs']))


def check_run(usage,addition,budget):
    next_usage={k:usage[k]+(1 if k=='graphs' else addition[k]) for k in ('graphs','transfers','nodes')}
    for k,v in next_usage.items():
        require(v<=budget['max_run_'+k],f'Run resource budget exceeded: {k}={v}')
    return next_usage
