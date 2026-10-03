"""Real multi-request, multilayer groups with independent shared-network audits."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import yaml

from check_shared_bottleneck import audit_queue


def read(path):return json.loads(path.read_text())
def rows(path):return [json.loads(line) for line in path.read_text().splitlines()]
def hashes(path):return {str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob('*') if p.is_file()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[2]
    out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    binary=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    fixture=root/'fixtures/m8_composed_service';network=root/'fixtures/m3_shared_bottleneck/network'
    system=root/'fixtures/m1_compute/system.json'
    base,segments=read(fixture/'service.json'),read(fixture/'layers.json')
    commands,cases,failures=[],{},{}

    def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n');return path

    def invoke(label,argv,expected=0,stdin=None):
        argv=[str(v) for v in argv];start=datetime.now(timezone.utc).isoformat()
        proc=subprocess.run(argv,cwd=root,input=stdin,capture_output=True,text=True,timeout=120)
        for key in ('stdout','stderr'):(out/f'{label}.{key}.log').write_text(getattr(proc,key))
        if stdin is not None:(out/f'{label}.stdin.log').write_text(stdin)
        commands.append(dict(label=label,argv=argv,cwd=str(root),started_utc=start,
            ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=proc.returncode))
        save(out/'commands.json',commands)
        assert proc.returncode==expected,(label,proc.returncode,proc.stderr)
        return proc

    def command(label,config,layers,mode=True,net=network,scenario=False):
        cp=save(out/f'{label}.service.json',config);lp=save(out/f'{label}.layers.json',layers)
        obs=read(root/'fixtures/m3_shared_bottleneck/observation.json');obs['record_provenance']=mode
        if scenario:
            data=yaml.safe_load((root/'fixtures/m6_scenario_backend/reference.yaml').read_text())
            data.update(service=str(cp),layer=str(lp),system=str(system),scenario_id='m8-composed')
            data['backend']['reference_file']=str(root/'docs/scenarios/cm384_deepseek_r1_decode_v0.1.yaml')
            data['network']['case_path']=str(net)
            data['network']['defaults']['rate_bps']=400000000000
            data['network']['links']=[dict(src=8,src_port=4,dst=9,dst_port=4,rate_bps=10000000000,delay_ns=20)]
            data['execution'].update(chunk_bytes=256,send_slots_per_rank=2)
            data['observation']=obs
            path=out/f'{label}.yaml';path.write_text(yaml.safe_dump(data))
            return [sys.executable,'-m','moe_ub_cosim.scenario','run','--input',path,'--backend',binary,'--output-dir',out/label]
        argv=[sys.executable,'-m','moe_ub_cosim.service','--input',cp,'--layer',lp,'--backend',binary,
              '--system',system,'--network',net,'--output-dir',out/label]
        if mode is not None:argv+=['--observation',save(out/f'{label}.observation.json',obs)]
        return argv

    def run(label,config=base,layers=segments,mode=True,net=network,bandwidth=10,scenario=False):
        invoke(label,command(label,config,layers,mode,net,scenario))
        path=out/label/('run' if scenario else '')
        front,result=read(path/'service_result.json'),read(path/'execution/result.json')
        group_inputs={g['group_id']:g for g in config['groups']}
        templates={g['group_id']:g['layers'] for g in layers['groups']}
        ranks=sorted(r for g in templates.values() for r in g[0]['rank_ids'])
        requests={str(r['request_id']):r for r in config['requests']}
        assert read(path/'manifest.json')['normal_exit'] and result['normal_exit']
        assert result['network_initializations']==1 and result['network_drain']['idle']
        assert result['network_drain']['started_packets']==result['network_drain']['arrived_packets']
        assert all(b['same_network_objects'] for b in result['boundary_resources'])
        assert front['pending']==front['waiting']==front['running']==front['inflight']==[0]*len(ranks)
        assert all(r[k]==0 for r in result['rank_resources'] for k in
                   ('compute_slots','send_slots','pending_events','pending_tasks','stale_callbacks'))
        assert all(not r['request_blocks'] and r['kv_used_bytes']==0 for r in front['memory_final'])
        fabric={t['transfer_id']:t for t in result['transfers']};local={t['transfer_id']:t for t in result['local_deliveries']}
        consumed=Counter();expected_fabric=0;overlaps=[];work={g:[] for g in templates};prior={}
        endpoint_expected={};plans=[]
        for wave in front['waves']:
            p=read(path/f"graphs/{wave['graph_id']}/plan.json");plans.append(p);group=wave['group_id']
            assert p['schema_version']=='moe-ub-multilayer-plan/2'
            assert p['executed_layer_ids']==[t['layer_id'] for t in templates[group]]
            assert p['ready_ns']==wave['submitted_ns']>=prior.get(group,0);prior[group]=wave['finished_ns']
            selected={t['request_id'] for t in wave['tokens']}
            events={(e['rank_id'],e['node_id']):e for e in result['node_execution'] if e['graph_id']==p['graph_id']}
            assert wave['finished_ns']==max(e['time_ns'] for e in result['graph_completions'] if e['graph_id']==p['graph_id'])
            for request in wave['after']:
                if str(request['request_id']) in selected:assert request['latest_completion_ns']==wave['finished_ns']
            layer_work=[]
            for li,child in enumerate(p['layers']):
                c=child['input'];lid=c['layer_id'];template=templates[group][li]
                assert all(c[k]==v for k,v in template.items() if k not in ('graph_id','wave_id','ready_ns','tokens'))
                assert [t['request_id'] for t in c['tokens']]==[t['request_id'] for t in wave['tokens']]
                limits=group_inputs[group]['scheduling'];counts=Counter(t['src_rank'] for t in c['tokens'])
                assert max(counts.values())<=min(limits.values())
                chips={r['rank_id']:r['chip_id'] for r in c.get('deployment',{}).get('ranks',[])}
                matrix=Counter();experts=Counter();expected_sources={};records=[]
                for index,token in enumerate(c['tokens']):
                    source=requests[token['request_id']];step=token['token_index']-(source['prompt_tokens']+source['generated_tokens']-1)
                    assert token['expert_ids']==source['decode_routes'][step][str(lid)] and token['src_rank']==source['rank_id']
                    assert wave['submitted_ns']>=source['arrival_ns'] and step>=0
                    key=token['request_id'],token['token_index'],lid;consumed[key]+=1
                    assert consumed[key]==1
                    records.append(dict(request_id=token['request_id'],token_index=token['token_index'],layer_id=lid,
                        expert_ids=token['expert_ids'],source_decode_index=step))
                    for e in token['expert_ids']:experts[e]+=1
                    for dst in {c['expert_placement'][e] for e in token['expert_ids']}-{token['src_rank']}:
                        record=dict(request_id=token['request_id'],phase='decode',token_index=token['token_index'],
                            layer_id=lid,source_record_index=index,
                            expert_ids=sorted(e for e in token['expert_ids'] if c['expert_placement'][e]==dst))
                        for kind,a,b in [('dispatch',token['src_rank'],dst),('combine',dst,token['src_rank'])]:
                            matrix[kind,a,b]+=c['hidden_dim']*c[kind+'_element_bytes']
                            expected_sources.setdefault((kind,a,b),[]).append(record)
                assert child['input_provenance']['route_records']==records
                operations={op['operation_id'] for op in child['operations']}
                transfers=[t for t in p['transfers'] if t['operation_id'] in operations]
                actual=Counter()
                for t in transfers:
                    key=t['kind'],t['src_rank'],t['dst_rank'];actual[key]+=t['business_payload_bytes']
                    assert t['sources']==expected_sources[key]
                    assert t['payload_bytes']==t['business_payload_bytes']
                    if chips and chips[t['src_rank']]==chips[t['dst_rank']]:
                        x=local[t['transfer_id']];assert t['transfer_id'] not in fabric and 'ub_task_id' not in t
                        assert x['send_callbacks']==x['recv_callbacks']==1
                        assert events[t['src_rank'],t['send_node_id']]['end_ns']==x['send_callback_ns']
                        assert events[t['dst_rank'],t['recv_node_id']]['end_ns']==x['recv_callback_ns']
                        assert x['recv_callback_ns']==max(x['ready_ns'],x['recv_registered_ns'])
                    else:
                        assert t['payload_bytes']<=c['chunk_bytes']
                        x=fabric[t['transfer_id']];expected_fabric+=t['business_payload_bytes']
                        assert x['bytes']==x['committed_bytes']==t['payload_bytes']
                        assert x['send_callbacks']==x['receive_callbacks']==x['submitted_tasks']==1
                        assert x['data_ready_ns']<x['transaction_done_ns']==x['send_done_ns']
                        assert x['receive_done_ns']==max(x['data_ready_ns'],x['receive_start_ns'])
                        assert events[t['src_rank'],t['send_node_id']]['end_ns']==x['send_done_ns']
                        assert events[t['dst_rank'],t['recv_node_id']]['end_ns']==x['receive_done_ns']
                assert matrix==actual
                for key,total in matrix.items():
                    parts=[t for t in transfers if (t['kind'],t['src_rank'],t['dst_rank'])==key]
                    cursor=0
                    same_chip=chips and chips[key[1]]==chips[key[2]]
                    if same_chip:assert len(parts)==1
                    for ci,t in enumerate(parts):
                        size=total if same_chip else min(c['chunk_bytes'],total-cursor)
                        assert t['chunk_index']==ci and t['pair_byte_range']==[cursor,cursor+size];cursor+=size
                    assert cursor==total
                ends=[];starts=[]
                for graph in p['rank_graphs']:
                    rank=graph['rank_id'];phase=graph['layer_phases'][li];ph=phase['phase_nodes']
                    previous=graph['layer_phases'][li-1]['terminal_nodes'] if li else []
                    assert phase['entry_deps']==previous
                    work_nodes=[n for n in graph['nodes'] if n['layer_id']==lid]
                    for n in work_nodes:
                        e=events[rank,n['node_id']]
                        assert all(e['start_ns']>=events[rank,d]['end_ns'] for d in n['deps'])
                        if n['kind']=='COMP':assert e['end_ns']-e['start_ns']==n['duration_ns']
                    expert_count=sum(count for expert,count in experts.items() if c['expert_placement'][expert]==rank)
                    for key,coefficient,count in [('prepare','attention_route_pack',counts[rank]),('expert','expert_per_input',expert_count),
                        ('local_merge','expert_local_merge',bool(expert_count)),('finish','combine_unpack_merge',counts[rank])]:
                        if count:assert events[rank,ph[key]]['end_ns']-events[rank,ph[key]]['start_ns']==c['compute_profile_ns'][coefficient]*count
                        else:assert ph[key] is None
                    shape=next(s for s in child['compute_inputs']['ranks'] if s['rank_id']==rank)
                    assert shape['expert_inputs']==[dict(expert_id=e,input_tokens=experts[e]) for e,owner in enumerate(c['expert_placement']) if owner==rank]
                    assert shape['requests']==[dict(request_id=t['request_id'],token_index=t['token_index'],context_tokens=t['token_index']+1,query_tokens=1)
                        for t in c['tokens'] if t['src_rank']==rank]
                    ready=max([p['ready_ns']]+[events[rank,d]['end_ns'] for d in previous])
                    def end(key,fallback):return fallback if ph[key] is None else events[rank,ph[key]]['end_ns']
                    def comm(kind,fallback):return max([fallback]+[events[rank,t[side+'_node_id']]['end_ns'] for t in transfers
                        if t['kind']==kind for side,field in [('send','src_rank'),('recv','dst_rank')] if t[field]==rank])
                    prepared=end('prepare',ready);dispatched=comm('dispatch',prepared)
                    merged=end('local_merge',end('expert',dispatched));finished=end('finish',comm('combine',merged))
                    for op in child['operations']:endpoint_expected[op['operation_id'],rank]=(prepared,dispatched) if op['kind']=='dispatch' else (merged,finished)
                    ends.append(max([ready]+[events[rank,n['node_id']]['end_ns'] for n in work_nodes]))
                    if li+1<len(p['layers']):
                        next_nodes=[n for n in graph['nodes'] if n['layer_id']==p['executed_layer_ids'][li+1]]
                        if next_nodes:starts.append(min(events[rank,n['node_id']]['start_ns'] for n in next_nodes))
                if starts and min(starts)<max(ends):overlaps.append(dict(group=group,graph=p['graph_id'],layer=lid,next_start=min(starts),previous_end=max(ends)))
                layer_work.append({k:v for k,v in c.items() if k not in ('graph_id','wave_id','ready_ns')})
            work[group].append(layer_work)
            raw='\n'.join(line for line in (path/'execution/log/log.log').read_text().splitlines()
                          if line.endswith(', graph_id='+p['graph_id']))
            traces=re.findall(r'(issue|callback),sys->id=(\d+), tick=(\d+), node->id=(\d+),',raw)
            assert len(traces)==2*len(events) and len({(a,r,n) for a,r,t,n in traces})==len(traces)
            for action,rank,time,node in traces:
                assert events[int(rank),int(node)]['start_ns' if action=='issue' else 'end_ns']==int(time)
            for graph in p['rank_graphs']:
                intervals=sorted((events[graph['rank_id'],n['node_id']]['start_ns'],events[graph['rank_id'],n['node_id']]['end_ns'])
                    for n in graph['nodes'] if n['kind']=='COMP')
                assert all(a[1]<=b[0] for a,b in zip(intervals,intervals[1:]))
        expected={(identity,r['prompt_tokens']+r['generated_tokens']-1+step,int(lid))
            for identity,r in requests.items() for step,route in enumerate(r['decode_routes']) for lid in route}
        assert set(consumed)==expected and front['route_records_consumed']==front['route_records_expected']==len(expected)
        assert sum(t['bytes'] for t in fabric.values())==expected_fabric
        assert len(result['wqe_starts'])==len(result['wqe_completions'])==len(fabric)
        all_transfers=[t for p in plans for t in p['transfers']]
        for key in ('transfer_id','comm_tag'):assert len({t[key] for t in all_transfers})==len(all_transfers)
        for request in front['requests']:
            source=requests[str(request['request_id'])]
            assert request['status']=='FINISHED' and request['preemptions']==0 and request['num_tokens_reached']==request['target_tokens']
            assert len(request['intertoken_latencies_ns'])==len(source['decode_routes'])
        for name in ('admit','release'):
            assert Counter(str(e['request_id']) for e in front['memory_events'] if e['event']==name)==Counter(requests.keys())
        for event in front['memory_events']:
            for rank in event['ranks']:assert sum(rank['request_blocks'].values())+rank['free_blocks']==rank['kv_capacity_blocks']
        completed=[e for e in front['batch_events'] if e['event']=='completed']
        assert sum(e['generated_tokens'] for e in completed)==sum(len(r['decode_routes']) for r in requests.values())
        queue=None
        if mode is not None:
            if result['network_drain']['started_packets']:
                queue=audit_queue(path/'execution',bandwidth_gbps=bandwidth,
                    native_directory=path/'execution/runlog' if scenario else None)
            else:
                assert rows(path/'execution/egress-queue-audit.jsonl')==[]
                assert read(path/'execution/observation-audit.json')['all_successful_start_packets']==0
        if mode is True:
            truth=rows(path/'execution/truth/transfer_registry.jsonl')
            assert sum(t['business_payload_bytes'] for t in truth)==expected_fabric
            counters=rows(path/'execution/observed/port_counters.jsonl');byte_truth=rows(path/'execution/truth/port_byte_truth.jsonl')
            assert len(counters)==len(byte_truth)
            for observed,t in zip(counters,byte_truth):
                assert observed['counted_packet_bytes']==sum(t[k] for k in ('business_payload_bytes','application_metadata_bytes',
                    'data_protocol_overhead_bytes','control_packet_bytes','unattributed_bytes')) and t['unattributed_bytes']==0
            endpoints={(r['rank_id'],r['local_record_id']):r for r in rows(path/'execution/observed/endpoint_ops.jsonl')}
            for op in rows(path/'execution/truth/operation_registry.jsonl'):
                for ref in op['endpoint_records']:
                    endpoint=endpoints[ref['rank_id'],ref['local_record_id']]
                    assert (endpoint['start_local_ns'],endpoint['end_local_ns'])==endpoint_expected[op['operation_id'],ref['rank_id']]
        cases[label]=dict(waves=len(front['waves']),finished_ns=front['finished_ns'],writes=len(fabric),
            business_bytes=expected_fabric,local_deliveries=len(local),route_records=len(consumed),overlaps=overlaps,
            groups={g:[dict(submitted_ns=w['submitted_ns'],finished_ns=w['finished_ns'],
                members=[t['request_id'] for t in w['tokens']]) for w in front['waves'] if w['group_id']==g] for g in templates})
        return dict(front=front,result=result,queue=queue,work=work,path=path)

    overlap=run('overlap')
    assert cases['overlap']['writes']==124 and cases['overlap']['business_bytes']==31744
    assert cases['overlap']['route_records']==36 and cases['overlap']['overlaps']
    assert [w['members'] for w in cases['overlap']['groups']['ep0']]==[['201','202','204'],['201','203','204'],['201','203']]
    kept=[p for p in overlap['queue']['packets'] if p['enqueue_ns']<600<p['dequeue_ns']]
    assert kept
    for group in ('ep0','ep1'):
        c,l=deepcopy(base),deepcopy(segments);c['groups']=[g for g in c['groups'] if g['group_id']==group]
        l['groups']=[g for g in l['groups'] if g['group_id']==group]
        selected=l['groups'][0]['layers'][0]['rank_ids'];c['requests']=[r for r in c['requests'] if r['rank_id'] in selected]
        single=run(group,c,l)
        assert single['work'][group]==overlap['work'][group]
        assert cases['overlap']['groups'][group][0]['finished_ns']>cases[group]['groups'][group][0]['finished_ns']
        assert cases['overlap']['groups'][group][1]['submitted_ns']>cases[group]['groups'][group][1]['submitted_ns']
    for label,mode in [('source-off',False),('no-observation',None)]:
        other=run(label,mode=mode)
        assert other['front']==overlap['front'] and other['result']==overlap['result']
        assert hashes(other['path']/'execution/network/runlog')==hashes(overlap['path']/'execution/network/runlog')
        if mode is False:assert hashes(other['path']/'execution/observed')==hashes(overlap['path']/'execution/observed')
    c=deepcopy(base)
    for r in c['requests']:
        if r['request_id']>=400:r['arrival_ns']+=500000
    staggered=run('staggered',c)
    assert staggered['work']==overlap['work']
    assert cases['staggered']['groups']['ep1'][0]['submitted_ns']>cases['staggered']['groups']['ep0'][-1]['finished_ns']
    slow=out/'slow-network';shutil.copytree(network,slow)
    p=slow/'topology.csv';p.write_text(p.read_text().replace('10Gbps','1Gbps'))
    slower=run('slow',net=slow,bandwidth=1)
    assert slower['work']==overlap['work']
    for group in ('ep0','ep1'):assert cases['slow']['groups'][group][1]['submitted_ns']>cases['overlap']['groups'][group][1]['submitted_ns']
    c=deepcopy(base);c['groups'][0]['memory']['ranks'][0]['device_bytes']-=8192
    waited=run('kv-wait',c)
    assert [w['members'] for w in cases['kv-wait']['groups']['ep0']]==[['201','202','204'],['201','204'],['201'],['203'],['203']]
    assert any(e.get('reason')=='kv_capacity' for e in waited['front']['memory_events'])
    c,l=deepcopy(base),deepcopy(segments);c['groups'][1]['scheduling']['max_num_batched_tokens']=1
    for layer in l['groups'][1]['layers']:layer['batch_limits']['max_num_batched_tokens']=1
    limited=run('token-budget',c,l)
    assert cases['token-budget']['groups']['ep1'][0]['members']==['401','404']
    event=next(e for e in limited['front']['batch_events'] if e['event']=='scheduled' and e['rank_id']==20)
    assert event['resident_request_ids']==[401,402]
    c,l=deepcopy(base),deepcopy(segments);c['groups'][0]['scheduling']['max_num_seqs']=1
    for layer in l['groups'][0]['layers']:layer['batch_limits']['max_num_seqs']=1
    run('sequence-budget',c,l)
    assert [w['members'] for w in cases['sequence-budget']['groups']['ep0']]==[
        ['201','204'],['201','204'],['201'],['202'],['203'],['203']]
    c=deepcopy(base);c['requests']=[];run('empty',c)
    c=deepcopy(base);c['requests'][2]['arrival_ns']=500000;run('late',c)
    assert cases['late']['groups']['ep0'][-2]['submitted_ns']==500000
    c,l=deepcopy(base),deepcopy(segments)
    for g in l['groups']:g['layers']=g['layers'][1:2]
    for r in c['requests']:r['decode_routes']=[{'2':step['2']} for step in r['decode_routes']]
    run('one-layer',c,l)
    l=deepcopy(segments)
    for g in l['groups']:
        for layer in g['layers']:layer.pop('deployment')
    run('no-chip-declaration',layers=l)
    assert cases['no-chip-declaration']['business_bytes']>cases['overlap']['business_bytes']
    run('scenario',scenario=True)
    assert read(out/'scenario/resolution.json')['execution_scope']=='configured_groups_moe_layer_segment_continuous_batching'
    missing=out/'missing-channel-network';shutil.copytree(network,missing)
    p=missing/'transport_channel.csv'
    p.write_text('\n'.join(line for line in p.read_text().splitlines() if not line.startswith('0,0,1,1,0,0,'))+'\n')
    proc=invoke('reject-channel',command('reject-channel',base,segments,net=missing,scenario=True),1)
    assert 'Precreated channels must cover mapped endpoint pairs' in proc.stderr
    assert not (out/'reject-channel').exists();failures['channel']='missing intra-group channel'
    for label,mutate,fragment in [
        ('missing-route',lambda c,l:c['requests'][0]['decode_routes'][0].pop('2'),'per-layer route'),
        ('overlapping-endpoint',lambda c,l:l['groups'][1]['layers'][0]['rank_to_ub_node'].__setitem__(0,0),'Overlapping'),
        ('placement',lambda c,l:l['groups'][0]['layers'][1]['expert_placement'].__setitem__(0,2),'placement/Top-K'),
        ('buffer',lambda c,l:c['groups'][0]['memory']['ranks'][0].update(communication_buffer_bytes=20000),'layer-segment bound'),
        ('permanent-kv',lambda c,l:c['requests'][2].update(prompt_tokens=40),'final KV demand')]:
        c,l=deepcopy(base),deepcopy(segments);mutate(c,l)
        proc=invoke('reject-'+label,command('reject-'+label,c,l),1)
        assert fragment in proc.stderr and not (out/('reject-'+label)).exists();failures[label]=fragment
    commit=rows(out/'overlap/protocol.jsonl')[1]
    for label,fragment in [('boundary','composition mismatch'),('context','compute shapes'),('duplicate-tag','duplicate comm_tag'),
                           ('static-placement','Static layer configuration changed'),('layer-order','nonconsecutive layer order')]:
        shared=out/('bad-'+label);shared.mkdir()
        shutil.copytree(out/'overlap/bootstrap',shared/'bootstrap');shutil.copytree(out/'overlap/graphs/g0',shared/'graphs/g0')
        deployment=read(out/'overlap/deployment.json');p=read(shared/'graphs/g0/plan.json')
        if label=='boundary':p['rank_graphs'][0]['layer_phases'][1]['entry_deps']=[]
        if label=='context':p['layers'][1]['compute_inputs']['ranks'][0]['requests'][0]['context_tokens']+=1
        if label=='duplicate-tag':p['layers'][1]['transfers'][0]['comm_tag']=p['layers'][0]['transfers'][0]['comm_tag']
        if label=='static-placement':deployment['groups'][0]['layer_configs'][1]['expert_placement'][0]=2
        if label=='layer-order':p['layers'][1],p['layers'][2]=p['layers'][2],p['layers'][1]
        save(shared/'deployment.json',deployment);save(shared/'graphs/g0/plan.json',p)
        proc=invoke('reject-'+label,[binary,base['run_id'],shared,system,network,'-',shared/'execution',1000000],1,json.dumps(commit)+'\n')
        assert fragment.lower() in proc.stderr.lower(),(label,proc.stderr)
        failure=read(shared/'execution/failure.json');assert failure['now_ns']==0 and not failure['transfers']
        failures[label]=fragment
    sources=[p for folder in ('backend','python/moe_ub_cosim','fixtures/m8_composed_service') for p in (root/folder).glob('*') if p.is_file()]
    save(out/'summary.json',dict(passed=True,cases=cases,failures=failures,preserved_packets=kept,
        source_hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}))
    print('PASS: composed batches, static layer placements, per-group feedback, shared queues and passive observations')


if __name__=='__main__':main()
