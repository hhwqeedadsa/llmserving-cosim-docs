"""Verify acquired R1 routes and replay a small routed-layer fragment on real UB.

The fixed-batch protocol driver is a test fixture, not an R1 Scheduler or memory
model. MLA, shared experts, full-model latency and capacity are not claimed.
Restricted files and generated route records remain outside Git.
"""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import selectors
import shutil
import subprocess
import sys

from moe_ub_cosim.composed_service import static_layer
from moe_ub_cosim.generate import export_bootstrap, export_plan
from moe_ub_cosim.multilayer import build_multilayer
from moe_ub_cosim.plan import RunIdentities
from moe_ub_cosim.route_evidence import read_json, select_routes
from moe_ub_cosim.service import read_line
from check_shared_bottleneck import audit_queue


def read(path): return json.loads(Path(path).read_text())
def save(path,value): Path(path).write_text(json.dumps(value,indent=2)+'\n')
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--selection',type=Path,default=Path('fixtures/m9_evidence/published-r1-selection.json'))
    args=parser.parse_args();root=Path(__file__).resolve().parents[2];out=args.output_dir.resolve()
    out.mkdir(parents=True,exist_ok=False)
    selection=read(args.selection);assert selection['schema_version']=='moe-ub-published-route-check/1'
    _,manifest=read_json(selection['manifest'])
    _,model=read_json({k:selection['structural_config'][k] for k in ('path','sha256')})
    assert manifest['source']['kind']=='published_model_trace'
    assert manifest['source']['precision']=='awq_4bit'
    assert manifest['source']['collector_model_revision'] is manifest['source']['runtime_revision'] is None
    assert (model['hidden_size'],model['num_hidden_layers'],model['first_k_dense_replace'],
            model['n_routed_experts'],model['num_experts_per_tok'])==(7168,61,3,256,8)
    inventory=[dict(layer_id=i,kind='dense' if i<3 else 'moe',num_experts=0 if i<3 else 256,
                    top_k=0 if i<3 else 8) for i in range(61)]
    assert manifest['layers']==inventory and manifest['model']['hidden_dim']==7168
    structure=dict(name=manifest['model']['name'],hidden_size=7168,layers=inventory)
    requested=selection['requests'];selected=selection['layer_ids']
    assert len(requested)==2 and selected==[3,4,5] and all(r['decode_steps']==2 and r['target_total_tokens'] is None for r in requested)
    normalized,records,evidence=select_routes(selection['manifest'],requested,[structure],selected)
    assert not evidence['route_data_is_synthetic'] and not evidence['infer_eos_from_file_end']
    assert not evidence['target_precision_match']
    raw={f['file_id']:read(root/f['file']['path']) for f in manifest['files']}
    files={f['file_id']:f for f in manifest['files']}
    # Independent source scan: no importer output is used as the expected route.
    expected_all={}
    for file_id,entries in raw.items():
        assert len(entries)==129
        prompt=len(entries[0]['3'])
        for entry,source in enumerate(entries):
            assert set(source)=={str(i) for i in range(61)}
            for lid in range(61):
                if lid<3:
                    assert source[str(lid)] is None;continue
                choices=source[str(lid)]
                assert len(choices)==(prompt if entry==0 else 1)
                assert all(len(row)==len(set(row))==8 and all(type(e) is int and 0<=e<256 for e in row) for row in choices)
                if entry: expected_all[file_id,entry,lid]=(prompt+entry-1,choices[0])
    commands=[]
    def record_command(label,command,started,code):
        commands.append(dict(label=label,argv=[str(v) for v in command],cwd=str(root),
            started_utc=started,ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=code))
        save(out/'commands.json',commands)
    command=[sys.executable,'-m','moe_ub_cosim.route_evidence','--manifest',str(root/selection['manifest']['path']),
             '--output-dir',str(out/'import')]
    started=datetime.now(timezone.utc).isoformat()
    p=subprocess.run(command,cwd=root,capture_output=True,text=True,timeout=60)
    (out/'import.stdout.log').write_text(p.stdout);(out/'import.stderr.log').write_text(p.stderr)
    record_command('import',command,started,p.returncode);assert p.returncode==0,p.stderr
    actual={}
    for line in (out/'import/normalized_decode.jsonl').read_text().splitlines():
        row=json.loads(line);key=row['file_id'],row['source_entry'],row['layer_id']
        assert key not in actual and row['decode_index']==row['source_entry']-1 and row['source_token_row']==0
        assert row['source_file']==files[row['file_id']]['file']
        actual[key]=row['token_index'],row['expert_ids']
    assert actual==expected_all and len(actual)==14848
    expected={}
    for r in requested:
        prompt=len(raw[r['file_id']][0]['3'])
        for entry in range(r['first_decode_entry'],r['first_decode_entry']+2):
            for lid in selected:
                expected[str(r['request_id']),prompt+entry-1,lid]=raw[r['file_id']][entry][str(lid)][0]
    assert set(records)==set(expected) and all(records[k]['expert_ids']==v for k,v in expected.items())

    template=read(root/'fixtures/m8_composed_service/layers.json')['groups'][0]['layers'][0]
    ranks=template['rank_ids'];templates=[]
    for lid in selected:
        c=deepcopy(template)
        c.update(layer_id=lid,hidden_dim=7168,top_k=8,chunk_bytes=4096,
                 expert_placement=[ranks[(e+lid-3)%len(ranks)] for e in range(256)])
        templates.append(c)
    group={k:deepcopy(templates[0][k]) for k in ('group_id','rank_ids','rank_to_ub_node','expert_placement','deployment','batch_limits')}
    group['layer_configs']=[static_layer(c) for c in templates]
    binary=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    system=root/'fixtures/m1_compute/system.json';network=root/'fixtures/m3_shared_bottleneck/network'
    cases={};runtime={}
    for label,observed,rate in [('on',True,10),('off',False,10),('absent',None,10),('slow',True,1)]:
        path=out/label;path.mkdir();(path/'graphs').mkdir()
        export_bootstrap(path/'bootstrap',ranks)
        save(path/'deployment.json',dict(schema_version='moe-ub-service-deployment/4',groups=[group]))
        net=network
        if rate==1:
            net=path/'network';shutil.copytree(network,net)
            file=net/'topology.csv';file.write_text(file.read_text().replace('10Gbps','1Gbps'))
        observation='-'
        if observed is not None:
            obs=read(root/'fixtures/m3_shared_bottleneck/observation.json')
            obs.update(record_provenance=observed,window_end_ns=10000000)
            observation=path/'observation.json';save(observation,obs)
        run_id='m9-published-r1-fragment'
        command=[str(x) for x in (binary,run_id,path,system,net,observation,path/'execution',10000000)]
        identities=RunIdentities();plans=[];transcript=[];consumed=set();starts=[]
        started=datetime.now(timezone.utc).isoformat();child=None
        try:
            with (path/'backend.stderr.log').open('w') as error:
                child=subprocess.Popen(command,cwd=root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=error,bufsize=0)
                with selectors.DefaultSelector() as selector:
                    selector.register(child.stdout,selectors.EVENT_READ);buffer=bytearray()
                    for step in range(3):
                        msg=json.loads(read_line(child,selector,buffer,60));transcript.append(msg)
                        assert msg['type']=='DECIDE' and msg['decision_id']==step and msg['run_id']==run_id
                        assert msg['available_rank_ids']==ranks
                        now=msg['now_ns'];starts.append(now)
                        assert msg['completed']==([] if step==0 else [dict(kind='WAVE_DONE',graph_id=f'g{step-1}',wave_id=f'D.w{step-1}',finished_ns=now)])
                        reply=dict(schema_version='cosim/1',type='COMMIT',run_id=run_id,decision_id=step,now_ns=now,
                                   submissions=[],next_wakeup_ns=None,input_exhausted=True,service_finished=step==2)
                        if step<2:
                            configs=[];provenance=[]
                            for t in templates:
                                c=deepcopy(t);c.update(graph_id=f'g{step}',wave_id=f'D.w{step}',ready_ns=now,tokens=[])
                                source=[]
                                for r in normalized:
                                    index=r['prompt_tokens']+r['generated_tokens']-1+step
                                    key=str(r['request_id']),index,c['layer_id']
                                    assert key not in consumed;consumed.add(key)
                                    c['tokens'].append(dict(request_id=key[0],src_rank=r['rank_id'],phase='decode',
                                        token_index=index,layer_id=c['layer_id'],expert_ids=r['decode_routes'][step][str(c['layer_id'])]))
                                    source.append(records[key])
                                configs.append(c);provenance.append(source)
                            plan=build_multilayer(configs,identities,provenance)
                            for c in plan['layers']:c['route_source']='published_model_trace'
                            plan.update(route_source='published_model_trace',input_provenance=evidence)
                            export_plan(plan,path/'graphs'/f'g{step}');plans.append(plan)
                            reply['submissions']=[dict(graph_id=f'g{step}',wave_id=f'D.w{step}',rank_ids=ranks,
                                workload_prefix=f'graphs/g{step}/layer',semantic_manifest=f'graphs/g{step}/plan.json')]
                        transcript.append(reply)
                        (path/'protocol.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in transcript))
                        child.stdin.write((json.dumps(reply)+'\n').encode());child.stdin.flush()
                    child.stdin.close();code=child.wait(timeout=60)
                    assert not buffer and child.stdout.read()==b'' and code==0,(label,code)
        finally:
            if child is not None and child.poll() is None:child.kill();child.wait()
            record_command(label,command,started,None if child is None else child.returncode)
        assert consumed==set(expected)
        result=read(path/'execution/result.json')
        assert result['normal_exit'] and result['network_initializations']==1 and result['network_drain']['idle']
        assert all(b['same_network_objects'] for b in result['boundary_resources'])
        assert all(all(r[k]==0 for k in ('compute_slots','send_slots','pending_events','pending_tasks','stale_callbacks')) for r in result['rank_resources'])
        assert len(result['graph_completions'])==6
        transfers={t['transfer_id']:t for t in result['transfers']};bytes_expected=0
        for step,plan in enumerate(plans):
            graph=plan['graph_id'];events={(e['rank_id'],e['node_id']):e for e in result['node_execution'] if e['graph_id']==graph}
            assert starts[step+1]==max(e['time_ns'] for e in result['graph_completions'] if e['graph_id']==graph)
            for child in plan['layers']:
                c=child['input'];matrix=Counter();experts=Counter()
                for token,source in zip(c['tokens'],child['input_provenance']['route_records']):
                    key=token['request_id'],token['token_index'],c['layer_id']
                    assert token['expert_ids']==expected[key]==source['expert_ids']
                    experts.update(c['expert_placement'][e] for e in expected[key])
                    for dst in {c['expert_placement'][e] for e in expected[key]}-{token['src_rank']}:
                        for kind,a,b in [('dispatch',token['src_rank'],dst),('combine',dst,token['src_rank'])]:
                            matrix[kind,a,b]+=7168*2
                assert child['expert_input_counts']==[experts[r] for r in ranks]
                actual=Counter()
                for t in child['transfers']:actual[t['kind'],t['src_rank'],t['dst_rank']]+=t['business_payload_bytes']
                assert actual==matrix
            for g in plan['rank_graphs']:
                for node in g['nodes']:
                    e=events[g['rank_id'],node['node_id']]
                    assert all(e['start_ns']>=events[g['rank_id'],d]['end_ns'] for d in node['deps'])
                    if node['kind']=='COMP':assert e['end_ns']-e['start_ns']==node['duration_ns']
            for t in plan['transfers']:
                if 'ub_task_id' not in t:continue
                actual=transfers[t['transfer_id']];bytes_expected+=t['business_payload_bytes']
                assert actual['bytes']==actual['committed_bytes']==t['business_payload_bytes']
                assert actual['submitted_tasks']==actual['send_callbacks']==actual['receive_callbacks']==1
                assert actual['data_ready_ns']<actual['transaction_done_ns']==actual['send_done_ns']
        assert bytes_expected==sum(t['bytes'] for t in result['transfers'])
        if observed is not None:audit_queue(path/'execution',bandwidth_gbps=rate)
        runtime[label]=(result,transcript)
        cases[label]=dict(route_records=len(consumed),expert_inputs=len(consumed)*8,iteration_boundaries_ns=starts,
            writes=len(transfers),fabric_business_bytes=bytes_expected,
            local_deliveries=len(result['local_deliveries']),request_targets=[None,None],eos_inferred=False)
    assert runtime['on']==runtime['off']==runtime['absent']
    for path in (out/'on/execution/observed').iterdir():assert sha(path)==sha(out/'off/execution/observed'/path.name)
    assert cases['slow']['iteration_boundaries_ns'][1]>cases['on']['iteration_boundaries_ns'][1]
    assert cases['slow']['iteration_boundaries_ns'][2]>cases['on']['iteration_boundaries_ns'][2]
    rejected=[]
    for label,requests,model_ref in [
        ('reused-file',[requested[0],dict(requested[1],file_id=requested[0]['file_id'])],structure),
        ('range',[dict(requested[0],first_decode_entry=128)],structure),
        ('model',requested,dict(structure,hidden_size=256))]:
        try:select_routes(selection['manifest'],requests,[model_ref],selected)
        except ValueError:rejected.append(label)
        else:raise AssertionError('Accepted '+label)
    summary=dict(passed=True,selection_sha256=sha(args.selection),imported_decode_layer_records=len(expected_all),
        source_evidence=evidence,cases=cases,rejected=rejected,
        scope=selection['scope'],native_scheduler_and_KV_in_this_replay=False,
        capacity_and_observation_service_validation='check_evidence_service.py',
        full_R1_execution_validated=False,target_hardware_conditions_validated=False,calibration_status='unvalidated_reference')
    save(out/'summary.json',summary)
    print('PASS: 14848 real source records, 12 selected records, four actual UB replays; no full R1 execution claim')


if __name__=='__main__':main()
