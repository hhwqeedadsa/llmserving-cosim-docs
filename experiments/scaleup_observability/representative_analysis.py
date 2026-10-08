"""Streaming full-run summaries without loading millions of nodes or transfers."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

from moe_ub_cosim.record_storage import iter_result
from moe_ub_cosim.research_storage import stored_rows

from .common import digest, read, require, save


def histogram_summary(hist):
    count=sum(hist.values());require(count>0 and all(n>0 for n in hist.values()),'Nonempty exact histogram required')
    ordered=sorted(hist.items());seen=0;result=dict(count=count,min=ordered[0][0],max=ordered[-1][0],
        mean=sum(value*n for value,n in ordered)/count)
    targets={name:(count*num+den-1)//den for name,num,den in [('median',1,2),('p95',95,100),('p99',99,100)]}
    for value,n in ordered:
        seen+=n
        for name,target in targets.items():
            if name not in result and seen>=target:result[name]=value
    return result


def locality_groups(network,rank_map):
    kinds={n['node_id']:n['kind'] for n in network['topology']['nodes']};access=defaultdict(set)
    for l in network['topology']['links']:
        for a,b in [('src','dst'),('dst','src')]:
            if kinds[l[a]]=='DEVICE':access[l[a]].add(l[b])
    require(all(access[n] for n in rank_map),'Rank has no access switch')
    return [frozenset(access[n]) for n in rank_map]


def fully_inside(row, window):
    return window[0]<=row['local_window_start_ns'] and row['local_window_end_ns']<=window[1]


def summarize(run, output, ports, window):
    run,output=Path(run).resolve(),Path(output).resolve()
    require(not output.exists(),'Execution summary output exists');output.mkdir(parents=True)
    manifest=read(run/'manifest.json');ex=run/'raw/execution';report=read(ex/'result.json')
    require(manifest['normal_exit'] and manifest['coverage']['execution_complete'] and
            manifest['coverage']['observation_complete'] and report['normal_exit'] and report['network_drain']['idle'],
            'Complete execution and observation required')
    network=read(run/'inputs/network/backend.json');access=locality_groups(network,report['rank_to_ub_node'])
    sizes={k:v for k,v in network['attributes'].items() if k.endswith(('StoreRequestSize','LoadResponseSize','LoadRequestSize'))}
    grains={op:int(sizes['ns3::UbLdstThread::'+key]) for op,key in [('LOAD','LoadResponseSize'),('STORE','StoreRequestSize')]}
    hist={};counts=Counter();segments=Counter();distinct=0;transfer_count=0;total_payload=0
    for r in iter_result(ex,'transfers',report):
        require(r['committed_bytes']==r['bytes'] and not r['context_active'] and r['initiator_resources_retired'] and
                r['submitted_tasks']==r['send_callbacks']==r['receive_callbacks']==1,'Transfer work or lifecycle mismatch')
        require(r['send_start_ns']<=r['data_ready_ns']<=r['transaction_done_ns']<=r['send_done_ns'] and
                r['receive_done_ns']>=r['data_ready_ns'],'Transfer event order differs')
        require(r['committed_segments']==(r['bytes']+grains[r['operation']]-1)//grains[r['operation']],'Actual segmentation differs')
        locality='shared_access_switch' if access[r['data_owner_rank']] & access[r['consumer_rank']] else 'different_access_switches'
        key=(r['operation'],r['bytes'],locality);counts[key]+=1;segments[key]+=r['committed_segments']
        values=dict(data_latency_ns=r['data_ready_ns']-r['send_start_ns'],
            task_latency_ns=r['transaction_done_ns']-r['send_start_ns'],
            data_to_protocol_complete_ns=r['transaction_done_ns']-r['data_ready_ns'])
        for name,value in values.items():
            h=hist.setdefault((*key,name),Counter())
            if value not in h:
                distinct+=1;require(distinct<=1000000,'Exact histogram exceeds declared one-million-entry bound')
            h[value]+=1
        transfer_count+=1;total_payload+=r['bytes']
    fields=report['record_storage']['fields']
    require(transfer_count==fields['transfers']['records']==fields['native_starts']['records']==fields['native_completions']['records'],
            'Native task counts differ')
    groups=[]
    for key,count in sorted(counts.items()):
        groups.append(dict(operation=key[0],payload_bytes=key[1],locality=key[2],tasks=count,
            committed_segments=segments[key],**{name:histogram_summary(hist[(*key,name)]) for name in
                ('data_latency_ns','task_latency_ns','data_to_protocol_complete_ns')}))
    compute=nodes=0;rank_end={r:0 for r in report['rank_ids']};rank_compute=Counter()
    for r in iter_result(ex,'node_execution',report):
        require(0<=r['start_ns']<=r['end_ns']<=report['service_finished_ns'],'Node outside execution')
        rank_end[r['rank_id']]=max(rank_end[r['rank_id']],r['end_ns']);nodes+=1
        if r['kind']=='COMP':compute+=r['end_ns']-r['start_ns'];rank_compute[r['rank_id']]+=r['end_ns']-r['start_ns']
    require(max(rank_end.values())==report['service_finished_ns'] and nodes==fields['node_execution']['records'],'Rank/node completion mismatch')
    observed={(n,p):dict(node_id=n,port_id=p,role=role,packets=0,packet_bytes=0,
        inner_window_packets=0,inner_window_packet_bytes=0,inner_window_business_bytes=0,
        voq_byte_ns=0,fifo_byte_ns=0,voq_peak_bytes=0,fifo_peak_bytes=0) for role,(n,p,_) in ports.items()}
    public=read(run/'observed/public_config.json')
    public_ports={(p['switch_id'],p['port_id']) for p in public['ports']}
    require(set(observed)<=public_ports,'Declared observed ports missing')
    for r in stored_rows(run/'observed','port_windows.csv'):
        key=r['switch_id'],r['port_id']
        if key not in observed:continue
        x=observed[key];x['packets']+=r['counted_packets'];x['packet_bytes']+=r['counted_packet_bytes']
        if fully_inside(r,window):x['inner_window_packets']+=r['counted_packets'];x['inner_window_packet_bytes']+=r['counted_packet_bytes']
    for r in stored_rows(run/'observed','queue_windows.csv'):
        key=r['switch_id'],r['port_id']
        if key not in observed:continue
        x=observed[key]
        for q in ['voq','fifo']:
            x[q+'_byte_ns']+=r[q+'_waiting_byte_ns'];x[q+'_peak_bytes']=max(x[q+'_peak_bytes'],r[q+'_waiting_peak_bytes'])
    for r in stored_rows(run/'truth','attribution_windows.csv'):
        key=r['switch_id'],r['port_id']
        if key in observed and fully_inside(r,window):observed[key]['inner_window_business_bytes']+=r['business_payload_bytes']
    semantic={k:network[k] for k in ('attributes','globals','profiles','topology')}
    result=dict(schema_version='scaleup-stream-execution-summary/1',access='researcher_internal',
        manifest_sha256=digest(run/'manifest.json'),round_ns=report['service_finished_ns'],
        simulation_end_ns=report['simulation_end_ns'],transfers=transfer_count,
        local_deliveries=fields['local_deliveries']['records'],nodes=nodes,compute_work_rank_ns=compute,
        rank_completion_ns=rank_end,rank_compute_ns=dict(rank_compute),task_payload_bytes=total_payload,
        actual_native_sizes=sizes,group_counts=[dict(operation=k[0],payload_bytes=k[1],locality=k[2],tasks=n,segments=segments[k]) for k,n in sorted(counts.items())],
        groups=groups,ports=list(observed.values()),observed_queue_area_byte_ns=sum(r['voq_byte_ns']+r['fifo_byte_ns'] for r in observed.values()),
        observation_window_ns=list(window),exact_histogram_entries=distinct,
        effective_service=network['port_service'],network_semantics_sha256=hashlib.sha256(json.dumps(semantic,sort_keys=True).encode()).hexdigest(),
        qualification='Aggregate port windows: wholly contained windows give an interior hit count; boundary windows omitted. No detailed nonempty-idle or credit history.')
    save(output/'summary.json',result)
    return result


def analyze(study,case_id,output):
    study=Path(study).resolve();meta=read(study/'study.json');run=study/'runs'/case_id/'run'
    manifest=read(run/'manifest.json')
    require(manifest['source_slice_sha256']==meta['source_slice_sha256'] and manifest['backend_executed'] and
            not manifest['frontend_executed'],'Need backend execution of fixed source')
    scope=meta['source_scope'];reference=read(Path(meta['source'])/'slice.json')['simulation_end_ns']
    result=summarize(run,output,meta['observed_ports'],(reference//4,3*reference//4))
    usage=scope['expected_usage']
    require(result['transfers']==usage['fabric_transfers'] and result['local_deliveries']==usage['local_transfers'] and
            result['nodes']==usage['nodes'] and len(result['rank_completion_ns'])==128,'Full executed work differs')
    case=next(c for c in meta['cases'] if c['case_id']==case_id)
    require(result['effective_service']['intervals']==case['port_service'],'Full service differs from frozen case')
    print('Complete fixed-source representative execution summarized',flush=True)


if __name__=='__main__':analyze(*sys.argv[1:])
