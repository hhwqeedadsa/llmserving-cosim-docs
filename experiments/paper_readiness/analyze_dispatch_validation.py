"""Compare fixed counts, static link service and actual completion; never reads truth."""
import argparse
from collections import Counter,defaultdict,deque
import csv
from fractions import Fraction
from itertools import combinations
import json
from pathlib import Path

def load(path):return json.loads(path.read_text())
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def sign(value):return (value>0)-(value<0)
def metric_order(name,left,right):
    # At fixed 100G/25G the service times are exact multiples of 1/25 ns.
    if name.endswith('_ns'):return sign(round(left*25)-round(right*25))
    return sign(left-right)


def hash32(words,seed):
    mask=0xffffffff;h=seed
    def rot(value,bits):return ((value<<bits)|(value>>(32-bits)))&mask
    for word in words:
        value=word*0xcc9e2d51&mask;value=rot(value,15)*0x1b873593&mask
        h=rot(h^value,13);h=(h*5+0xe6546b64)&mask
    h^=12;h^=h>>16;h=h*0x85ebca6b&mask;h^=h>>13;h=h*0xc2b2ae35&mask
    return h^(h>>16)


def static_load(physical,work,seed):
    """Only topology/work/seed: data bytes + actual model header/ACK layout.

    No completion times, captures, or decoded expert selections are read here.
    Valid for this no-retransmission 1000B payload/ACK-every-packet probe only.
    """
    endpoints={};adjacency=defaultdict(list)
    for edge in physical['links']:
        for node,port,peer in ((edge['src'],edge['src_port'],edge['dst']),(edge['dst'],edge['dst_port'],edge['src'])):
            endpoints[node,port]=(peer,edge['rate_bps']);adjacency[node].append(peer)
    streams={s['id']:s for s in work['streams']};loads=defaultdict(Counter)
    for item in work['work']:
        stream=streams[item['stream']];src,dst=stream['src'],stream['dst'];wire=stream['wire_port']
        packets=(item['bytes']+999)//1000
        for source,target,model_bytes in ((src,dst,item['bytes']+36*packets),(dst,src,48*packets)):
            distances={target:0};pending=deque([target])
            while pending:
                node=pending.popleft()
                for peer in adjacency[node]:
                    if peer not in distances:
                        distances[peer]=distances[node]+1
                        if peer>=8:pending.append(peer)
            node=source;visited=set()
            while node!=target:
                assert node not in visited;visited.add(node)
                ports=[key for key,(peer,rate) in endpoints.items() if key[0]==node and distances[peer]==distances[node]-1]
                if node<8:assert len(ports)==1;key=ports[0]
                else:key=ports[hash32([0x0b000001+source*32,0x0b000001+target*32,wire|(wire<<16)],seed+node)%len(ports)]
                loads[item['id']//16][key]+=model_bytes;node=endpoints[key][0]
    return loads,endpoints


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('observed','run','analysis','output'):parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args();cfg=load(args.observed/'index.json')['config'];rounds=load(args.analysis/'rounds.json')
    summary=load(args.analysis/'summary.json');rows=[];projection_checks=0
    # Aggregate actual capture transmission rows solely to check the static projection.
    with (args.analysis/'packets.csv').open() as stream:packets=list(csv.DictReader(stream))
    for network,upper_rate in cfg['network_profiles'].items():
        physical=load(args.run/('topology-'+network)/'topology.json')
        for condition in cfg['placement_conditions']:
            work=load(args.observed/('work-'+condition+'.json'))
            prediction,endpoints=static_load(physical,work,cfg['network_seeds'][network])
            for step,load_by_port in prediction.items():
                case=network+'-'+condition;r=next(r for r in rounds if r['case']==case and r['step']==step)
                selected=[w for w in work['work'] if w['id']//16==step]
                stream_map={s['id']:s for s in work['streams']}
                incoming=Counter();outgoing=Counter()
                for item in selected:
                    s=stream_map[item['stream']];outgoing[s['src']]+=1;incoming[s['dst']]+=1
                actual=Counter()
                for packet in packets:
                    if packet['case']!=case or int(packet['step'])!=step:continue
                    path=list(map(int,packet['path'].split('>')))
                    for node,peer in zip(path,path[1:]):
                        key=next(key for key,(target,rate) in endpoints.items() if key[0]==node and target==peer)
                        actual[key]+=int(packet['model_bytes'])
                assert actual==load_by_port;projection_checks+=len(load_by_port)
                max_service=max(Fraction(value*8*10**9,endpoints[key][1]) for key,value in load_by_port.items())
                endpoint_service=max(Fraction(value*8*10**9,endpoints[key][1]) for key,value in load_by_port.items()
                    if key[0]<8 or endpoints[key][0]<8)
                assert float(max_service)==r['busiest_port']['serialization_lower_bound_ns']
                rows.append(dict(network=network,seed=cfg['network_seeds'][network],upper_rate_bps=upper_rate,
                    condition=condition,step=step,source_window=cfg['source_windows'][step],work_count=len(selected),
                    max_gpu_data_copies=max([*incoming.values(),*outgoing.values()]),
                    endpoint_service_ns=float(endpoint_service),static_max_port_service_ns=float(max_service),
                    ack_duration_ns=r['duration_ns'],accepted_duration_ns=r['accepted_ns']-r['ready_ns'],
                    static_projection_port_count=len(load_by_port)))
    by_network_step=defaultdict(list)
    for row in rows:by_network_step[row['network'],row['step']].append(row)
    metrics=('work_count','max_gpu_data_copies','endpoint_service_ns','static_max_port_service_ns')
    comparisons=[];counts={name:Counter() for name in metrics}
    for (network,step),values in by_network_step.items():
        for left,right in combinations(values,2):
            actual=sign(left['ack_duration_ns']-right['ack_duration_ns']);evaluation={}
            for metric in metrics:
                predicted=metric_order(metric,left[metric],right[metric])
                status='both_tie' if predicted==actual==0 else 'metric_tie' if predicted==0 else 'latency_tie' if actual==0 else 'agrees' if predicted==actual else 'reverses'
                counts[metric][status]+=1;evaluation[metric]=status
            comparisons.append(dict(network=network,step=step,left=left['condition'],right=right['condition'],
                left_ack_ns=left['ack_duration_ns'],right_ack_ns=right['ack_duration_ns'],metrics=evaluation))
    seed_sensitivity=[]
    for step in range(4):
        for rate in (100000000000,25000000000):
            for condition in cfg['placement_conditions']:
                values=[r for r in rows if r['step']==step and r['upper_rate_bps']==rate and r['condition']==condition]
                assert len(values)==3
                for metric in metrics[:-1]:assert len({r[metric] for r in values})==1
                low=min(r['ack_duration_ns'] for r in values);high=max(r['ack_duration_ns'] for r in values)
                seed_sensitivity.append(dict(step=step,upper_rate_bps=rate,condition=condition,
                    min_ack_ns=low,max_ack_ns=high,relative_spread=(high-low)/low,
                    observations={r['seed']:r['ack_duration_ns'] for r in values}))
    overall=[]
    overall_comparisons=[];overall_counts={name:Counter() for name in metrics}
    for network in cfg['network_profiles']:
        values=[v for v in summary['cases'] if v['network']==network]
        totals=[]
        for value in values:
            selected=[r for r in rows if r['network']==network and r['condition']==value['condition']]
            row=dict(condition=value['condition'],acked_ns=value['acked_ns'],
                **{key:sum(r[key] for r in selected) for key in metrics})
            assert sum(r['ack_duration_ns'] for r in selected)==value['acked_ns']
            totals.append(row)
        overall.append(dict(network=network,conditions=sorted(totals,key=lambda r:r['acked_ns'])))
        for left,right in combinations(totals,2):
            actual=sign(left['acked_ns']-right['acked_ns']);evaluation={}
            for metric in metrics:
                predicted=metric_order(metric,left[metric],right[metric])
                status='both_tie' if predicted==actual==0 else 'metric_tie' if predicted==0 else 'latency_tie' if actual==0 else 'agrees' if predicted==actual else 'reverses'
                overall_counts[metric][status]+=1;evaluation[metric]=status
            overall_comparisons.append(dict(network=network,left=left['condition'],right=right['condition'],
                left_ack_ns=left['acked_ns'],right_ack_ns=right['acked_ns'],metrics=evaluation))
    args.output.mkdir(exist_ok=False)
    save(args.output/'metrics.json',dict(rows=rows,pair_comparisons=comparisons,comparison_counts=counts,
        seed_sensitivity=seed_sensitivity,overall_rankings=overall,static_projection_checks=projection_checks,
        overall_comparisons=overall_comparisons,overall_comparison_counts=overall_counts,
        scope='post-run evaluation of fixed descriptive metrics; static metric uses only input; no prospective policy or independent-request claim'))
    print(json.dumps(dict(rows=len(rows),comparisons=len(comparisons),counts=counts),ensure_ascii=False))


if __name__=='__main__':main()
