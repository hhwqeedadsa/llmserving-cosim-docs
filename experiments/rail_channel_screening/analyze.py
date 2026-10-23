"""Evaluate native transport records; never reads original routes or truth/."""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path


def load(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def tx_ns(size,rate):return (2*size*8*10**9+rate)//(2*rate)


def analyze(obs,plans,network,out):
    assert not out.exists();out.mkdir()
    cfg=load(obs/'config.json');projection=load(plans/'projection.json');rows=[];port_rows=[]
    for rate in cfg['upper_rates']:
        for policy in cfg['policies']:
            name=f'{rate}-{policy}';r=load(network/name/'result.json');p=r['persistent_transport']
            capture=r['packet_capture'];columns=capture['columns'];codes=capture['event_codes']
            sizes=defaultdict(Counter)
            for values in capture['events']:
                e=dict(zip(columns,values))
                if e['event']==codes['tx']:sizes[e['node'],e['port']][e['model_bytes']]+=1
            services=[]
            for port in r['ports']:
                counts=sizes[port['node'],port['port']]
                busy=sum(n*tx_ns(size,port['rate_bps']) for size,n in counts.items())
                item=dict(condition=name,node=port['node'],port=port['port'],peer=port['peer'],
                    tier=port['tier'],rate_bps=port['rate_bps'],data_tx_bytes=port['data_tx_bytes'],
                    control_tx_bytes=port['control_tx_bytes'],model_tx_bytes=port['model_tx_bytes'],
                    serialized_service_ns=busy,frame_sizes={str(k):v for k,v in sorted(counts.items())})
                services.append(item);port_rows.append(item)
            bound=max(s['serialized_service_ns'] for s in services)
            bottlenecks=[s for s in services if s['serialized_service_ns']==bound]
            demand=next(c for c in projection['cases'] if c['policy']==policy)
            rows.append(dict(condition=name,policy=policy,upper_rate_bps=rate,
                writes=len(p['work']),source_data_packets=len(p['source_packets']),
                source_payload_bytes=sum(s['source_payload_bytes'] for s in p['streams']),
                qp_objects=len(p['streams']),active_qps=sum(s['source_data_packets']>0 for s in p['streams']),
                last_accepted_ns=max(w['accepted_ns'] for w in p['work']),
                last_acked_ns=max(w['acked_ns'] for w in p['work']),finish_ns=r['finish_ns'],
                max_directed_port_service_ns=bound,finish_minus_port_bound_ns=r['finish_ns']-bound,
                bottleneck_ports=[{k:b[k] for k in ('node','port','peer','tier','model_tx_bytes')} for b in bottlenecks],
                maximum_source_tokens=demand['maximum_source_tokens'],maximum_destination_tokens=demand['maximum_destination_tokens'],
                max_queue_bytes=max(p.get('queue_metrics',{}).get('peak_bytes',0) for p in r['ports']),
                pfc_pause_events=sum(p['pfc_pause'] for p in r['ports']),
                ecn_ce_port_observations=sum(p['ecn_ce_seen'] for p in r['ports']),
                packet_capture_events=len(capture['events']),physical_drained=p['physical_drained']))
    comparisons=[]
    for rate in cfg['upper_rates']:
        baseline=next(r for r in rows if r['upper_rate_bps']==rate and r['policy']=='identity')
        for policy in ('rotate','local_lpt'):
            alternative=next(r for r in rows if r['upper_rate_bps']==rate and r['policy']==policy)
            delta=baseline['finish_ns']-alternative['finish_ns']
            service_delta=baseline['max_directed_port_service_ns']-alternative['max_directed_port_service_ns']
            comparisons.append(dict(upper_rate_bps=rate,policy=policy,finish_reduction_ns=delta,
                finish_reduction_fraction=delta/baseline['finish_ns'],port_service_reduction_ns=service_delta,
                remaining_difference_ns=delta-service_delta,
                service_fraction_of_observed_difference=service_delta/delta if delta else None))
    save(out/'conditions.json',rows);save(out/'ports.json',port_rows);save(out/'comparisons.json',comparisons)
    save(out/'scope.json',dict(real_native_network=True,all_data_ready_microbenchmark=True,
        modern_end_to_end=False,hardware_calibrated=False,reorder_cost_included=False,
        logical_rail_count=1,planes=2,physical_nics_per_gpu=1,ports_per_nic=2,
        independent_requests=False,algorithm_novelty_claimed=False))
    print(json.dumps(comparisons))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('observed','plans','network','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();analyze(a.observed,a.plans,a.network,a.output)
