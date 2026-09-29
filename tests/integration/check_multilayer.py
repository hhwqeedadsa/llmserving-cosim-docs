"""Actual continuous multilayer DAGs, per-Token sources and passive local observations."""
import argparse
from collections import Counter
from copy import deepcopy
import csv
import datetime
from decimal import Decimal
from fractions import Fraction
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys

from check_delay import read,sha
from check_variants import rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[2];out=args.output_dir.resolve()
    out.mkdir(parents=True,exist_ok=False);binary=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    base=read(root/'fixtures/m5_multilayer/run.json');source=read(root/base['inputs']['service'])
    commands=[];results={};failures={}
    def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
    def invoke(label,argv,stdin=None):
        argv=[str(v) for v in argv];start=datetime.datetime.now(datetime.timezone.utc).isoformat()
        r=subprocess.run(argv,cwd=root,input=stdin,capture_output=True,text=True,timeout=180)
        for stream in ('stdout','stderr'):(out/(label+'.'+stream+'.log')).write_text(getattr(r,stream))
        if stdin is not None:(out/(label+'.stdin.jsonl')).write_text(stdin)
        commands.append(dict(label=label,argv=argv,cwd=str(root),started_utc=start,
            finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),exit_code=r.returncode))
        save(out/'commands.json',commands);return r
    def file_config(label,c,records):
        file=out/(label+'.routes.jsonl');file.write_text(''.join(json.dumps(r)+'\n' for r in records))
        c['routes']['file']=dict(path=str(file),sha256=sha(file))
        file=out/(label+'.service.json');save(file,c)
        run=deepcopy(base);run['inputs']['service']=str(file);return run
    def research(label,c):
        file=out/(label+'.run.json');save(file,c)
        r=invoke(label,[sys.executable,'-m','moe_ub_cosim.research','run','--config',file,'--backend',binary,'--output-dir',out/label])
        assert r.returncode==0,(label,r.stderr)
        return audit(label,c)
    def hashes(path):return {str(p.relative_to(path)):sha(p) for p in path.rglob('*') if p.is_file()}
    def runtime(raw):
        return dict(report=read(raw/'execution/result.json'),front=read(raw/'service_result.json'),protocol=rows(raw/'protocol.jsonl'),
            graphs={str(p.relative_to(raw/'graphs')):sha(p) for p in (raw/'graphs').rglob('*')
                    if p.is_file() and p.suffix!='.pyc' and p.name!='compiler.json'},
            native=hashes(raw/'execution/network/runlog'),
            astra=re.sub(r'^\[\d{4}-\d{2}-\d{2} [^]]+\] ','',(raw/'execution/log/log.log').read_text(),flags=re.MULTILINE))
    def audit(label,c):
        path=out/label;raw=path/'logs/raw-run';value=runtime(raw);report=value['report'];front=value['front']
        manifest=read(path/'manifest.json');metrics=read(path/'metrics/validation.json');evidence=read(raw/'input_evidence.json')
        assert manifest['normal_exit'] and manifest['calibration_status']=='unvalidated_reference'
        assert manifest['compute_scope']=='consecutive_moe_layer_segment' and not manifest['full_model_execution_validated']
        assert 'not_full_model' in manifest['latency_scope'] and metrics['passed']
        assert front['service_finished'] and front['finished_ns']==report['service_finished_ns']
        assert front['waiting']==front['pending']==front['running']==front['inflight']==[0]*4
        assert report['network_initializations']==1 and report['network_drain']['idle'] and report['csv_completed_tasks']==0
        assert all(b['same_network_objects'] for b in report['boundary_resources'])
        assert report['active_wqes']==[0]*4 and all(r['finished'] and not r['remaining_nodes'] and
            r['pending_events']==r['pending_tasks']==r['compute_slots']==r['send_slots']==r['stale_callbacks']==0 for r in report['rank_resources'])
        libs={Path(p).resolve() for p in report['loaded_libraries'] if Path(p).name.startswith('libns3')}
        assert len(libs)==10 and all(p.parent==args.build_dir.resolve()/'output/lib' for p in libs)
        assert all(sha(path/p)==digest for p,digest in manifest['artifact_hashes'].items())
        normalized={(r['request_id'],r['token_index'],r['layer_id']):r for r in evidence['normalized_routes']}
        config=read(path/'inputs/service.json');request_by_id={str(r['request_id']):r for r in config['requests']}
        layer_ids=[p['layer_id'] for p in config['layer_profiles']]
        assert manifest['executed_layer_ids']==evidence['model']['executed_layer_ids']==layer_ids
        assert front['route_records_consumed']==front['route_records_expected']==len(normalized)
        reserved=25759850496+config['memory']['workspace_bytes']+config['memory']['communication_buffer_bytes']
        for event in front['memory_events']:
            for rank in event['ranks']:
                assert rank['used_bytes']==reserved+rank['kv_used_bytes']<=config['memory']['device_bytes']
        assert all(r['kv_used_bytes']==0 and r['used_bytes']==reserved for r in front['memory_final'])
        executions={(r['graph_id'],r['rank_id'],r['node_id']):r for r in report['node_execution']}
        transfers={t['transfer_id']:t for t in report['transfers']};delivered=Counter()
        for r in report['target_writes']:delivered[r['task']]+=r['bytes']
        consumed=set();plans=[];expected_endpoints={};overlaps=[];all_transfers=[]
        for wave in front['waves']:
            p=read(path/f"graphs/{wave['graph_id']}/plan.json");plans.append(p)
            assert p['schema_version']=='moe-ub-multilayer-plan/1' and p['executed_layer_ids']==layer_ids
            assert p['ready_ns']==wave['submitted_ns']
            events={(rank,node):e for (graph,rank,node),e in executions.items() if graph==p['graph_id']}
            assert wave['finished_ns']==max(e['end_ns'] for e in events.values())
            assert wave['finished_ns']==max(e['time_ns'] for e in report['graph_completions'] if e['graph_id']==p['graph_id'])
            for rank in p['rank_ids']:
                before=next(r for r in wave['before'] if r['rank_id']==rank) if label!='sparse' else None
                for state in wave['after']:
                    if any(t['request_id']==str(state['request_id']) for t in p['layers'][0]['input']['tokens']):
                        assert state['latest_completion_ns']==wave['finished_ns']
            for li,child in enumerate(p['layers']):
                lid=layer_ids[li];input=child['input'];tokens=input['tokens'];placement=input['expert_placement']
                assert input['layer_id']==lid and input['compute_profile_ns']==config['layer_profiles'][li]['compute_profile_ns']
                pairs=Counter();experts=Counter();local=Counter();expected_sources={}
                for ti,(token,record) in enumerate(zip(tokens,child['input_provenance']['route_records'])):
                    key=token['request_id'],token['token_index'],lid
                    assert key not in consumed and record==normalized[key];consumed.add(key)
                    assert record['source_token_index']==request_by_id[key[0]]['prompt_tokens']+key[1]
                    assert token['expert_ids']==record['expert_ids']
                    src=token['src_rank'];local[src]+=1
                    for expert in token['expert_ids']:experts[placement[expert]]+=1
                    for dst in set(placement[e] for e in token['expert_ids'])-{src}:
                        item=dict(request_id=token['request_id'],phase='decode',token_index=token['token_index'],
                            source_record_index=ti,layer_id=lid,expert_ids=sorted(e for e in token['expert_ids'] if placement[e]==dst))
                        for kind,a,b in (('dispatch',src,dst),('combine',dst,src)):
                            pairs[kind,a,b]+=input['hidden_dim']*input[kind+'_element_bytes']
                            expected_sources.setdefault((kind,a,b),[]).append(item)
                actual_pairs=Counter();op_ids={op['operation_id'] for op in child['operations']}
                layer_transfers=[t for t in p['transfers'] if t['operation_id'] in op_ids]
                for t in layer_transfers:
                    key=t['kind'],t['src_rank'],t['dst_rank'];actual_pairs[key]+=t['business_payload_bytes']
                    assert t['sources']==expected_sources[key]
                    x=transfers[t['transfer_id']]
                    assert x['committed_bytes']==x['bytes']==t['business_payload_bytes']==delivered[t['ub_task_id']]
                    assert x['submitted_tasks']==x['send_callbacks']==x['receive_callbacks']==1 and not x['context_active']
                    assert events[t['src_rank'],t['send_node_id']]['start_ns']==x['send_start_ns']
                    assert events[t['src_rank'],t['send_node_id']]['end_ns']==x['transaction_done_ns']
                    assert events[t['dst_rank'],t['recv_node_id']]['end_ns']==max(x['data_ready_ns'],x['receive_start_ns'])
                assert actual_pairs==pairs
                for kind,a,b in pairs:
                    parts=[t for t in layer_transfers if (t['kind'],t['src_rank'],t['dst_rank'])==(kind,a,b)];cursor=0
                    for index,t in enumerate(parts):
                        size=min(input['chunk_bytes'],pairs[kind,a,b]-cursor)
                        assert t['chunk_index']==index and t['pair_byte_range']==[cursor,cursor+size] and t['payload_bytes']==size
                        cursor+=size
                    assert cursor==pairs[kind,a,b]
                ends=[];starts=[]
                for graph in p['rank_graphs']:
                    rank=graph['rank_id'];phase=graph['layer_phases'][li];nodes=[n for n in graph['nodes'] if n['layer_id']==lid]
                    previous=graph['layer_phases'][li-1]['terminal_nodes'] if li else []
                    assert phase['entry_deps']==previous
                    internal={n['node_id'] for n in nodes}
                    for n in nodes:
                        event=events[rank,n['node_id']]
                        assert all(event['start_ns']>=events[rank,d]['end_ns'] for d in n['deps'])
                        if not (set(n['deps']) & internal):assert n['deps']==previous
                        if n['kind']=='COMP':assert event['end_ns']-event['start_ns']==n['duration_ns']
                    profile=input['compute_profile_ns'];ph=phase['phase_nodes']
                    for field,key,count in (('prepare','attention_route_pack',local[rank]),('expert','expert_per_input',experts[rank]),
                        ('local_merge','expert_local_merge',bool(experts[rank])),('finish','combine_unpack_merge',local[rank])):
                        if count:
                            assert events[rank,ph[field]]['end_ns']-events[rank,ph[field]]['start_ns']==profile[key]*count
                        else:assert ph[field] is None
                    assert ph['next_layer'] is None and all(n['name']!='next-layer' for n in nodes)
                    entry=max([p['ready_ns']]+[events[rank,d]['end_ns'] for d in previous])
                    def end(name,fallback):return fallback if ph[name] is None else events[rank,ph[name]]['end_ns']
                    def comm(kind,fallback):return max([fallback]+[events[rank,t[side+'_node_id']]['end_ns']
                        for t in layer_transfers if t['kind']==kind for side,field in (('send','src_rank'),('recv','dst_rank')) if t[field]==rank])
                    prepared=end('prepare',entry);dispatched=comm('dispatch',prepared)
                    merged=end('local_merge',end('expert',dispatched));finished=end('finish',comm('combine',merged))
                    for op in child['operations']:
                        expected_endpoints[op['operation_id'],rank]=(prepared,dispatched) if op['kind']=='dispatch' else (merged,finished)
                    ends.append(max([entry]+[events[rank,n['node_id']]['end_ns'] for n in nodes]))
                    if li+1<len(layer_ids):
                        next_nodes=[n for n in graph['nodes'] if n['layer_id']==layer_ids[li+1]]
                        if next_nodes:starts.append(min(events[rank,n['node_id']]['start_ns'] for n in next_nodes))
                if starts and min(starts)<max(ends):overlaps.append(dict(graph=p['graph_id'],layer=lid,next_local_start=min(starts),previous_last_end=max(ends)))
            all_transfers+=p['transfers']
        assert consumed==set(normalized)
        assert len(all_transfers)==len(transfers)==metrics['transfers']
        assert sum(t['payload_bytes'] for t in all_transfers)==metrics['unique_delivery_bytes']==sum(delivered.values())
        for field in ('transfer_id','ub_task_id','comm_tag'):assert len({t[field] for t in all_transfers})==len(all_transfers)
        for request in front['requests']:
            source_request=request_by_id[str(request['request_id'])]
            assert request['completed_segment_steps']==len(request['intertoken_latencies_ns'])==source_request['decode_steps']
            assert request['next_route_decode_index']==source_request['generated_tokens']-1+source_request['decode_steps']
        for a,b in zip(front['waves'],front['waves'][1:]):assert b['submitted_ns']>=a['finished_ns']
        clock={(x['device_type'],x['device_id']):x for x in c['observation']['clocks']}
        def local_time(t,device):return math.floor(Fraction(10**9+device['drift_ppb'],10**9)*t+device['offset_ns'])
        endpoint={(r['rank_id'],r['local_record_id']):r for r in rows(path/'observed/endpoint_ops.jsonl')}
        if c['observation']['record_provenance']:
            bound=set()
            for op in rows(path/'truth/operation_registry.jsonl'):
                for ref in op['endpoint_records']:
                    key=ref['rank_id'],ref['local_record_id'];assert key not in bound;bound.add(key)
                    begin,end=expected_endpoints[op['operation_id'],ref['rank_id']]
                    assert (begin,end)==(ref['start_global_ns'],ref['end_global_ns'])
                    assert endpoint[key]==dict(rank_id=key[0],local_record_id=key[1],kind=op['kind'],
                        start_local_ns=local_time(begin,clock['rank',key[0]]),end_local_ns=local_time(end,clock['rank',key[0]]))
            assert bound==set(endpoint) and metrics['attribution_status']=='checked'
        else:assert metrics['attribution_status']=='disabled'
        with (path/'observed/port_windows.csv').open() as f:windows=list(csv.DictReader(f))
        with (path/'truth/attribution_windows.csv').open() as f:attribution=list(csv.DictReader(f))
        expected=Counter()
        for port in read(path/'observed/public_config.json')['ports']:
            s,q=port['switch_id'],port['port_id'];d=raw/f'execution/ports/{s}-{q}'
            events=rows(d/'packet-events.jsonl');trace=raw/f'execution/network/runlog/PortTrace_node_{s}_port_{q}.tr'
            if trace.exists():
                native=[(int(Decimal(t)*1000),int(n)) for t,n in re.findall(r'\[([\d.]+)us\] Port Tx, port ID: \d+ PacketSize: (\d+)',trace.read_text())]
                assert native==[(e['sim_time_ns'],e['counted_packet_bytes']) for e in events]
            else:assert not events and read(d/'observation-audit.json')['native_tx_bytes_delta']==0
            parts=rows(d/'truth/packet_ranges.jsonl') if c['observation']['record_provenance'] else []
            for w in (w for w in windows if int(w['switch_id'])==s and int(w['port_id'])==q):
                a,b=int(w['local_window_start_ns']),int(w['local_window_end_ns'])
                included=[e for e in events if a<=local_time(e['sim_time_ns'],clock['switch',s])<b]
                assert (int(w['counted_packets']),int(w['counted_packet_bytes']))==(len(included),sum(e['counted_packet_bytes'] for e in included))
                for part in parts:
                    if a<=local_time(part['sim_time_ns'],clock['switch',s])<b:
                        expected[part['operation_id'],s,q,a,b]+=part['task_byte_range'][1]-part['task_byte_range'][0]
        got=Counter({(r['operation_id'],int(r['switch_id']),int(r['port_id']),int(r['local_window_start_ns']),int(r['local_window_end_ns'])):
                     int(r['business_payload_bytes']) for r in attribution})
        assert len(got)==len(attribution) and got==expected
        value.update(plans=plans,metrics=metrics,overlaps=overlaps,observed=hashes(path/'observed'),consumed=len(consumed))
        results[label]=value;print(f'PASS {label}: {len(layer_ids)} layers, {len(plans)} waves, {len(all_transfers)} writes, {front["finished_ns"]} ns',flush=True)
        return value
    normal=research('on',base);assert normal['overlaps']
    research('repeat',base)
    c=deepcopy(base);c['observation']['record_provenance']=False;research('off',c)
    c=deepcopy(base)
    for clock in c['observation']['clocks']:clock.update(offset_ns=0,drift_ppb=0)
    for switch in c['observation']['switches']:switch['start_local_ns']=123
    research('zero',c)
    for label in ('repeat','off','zero'):
        for key in ('report','front','protocol','graphs','native','astra'):assert results[label][key]==normal[key],(label,key)
    assert normal['observed']==results['repeat']['observed']==results['off']['observed']!=results['zero']['observed']
    raw=out/'on/logs/raw-run'
    r=invoke('absent',[sys.executable,'-m','moe_ub_cosim.service','--input',out/'on/inputs/service.json',
        '--layer',out/'on/inputs/layer.json','--backend',binary,'--system',out/'on/inputs/system.json',
        '--network',out/'on/inputs/network','--max-simulation-ns',str(base['max_simulation_ns']),'--output-dir',out/'absent'])
    assert r.returncode==0,r.stderr
    for key,value in runtime(out/'absent').items():assert value==normal[key],('absent',key)
    network=out/'slow-network';shutil.copytree(root/base['inputs']['network'],network)
    file=network/'topology.csv';file.write_text(file.read_text().replace('1,0,4,1,400Gbps,20ns','1,0,4,1,400Gbps,2020ns'))
    c=deepcopy(base);c['inputs']['network']=str(network);slow=research('slow',c)
    assert slow['front']['finished_ns']>normal['front']['finished_ns'] and slow['front']['waves'][1]['submitted_ns']>normal['front']['waves'][1]['submitted_ns']
    for a,b in zip(normal['plans'],slow['plans']):assert a['transfers']==b['transfers']
    old={(r['graph_id'],r['rank_id'],r['node_id']):r for r in normal['report']['node_execution']}
    assert any(r['start_ns']>old[r['graph_id'],r['rank_id'],r['node_id']]['start_ns'] for r in slow['report']['node_execution'] if r['kind']=='COMP')
    records=rows(root/source['routes']['file']['path'])
    c=deepcopy(source);c['requests']=c['requests'][:2];c['requests'][1]['arrival_ns']=1000
    sparse=file_config('sparse',c,[r for r in records if r['request_id'] in ('701','702')]);research('sparse',sparse)
    c=deepcopy(base);layer=read(root/base['inputs']['layer']);layer['chunk_bytes']=3072
    file=out/'chunks.layer.json';save(file,layer);c['inputs']['layer']=str(file);chunked=research('chunks',c)
    assert chunked['metrics']['unique_delivery_bytes']==normal['metrics']['unique_delivery_bytes']
    assert chunked['metrics']['transfers']==3*normal['metrics']['transfers']
    c=deepcopy(source);c['layer_profiles']=[dict(layer_id=i,compute_profile_ns=deepcopy(source['layer_profiles'][i%3]['compute_profile_ns'])) for i in range(32)]
    c['memory']['communication_buffer_bytes']=8*2**20
    for r in c['requests']:r['decode_steps']=2
    extended=[]
    for step in range(2):
        for lid in range(32):
            for request in c['requests']:
                original=next(r for r in records if r['request_id']==str(request['request_id']) and r['layer_id']==6+lid%3 and
                    r['source_token_index']==request['prompt_tokens']+request['generated_tokens']-1+step)
                row=deepcopy(original);row.update(layer_id=lid,source_index=9000+len(extended));extended.append(row)
    run=file_config('layers32',c,extended);layer=read(root/base['inputs']['layer']);layer['layer_id']=0
    for token in layer['tokens']:token['layer_id']=0
    file=out/'layers32.layer.json';save(file,layer);run['inputs']['layer']=str(file);research('layers32',run)
    # CLI input failures never silently fill absent layers or route choices.
    for name in ('missing-route','duplicate-route','wrong-layer','layer-order','next-placeholder'):
        c=deepcopy(source);rr=deepcopy(records)
        if name=='missing-route':rr.pop(5)
        elif name=='duplicate-route':rr.append(deepcopy(rr[5]));rr[-1]['source_index']=999999
        elif name=='wrong-layer':rr[5]['layer_id']=10
        elif name=='layer-order':c['layer_profiles'].reverse()
        else:c['layer_profiles'][1]['compute_profile_ns']['next_layer']=1
        run=file_config('bad-'+name,c,rr);file=out/('bad-'+name+'.run.json');save(file,run)
        r=invoke('bad-'+name,[sys.executable,'-m','moe_ub_cosim.research','run','--config',file,'--backend',binary,'--output-dir',out/('bad-'+name)])
        assert r.returncode!=0 and not (out/('bad-'+name)/'logs/raw-run/execution').exists()
        failures[name]=dict(exit_code=r.returncode,error=r.stderr.splitlines()[-1])
    commits=[m for m in normal['protocol'] if m['type']=='COMMIT']
    for name in ('boundary-dependency','cross-layer-id','layer-order','last-rank','time-limit'):
        shared=out/('bad-backend-'+name+'-input');shutil.copytree(raw/'bootstrap',shared/'bootstrap')
        shutil.copytree(raw/'graphs',shared/'graphs');shutil.copyfile(raw/'deployment.json',shared/'deployment.json')
        file=shared/'graphs/g0/plan.json';p=read(file)
        if name=='boundary-dependency':
            node=next(n for n in p['rank_graphs'][3]['nodes'] if n['layer_id']==7);node['deps']=[]
        elif name=='cross-layer-id':p['layers'][1]['transfers'][0]['comm_tag']=p['layers'][0]['transfers'][0]['comm_tag']
        elif name=='layer-order':p['layers'].reverse()
        elif name=='last-rank':(shared/'graphs/g0/layer.3.et').write_bytes(b'\x80')
        save(file,p)
        r=invoke('bad-backend-'+name,[binary,base['run_id'],shared,root/base['inputs']['system'],root/base['inputs']['network'],
            '-',out/('bad-backend-'+name),'10000' if name=='time-limit' else '10000000'],json.dumps(commits[0])+'\n')
        assert r.returncode==1 and not (out/('bad-backend-'+name)/'result.json').exists(),(name,r.stderr)
        failure=read(out/('bad-backend-'+name)/'failure.json')
        if name!='time-limit':
            assert failure['now_ns']==0 and 'graph_id=g0' not in (out/('bad-backend-'+name)/'log/log.log').read_text()
        else:assert failure['now_ns']==10000 and failure['transfers']
        failures['backend-'+name]=dict(exit_code=r.returncode,error=failure['error'],now_ns=failure['now_ns'])
    r=invoke('validate-run',[sys.executable,'-m','moe_ub_cosim.research','validate-run','--run-dir',out/'on']);assert r.returncode==0,r.stderr
    files=[p for folder in ('backend','python/moe_ub_cosim','fixtures/m5_multilayer') for p in (root/folder).iterdir() if p.is_file() and p.suffix!='.md']
    summary=dict(passed=True,research_runs=len(results),unobserved_runs=1,failures=failures,
        metrics={name:dict(finished_ns=r['front']['finished_ns'],layers=r['plans'][0]['executed_layer_ids'],waves=len(r['plans']),
            route_records=r['consumed'],writes=r['metrics']['transfers'],unique_bytes=r['metrics']['unique_delivery_bytes'],
            endpoint_records=r['metrics']['endpoint_records'],windows=r['metrics']['port_windows'],
            local_layer_overlap_count=len(r['overlaps'])) for name,r in results.items()},
        overlap_examples=normal['overlaps'],source_hashes={str(p.relative_to(root)):sha(p) for p in files+[Path(__file__).resolve()]})
    save(out/'summary.json',summary);print(json.dumps({k:v for k,v in summary.items() if k!='source_hashes'},indent=2))


if __name__=='__main__':main()
