"""Complete Decode reference DAG on real shared UB, with independent stage checks."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import yaml

from moe_ub_cosim.generate import _schema
from moe_ub_cosim.observation import backend_observation
from moe_ub_cosim.research_export import export_records, write_export


def read(path):return json.loads(path.read_text())
def rows(path):return [json.loads(line) for line in path.read_text().splitlines()]
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n');return path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--native',action='store_true',help='Check native STORE dispatch / LOAD combine in the complete model')
    args=parser.parse_args();root=Path(__file__).resolve().parents[2];out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    binary=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    fixture=root/'fixtures/m10_full_model';base=read(fixture/'service.json');layers=read(fixture/'layers.json')
    network=root/'fixtures/m3_shared_bottleneck/network';system=root/'fixtures/m1_compute/system.json';commands=[];cases={};max_ns=4000000

    def invoke(label,argv,code=0,stdin=None):
        argv=[str(v) for v in argv];start=datetime.now(timezone.utc).isoformat()
        p=subprocess.run(argv,cwd=root,input=stdin,capture_output=True,text=True,timeout=120)
        for stream in ('stdout','stderr'):(out/f'{label}.{stream}.log').write_text(getattr(p,stream))
        if stdin is not None:(out/f'{label}.stdin.log').write_text(stdin)
        commands.append(dict(label=label,argv=argv,cwd=str(root),started_utc=start,ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=p.returncode))
        save(out/'commands.json',commands);assert p.returncode==code,(label,p.returncode,p.stderr)
        return p

    def run(label,c=base,l=layers,mode=True,net=network,scenario=False):
        cp=save(out/f'{label}.service.json',c);lp=save(out/f'{label}.layers.json',l)
        ranks=sorted(r for g in l['groups'] for r in g['layers'][0]['rank_ids'])
        obs=dict(clocks=[dict(device_type='rank',device_id=r,offset_ns=0,drift_ppb=0) for r in ranks]+
            [dict(device_type='switch',device_id=8,offset_ns=0,drift_ppb=0)],
            switches=[dict(switch_id=8,port_ids=[4],start_local_ns=0,end_local_ns=max_ns,period_ns=1000)],record_provenance=mode)
        raw=backend_observation(obs,ranks,max_ns);op=save(out/f'{label}.observation.json',raw)
        if scenario:
            d=yaml.safe_load((root/'fixtures/m6_scenario_backend/reference.yaml').read_text())
            d.update(service=str(cp),layer=str(lp),system=str(system),scenario_id='m10-full-model',max_simulation_ns=max_ns,observation=raw)
            d['backend']['reference_file']=str(root/'docs/scenarios/cm384_deepseek_r1_decode_v0.1.yaml')
            if args.native:
                native=yaml.safe_load((root/'fixtures/m11_native/scenario.yaml').read_text())
                d['backend']=native['backend']
                d['backend']['reference_file']=str(root/'fixtures/m11_native/backend-reference.yaml')
            d['network']['case_path']=str(net);d['network']['defaults']['rate_bps']=400000000000
            d['network']['links']=[dict(src=8,src_port=4,dst=9,dst_port=4,rate_bps=10000000000,delay_ns=20)]
            d['execution'].update(chunk_bytes=1024,send_slots_per_rank=2)
            path=out/f'{label}.yaml';path.write_text(yaml.safe_dump(d))
            argv=[sys.executable,'-m','moe_ub_cosim.scenario','run','--input',path,'--backend',binary,'--output-dir',out/label]
        else:argv=[sys.executable,'-m','moe_ub_cosim.service','--input',cp,'--layer',lp,'--backend',binary,
            '--system',system,'--network',net,'--output-dir',out/label,'--max-simulation-ns',max_ns,'--observation',op]
        invoke(label,argv);path=out/label/('run' if scenario else '')
        front=read(path/'service_result.json');execution=read(path/'execution/result.json');manifest=read(path/'manifest.json')
        assert manifest['full_model_graph_execution_validated'] and not manifest['numerical_model_executed']
        assert not manifest['mla_kernel_validated'] and not manifest['target_deployment_validated']
        assert front['service_finished'] and execution['normal_exit'] and execution['network_initializations']==1
        assert execution['network_drain']['idle'] and all(b['same_network_objects'] for b in execution['boundary_resources'])
        assert all(r[k]==0 for r in execution['rank_resources'] for k in ('compute_slots','send_slots','pending_events','pending_tasks','stale_callbacks'))
        assert all(not r['request_blocks'] and r['kv_used_bytes']==0 for r in front['memory_final'])
        for event in front['memory_events']:
            for rank in event['ranks']:
                used=sum(rank['request_blocks'].values());assert used+rank['free_blocks']==rank['kv_capacity_blocks']
                assert rank['kv_used_bytes']==used*1536 and rank['used_bytes']==rank['non_kv_bytes']+rank['kv_used_bytes']
        assert front['route_records_consumed']==front['route_records_expected']==36
        actual={t['transfer_id']:t for t in execution['transfers']};local={t['transfer_id']:t for t in execution['local_deliveries']}
        contributions=business=stages=empty=0;shared_last=routed_last=0;plans=[];prior={};overlaps=0
        for wave in front['waves']:
            p=read(path/f"graphs/{wave['graph_id']}/plan.json");plans.append(p)
            assert p['executed_layer_ids']==[0,1,2,3] and p['communication_layer_ids']==[1,2,3]
            assert wave['submitted_ns']>=prior.get(wave['group_id'],0);prior[wave['group_id']]=wave['finished_ns']
            events={(e['rank_id'],e['node_id']):e for e in execution['node_execution'] if e['graph_id']==wave['graph_id']}
            assert wave['finished_ns']==max(e['end_ns'] for e in events.values())
            tokens=p['layers'][0]['input']['tokens'];profile=p['full_model_profile'];model=p['model_spec']
            for graph in p['rank_graphs']:
                rank=graph['rank_id'];nt=sum(t['src_rank']==rank for t in tokens);contexts=[t['token_index']+1 for t in tokens if t['src_rank']==rank]
                expected={(None,k):nt*v for k,v in profile['per_token_ns'].items()}
                for block,cost in zip(model['layers'],profile['layers']):
                    lid=block['layer_id']
                    for name,value in cost['per_token_ns'].items():
                        if name in ('dispatch_quant_pack','combine_unpack_merge'):continue
                        expected[lid,name]=nt*value*(block['shared_experts'] if name=='shared_expert' else 1)
                    expected[lid,'mla_attention']=sum(contexts)*cost['attention_per_context_token_ns']
                got={(s['layer_id'],s['stage']):s['duration_ns'] for s in graph['model_stages']}
                assert got==expected and len(got)==len(graph['model_stages']);stages+=len(got)
                by_stage={(s['layer_id'],s['stage']):s for s in graph['model_stages']}
                for s in graph['model_stages']:
                    assert s['context_tokens']==contexts and s['batch_size']==nt
                    if not nt:assert s['node_id'] is None and s['duration_ns']==0;empty+=1
                    else:
                        e=events[rank,s['node_id']];assert e['end_ns']-e['start_ns']==expected[s['layer_id'],s['stage']]
                for node in graph['nodes']:
                    e=events[rank,node['node_id']]
                    assert all(e['start_ns']>=events[rank,d]['end_ns'] for d in node['deps'])
                    if node['kind']=='COMP':assert e['end_ns']-e['start_ns']==node['duration_ns']
                for i,child in enumerate(p['layers']):
                    lid=child['input']['layer_id'];ph=graph['layer_phases'][i];cfg=child['input'];owned=cfg['expert_placement']
                    count=sum(owned[e]==rank for t in cfg['tokens'] for e in t['expert_ids']);cost=cfg['cann_profile']['local_cost_ns']
                    durations=dict(prepare=nt*20,expert=count*cfg['compute_profile_ns']['expert_per_input'],
                        dispatch_reorder=count*cost['dispatch_reorder_per_input']+len(owned)*cost['status_check'],
                        local_merge=count*cost['result_pack_per_input'],finish=nt*(100+cfg['top_k']*(cost['status_check']+cost['reduction_per_input'])))
                    for stage,duration in durations.items():
                        node=ph['phase_nodes'][stage]
                        if duration:assert events[rank,node]['end_ns']-events[rank,node]['start_ns']==duration
                        else:assert node is None
                    if nt:
                        sh=by_stage[lid,'shared_expert'];rt=by_stage[lid,'router'];join=by_stage[lid,'shared_routed_add']
                        assert sh['entry_deps']==rt['entry_deps'] and sh['node_id'] in join['entry_deps']
                        assert set(ph['terminal_nodes'])<=set(join['entry_deps'])
                        se=events[rank,sh['node_id']]['end_ns'];re=max(events[rank,d]['end_ns'] for d in ph['terminal_nodes'])
                        assert events[rank,join['node_id']]['start_ns']>=max(se,re)
                        shared_last+=se>re;routed_last+=re>se
                        assert events[rank,by_stage[None,'sampling']['node_id']]['end_ns']<=wave['finished_ns']
            for lid in (1,2):
                ends=[events[g['rank_id'],s['node_id']]['end_ns'] for g in p['rank_graphs'] for s in g['model_stages'] if s['layer_id']==lid and s['stage']=='ffn_residual' and s['node_id']]
                starts=[events[g['rank_id'],s['node_id']]['start_ns'] for g in p['rank_graphs'] for s in g['model_stages'] if s['layer_id']==lid+1 and s['stage']=='attention_norm' and s['node_id']]
                overlaps+=bool(starts and ends and min(starts)<max(ends))
            contributions+=sum(len(child['contributions']) for child in p['layers'])
            for t in p['transfers']:
                if 'ub_task_id' not in t:
                    x=local[t['transfer_id']];assert x['send_callbacks']==x['recv_callbacks']==1
                    send,recv=x['send_callback_ns'],x['recv_callback_ns']
                else:
                    x=actual[t['transfer_id']];business+=t['business_payload_bytes']
                    assert x['bytes']==x['committed_bytes']==t['payload_bytes']
                    assert x['submitted_tasks']==x['receive_callbacks']==x['send_callbacks']==1
                    if t.get('operation')=='LOAD':
                        assert x['data_ready_ns']==x['transaction_done_ns']==x['send_done_ns']
                        assert (x['data_owner_rank'],x['consumer_rank'])==(t['dst_rank'],t['src_rank'])
                    else:assert x['data_ready_ns']<x['transaction_done_ns']==x['send_done_ns']
                    send,recv=x['send_done_ns'],max(x['data_ready_ns'],x['receive_start_ns'])
                assert events[t['src_rank'],t['send_node_id']]['end_ns']==send
                assert events[t['dst_rank'],t['recv_node_id']]['end_ns']==recv
        assert contributions==72 and empty>0 and stages>0
        export=export_records(path,obs,ranks,max_ns);target=out/f'{label}-export';target.mkdir();write_export(target,export)
        assert export['validation']['unique_delivery_bytes']==business
        if mode:
            assert len(export['operations'])==6*len(front['waves'])
            assert all(op['layer_id'] in (1,2,3) and op['kind'] in ('dispatch','combine') for op in export['operations'])
        cases[label]=dict(finished_ns=front['finished_ns'],writes=len(actual),payload_bytes=sum(t['bytes'] for t in actual.values()),business_bytes=business,
            waves=len(front['waves']),stage_records=stages,empty_stage_records=empty,shared_last=shared_last,routed_last=routed_last,layer_overlaps=overlaps)
        return path,front,execution,plans

    if args.native:
        for group in layers['groups']:
            for layer in group['layers']:
                layer.update(template='cann_a3_v850_native_v1',
                             native_profile=dict(dispatch='STORE',combine='LOAD',ready_notice_bytes=32))
        primary=run('native',scenario=True)
        off=run('native-truth-off',mode=False,scenario=True)
        assert primary[2]==off[2]
        assert all(t['operation'] in ('STORE','LOAD') for t in primary[2]['transfers'])
        from moe_ub_cosim.record_storage import iter_result
        from moe_ub_cosim.bounded_export import export_bounded
        from moe_ub_cosim.research_storage import logical_windows,stored_rows
        policy=save(out/'record-policy.json',dict(schema_version='moe-ub-record-storage/1',
            max_compressed_bytes=128*2**20,max_buffer_bytes=2**20,max_record_bytes=2**18))
        invoke('native-bounded',[sys.executable,'-m','moe_ub_cosim.service',
            '--input',out/'native.service.json','--layer',out/'native.layers.json','--backend',binary,
            '--system',system,'--network',out/'native/network','--output-dir',out/'native-bounded',
            '--observation',out/'native.observation.json','--max-simulation-ns',max_ns,'--record-storage',policy])
        bounded=out/'native-bounded';report=read(bounded/'execution/result.json')
        for field in ('transfers','native_starts','native_completions','node_execution'):
            assert list(iter_result(bounded/'execution',field,report))==primary[2][field],field
        life=report['resource_lifecycle'];assert life['resident_graphs_at_exit']==life['resident_transfers_at_exit']==0
        assert life['provenance']['resident_packets']==life['provenance']['pending_native_requests']==0
        ranks=sorted(r for g in layers['groups'] for r in g['layers'][0]['rank_ids'])
        obs=dict(clocks=[dict(device_type='rank',device_id=r,offset_ns=0,drift_ppb=0) for r in ranks]+
            [dict(device_type='switch',device_id=8,offset_ns=0,drift_ppb=0)],
            switches=[dict(switch_id=8,port_ids=[4],start_local_ns=0,end_local_ns=max_ns,period_ns=1000)],record_provenance=True)
        limits=dict(schema_version='moe-ub-research-export-policy/1',max_index_bytes=128*2**20,
            max_compressed_bytes=128*2**20,max_record_bytes=2**20,max_endpoint_records=100000,
            max_operations=10000,max_layouts=1000,max_id_ranges=10000,max_graphs=1000,cache_rows=16)
        result=export_bounded(bounded,obs,ranks,max_ns,out/'native-bounded-export',limits)
        expected=export_records(primary[0],obs,ranks,max_ns)
        assert list(logical_windows(out/'native-bounded-export/observed'))==expected['counters']
        for table,key in (('transfer_registry.jsonl','transfers'),('attribution_windows.csv','attribution')):
            assert list(stored_rows(out/'native-bounded-export/truth',table))==expected[key]
        assert result['unique_delivery_bytes']==expected['validation']['unique_delivery_bytes']
        cases['bounded']=dict(storage_equivalent=True,export_equivalent=True,resource_lifecycle=life)
        save(out/'summary.json',dict(passed=True,cases=cases,native=True,hardware_calibrated=False,target_scale_executed=False))
        print('PASS: native complete model, composed dependencies, byte export, passive observations and resource exit')
        return
    primary=run('reference');assert cases['reference']['routed_last']>0 and cases['reference']['layer_overlaps']>0
    off=run('provenance-off',mode=False);assert primary[2]==off[2]
    shared=deepcopy(base)
    for g in shared['groups']:
        for p in g['full_model_profile']['layers'][1:]:p['per_token_ns']['shared_expert']=100000
    # One compute slot can serialize routed reduction behind the long shared FFN.
    # The acceptance condition is delayed completion with BOTH dependencies, not a chosen winner.
    run('shared-slow',shared)
    assert cases['shared-slow']['finished_ns']>cases['reference']['finished_ns']+100000
    late=deepcopy(base)
    for g in late['groups']:g['full_model_profile']['per_token_ns']['sampling']=100000
    delayed=run('sampling-slow',late)
    assert delayed[1]['waves'][0]['finished_ns']>primary[1]['waves'][0]['finished_ns']
    tight=deepcopy(base)
    for g in tight['groups']:
        for r in g['memory']['ranks']:r['device_bytes']-=5*1536
    waited=run('tight',tight)[1]
    assert any(e.get('reason')=='kv_capacity' and e['request_id']==202 for e in waited['memory_events'])
    released={e['request_id']:e['time_ns'] for e in waited['memory_events'] if e['event']=='release'}
    admitted={e['request_id']:e['time_ns'] for e in waited['memory_events'] if e['event']=='admit'}
    assert admitted[202]==released[201] and admitted[203]==released[202]
    window=read(fixture/'evidence.json');limited=deepcopy(layers)
    window['service']['groups'][1]['scheduling']['max_num_batched_tokens']=1
    for row in limited['groups'][1]['layers']:row['batch_limits']['max_num_batched_tokens']=1
    observed=run('window',window,limited)[1]
    assert all(r['status']=='OBSERVATION_ENDED' and r['target_tokens'] is None and not r['eos_known'] for r in observed['requests'])
    assert observed['requests_completed']==[] and len(observed['requests_censored'])==7 and sum(observed['done'])==0
    assert Counter(e['request_id'] for e in observed['memory_events'] if e['event']=='observation_cleanup')==Counter(r['request_id'] for r in observed['requests'])
    assert next(e for e in observed['batch_events'] if e['event']=='scheduled' and e['rank_id']==20)['request_ids']==[401]
    slow=out/'slow-network';shutil.copytree(network,slow);p=slow/'topology.csv';p.write_text(p.read_text().replace('10Gbps','1Gbps'))
    slower=run('network-slow',net=slow)[1]
    for group in ('ep0','ep1'):
        assert [w for w in slower['waves'] if w['group_id']==group][1]['submitted_ns']>[w for w in primary[1]['waves'] if w['group_id']==group][1]['submitted_ns']
    run('scenario',scenario=True)
    # A normally exited empty service has executed no complete model graph.
    empty=deepcopy(base);empty['requests']=[];cp=save(out/'empty.service.json',empty)
    invoke('empty',[sys.executable,'-m','moe_ub_cosim.service','--input',cp,'--layer',fixture/'layers.json',
        '--backend',binary,'--system',system,'--network',network,'--output-dir',out/'empty','--max-simulation-ns',max_ns])
    empty_manifest=read(out/'empty/manifest.json');empty_result=read(out/'empty/service_result.json')
    assert empty_manifest['normal_exit'] and not empty_manifest['full_model_graph_execution_validated'] and empty_result['waves']==[]
    commit=rows(primary[0]/'protocol.jsonl')[1];failures={}
    for label in ('stage','dependency','coverage','cost-domain','declaration','chakra'):
        shared=out/f'bad-{label}';shared.mkdir();shutil.copytree(primary[0]/'bootstrap',shared/'bootstrap');shutil.copytree(primary[0]/'graphs/g0',shared/'graphs/g0')
        shutil.copy2(primary[0]/'deployment.json',shared/'deployment.json');p=read(shared/'graphs/g0/plan.json')
        if label=='stage':p['rank_graphs'][0]['nodes'][-1]['duration_ns']+=1
        elif label=='dependency':next(n for n in p['rank_graphs'][0]['nodes'] if n.get('model_stage')=='shared_routed_add')['deps']=[]
        elif label=='coverage':p['executed_layer_ids'].pop()
        elif label=='cost-domain':p['full_model_profile']['model_structure']['hidden_size']+=1
        elif label=='declaration':
            d=read(shared/'deployment.json');d['groups'][0]['full_model_profile']['per_token_ns']['sampling']+=1;save(shared/'deployment.json',d)
        else:
            # Valid protobuf with a changed compute duration must fail ET agreement.
            from google.protobuf.internal.decoder import _DecodeVarint32
            from google.protobuf.internal.encoder import _VarintBytes
            pb,_=_schema(shared);et=shared/'graphs/g0/layer.2.et';data=et.read_bytes();offset=0;parts=[];index=0
            while offset<len(data):
                size,start=_DecodeVarint32(data,offset);payload=data[start:start+size];offset=start+size
                if index==1:
                    node=pb.Node();node.ParseFromString(payload);node.duration_micros+=1;payload=node.SerializeToString()
                parts.append(_VarintBytes(len(payload))+payload);index+=1
            et.write_bytes(b''.join(parts))
        save(shared/'graphs/g0/plan.json',p)
        proc=invoke('reject-'+label,[binary,base['run_id'],shared,system,network,'-',shared/'execution',max_ns],1,json.dumps(commit)+'\n')
        failure=read(shared/'execution/failure.json');assert failure['now_ns']==0 and not failure['transfers']
        failures[label]=proc.stderr.strip().splitlines()[-1]
    save(out/'summary.json',dict(passed=True,cases=cases,failures=failures,hardware_calibrated=False,target_scale_executed=False))
    print('PASS: complete model reference graph, shared/routed joins, context costs, native feedback and passive UB observations')


if __name__=='__main__':main()
