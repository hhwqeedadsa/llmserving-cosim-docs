"""Continuous batches driven by actual ASTRA/UB completion, with independent accounting."""
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

from check_service_boundaries import check_sparse_graph


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    out=args.output_dir.resolve(); out.mkdir(parents=True,exist_ok=False)
    binary=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    fixture=root/'fixtures/m7_continuous_batching'
    system=root/'fixtures/m1_compute/system.json'; network=root/'fixtures/m2_single_layer/network'
    commands,cases,failures=[],{},{}

    def save(path,value):
        path.write_text(json.dumps(value,indent=2)+'\n'); return path

    def invoke(label,argv,expected=0,stdin=None):
        argv=[str(a) for a in argv]; start=datetime.now(timezone.utc).isoformat()
        proc=subprocess.run(argv,cwd=root,input=stdin,capture_output=True,text=True,timeout=120)
        for key in ('stdout','stderr'):(out/f'{label}.{key}.log').write_text(getattr(proc,key))
        if stdin is not None:(out/f'{label}.stdin.log').write_text(stdin)
        commands.append(dict(label=label,argv=argv,cwd=str(root),started_utc=start,
            ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=proc.returncode))
        save(out/'commands.json',commands)
        assert proc.returncode==expected,(label,proc.returncode,proc.stderr)
        return proc

    def command(label,config,layer,mode=True,net=network,scenario=False):
        cp=save(out/f'{label}.service.json',config); lp=save(out/f'{label}.layer.json',layer)
        obs=read(root/'fixtures/m2_single_layer/observation.json')
        obs.update(port_id=3,window_end_ns=2000000,record_provenance=mode)
        if scenario:
            data=yaml.safe_load((root/'fixtures/m6_scenario_backend/reference.yaml').read_text())
            data.update(service=str(cp),layer=str(lp),system=str(system),scenario_id='m7-batch-scenario')
            data['backend']['reference_file']=str(root/'docs/scenarios/cm384_deepseek_r1_decode_v0.1.yaml')
            data['network']['case_path']=str(net)
            data['execution'].update(chunk_bytes=layer['chunk_bytes'],send_slots_per_rank=layer['send_slots_per_rank'])
            data['observation']=None if mode is None else obs
            path=out/f'{label}.yaml'; path.write_text(yaml.safe_dump(data))
            return [sys.executable,'-m','moe_ub_cosim.scenario','run','--input',path,'--backend',binary,'--output-dir',out/label]
        argv=[sys.executable,'-m','moe_ub_cosim.service','--input',cp,'--layer',lp,'--backend',binary,
              '--system',system,'--network',net,'--output-dir',out/label,'--max-simulation-ns',10000000]
        if mode is not None:argv+=['--observation',save(out/f'{label}.observation.json',obs)]
        return argv

    def run(label,config,layer,mode=True,net=network,scenario=False):
        invoke(label,command(label,config,layer,mode,net,scenario))
        path=out/label/('run' if scenario else '')
        front,result=read(path/'service_result.json'),read(path/'execution/result.json')
        assert read(path/'manifest.json')['normal_exit'] and result['normal_exit']
        assert front['pending']==front['waiting']==front['running']==front['inflight']==[0]*len(layer['rank_ids'])
        assert result['network_initializations']==1 and result['network_drain']['idle']
        assert result['network_drain']['started_packets']==result['network_drain']['arrived_packets']
        assert result['active_wqes']==[0]*len(layer['rank_ids'])
        assert all(r[k]==0 for r in result['rank_resources'] for k in
                   ('compute_slots','send_slots','pending_events','pending_tasks','stale_callbacks'))
        assert all(r['kv_used_bytes']==0 and not r['request_blocks'] and r['used_bytes']==r['non_kv_bytes'] for r in front['memory_final'])
        source={r['request_id']:r for r in config['requests']}; consumed=Counter(); expected_bytes=0
        fabric={t['transfer_id']:t for t in result['transfers']}
        local={t['transfer_id']:t for t in result['local_deliveries']}
        chips={r['rank_id']:r['chip_id'] for r in layer.get('deployment',{}).get('ranks',[])}
        for wave in front['waves']:
            plan=read(path/f"graphs/{wave['graph_id']}/plan.json")
            assert plan['schema_version']=='moe-ub-execution-plan/3' and plan['batch_limits']==config['scheduling']
            assert plan['input']['tokens']==wave['tokens']
            counts=Counter(t['src_rank'] for t in wave['tokens'])
            assert max(counts.values())<=min(config['scheduling'].values())
            assert len({t['request_id'] for t in wave['tokens']})==len(wave['tokens'])
            matrix=Counter(); expert_counts=Counter()
            for token in wave['tokens']:
                identity=int(token['request_id']); request=source[identity]; offset=consumed[identity]
                assert token['token_index']==request['prompt_tokens']+request['generated_tokens']-1+offset
                assert token['expert_ids']==request['decode_routes'][offset]
                assert token['src_rank']==request['rank_id'] and wave['submitted_ns']>=request['arrival_ns']
                consumed[identity]+=1
                for expert in token['expert_ids']:expert_counts[expert]+=1
                for dst in {layer['expert_placement'][e] for e in token['expert_ids']} - {token['src_rank']}:
                    matrix['dispatch',token['src_rank'],dst]+=layer['hidden_dim']*layer['dispatch_element_bytes']
                    matrix['combine',dst,token['src_rank']]+=layer['hidden_dim']*layer['combine_element_bytes']
            actual=Counter()
            for transfer in plan['transfers']:
                actual[transfer['kind'],transfer['src_rank'],transfer['dst_rank']]+=transfer['payload_bytes']
                same_chip=chips and chips[transfer['src_rank']]==chips[transfer['dst_rank']]
                if same_chip:
                    delivery=local[transfer['transfer_id']]
                    assert transfer['transfer_id'] not in fabric and 'ub_task_id' not in transfer
                    assert delivery['send_callbacks']==delivery['recv_callbacks']==1
                else:
                    delivery=fabric[transfer['transfer_id']]; expected_bytes+=transfer['payload_bytes']
                    assert delivery['bytes']==transfer['payload_bytes']
                    assert delivery['send_callbacks']==delivery['receive_callbacks']==delivery['submitted_tasks']==1
                    assert delivery['send_done_ns']==delivery['transaction_done_ns']>delivery['data_ready_ns']
                    assert delivery['receive_done_ns']==max(delivery['receive_start_ns'],delivery['data_ready_ns'])
            assert actual==matrix
            for shape in plan['compute_inputs']['ranks']:
                rank=shape['rank_id']; assert shape['batch_size']==counts[rank]
                assert shape['expert_inputs']==[dict(expert_id=e,input_tokens=expert_counts[e])
                    for e,owner in enumerate(layer['expert_placement']) if owner==rank]
                assert shape['requests']==[dict(request_id=t['request_id'],token_index=t['token_index'],
                    context_tokens=t['token_index']+1,query_tokens=1) for t in wave['tokens'] if t['src_rank']==rank]
            if not chips:check_sparse_graph(plan,result,path/'execution')
            events={(e['rank_id'],e['node_id']):e for e in result['node_execution'] if e['graph_id']==wave['graph_id']}
            for graph in plan['rank_graphs']:
                rank=graph['rank_id']; intervals=[]
                for node in graph['nodes']:
                    event=events[rank,node['node_id']]
                    assert all(event['start_ns']>=events[rank,d]['end_ns'] for d in node['deps'])
                    if node['kind']=='COMP':
                        assert event['end_ns']-event['start_ns']==node['duration_ns']
                        intervals.append((event['start_ns'],event['end_ns']))
                intervals.sort(); assert all(a[1]<=b[0] for a,b in zip(intervals,intervals[1:]))
            assert wave['finished_ns']==max(e['time_ns'] for e in result['graph_completions'] if e['graph_id']==wave['graph_id'])
        assert consumed==Counter({identity:len(r['decode_routes']) for identity,r in source.items()})
        assert sum(t['bytes'] for t in result['transfers'])==expected_bytes
        assert len(result['wqe_starts'])==len(result['wqe_completions'])==len(fabric)
        releases=[e for e in front['memory_events'] if e['event']=='release']
        admits=[e for e in front['memory_events'] if e['event']=='admit']
        assert Counter(e['request_id'] for e in releases)==Counter(source.keys())==Counter(e['request_id'] for e in admits)
        for request in front['requests']:
            assert request['status']=='FINISHED' and request['preemptions']==0
            assert request['num_tokens_reached']==request['target_tokens'] and len(request['intertoken_latencies_ns'])==consumed[request['request_id']]
        scheduled=[e for e in front['batch_events'] if e['event']=='scheduled']
        completed=[e for e in front['batch_events'] if e['event']=='completed']
        assert sum(e['generated_tokens'] for e in completed)==sum(consumed.values())
        assert len(scheduled)==len(completed)==front['scheduler_calls']
        for event in scheduled:
            assert len(event['request_ids'])<=min(config['scheduling'].values())
            assert set(event['scheduled_tokens'])=={str(r) for r in event['request_ids']}
            assert set(event['scheduled_tokens'].values())=={1}
            memory=next(r for r in event['ranks'] if r['rank_id']==event['rank_id'])
            assert set(memory['request_blocks'])=={str(r) for r in event['resident_request_ids']}
        for event in front['memory_events']:
            for rank in event['ranks']:
                assert sum(rank['request_blocks'].values())+rank['free_blocks']==rank['kv_capacity_blocks']
        if mode is True:
            truth=rows(path/'execution/truth/transfer_registry.jsonl')
            assert sum(t['business_payload_bytes'] for t in truth)==expected_bytes and {t['transfer_id'] for t in truth}==set(fabric)
            for observed,record in zip(rows(path/'execution/observed/port_counters.jsonl'),rows(path/'execution/truth/port_byte_truth.jsonl')):
                assert observed['counted_packet_bytes']==sum(record[k] for k in
                    ('business_payload_bytes','application_metadata_bytes','data_protocol_overhead_bytes','control_packet_bytes','unattributed_bytes'))
                assert record['unattributed_bytes']==0
        cases[label]=dict(waves=len(front['waves']),finished_ns=front['finished_ns'],writes=len(fabric),
            business_bytes=expected_bytes,local_deliveries=len(local),generated_tokens=sum(consumed.values()),
            batch_members=[[t['request_id'] for t in w['tokens']] for w in front['waves']])
        return front,result

    config,layer=read(fixture/'service.json'),read(fixture/'layer.json')
    base=run('abc',config,layer)
    assert cases['abc']['batch_members']==[['201','202'],['201','203'],['201','203']]
    assert cases['abc']['business_bytes']==512 and cases['abc']['writes']==6
    assert base[0]['arrivals'][-1]['active_graph_id']=='g0'
    assert base[0]['waves'][1]['submitted_ns']==base[0]['waves'][0]['finished_ns']
    assert run('source-off',config,layer,False)==base
    assert run('no-observation',config,layer,None)==base
    for p in (out/'abc/execution/observed').iterdir():assert p.read_bytes()==(out/'source-off/execution/observed'/p.name).read_bytes()
    scarce=deepcopy(config); scarce['memory']['ranks'][0]['device_bytes']-=512
    run('kv-wait',scarce,layer)
    assert cases['kv-wait']['batch_members']==[['201','202'],['201'],['201'],['203'],['203']]
    for label,seq,tokens in [('sequence1',1,2),('token1',3,1)]:
        c,l=deepcopy(config),deepcopy(layer); c['scheduling']=l['batch_limits']=dict(max_num_seqs=seq,max_num_batched_tokens=tokens)
        l['tokens']=l['tokens'][:1]; c['memory']['ranks'][0]['device_bytes']+=512
        if label=='token1':c['requests'][2]['arrival_ns']=0
        front,_=run(label,c,l)
        assert cases[label]['batch_members']==[['201'],['201'],['201'],['202'],['203'],['203']]
        if label=='token1':
            event=next(e for e in front['batch_events'] if e['event']=='scheduled')
            assert event['resident_request_ids']==[201,202,203] and event['ranks'][0]['request_blocks']=={'201':1,'202':1,'203':2}
    c=deepcopy(config); c['requests']=[]; run('empty',c,layer)
    c=deepcopy(config); c['requests'][2]['arrival_ns']=500000
    run('idle-arrival',c,layer)
    assert read(out/'idle-arrival/service_result.json')['waves'][-2]['submitted_ns']==500000
    slow=out/'slow-network'; shutil.copytree(network,slow)
    p=slow/'topology.csv'; p.write_text(p.read_text().replace('400Gbps','1Gbps').replace('20ns','2000ns'))
    changed=run('slow',config,layer,net=slow)
    assert changed[1]['transfers'][0]['data_ready_ns']>base[1]['transfers'][0]['data_ready_ns']
    assert changed[0]['waves'][1]['submitted_ns']>base[0]['waves'][1]['submitted_ns']
    assert changed[0]['finished_ns']>base[0]['finished_ns']
    run('scenario',config,layer,scenario=True)
    assert read(out/'scenario/resolution.json')['execution_scope'].endswith('continuous_batching')
    # Uneven ranks and batch scaling: both local requests remain distinct vectors.
    c=deepcopy(config)
    c['requests']=[dict(config['requests'][0],decode_routes=[[2]]),dict(config['requests'][1],decode_routes=[[2]]),
        dict(config['requests'][2],rank_id=10,arrival_ns=0,decode_routes=[[0]])]
    c['memory']['ranks'][1]['device_bytes']+=512
    run('uneven',c,layer)
    assert cases['uneven']['waves']==1 and cases['uneven']['business_bytes']==384
    scaled=deepcopy(c); scaled['requests'].pop(1); run('smaller-batch',scaled,layer)
    assert cases['smaller-batch']['business_bytes']==256
    l=deepcopy(layer); l['chunk_bytes']=32
    run('chunked',config,l)
    assert cases['chunked']['business_bytes']==512 and cases['chunked']['writes']==16
    c=deepcopy(config)
    for request in c['requests']:request['decode_routes']=[[0] for _ in request['decode_routes']]
    front,result=run('local-only',c,layer)
    assert cases['local-only']['batch_members']==cases['abc']['batch_members']
    assert not result['transfers'] and result['network_drain']['started_packets']==0
    # Three logical ranks, local contributions and remote writes in the same batch.
    c=read(root/'fixtures/m6_locality/service.json'); l=read(root/'fixtures/m6_locality/service-layer.json')
    c['schema_version']='moe-ub-configured-service/2'; c['scheduling']=deepcopy(config['scheduling'])
    l.update(schema_version='moe-ub-layer-fixture/3',batch_limits=c['scheduling'],
        compute_dtypes={k:v for k,v in c['model_spec']['dtypes'].items() if k!='weights'})
    for rank in c['memory']['ranks']:
        rank['device_bytes']+=4096; rank['communication_buffer_bytes']+=4096
    c['requests']=[dict(request_id=identity,rank_id=rank,arrival_ns=0,prompt_tokens=1,generated_tokens=1,
        decode_routes=[route]) for identity,rank,route in [(301,2,[0,1,4]),(302,2,[0,1,4]),(303,7,[0,2,4])]]
    run('mixed-locality',c,l)
    assert cases['mixed-locality']['business_bytes']==1152 and cases['mixed-locality']['writes']==4
    assert cases['mixed-locality']['local_deliveries']==4
    for label,mutate,fragment in [
        ('impossible',lambda c,l:c['requests'][2].update(prompt_tokens=30),'final KV demand'),
        ('budget',lambda c,l:c['scheduling'].update(max_num_seqs=0),'batch limits differ'),
        ('buffer',lambda c,l:c['memory']['ranks'][0].update(communication_buffer_bytes=1),'Communication buffer'),
        ('route',lambda c,l:c['requests'][0]['decode_routes'].__setitem__(0,[3]),'expert_id'),
        ('legacy',lambda c,l:c.update(schema_version='moe-ub-configured-service/1'),'schema mismatch')]:
        c,l=deepcopy(config),deepcopy(layer); mutate(c,l)
        proc=invoke('reject-'+label,command('reject-'+label,c,l),1)
        assert fragment in proc.stderr and not (out/('reject-'+label)).exists(); failures[label]=fragment
    # Backend rejects altered semantics independently of the Python validator.
    commit=rows(out/'abc/protocol.jsonl')[1]
    for label,mutate,fragment in [
        ('repeat-request',lambda p:p['input']['tokens'][1].update(request_id='201',token_index=3),'request repeated'),
        ('shape',lambda p:p['compute_inputs']['ranks'][1]['expert_inputs'][0].update(input_tokens=1),'compute shapes'),
        ('budget-overrun',lambda p:(p['input']['batch_limits'].update(max_num_batched_tokens=1),p['batch_limits'].update(max_num_batched_tokens=1)),'exceeded batch budget'),
        ('changed-budget',lambda p:(p['input']['batch_limits'].update(max_num_seqs=3),p['batch_limits'].update(max_num_seqs=3)),'Batch limits changed'),
        ('merge-request-vectors',lambda p:p['transfers'][0]['sources'].pop(),'source'),
        ('compute',lambda p:p['rank_graphs'][0]['nodes'][0].update(duration_ns=5000),'compute phase work/duration/dependency mismatch'),
    ]:
        shared=out/('bad-'+label); shared.mkdir()
        shutil.copytree(out/'abc/bootstrap',shared/'bootstrap'); shutil.copytree(out/'abc/graphs/g0',shared/'graphs/g0')
        shutil.copyfile(out/'abc/deployment.json',shared/'deployment.json')
        p=read(shared/'graphs/g0/plan.json'); mutate(p); save(shared/'graphs/g0/plan.json',p)
        proc=invoke('reject-'+label,[binary,config['run_id'],shared,system,network,'-',shared/'execution',1000000],1,json.dumps(commit)+'\n')
        assert fragment.lower() in proc.stderr.lower(),(label,proc.stderr)
        failure=read(shared/'execution/failure.json')
        assert failure['now_ns']==0 and not failure['transfers']
        failures[label]=fragment
    sources=[p for folder in ('backend','python/moe_ub_cosim','fixtures/m7_continuous_batching') for p in (root/folder).glob('*') if p.is_file()]
    save(out/'summary.json',dict(passed=True,cases=cases,failures=failures,
        source_hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}))
    print('PASS: ABC continuous batches, per-request progress, KV waits, budgets, real UB feedback and observation')


if __name__=='__main__':main()
