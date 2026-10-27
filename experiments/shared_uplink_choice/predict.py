"""Ordinary phase/resource baselines. This module only receives observed input."""
import argparse
from collections import Counter, defaultdict, deque
import json
from pathlib import Path

from experiments.compact_network_choice.compact import STAGES, stage_of, summarize
from experiments.shared_uplink_choice.prepare import CONDITIONS, load, save


def combine_context(binding, slot, execution):
    """Independent arithmetic from the public symbolic binding; no slot order guess."""
    sms, warps = execution['num_sms'], execution['combine_warps']
    width = (binding['owner_received_tokens']+sms*warps-1)//(sms*warps)
    channel = (binding['source_rank_prefix']+slot)//width
    sm, warp = channel % sms, channel//sms
    contexts = execution['call_contexts']
    if contexts == 1: return 0
    if sms <= contexts:
        count = contexts//sms+int(sm < contexts % sms)
        return sm+(warp % count)*sms
    return (sm*warps+warp) % contexts


def messages(cfg, plan):
    cells = Counter()
    execution = cfg['modern']['execution']
    deployment = {r['rank']: r for r in cfg['deployment']}
    def add(op, context):
        src, dst = op['src'], op['dst']
        connection = context % execution['connections']
        devices = []
        for rank in (src, dst):
            table = execution['local_devices'][rank]
            device = table[connection % len(table)]
            devices.append(next(d for d in deployment[rank]['devices'] if d['device'] == device))
        assert devices[0]['plane'] == devices[1]['plane']
        stage = stage_of(op)
        a, b = devices[0]['nic'], devices[1]['nic']
        cells[stage, src, dst, a, b, 'request'] += 32+op['operand_bytes']
        if op['kind'] == 'barrier_signal': cells[stage, dst, src, b, a, 'reply'] += 40
    groups = defaultdict(list)
    for op in plan['operations']:
        if op['path'] != 'rdma': continue
        if op['phase'] == 'combine' and op['kind'] == 'token_data':
            groups[op['call'], op['src'], op['dst']].append(op)
        else: add(op, op['binding']['context_index'])
    for ops in groups.values():
        op = ops[0]
        assert len(ops) == op['binding']['source_to_owner_count']
        common = lambda binding: {k:v for k,v in binding.items() if k != 'slot_variable'}
        assert all(o['operand_bytes'] == op['operand_bytes'] and common(o['binding']) == common(op['binding']) for o in ops)
        for slot in range(len(ops)): add(op, combine_context(op['binding'], slot, execution))
    return [dict(stage=s, src=a, dst=b, source_nic=an, destination_nic=bn, direction=d, bytes=n)
            for (s,a,b,an,bn,d),n in sorted(cells.items())]


def route(topology, src, dst, snic, dnic):
    kinds = {n['id']: n['kind'] for n in topology['nodes']}
    edges, graph = {}, defaultdict(list)
    for link in topology['links']:
        for a,b,ap,bp in ((link['src'],link['dst'],link['src_port'],link['dst_port']),
                          (link['dst'],link['src'],link['dst_port'],link['src_port'])):
            edges[a,ap] = dict(src=a, dst=b, src_port=ap, dst_port=bp,
                               rate_bps=link['rate_bps'], delay_ns=link['delay_ns'], tier=link['tier'])
            if kinds[a] != 'gpu' and kinds[b] != 'gpu': graph[a].append((b, ap))
    first, reverse = edges[src,snic], edges[dst,dnic]
    start, target = first['dst'], reverse['dst']
    distance, count, previous = {start:0}, {start:1}, {}
    pending = deque([start])
    while pending:
        node = pending.popleft()
        for peer, port in graph[node]:
            d = distance[node]+1
            if peer not in distance:
                distance[peer], count[peer], previous[peer] = d, count[node], (node, port)
                pending.append(peer)
            elif distance[peer] == d: count[peer] += count[node]
    assert target in distance and count[target] == 1, 'requires one shortest path with fixed NICs'
    middle, node = [], target
    while node != start:
        parent, port = previous[node]
        middle.append(edges[parent,port])
        node = parent
    last = edges[reverse['dst'],reverse['dst_port']]
    return [first, *reversed(middle), last]


