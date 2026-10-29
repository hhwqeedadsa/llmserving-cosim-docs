"""Human-readable simulated hardware, deployment and outcome for every SimAI run."""
from collections import Counter,defaultdict
import json
from pathlib import Path

from .scaleout_rdma import checked_json,save
from .scaleout_limits import limits
from .service_terminal import service_terminal


def describe(directory):
    d=Path(directory);c=checked_json(d/'input.json',1048576)
    t=checked_json(d/'topology.json',8388608);p=checked_json(d/'rdma.json',65536)
    s=c['service'];gpus=[v for v in t['nodes'] if v['kind']=='gpu'];switches=Counter(v['kind'] for v in t['nodes'] if v['kind']!='gpu')
    rates=defaultdict(set);nic=Counter()
    for edge in t['links']:
        rates[edge['tier']].add(edge['rate_bps'])
        if edge['src']<len(gpus):nic[edge['src']]+=edge['rate_bps']
        if edge['dst']<len(gpus):nic[edge['dst']]+=edge['rate_bps']
    requests=c['routes']['requests'];counts=Counter(q['rank_id'] for q in requests)
    result=checked_json(d/'result.json',limits(p)['result_json_bytes']) if (d/'result.json').is_file() else None
    f=checked_json(d/'frontend.json',4*2**20) if (d/'frontend.json').is_file() else None
    groups=[]
    for i,group in enumerate(s['parallel']['groups']):
        groups.append(dict(group=i,ep=len(group),rank_ids=group,experts_per_rank_per_layer=s['model']['experts']//len(group),
            source_requests=sum(counts[r] for r in group),source_ranks=sum(counts[r]>0 for r in group),
            actual_requests_per_source_rank={str(r):counts[r] for r in group if counts[r]}))
    outcome=dict(state='not_started_or_no_result') if result is None else dict(state='completed' if result['passed'] else 'failed',
        native_network_executed=result['native_network_executed'],finish_ns=result['finish_ns'],
        submitted_messages=len(result['messages']),logical_submitted_bytes=sum(v['bytes'] for v in result['messages']),
        actual_data_ranks=len({rank for m in result['messages'] for rank in (m['src'],m['dst'])}),
        complete_packet_capture=result.get('packet_capture',{}).get('complete'),
        packet_capture_events=len(result.get('packet_capture',{}).get('events',[])),
        fault_drops=result['fault_drops'],data_loss=result.get('data_loss'),failure=result.get('failure'))
    outcome.update(service_terminal(result,f))
    return dict(schema_version='moe-run-summary/1',scope='simulated hardware/deployment; no GPU workload or hardware timing calibration',
        gpu_label=s['model']['gpu_type'],simai_capacity_reference=c['simai'],physical_gpu_count=len(gpus),
        server_count=len({n['server'] for n in gpus}),pod_count=len({n['pod'] for n in gpus}),
        switches=dict(switches),physical_links=len(t['links']),planes=len({n['plane'] for n in t['nodes'] if n['kind']!='gpu'}),
        aggregate_access_bps_per_gpu=sorted(set(nic.values())),tier_rates_bps={k:sorted(v) for k,v in rates.items()},
        model=s['model'],ep_groups=groups,source_requests=len(requests),source_ranks=len(counts),
        batch_cap_per_rank=s['workload']['batch_per_rank'],largest_actual_batch=max(counts.values()),
        source_layers=c['routes']['layer_ids'],decode_steps=s['workload']['decode_steps'],
        context_limit_tokens=s['workload']['context_tokens'],compute=s['compute'],
        recovery=p.get('recovery',{'mode':'native'}),data_loss_configuration=p.get('data_loss'),
        source=c['routes']['source'],frontend_calls=f.get('scheduler_calls') if f else None,outcome=outcome)


def write(directory):
    d=Path(directory);v=describe(d);save(d/'scenario.json',v)
    ep='；'.join(f"组{g['group']}: EP{g['ep']}，每rank每层{g['experts_per_rank_per_layer']}专家，{g['source_requests']}请求/{g['source_ranks']}源rank" for g in v['ep_groups'])
    links='；'.join(k+': '+','.join(f'{r/1e9:g}Gb/s' for r in rates) for k,rates in v['tier_rates_bps'].items())
    text=f"""# 本次实验条件与结果

- 硬件口径：模拟{v['gpu_label']}配置，{v['physical_gpu_count']}卡/{v['server_count']}服务器/{v['pod_count']}Pod；没有执行真实GPU或校准硬件时延。
- 部署：{ep}。
- 网络：{v['planes']}plane，{v['physical_links']}条边，交换机{v['switches']}；{links}。
- 工作量：{v['source_requests']}原件请求，batch上限{v['batch_cap_per_rank']}/rank，实际最大{v['largest_actual_batch']}；原层{v['source_layers']}，{v['decode_steps']}步Decode，初始KV已就绪。
- 计算：{v['compute']['source']}，不是完整R1数值执行或计算性能预测。
- 恢复：{json.dumps(v['recovery'],ensure_ascii=False)}。
- 周期性人工丢包：{json.dumps(v['data_loss_configuration'],ensure_ascii=False)}。
- 执行结果：{json.dumps(v['outcome'],ensure_ascii=False)}。

完整机器可读字段见scenario.json。窗口丢包率与全任务丢包率分别计算，不将人工故障称为拥塞自然产生的丢包。
"""
    (d/'scenario.md').write_text(text);return v
