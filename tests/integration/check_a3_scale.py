"""Prepare or verify target-scale finite blocks through public platform commands.

This is platform acceptance, not a workload-selection or performance experiment.
No test helper, graph cache or historical resource probe is imported.
"""
import argparse
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import sys
import yaml

from moe_ub_cosim.plan_io import file_sha256
from moe_ub_cosim.resources import full_usage,check_graph
from moe_ub_cosim.record_storage import iter_result
from moe_ub_cosim.user_io import ROOT,read,save


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('prepare','verify'))
    p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--requests',type=int,choices=(128,1024),default=128)
    p.add_argument('--backend',type=Path);p.add_argument('--host-policy',type=Path);p.add_argument('--run',type=Path)
    a=p.parse_args();out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=False);fixture=ROOT/'fixtures/m21_platform'
    if a.action=='verify':
        run=a.run.resolve();source=read(run/'inputs/service.json');layers=read(run/'inputs/layer.json')['groups'][0]['layers']
        proof=read(run/'inputs/preparation.json');model=source['groups'][0]['model_spec'];scope=source['execution']['segment']
        assert model['hidden_size']==7168 and len(model['layers'])==61 and len(source['requests'])==a.requests
        assert (scope['first_layer'],scope['layer_count'],scope['decode_step'])==(3,3,0)
        assert layers[0]['rank_ids']==list(range(128)) and len(layers[0]['expert_placement'])==256 and layers[0]['top_k']==8
        evidence=proof['route_evidence'];assert not evidence['route_data_is_synthetic'] and not evidence['eos_inferred']
        records={(r['request_id'],r['layer_id']):r for r in evidence['records']}
        for request in source['requests']:
            for lid,experts in request['decode_routes'][0].items():
                r=records[str(request['request_id']),int(lid)]
                assert r['source_layer_id']==int(lid) and r['source_entry']==1 and r['expert_ids']==experts
                assert r['token_index']==request['prompt_tokens']
        execution=run/'raw/execution';report=read(execution/'result.json');front=read(run/'raw/service_result.json')
        assert report['normal_exit'] and front['completion_scope']=='selected_segment_only' and front['service_finished']
        assert len(front['waves'])==1 and report['network_drain']['idle'] and report['network_initializations']==1
        assert report['network_drain']['started_packets']==report['network_drain']['arrived_packets']
        assert all(row[k]==0 for row in report['rank_resources'] for k in ('compute_slots','send_slots','pending_events','pending_tasks','stale_callbacks'))
        assert all(not r['request_blocks'] and r['kv_used_bytes']==0 for r in front['memory_final'])
        # Arithmetic independent of generated transfers, using original expert placement and physical chips.
        chips={r['rank_id']:r['chip_id'] for r in layers[0]['deployment']['ranks']};h=7168
        expected_far=expected_near=expected_business=expected_bytes=0
        for layer in layers:
            placement=layer['expert_placement'];lid=str(layer['layer_id'])
            far_status=sum(chips[r]!=chips[dst] for r in layer['rank_ids'] for dst in placement)
            near_status=sum(r!=dst and chips[r]==chips[dst] for r in layer['rank_ids'] for dst in placement)
            far=near=0
            for request in source['requests']:
                r=request['rank_id']
                for expert in request['decode_routes'][0][lid]:
                    dst=placement[expert]
                    if chips[r]!=chips[dst]:far+=1
                    elif r!=dst:near+=1
            expected_far+=far_status+4*far;expected_near+=near_status+3*near
            expected_business+=far*3*h;expected_bytes+=far_status*32+far*(h+44+2*h+32)
        count=total=business=metadata=0
        planned=iter(iter_result(execution,'planned_transfers',report));native=iter(iter_result(execution,'transfers',report));local=iter(iter_result(execution,'local_deliveries',report))
        for t in planned:
            if 'ub_task_id' not in t:
                row=next(local);assert row['transfer_id']==t['transfer_id'] and row['send_callbacks']==row['recv_callbacks']==1;continue
            row=next(native);count+=1;total+=row['bytes'];business+=t['business_payload_bytes'];metadata+=t['application_metadata_bytes']
            assert row['transfer_id']==t['transfer_id'] and row['bytes']==row['committed_bytes']==t['payload_bytes']
            assert row['send_callbacks']==row['receive_callbacks']==row['submitted_tasks']==1 and row['initiator_resources_retired']
            assert row['operation']=='STORE' and row['data_owner_rank']==t['src_rank'] and row['consumer_rank']==t['dst_rank']
            facts=row['local_facts'];assert facts['local_read_complete_ns']<=row['transaction_done_ns']
            assert (facts['business_ready_ns'] is not None)==bool(t['business_payload_bytes'])
            assert (facts['application_metadata_ready_ns'] is not None)==bool(t['application_metadata_bytes'])
        assert next(native,None) is None and next(local,None) is None
        fields=report['record_storage']['fields']
        assert count==expected_far and fields['local_deliveries']['records']==expected_near
        assert total==expected_bytes and business==expected_business
        assert fields['node_execution']['records']==report['graph_resources']['used']['nodes']
        assert fields['native_starts']['records']==fields['native_completions']['records']==count
        save(out/'summary.json',dict(passed=True,requests=a.requests,ranks=128,experts=256,top_k=8,hidden_dim=h,
            original_layers=[3,4,5],full_deployed_blocks=61,simulation_end_ns=report['simulation_end_ns'],
            nodes=fields['node_execution']['records'],fabric_transfers=count,local_deliveries=expected_near,
            fabric_payload_bytes=total,business_payload_bytes=business,application_metadata_bytes=metadata,
            physical_packets=report['network_drain']['started_packets'],route_records=len(records),
            plan_bytes=(run/'raw/graphs/g0/plan.json').stat().st_size,
            trace_dataset_revision=evidence['source']['dataset_revision'],hardware_calibrated=False,
            scope='functional_target_scale_finite_segment_not_performance_experiment'))
        print('PASS: independent target byte/count/identity, capacity scope, native completion, and resource checks');return
    assert a.backend and a.host_policy
    binary=a.backend.resolve();host=read(a.host_policy)
    host['artifact_roots']=[str((ROOT/p).resolve()) for p in host['artifact_roots']]
    commands=[]
    def cli(label,words,module='moe_ub_cosim'):
        cmd=[sys.executable,'-m',module,*map(str,words)];start=datetime.now(timezone.utc).isoformat()
        result=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=1700)
        for stream in ('stdout','stderr'):(out/f'{label}.{stream}.log').write_text(getattr(result,stream))
        commands.append(dict(command=cmd,cwd=str(ROOT),started_utc=start,exit_code=result.returncode,log_prefix=str(out/label)))
        save(out/'commands.json',commands);assert result.returncode==0,(label,result.stderr)
    defaults=dict(rate_bps=400000000000,delay_ns=20,allocation_ns=1,forward_ns=0);save(out/'network-defaults.json',defaults)
    cli('fabric',['--input',ROOT/'fixtures/m10_fabric/target.json','--defaults',out/'network-defaults.json','--output-dir',out/'source-network'],module='moe_ub_cosim.fabric')
    source=read(fixture/'target-source.json');source['requests']=source['requests'][:a.requests];save(out/'source.json',source)
    config=dict(schema_version='moe-ub-segment-source/1',host=host,inputs=dict(service=str(out/'source.json'),
        layer=str(fixture/'target-layers.json'),system=str(ROOT/'examples/a3_segment/system.json'),network=str(out/'source-network')),
        observation=str(fixture/'observation.json'),record_policy=str(fixture/'record-policy.json'),export_policy=str(fixture/'export-policy.json'),
        backend=str(binary),backend_timeout_seconds=1700,max_simulation_ns=1000000000)
    save(out/'source-run.json',config);save(out/'select.json',dict(schema_version='moe-ub-segment-prepare/1',source_run='source-run.json',first_layer=3,layer_count=3,decode_step=0))
    cli('prepare',['segment','prepare','--config',out/'select.json','--output',out/'prepared'])
    service=read(out/'prepared/service.json');layer_inputs=read(out/'prepared/layers.json');configs=deepcopy(layer_inputs['groups'][0]['layers'])
    for c in configs:
        c['tokens']=[dict(request_id=str(r['request_id']),src_rank=r['rank_id'],phase='decode',token_index=r['prompt_tokens'],layer_id=c['layer_id'],expert_ids=r['decode_routes'][0][str(c['layer_id'])]) for r in service['requests']]
    counts=check_graph(full_usage(configs,service['groups'][0]['model_spec'],segment=service['execution']['segment']),service['execution']['resource_budget'])
    scenario=yaml.safe_load((ROOT/'examples/a3_segment/scenario.yaml').read_text())
    scenario.update(scenario_id='m21-a3-scale-'+str(a.requests),service=str(out/'prepared/service.json'),layer=str(out/'prepared/layers.json'),system=str(ROOT/'examples/a3_segment/system.json'),max_simulation_ns=1000000000)
    scenario['network'].update(fabric_file=str(ROOT/'fixtures/m10_fabric/target.json'),defaults=defaults)
    scenario['backend']['reference_file']=str(ROOT/'examples/a3_segment/backend-reference.yaml')
    scenario['backend']['attributes'].update(read(fixture/'input-basis.json')['native_attributes'])
    scenario['execution']['chunk_bytes']=8192
    (out/'scenario.yaml').write_text(yaml.safe_dump(scenario,sort_keys=False))
    cli('resolve',['resolve','--config',out/'scenario.yaml','--backend',binary,'--output',out/'resolved'])
    run=read(out/'prepared/run.json');run['inputs']={k:str(out/'prepared'/v) for k,v in run['inputs'].items()}
    run['inputs']['network']=str(out/'resolved/network')
    for k in ('observation','record_policy','export_policy','input_evidence'):run[k]=str(out/'prepared'/run[k])
    save(out/'run.json',run)
    save(out/'preflight.json',dict(passed=True,requests=a.requests,usage=counts,simulation_executed=False,
        source_manifest=source['route_manifest'],initial_scope=service['execution']['segment'],
        run_config=str(out/'run.json'),host=host,input_sha256={str(f.relative_to(fixture)):file_sha256(f) for f in sorted(fixture.iterdir()) if f.is_file()}))
    print(json.dumps(dict(passed=True,requests=a.requests,usage=counts,run_config=str(out/'run.json'))))


if __name__=='__main__':main()
