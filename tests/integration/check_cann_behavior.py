"""Independent CANN contribution/layout/dependency audits on one real UB network."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import yaml

from check_shared_bottleneck import audit_queue
from moe_ub_cosim.observation import backend_observation
from moe_ub_cosim.research_export import export_records, write_export


def read(p):return json.loads(p.read_text())
def rows(p):return [json.loads(s) for s in p.read_text().splitlines()]
def hashes(p):return {str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest() for f in p.rglob('*') if f.is_file()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[2];out=args.output_dir.resolve()
    out.mkdir(parents=True,exist_ok=False)
    binary=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    fixture=root/'fixtures/m10_cann';network=root/'fixtures/m3_shared_bottleneck/network'
    system=root/'fixtures/m1_compute/system.json';base=read(fixture/'service.json');layers=read(fixture/'layers.json')
    commands=[];cases={};failures={};max_ns=2000000

    def save(p,value):p.write_text(json.dumps(value,indent=2)+'\n');return p

    def invoke(label,argv,expected=0,stdin=None):
        argv=[str(a) for a in argv];start=datetime.now(timezone.utc).isoformat()
        p=subprocess.run(argv,cwd=root,input=stdin,capture_output=True,text=True,timeout=120)
        for stream in ('stdout','stderr'):(out/f'{label}.{stream}.log').write_text(getattr(p,stream))
        if stdin is not None:(out/f'{label}.stdin.log').write_text(stdin)
        commands.append(dict(label=label,argv=argv,cwd=str(root),started_utc=start,
            ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=p.returncode))
        save(out/'commands.json',commands)
        assert p.returncode==expected,(label,p.returncode,p.stderr)
        return p

    def observation(l,mode=True,period=1000):
        ranks=sorted(r for g in l['groups'] for r in g['layers'][0]['rank_ids'])
        return dict(clocks=[dict(device_type='rank',device_id=r,offset_ns=0,drift_ppb=0) for r in ranks]+
            [dict(device_type='switch',device_id=8,offset_ns=0,drift_ppb=0)],
            switches=[dict(switch_id=8,port_ids=[4],start_local_ns=0,end_local_ns=max_ns,period_ns=period)],
            record_provenance=mode),ranks

    def command(label,c,l,mode=True,net=network,scenario=False):
        cp=save(out/f'{label}.service.json',c);lp=save(out/f'{label}.layers.json',l)
        obs,ranks=observation(l,mode);raw_obs=backend_observation(obs,ranks,max_ns) if mode is not None else None
        if scenario:
            d=yaml.safe_load((root/'fixtures/m6_scenario_backend/reference.yaml').read_text())
            d.update(service=str(cp),layer=str(lp),system=str(system),scenario_id='m10-cann')
            d['backend']['reference_file']=str(root/'docs/scenarios/cm384_deepseek_r1_decode_v0.1.yaml')
            d['network']['case_path']=str(net);d['network']['defaults']['rate_bps']=400000000000
            d['network']['links']=[dict(src=8,src_port=4,dst=9,dst_port=4,rate_bps=10000000000,delay_ns=20)]
            d['execution'].update(chunk_bytes=1024,send_slots_per_rank=2)
            d['max_simulation_ns']=max_ns
            d['observation']=raw_obs
            path=out/f'{label}.yaml';path.write_text(yaml.safe_dump(d))
            return [sys.executable,'-m','moe_ub_cosim.scenario','run','--input',path,'--backend',binary,'--output-dir',out/label]
        argv=[sys.executable,'-m','moe_ub_cosim.service','--input',cp,'--layer',lp,'--backend',binary,
              '--system',system,'--network',net,'--output-dir',out/label,'--max-simulation-ns',max_ns]
        if mode is not None:argv+=['--observation',save(out/f'{label}.observation.json',raw_obs)]
        return argv

    def run(label,c=base,l=layers,mode=True,net=network,bandwidth=10,scenario=False):
        invoke(label,command(label,c,l,mode,net,scenario))
        path=out/label/('run' if scenario else '');front=read(path/'service_result.json');result=read(path/'execution/result.json')
        assert front['service_finished'] and result['normal_exit'] and result['network_initializations']==1
        assert result['network_drain']['idle'] and result['network_drain']['started_packets']==result['network_drain']['arrived_packets']
        assert all(b['same_network_objects'] for b in result['boundary_resources'])
        assert all(r[k]==0 for r in result['rank_resources'] for k in ('compute_slots','send_slots','pending_events','pending_tasks','stale_callbacks'))
        assert all(not r['request_blocks'] and r['kv_used_bytes']==0 for r in front['memory_final'])
        actual={t['transfer_id']:t for t in result['transfers']};local={t['transfer_id']:t for t in result['local_deliveries']}
        requests={str(r['request_id']):r for r in c['requests']};consumed=Counter();totals=Counter();prior={};overlaps=[];plans=[]
        expected_endpoints={};contrib_localities=Counter();zero_counts=0;duplicate_destinations=0
        for wave in front['waves']:
            p=read(path/f"graphs/{wave['graph_id']}/plan.json");plans.append(p);group=wave['group_id']
            assert wave['submitted_ns']==p['ready_ns']>=prior.get(group,0);prior[group]=wave['finished_ns']
            events={(e['rank_id'],e['node_id']):e for e in result['node_execution'] if e['graph_id']==p['graph_id']}
            assert wave['finished_ns']==max(e['time_ns'] for e in result['graph_completions'] if e['graph_id']==p['graph_id'])
            for li,child in enumerate(p['layers']):
                cfg=child['input'];h=cfg['hidden_dim'];lid=cfg['layer_id'];ranks=cfg['rank_ids'];owned=cfg['expert_placement']
                chips={r['rank_id']:r['chip_id'] for r in cfg['deployment']['ranks']};matrix=Counter();expert_counts=Counter();contributions=[]
                for i,t in enumerate(cfg['tokens']):
                    req=requests[t['request_id']];step=t['token_index']-(req['prompt_tokens']+req['generated_tokens']-1)
                    assert t['expert_ids']==req['decode_routes'][step][str(lid)] and t['src_rank']==req['rank_id']
                    consumed[t['request_id'],t['token_index'],lid]+=1
                    destinations=[owned[e] for e in t['expert_ids']]
                    duplicate_destinations+=len(destinations)-len(set(destinations))
                    for k,e in enumerate(t['expert_ids']):
                        src,dst=t['src_rank'],owned[e];expert_counts[e]+=1
                        loc='same_rank' if src==dst else 'same_chip' if chips[src]==chips[dst] else 'cross_chip'
                        contrib_localities[loc]+=1
                        contributions.append(dict(source_record_index=i,topk_index=k,expert_id=e,src_rank=src,dst_rank=dst,locality=loc))
                        if src!=dst:
                            matrix['dispatch','data',src,dst]+=((h+31)//32)*32+44
                            matrix['combine','data',dst,src]+=h*2
                            matrix['combine','status',dst,src]+=32
                for src in ranks:
                    for e,dst in enumerate(owned):
                        if src!=dst:matrix['dispatch','status',src,dst]+=32
                assert child['contributions']==contributions
                ops={o['operation_id'] for o in child['operations']};transfers=[t for t in p['transfers'] if t['operation_id'] in ops]
                got=Counter();messages={}
                for t in transfers:
                    key=t['kind'],t['message_role'],t['src_rank'],t['dst_rank'];got[key]+=t['payload_bytes']
                    messages.setdefault(t['message_id'],[]).append(t)
                    is_local=chips[t['src_rank']]==chips[t['dst_rank']]
                    assert is_local==('ub_task_id' not in t)
                    assert t['payload_bytes']==sum(t[k] for k in ('business_payload_bytes','application_metadata_bytes','padding_bytes'))
                    if t['message_role']=='status':
                        assert t['payload_bytes']==t['application_metadata_bytes']==32 and t['business_payload_bytes']==t['padding_bytes']==0
                        if t['kind']=='dispatch':
                            n=sum(x['src_rank']==t['src_rank'] and t['expert_id'] in x['expert_ids'] for x in cfg['tokens'])
                            assert t['token_count']==n;zero_counts+=n==0
                    else:
                        assert len(t['sources'])==1 and len(t['sources'][0]['expert_ids'])==1
                        s=t['sources'][0];token=cfg['tokens'][s['source_record_index']]
                        assert s['request_id']==token['request_id'] and s['expert_ids']==[token['expert_ids'][s['topk_index']]]
                    if is_local:
                        x=local[t['transfer_id']];assert t['transfer_id'] not in actual
                        assert x['send_callbacks']==x['recv_callbacks']==1 and x['payload_bytes']==t['payload_bytes'] and not x['active']
                        assert x['recv_callback_ns']==max(x['ready_ns'],x['recv_registered_ns'])
                        send,recv=x['send_callback_ns'],x['recv_callback_ns']
                    else:
                        x=actual[t['transfer_id']]
                        assert x['bytes']==x['committed_bytes']==t['payload_bytes']
                        assert x['submitted_tasks']==x['send_callbacks']==x['receive_callbacks']==1 and not x['context_active']
                        assert x['data_ready_ns']<x['transaction_done_ns']==x['send_done_ns']
                        assert x['receive_done_ns']==max(x['data_ready_ns'],x['receive_start_ns'])
                        send,recv=x['send_done_ns'],x['receive_done_ns']
                        for k in ('payload_bytes','business_payload_bytes','application_metadata_bytes','padding_bytes'):totals[k]+=t[k]
                    assert events[t['src_rank'],t['send_node_id']]['end_ns']==send
                    assert events[t['dst_rank'],t['recv_node_id']]['end_ns']==recv
                assert got==matrix
                for parts in messages.values():
                    first=parts[0];cursor=0
                    limit=first['pair_payload_bytes'] if 'ub_task_id' not in first or first['message_role']=='status' else cfg['chunk_bytes']
                    for ci,t in enumerate(parts):
                        size=min(limit,first['pair_payload_bytes']-cursor)
                        assert t['chunk_index']==ci and t['pair_byte_range']==[cursor,cursor+size];cursor+=size
                    assert cursor==first['pair_payload_bytes']
                    if first['kind']=='combine' and first['message_role']=='status':
                        data=messages[first['message_id']-1]
                        assert events[first['src_rank'],first['send_node_id']]['start_ns']>=max(events[t['src_rank'],t['send_node_id']]['end_ns'] for t in data)
                ends=[];starts=[]
                for graph in p['rank_graphs']:
                    rank=graph['rank_id'];ph=graph['layer_phases'][li]['phase_nodes'];count=sum(n for e,n in expert_counts.items() if owned[e]==rank)
                    nt=sum(t['src_rank']==rank for t in cfg['tokens']);cost=cfg['cann_profile']['local_cost_ns'];profile=cfg['compute_profile_ns']
                    expected_durations=dict(prepare=nt*profile['attention_route_pack'],expert=count*profile['expert_per_input'],
                        dispatch_reorder=count*cost['dispatch_reorder_per_input']+len(owned)*cost['status_check'],
                        local_merge=count*cost['result_pack_per_input'],finish=nt*(profile['combine_unpack_merge']+cfg['top_k']*(cost['status_check']+cost['reduction_per_input'])))
                    for phase,duration in expected_durations.items():
                        if duration:assert events[rank,ph[phase]]['end_ns']-events[rank,ph[phase]]['start_ns']==duration
                        else:assert ph[phase] is None
                    nodes=[n for n in graph['nodes'] if n['layer_id']==lid]
                    for n in nodes:
                        e=events[rank,n['node_id']]
                        assert all(e['start_ns']>=events[rank,d]['end_ns'] for d in n['deps'])
                        if n['kind']=='COMP':assert e['end_ns']-e['start_ns']==n['duration_ns']
                    data_end=max([0]+[events[rank,t['send_node_id']]['end_ns'] for t in transfers if t['src_rank']==rank and t['kind']=='dispatch' and t['message_role']=='data'])
                    for t in transfers:
                        if t['src_rank']==rank and t['kind']=='dispatch' and t['message_role']=='status':assert events[rank,t['send_node_id']]['start_ns']>=data_end
                    previous=graph['layer_phases'][li]['entry_deps'];ready=max([p['ready_ns']]+[events[rank,d]['end_ns'] for d in previous])
                    def end(phase,fallback):return fallback if ph[phase] is None else events[rank,ph[phase]]['end_ns']
                    def comm(kind,fallback):return max([fallback]+[events[rank,t[side+'_node_id']]['end_ns'] for t in transfers if t['kind']==kind
                        for side,field in [('send','src_rank'),('recv','dst_rank')] if t[field]==rank])
                    prepared=end('prepare',ready);dispatched=end('dispatch_reorder',comm('dispatch',prepared));packed=end('local_merge',end('expert',dispatched))
                    for op in child['operations']:expected_endpoints[op['operation_id'],rank]=(prepared,dispatched) if op['kind']=='dispatch' else (packed,end('finish',comm('combine',packed)))
                    ends.append(max(events[rank,n['node_id']]['end_ns'] for n in nodes))
                    if li+1<len(p['layers']):starts.append(min(events[rank,n['node_id']]['start_ns'] for n in graph['nodes'] if n['layer_id']==p['executed_layer_ids'][li+1]))
                if starts and min(starts)<max(ends):overlaps.append([min(starts),max(ends)])
        expected={(rid,r['prompt_tokens']+r['generated_tokens']-1+step,int(lid)) for rid,r in requests.items() for step,route in enumerate(r['decode_routes']) for lid in route}
        assert consumed==Counter({key:1 for key in expected}) and front['route_records_consumed']==len(expected)
        assert sum(t['bytes'] for t in actual.values())==totals['payload_bytes']
        assert len(result['wqe_starts'])==len(result['wqe_completions'])==len(actual)
        slot_limit=l['groups'][0]['layers'][0]['send_slots_per_rank'];assert all(n<=slot_limit for n in result['peak_wqes'])
        queue=None;export=None
        if mode is not None:
            directory=path/'execution/ports/8-4'
            if result['network_drain']['started_packets']:
                queue=audit_queue(directory,bandwidth_gbps=bandwidth,native_directory=path/('execution/runlog' if scenario else 'execution/network/runlog'))
            else:assert not rows(directory/'packet-events.jsonl')
            obs,ranks=observation(l,mode);export=export_records(path,obs,ranks,max_ns)
            target=out/f'{label}-export';target.mkdir();write_export(target,export)
            assert export['validation']['unique_delivery_bytes']==totals['business_payload_bytes']
            if mode:
                for op in export['operations']:
                    for ref in op['endpoint_records']:assert (ref['start_global_ns'],ref['end_global_ns'])==expected_endpoints[op['operation_id'],ref['rank_id']]
                classified=sum(r['business_payload_bytes'] for r in export['validation']['byte_classification'])
                assert classified==sum(r['business_payload_bytes'] for r in export['attribution'])
                if actual:assert sum(r['application_metadata_bytes'] for r in export['validation']['byte_classification'])>0
        cases[label]=dict(finished_ns=front['finished_ns'],waves=len(front['waves']),writes=len(actual),local_deliveries=len(local),
            bytes=dict(totals),route_records=len(consumed),contribution_localities=dict(contrib_localities),zero_count_publications=zero_counts,
            repeated_expert_destinations=duplicate_destinations,peak_wqes=result['peak_wqes'],layer_overlaps=len(overlaps),
            queue=None if queue is None else {k:v for k,v in queue.items() if k not in ('packets','starts')},
            groups={g['group_id']:[dict(submitted_ns=w['submitted_ns'],finished_ns=w['finished_ns'],members=[t['request_id'] for t in w['tokens']])
                for w in front['waves'] if w['group_id']==g['group_id']] for g in l['groups']})
        return dict(front=front,result=result,path=path,export=export)

    on=run('on');assert cases['on']['repeated_expert_destinations'] and cases['on']['zero_count_publications']
    assert cases['on']['layer_overlaps'] and set(cases['on']['contribution_localities'])=={'same_rank','same_chip','cross_chip'}
    for label,mode in [('off',False),('absent',None)]:
        other=run(label,mode=mode)
        assert other['front']==on['front'] and other['result']==on['result']
        assert hashes(other['path']/'execution/network/runlog')==hashes(on['path']/'execution/network/runlog')
        if mode is False:assert hashes(out/'off-export/observed')==hashes(out/'on-export/observed')
    for group in ('ep0','ep1'):
        c,l=deepcopy(base),deepcopy(layers);c['groups']=[g for g in c['groups'] if g['group_id']==group];l['groups']=[g for g in l['groups'] if g['group_id']==group]
        ranks=l['groups'][0]['layers'][0]['rank_ids'];c['requests']=[r for r in c['requests'] if r['rank_id'] in ranks];run(group,c,l)
        assert cases['on']['groups'][group][0]['finished_ns']>cases[group]['groups'][group][0]['finished_ns']
        assert cases['on']['groups'][group][1]['submitted_ns']>cases[group]['groups'][group][1]['submitted_ns']
    slow=out/'slow-network';shutil.copytree(network,slow);p=slow/'topology.csv';p.write_text(p.read_text().replace('10Gbps','1Gbps'))
    run('slow',net=slow,bandwidth=1)
    for group in ('ep0','ep1'):assert cases['slow']['groups'][group][1]['submitted_ns']>cases['on']['groups'][group][1]['submitted_ns']
    for label in ('chunks','slots','lanes','local-costs','all-local'):
        l=deepcopy(layers)
        for g in l['groups']:
            for layer in g['layers']:
                if label=='chunks':layer['chunk_bytes']=257
                elif label=='slots':layer['send_slots_per_rank']=1
                elif label=='lanes':layer['cann_profile']['aiv_lanes']=1
                elif label=='local-costs':layer['cann_profile']['local_cost_ns']={k:v*100 for k,v in layer['cann_profile']['local_cost_ns'].items()}
                else:
                    for r in layer['deployment']['ranks']:r['chip_id']='one-chip'
        run(label,l=l)
    assert cases['chunks']['writes']>cases['on']['writes'] and cases['chunks']['bytes']==cases['on']['bytes']
    assert cases['slots']['finished_ns']!=cases['on']['finished_ns'] and max(cases['slots']['peak_wqes'])==1
    assert cases['lanes']['finished_ns']!=cases['on']['finished_ns'] and cases['local-costs']['finished_ns']>cases['on']['finished_ns']
    assert cases['all-local']['writes']==0 and cases['all-local']['local_deliveries']>0
    c=deepcopy(base);c['requests']=[];run('empty',c)
    run('scenario',scenario=True)
    obs,ranks=observation(layers,period=10000);sampled=export_records(on['path'],obs,ranks,max_ns)
    assert sum(r['business_payload_bytes'] for r in sampled['attribution'])==sum(r['business_payload_bytes'] for r in on['export']['attribution'])
    assert len(sampled['counters'])*10==len(on['export']['counters'])
    save(out/'sampling.json',sampled['validation'])
    # Nonaligned H and a 17-byte vector chunk split useful data, scale, tuple
    # and padding across different actual WRITE transactions/packets.
    padding_cases={}
    for chunk in (1024,17):
        label=f'padding-{chunk}';cfg=read(fixture/'layer.json');cfg['chunk_bytes']=chunk;cfg['tokens'][0]['expert_ids']=[4,5]
        cp=save(out/f'{label}.json',cfg);plan_dir=out/f'{label}-plan'
        invoke(label+'-generate',[sys.executable,'-m','moe_ub_cosim.generate','--input',cp,'--output-dir',plan_dir])
        obs=save(out/f'{label}-observation.json',read(root/'fixtures/m3_shared_bottleneck/observation.json'))
        target=out/label
        invoke(label,[binary.parent/'ns3.44-cosim-layer',plan_dir,system,network,obs,target])
        p=read(plan_dir/'plan.json');result=read(target/'result.json')
        planned=[t for t in p['transfers'] if 'ub_task_id' in t]
        assert result['later_event_executed'] and len(result['graph_completions'])==3 and result['active_wqes']==[0,0,0]
        assert all(r['finished'] and not r['remaining_nodes'] and r['pending_events']==r['pending_tasks']==r['send_slots']==0 for r in result['rank_resources'])
        assert len(result['transfers'])==len(planned) and all(t['submitted_tasks']==t['send_callbacks']==t['receive_callbacks']==1 and
            t['bytes']==t['committed_bytes'] and not t['context_active'] for t in result['transfers'])
        assert sum(t['bytes'] for t in result['transfers'])==6620
        assert [sum(t[k] for t in planned) for k in ('business_payload_bytes','application_metadata_bytes','padding_bytes')]==[6150,352,118]
        byte_truth=rows(target/'truth/port_byte_truth.jsonl');observed=rows(target/'observed/port_counters.jsonl')
        for r,b in zip(observed,byte_truth):assert r['counted_packet_bytes']==sum(v for k,v in b.items() if k not in ('window_start_sim_ns','window_end_sim_ns'))
        assert [sum(t[k] for t in byte_truth) for k in ('business_payload_bytes','application_metadata_bytes','padding_bytes')]==[2050,160,118]
        packets=rows(target/'truth/packet_ranges.jsonl');by_id={t['transfer_id']:t for t in planned}
        assert sum(t['task_byte_range'][1]-t['task_byte_range'][0] for t in packets)==2050
        for part in packets:
            a,b=part['task_byte_range']
            assert any(r['kind']=='business' and r['begin']<=a<b<=r['end'] for r in by_id[part['transfer_id']]['payload_regions'])
        padding_cases[label]=dict(writes=len(planned),payload_bytes=6620,business_bytes=6150,metadata_bytes=352,padding_bytes=118)
    for label,key,value in [('revision','source_revision','main'),('algorithm','algorithm','fullmesh_v2'),('window','window_bytes',1)]:
        l=deepcopy(layers);l['groups'][0]['layers'][0]['cann_profile'][key]=value
        proc=invoke('reject-'+label,command('reject-'+label,base,l),1)
        assert 'CANN' in proc.stderr and not (out/('reject-'+label)).exists();failures[label]=proc.stderr.strip().splitlines()[-1]
    commit=rows(on['path']/'protocol.jsonl')[1]
    for label in ('status-count','layout','dependency','evidence','buffers'):
        shared=out/f'bad-{label}';shared.mkdir();shutil.copytree(on['path']/'bootstrap',shared/'bootstrap');shutil.copytree(on['path']/'graphs/g0',shared/'graphs/g0')
        shutil.copy2(on['path']/'deployment.json',shared/'deployment.json');p=read(shared/'graphs/g0/plan.json');child=p['layers'][0]
        if label=='status-count':next(t for t in child['transfers'] if t['kind']=='dispatch' and t['message_role']=='status')['token_count']+=1
        elif label=='layout':next(t for t in child['transfers'] if t['kind']=='dispatch' and t['message_role']=='data')['payload_regions'][0]['kind']='metadata'
        elif label=='dependency':child['rank_graphs'][0]['nodes'][-1]['deps']=[]
        elif label=='evidence':child['communication_evidence']['native_cann_execution']=True
        else:child['communication_buffers']['total_bytes']-=1
        save(shared/'graphs/g0/plan.json',p)
        proc=invoke('reject-'+label,[binary,base['run_id'],shared,system,network,'-',shared/'execution',max_ns],1,json.dumps(commit)+'\n')
        failure=read(shared/'execution/failure.json');assert failure['now_ns']==0 and not failure['transfers']
        assert 'CANN' in proc.stderr;failures[label]=proc.stderr.strip().splitlines()[-1]
    save(out/'summary.json',dict(passed=True,cases=cases,padding_cases=padding_cases,failures=failures,
        scope='pinned_source_behavior_on_WRITE_RTP_reference_not_native_CANN_or_target_scale'))
    print('PASS: CANN copies, status dependencies, classified bytes, finite resources, shared queues and passive exports')


if __name__=='__main__':main()