def estimate(cfg, plan, topology):
    summary, rows = summarize(cfg, plan), messages(cfg, plan)
    placement = {r['rank']:r['node'] for r in cfg['deployment']}
    rates = Counter()
    for port in topology['nic_ports']: rates[port['gpu']] += port['rate_bps']
    methods = {m:[] for m in ('endpoint_phase','nic_phase','link_phase')}
    loads = []
    for stage in STAGES:
        endpoint, nic, linkload = Counter(), Counter(), Counter()
        links, propagation = {}, 0
        for row in rows:
            if row['stage'] != stage: continue
            src, dst, n = placement[row['src']], placement[row['dst']], row['bytes']
            path = route(topology,src,dst,row['source_nic'],row['destination_nic'])
            propagation = max(propagation,sum(p['delay_ns'] for p in path))
            endpoint[src,'send'] += n
            endpoint[dst,'receive'] += n
            nic[src,row['source_nic'],'send'] += n
            nic[dst,row['destination_nic'],'receive'] += n
            for edge in path:
                key = edge['src'],edge['src_port']
                linkload[key] += n
                links[key] = edge
        services = dict(endpoint_phase=max([0,*[n*8e9/rates[node] for (node,d),n in endpoint.items()]]),
            nic_phase=max([0,*[n*8e9/next(p['rate_bps'] for p in topology['nic_ports']
                if p['gpu']==node and p['gpu_port']==port) for (node,port,d),n in nic.items()]]),
            link_phase=max([0,*[n*8e9/links[key]['rate_bps'] for key,n in linkload.items()]]))
        local = max(r['self_ns']+r['local_ns'] for r in summary['local'] if r['stage']==stage)
        for method,service in services.items():
            methods[method].append(dict(stage=stage, service_ns=service, propagation_ns=propagation,
                local_service_ns=local, estimated_ns=max(service+propagation,local)))
        loads += [dict(stage=stage, **links[key], framed_bytes=n) for key,n in sorted(linkload.items())]
    values = {m:dict(estimated_application_ns=summary['expert_ns']+sum(s['estimated_ns'] for s in stages), stages=stages)
              for m,stages in methods.items()}
    values['volume'] = dict(estimated_application_ns=sum(max(
        r['bytes']*8e9/rates[placement[r['rank']]] for r in summary['volume'] if r['phase']==phase)
        for phase in ('dispatch','combine')))
    return dict(methods=values, messages=rows, loads=loads, summary=summary)


def execute(observed, output):
    output.mkdir()
    for batch in (4,8):
        cfg = load(observed/f'batch{batch}/input.json')
        plan = load(observed/f'batch{batch}/plan/plan.json')
        predictions = {name:estimate(cfg,plan,load(observed/(name+'-topology/topology.json'))) for name in CONDITIONS}
        choices = {}
        for method in ('volume','endpoint_phase','nic_phase','link_phase'):
            values = {n:p['methods'][method]['estimated_application_ns'] for n,p in predictions.items()}
            ties = [n for n in CONDITIONS if values[n] == min(values.values())]
            choices[method] = dict(selected=ties[0], tied_best=ties)
        save(output/f'batch{batch}.json',dict(batch=batch,predictions=predictions,choices=choices,
            future_results_read=False, fitted_constants=False, dynamic_binding='slot_bijection_multiset_only',
            plan_bytes=len(json.dumps(plan,separators=(',',':'),sort_keys=True).encode()),
            message_representation_bytes=len(json.dumps(predictions['baseline']['messages'],separators=(',',':'),sort_keys=True).encode())))


def main():
    p = argparse.ArgumentParser()
    for n in ('observed','output'): p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args()
    execute(a.observed.resolve(),a.output.resolve())


if __name__ == '__main__': main()
