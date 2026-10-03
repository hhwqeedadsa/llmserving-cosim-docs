"""Execute censored route windows and exact operator profiles in one UB network."""
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


def read(path): return json.loads(Path(path).read_text())
def rows(path): return [json.loads(line) for line in Path(path).read_text().splitlines()]
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def ref(path): return dict(path=str(Path(path).resolve()),sha256=sha(path))
def save(path,value): Path(path).write_text(json.dumps(value,indent=2)+'\n');return Path(path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--measured-profile',type=Path)
    args=parser.parse_args();root=Path(__file__).resolve().parents[2];out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    binary=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    base=read(root/'fixtures/m9_evidence/profiled-service.json');layers=read(root/'fixtures/m8_composed_service/layers.json')
    network=root/'fixtures/m3_shared_bottleneck/network';system=root/'fixtures/m1_compute/system.json'
    commands=[];cases={};failures={}
    def invoke(label,command,code=0,stdin=None):
        command=[str(a) for a in command];start=datetime.now(timezone.utc).isoformat()
        p=subprocess.run(command,cwd=root,capture_output=True,text=True,input=stdin,timeout=180)
        (out/f'{label}.stdout.log').write_text(p.stdout);(out/f'{label}.stderr.log').write_text(p.stderr)
        if stdin is not None:(out/f'{label}.stdin.log').write_text(stdin)
        commands.append(dict(label=label,argv=command,cwd=str(root),started_utc=start,
                             ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=p.returncode))
        save(out/'commands.json',commands);assert p.returncode==code,(label,p.returncode,p.stderr)
        return p
    def command(label,c,l,observed=True,net=network,max_ns=1000000):
        cp=save(out/f'{label}.service.json',c);lp=save(out/f'{label}.layers.json',l)
        cmd=[sys.executable,'-m','moe_ub_cosim.service','--input',cp,'--layer',lp,'--backend',binary,
             '--system',system,'--network',net,'--output-dir',out/label,'--max-simulation-ns',max_ns]
        if observed is not None:
            obs=read(root/'fixtures/m3_shared_bottleneck/observation.json');obs['record_provenance']=observed
            obs['window_end_ns']=max_ns
            cmd+=['--observation',save(out/f'{label}.observation.json',obs)]
        return cmd
    def audit(label,c,path,observed=True,rate=10,scenario=False):
        front=read(path/'service_result.json');execution=read(path/'execution/result.json')
        assert read(path/'manifest.json')['normal_exit'] and execution['normal_exit']
        assert execution['network_initializations']==1 and execution['network_drain']['idle']
        assert all(b['same_network_objects'] for b in execution['boundary_resources'])
        assert front['observation_finished'] and front['service_finished_semantics']=='declared_observation_workload_finished'
        request={str(r['request_id']):r for r in c['requests']};manifest=read(root/c['route_manifest']['path'])
        files={f['file_id']:read(root/f['file']['path']) for f in manifest['files']}
        expected=set();completed=set();censored=set()
        for rid,r in request.items():
            raw=files[r['file_id']];prompt=len(raw[0]['1']);reached=prompt+r['first_decode_entry']+r['decode_steps']
            row=next(x for x in front['requests'] if str(x['request_id'])==rid)
            assert row['target_tokens']==r['target_total_tokens'] and row['num_tokens_reached']==reached
            assert row['num_computed_tokens']==reached-1 and row['observed_steps_completed']==r['decode_steps']
            assert len(row['intertoken_latencies_ns'])==r['decode_steps'] and row['eos_known'] is False
            (completed if reached==r['target_total_tokens'] else censored).add(int(rid))
            assert row['status']==('FINISHED' if int(rid) in completed else 'OBSERVATION_ENDED')
            for step in range(r['first_decode_entry'],r['first_decode_entry']+r['decode_steps']):
                expected.update((rid,prompt+step-1,lid) for lid in (1,2,3))
        assert set(front['requests_completed'])==completed and set(front['requests_censored'])==censored
        assert sum(front['done'])==len(completed) and front['all_requests_completed']==(not censored)
        assert front['pending']==front['waiting']==front['running']==front['inflight']==[0]*len(front['memory_final'])
        assert all(not r['request_blocks'] and r['kv_used_bytes']==0 for r in front['memory_final'])
        assert Counter(e['request_id'] for e in front['memory_events'] if e['event']=='release')==Counter(completed)
        assert Counter(e['request_id'] for e in front['memory_events'] if e['event']=='observation_cleanup')==Counter(censored)
        assert len(front['observation_events'])==len(request)
        for event in front['memory_events']:
            for rank in event['ranks']:
                assert sum(rank['request_blocks'].values())+rank['free_blocks']==rank['kv_capacity_blocks']
                assert rank['used_bytes']==rank['non_kv_bytes']+rank['kv_used_bytes']
        consumed=Counter();total=0;compute=[]
        actual_writes={t['transfer_id']:t for t in execution['transfers']}
        for wave in front['waves']:
            plan=read(path/f"graphs/{wave['graph_id']}/plan.json")
            events={(e['rank_id'],e['node_id']):e for e in execution['node_execution'] if e['graph_id']==wave['graph_id']}
            assert wave['finished_ns']==max(e['time_ns'] for e in execution['graph_completions'] if e['graph_id']==wave['graph_id'])
            for li,child in enumerate(plan['layers']):
                cfg=child['input'];lid=cfg['layer_id'];experts=Counter();tokens=Counter();matrix=Counter()
                for token,source in zip(cfg['tokens'],child['input_provenance']['route_records']):
                    rid=token['request_id'];r=request[rid];raw=files[r['file_id']];prompt=len(raw[0]['1'])
                    entry=token['token_index']-prompt+1
                    assert token['expert_ids']==raw[entry][str(lid)][0]==source['expert_ids']
                    assert source['source_entry']==entry and source['source_token_row']==0 and source['decode_index']==entry-1
                    key=rid,token['token_index'],lid;assert key in expected;consumed[key]+=1
                    tokens[token['src_rank']]+=1;experts.update(token['expert_ids'])
                    for dst in {cfg['expert_placement'][e] for e in token['expert_ids']}-{token['src_rank']}:
                        for kind,a,b in [('dispatch',token['src_rank'],dst),('combine',dst,token['src_rank'])]:
                            matrix[kind,a,b]+=cfg['hidden_dim']*cfg[kind+'_element_bytes']
                operations={o['operation_id'] for o in child['operations']};actual=Counter()
                for t in plan['transfers']:
                    if t['operation_id'] not in operations:continue
                    actual[t['kind'],t['src_rank'],t['dst_rank']]+=t['business_payload_bytes']
                    if 'ub_task_id' in t:
                        x=actual_writes[t['transfer_id']];total+=t['business_payload_bytes']
                        assert x['committed_bytes']==x['bytes']==t['business_payload_bytes']
                        assert x['submitted_tasks']==x['send_callbacks']==x['receive_callbacks']==1
                        assert x['data_ready_ns']<x['send_done_ns']==x['transaction_done_ns']
                assert matrix==actual
                for graph in plan['rank_graphs']:
                    rank=graph['rank_id'];ph=graph['layer_phases'][li]['phase_nodes'];n=tokens[rank]
                    expert_count=sum(count for e,count in experts.items() if cfg['expert_placement'][e]==rank)
                    prepare=n*cfg['compute_profile_ns']['attention_route_pack'];expert=expert_count*cfg['compute_profile_ns']['expert_per_input']
                    if 'operator_profile' in cfg:
                        table={(r['operator'],tuple(r['shape'])):r['duration_ns'] for r in cfg['operator_profile']['rows']}
                        contexts=[t['token_index']+1 for t in cfg['tokens'] if t['src_rank']==rank]
                        counts=[experts[e] for e,owner in enumerate(cfg['expert_placement']) if owner==rank]
                        if contexts:prepare+=table['attention_decode',tuple(contexts)]
                        expert=table['expert_ffn',tuple(counts)] if any(counts) else 0
                    for phase,duration in [('prepare',prepare),('expert',expert)]:
                        if not duration:assert ph[phase] is None
                        else:
                            event=events[rank,ph[phase]];assert event['end_ns']-event['start_ns']==duration
                            compute.append(dict(rank=rank,layer=lid,phase=phase,duration_ns=duration))
                    for node in (n for n in graph['nodes'] if n['layer_id']==lid):
                        event=events[rank,node['node_id']]
                        assert all(event['start_ns']>=events[rank,d]['end_ns'] for d in node['deps'])
        assert set(consumed)==expected and set(consumed.values())=={1}
        assert front['route_records_consumed']==front['route_records_expected']==len(expected)
        assert sum(t['bytes'] for t in actual_writes.values())==total
        assert all(r[k]==0 for r in execution['rank_resources'] for k in ('compute_slots','send_slots','pending_events','pending_tasks','stale_callbacks'))
        if observed is not None:
            audit_queue(path/'execution',bandwidth_gbps=rate,native_directory=path/'execution/runlog' if scenario else None)
        if observed:
            assert sum(t['business_payload_bytes'] for t in rows(path/'execution/truth/transfer_registry.jsonl'))==total
        cases[label]=dict(finished_ns=front['finished_ns'],waves=len(front['waves']),writes=len(actual_writes),
            business_bytes=total,route_records=len(expected),completed=sorted(completed),censored=sorted(censored),compute=compute)
        return front,execution,path
    def run(label,c=base,l=layers,observed=True,net=network,rate=10,max_ns=1000000):
        invoke(label,command(label,c,l,observed,net,max_ns));return audit(label,c,out/label,observed,rate)
    reference=deepcopy(base);reference['compute_profiles']=[];run('reference',reference)
    primary=run('profile-test')
    assert cases['profile-test']['business_bytes']==31744 and cases['profile-test']['route_records']==36
    assert cases['profile-test']['finished_ns']!=cases['reference']['finished_ns']
    for label,mode in [('source-off',False),('no-observation',None)]:
        other=run(label,observed=mode);assert other[:2]==primary[:2]
        assert sha(other[2]/'protocol.jsonl')==sha(primary[2]/'protocol.jsonl')
        if mode is False:
            for p in (primary[2]/'execution/observed').iterdir():assert sha(p)==sha(other[2]/'execution/observed'/p.name)
    c=deepcopy(base);c['requests'][0]['target_total_tokens']=100;c['requests'][1]['target_total_tokens']=7
    run('known-target',c);assert cases['known-target']['completed']==[202]
    c=deepcopy(base);c['service']['groups'][0]['memory']['ranks'][0]['device_bytes']-=8192
    waited=run('kv-wait',c);assert any(e.get('reason')=='kv_capacity' for e in waited[0]['memory_events'])
    c,l=deepcopy(base),deepcopy(layers);c['service']['groups'][1]['scheduling']['max_num_batched_tokens']=1
    for layer in l['groups'][1]['layers']:layer['batch_limits']['max_num_batched_tokens']=1
    limited=run('token-budget',c,l)
    event=next(e for e in limited[0]['batch_events'] if e['event']=='scheduled' and e['rank_id']==20)
    assert event['request_ids']==[401] and event['resident_request_ids']==[401,402]
    slow=out/'slow-network';shutil.copytree(network,slow)
    p=slow/'topology.csv';p.write_text(p.read_text().replace('10Gbps','1Gbps'))
    slower=run('slow',net=slow,rate=1)
    assert cases['slow']['finished_ns']>cases['profile-test']['finished_ns']
    for g in ('ep0','ep1'):
        assert [w for w in slower[0]['waves'] if w['group_id']==g][1]['submitted_ns']>[w for w in primary[0]['waves'] if w['group_id']==g][1]['submitted_ns']
    c=deepcopy(base);c['requests'][0]['decode_steps']=1;run('short-window',c)
    scenario=yaml.safe_load((root/'fixtures/m6_scenario_backend/reference.yaml').read_text())
    scenario.update(service=str(save(out/'scenario-service.json',base)),layer=str(save(out/'scenario-layers.json',layers)),system=str(system))
    scenario['backend']['reference_file']=str(root/'docs/scenarios/cm384_deepseek_r1_decode_v0.1.yaml')
    scenario['network']['case_path']=str(network);scenario['network']['defaults']['rate_bps']=400000000000
    scenario['network']['links']=[dict(src=8,src_port=4,dst=9,dst_port=4,rate_bps=10000000000,delay_ns=20)]
    scenario['execution'].update(chunk_bytes=256,send_slots_per_rank=2)
    scenario['observation']=read(root/'fixtures/m3_shared_bottleneck/observation.json')
    (out/'scenario.yaml').write_text(yaml.safe_dump(scenario))
    invoke('scenario',[sys.executable,'-m','moe_ub_cosim.scenario','run','--input',out/'scenario.yaml','--backend',binary,'--output-dir',out/'scenario'])
    audit('scenario',base,out/'scenario/run',scenario=True)
    def compare_expert_distribution(label,profile):
        # Keep destinations, bytes, context and total expert inputs identical.
        # Only the distribution across the two experts on rank 2 changes.
        pair=[]
        for name,choices in [('concentrated',[[0,2]]*4),('spread',[[0,2]]*2+[[1,2]]*2)]:
            layer=deepcopy(layers['groups'][0]['layers'][0])
            layer['operator_profile']=profile;layer['expert_placement']=[2,2,7,12]
            layer['tokens']=[dict(request_id=str(i),src_rank=r,phase='decode',token_index=2,
                layer_id=layer['layer_id'],expert_ids=e) for i,(r,e) in enumerate(zip([2,2,12,12],choices))]
            prefix=label+'-'+name;input_path=save(out/(prefix+'.json'),layer)
            graph=out/(prefix+'-graph');result_dir=out/prefix
            invoke(prefix+'-generate',[sys.executable,'-m','moe_ub_cosim.generate','--input',input_path,'--output-dir',graph])
            invoke(prefix,[binary.parent/'ns3.44-cosim-layer',graph,system,network,'-',result_dir])
            plan=read(graph/'plan.json');result=read(result_dir/'result.json')
            assert len(result['graph_completions'])==3 and result['transfers']
            assert all(r['finished'] and not r['remaining_nodes'] and
                       all(r[k]==0 for k in ('compute_slots','send_slots','pending_events','pending_tasks'))
                       for r in result['rank_resources'])
            rank=next(g for g in plan['rank_graphs'] if g['rank_id']==2)
            node=rank['phase_nodes']['expert']
            event=next(e for e in result['node_execution'] if e['rank_id']==2 and e['node_id']==node)
            shape=[4,0] if name=='concentrated' else [2,2]
            duration=next(r['duration_ns'] for r in profile['rows'] if r['operator']=='expert_ffn' and r['shape']==shape)
            assert event['end_ns']-event['start_ns']==duration
            matrix=Counter()
            for t in plan['transfers']:matrix[t['kind'],t['src_rank'],t['dst_rank']]+=t['business_payload_bytes']
            pair.append((matrix,duration))
            cases[prefix]=dict(shape=shape,expert_duration_ns=duration,expert_inputs=4,
                               business_bytes=sum(matrix.values()),basis=profile['basis'])
        assert pair[0][0]==pair[1][0] and pair[0][1]!=pair[1][1]
    compare_expert_distribution('distribution-test',read(root/'fixtures/m9_evidence/profile-test.json'))
    if args.measured_profile:
        profile=read(args.measured_profile);assert profile['basis']=='measured_operator' and 'A100' in profile['device']['name']
        c=deepcopy(base)
        for entry in c['compute_profiles']:entry['file']=ref(args.measured_profile)
        run('a100',c,max_ns=10000000)
        table={(r['operator'],tuple(r['shape'])):r['duration_ns'] for r in profile['rows']}
        assert table['expert_ffn',(4,0)]!=table['expert_ffn',(2,2)]
        compare_expert_distribution('distribution-a100',profile)
    for label,mutation,fragment in [
        ('hash',lambda c:c['route_manifest'].update(sha256='0'*64),'SHA256'),
        ('range',lambda c:c['requests'][0].update(decode_steps=4),'observation steps'),
        ('target',lambda c:c['requests'][0].update(target_total_tokens=5),'declared total target'),
        ('profile-hash',lambda c:c['compute_profiles'][0]['file'].update(sha256='0'*64),'SHA256')]:
        c=deepcopy(base);mutation(c);p=invoke('reject-'+label,command('reject-'+label,c,layers),1)
        assert fragment in p.stderr and not (out/('reject-'+label)).exists();failures[label]=fragment
    commit=rows(out/'profile-test/protocol.jsonl')[1]
    for label,fragment in [('node-duration','compute phase'),('evaluation','profile evaluation'),('statistic','statistic'),('missing-shape','outside measured domain')]:
        shared=out/('bad-'+label);shared.mkdir()
        shutil.copytree(out/'profile-test/bootstrap',shared/'bootstrap');shutil.copytree(out/'profile-test/graphs/g0',shared/'graphs/g0')
        shutil.copyfile(out/'profile-test/deployment.json',shared/'deployment.json')
        p=read(shared/'graphs/g0/plan.json');child=p['layers'][0]
        if label=='node-duration':child['rank_graphs'][0]['nodes'][0]['duration_ns']+=1
        if label=='evaluation':child['compute_evaluation']['ranks'][0]['operators']['attention_decode']['duration_ns']+=1
        if label=='statistic':child['input']['operator_profile']['rows'][0]['duration_ns']+=1
        if label=='missing-shape':
            index=child['compute_evaluation']['ranks'][0]['operators']['attention_decode']['row_index']
            child['input']['operator_profile']['rows'].pop(index)
        save(shared/'graphs/g0/plan.json',p)
        result=invoke('reject-'+label,[binary,base['run_id'],shared,system,network,'-',shared/'execution',1000000],1,json.dumps(commit)+'\n')
        assert fragment in result.stderr.lower(),(label,result.stderr)
        failure=read(shared/'execution/failure.json');assert failure['now_ns']==0 and not failure['transfers']
        failures[label]=fragment
    save(out/'summary.json',dict(passed=True,cases=cases,failures=failures,
        measured_profile=ref(args.measured_profile) if args.measured_profile else None,
        source_hashes={str(p.relative_to(root)):sha(p) for folder in ('backend','python/moe_ub_cosim','fixtures/m9_evidence','tests')
                       for p in (root/folder).rglob('*') if p.is_file() and p.suffix not in ('.pyc','.md')}))
    print('PASS: actual censored windows, exact profile durations, route sources, KV and UB feedback')


if __name__=='__main__':main()
