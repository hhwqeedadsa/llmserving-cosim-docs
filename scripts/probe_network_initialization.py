"""Initialize the complete target fabric and all egress observers, with zero work."""
import argparse
from copy import deepcopy
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import selectors
import subprocess

from moe_ub_cosim.fabric import generate
from moe_ub_cosim.generate import export_bootstrap
from moe_ub_cosim.observation import backend_observation
from moe_ub_cosim.plan_io import file_sha256
from moe_ub_cosim.scenario import write_network
from moe_ub_cosim.service import read_line
from moe_ub_cosim.route_evidence import read_json


def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--input-dir',type=Path,required=True)
    parser.add_argument('--prepared',type=Path)
    parser.add_argument('--prepared-graph',type=Path)
    parser.add_argument('--execute-first-round',action='store_true',
        help='Execute exactly the frozen first graph using production service; never the 64-step workload')
    args=parser.parse_args();root=Path.cwd();out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    assert (args.prepared is None)==(args.prepared_graph is None)
    preparation=args.prepared_graph is not None
    one_round=args.execute_first_round;assert not one_round or preparation
    max_ns=1000000000 if one_round else 1
    run_id='m10-target-first-round-resource' if one_round else 'm10-network-init'
    binary=args.build_dir.resolve()/('output/cosim/ns3.44-cosim-service-resource-probe' if preparation and not one_round else
                                    'output/cosim/ns3.44-cosim-service')
    spec=read(root/'fixtures/m10_fabric/target.json');defaults=read(root/'fixtures/m10_fabric/defaults.json')
    topology,routes,channels,audit=generate(spec,defaults)
    prior=root/'runs/v03-target-fabric/integration-final/reference/network/backend.json'
    backend=read(prior)
    catalog_command=[str(binary),'--backend-catalog']
    catalog=subprocess.run(catalog_command,capture_output=True,text=True,check=True)
    save(out/'catalog-command.json',dict(argv=catalog_command,cwd=str(root),exit_code=catalog.returncode,
        stdout='catalog.json',stderr=catalog.stderr))
    save(out/'catalog.json',json.loads(catalog.stdout))
    assert backend['catalog']==json.loads(catalog.stdout)
    backend.update(scenario_id='m10-full-network-initialization',fabric=spec,topology=topology,
        execution=dict(chunk_bytes=8192,send_slots_per_rank=1,seed=1,run=1))
    write_network(out/'network',topology,routes,channels,backend)
    save(out/'fabric-audit.json',audit)
    layers=read(args.input_dir/'layers.json')['groups'][0]['layers']
    config=read(args.input_dir/'input.json')['service'];declaration=config['groups'][0]
    if preparation:
        # Keep the checked historical graph's exact model label, costs, budgets
        # and mappings. Capacity admission is measured independently; this probe
        # does not rename or edit the 9 GB plan to pass compatibility checks.
        _,bundle=read_json(read(args.prepared)['bundle'])
        layers=bundle['configs']
        declaration=dict(model_spec=bundle['model'],full_model_profile=bundle['profile'])
    layer=layers[0];ranks=layer['rank_ids'];shared=out/'shared';shared.mkdir()
    group={k:deepcopy(layer[k]) for k in ('group_id','rank_ids','rank_to_ub_node','expert_placement','deployment','batch_limits')}
    group.update(layer_configs=[{k:deepcopy(v) for k,v in c.items() if k not in ('graph_id','wave_id','ready_ns','tokens')} for c in layers],
        model_spec=declaration['model_spec'],full_model_profile=declaration['full_model_profile'])
    save(shared/'deployment.json',dict(schema_version='moe-ub-service-deployment/5',groups=[group]))
    export_bootstrap(shared/'bootstrap',ranks)
    storage=dict(schema_version='moe-ub-record-storage/1',max_compressed_bytes=(3072 if one_round else 2048 if preparation else 128)*2**20,max_buffer_bytes=2**20,max_record_bytes=2**18)
    save(shared/'record-storage.json',storage)
    switches=[node for node in topology['nodes'] if node['kind']=='SWITCH']
    ports=sum(node['ports'] for node in switches);assert ports==16128
    budget=read(root/'fixtures/m10_resources/observation-budget.json')
    budget.update(max_ports=ports,max_total_windows=2*ports,max_registered_transfers=4000000)
    if one_round:budget.update(max_windows_per_port=max_ns//1000,max_total_windows=ports*(max_ns//1000),max_ledger_packets=8000000)
    observation=dict(clocks=[dict(device_type='rank',device_id=r,offset_ns=0,drift_ppb=0) for r in ranks]+
        [dict(device_type='switch',device_id=n['node_id'],offset_ns=0,drift_ppb=0) for n in switches],
        switches=[dict(switch_id=n['node_id'],port_ids=list(range(n['ports'])),start_local_ns=0,end_local_ns=max_ns,period_ns=1000 if one_round else 1) for n in switches],
        record_provenance=True,resource_budget=budget)
    save(out/'observation-input.json',observation)
    save(out/'observation.json',backend_observation(observation,ranks,max_ns))
    if preparation:
        original=args.prepared_graph.resolve();graph=shared/'graph';graph.mkdir()
        links=[]
        for source in sorted(original.rglob('*')):
            assert not source.is_symlink()
            target=graph/source.relative_to(original)
            if source.is_dir():target.mkdir()
            elif source.is_file():
                os.link(source,target);links.append(dict(source=str(source),path=str(target),bytes=source.stat().st_size))
        save(out/'graph-links.json',dict(reason='reuse_registered_first_target_graph_without_copying',files=links))
        # The final original graph was already checked in checkpoint-load. Its
        # complete semantic and Chakra validation runs again in the real loader.
        del bundle
    save(out/'scope.json',dict(kind='frozen_first_graph_resource_execution' if one_round else 'graph_preparation_before_activation' if preparation else 'zero_business_network_initialization',
        complete_service_input_validated=False,capacity_budget_not_used=True,
        model_graph_submission_requested=preparation,business_activation_requested=one_round,
        active_endpoints=len(ranks),inactive_endpoints=audit['counts']['devices']-len(ranks),
        observed_egress_ports=ports,source_backend=dict(path=str(prior),sha256=file_sha256(prior))))
    command=[str(binary),run_id,str(shared),str(root/'fixtures/m1_compute/system.json'),
        str(out/'network'),str(out/'observation.json'),str(out/'execution'),str(max_ns)]
    started=datetime.now(timezone.utc).isoformat();child=None
    try:
        with (out/'backend.stderr.log').open('x') as errors,(out/'protocol.jsonl').open('x') as transcript:
            environment=dict(os.environ)
            if one_round:environment['MOE_UB_HOST_PREPARATION_TRACE']='1'
            child=subprocess.Popen(command,cwd=root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors,bufsize=0,env=environment)
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout,selectors.EVENT_READ);buffer=bytearray()
                line=read_line(child,selector,buffer,1800);transcript.write(line);transcript.flush();message=json.loads(line)
                assert message==dict(schema_version='cosim/1',type='DECIDE',run_id=run_id,decision_id=0,
                    now_ns=0,completed=[],available_rank_ids=ranks)
                response=dict(schema_version='cosim/1',type='COMMIT',run_id=run_id,decision_id=0,
                    now_ns=0,submissions=[],next_wakeup_ns=None,input_exhausted=True,service_finished=True)
                if preparation:
                    response.update(service_finished=False,submissions=[dict(graph_id=layer['graph_id'],
                        wave_id=layer['wave_id'],rank_ids=ranks,workload_prefix='graph/layer',semantic_manifest='graph/plan.json')])
                line=json.dumps(response)+'\n';transcript.write(line);transcript.flush();child.stdin.write(line.encode());child.stdin.flush()
                if one_round:
                    line=read_line(child,selector,buffer,3500);transcript.write(line);transcript.flush();message=json.loads(line)
                    assert message['run_id']==run_id and message['decision_id']==1 and message['available_rank_ids']==ranks
                    assert message['now_ns']>0 and message['completed']==[dict(kind='WAVE_DONE',graph_id=layer['graph_id'],
                        wave_id=layer['wave_id'],finished_ns=message['now_ns'])]
                    response.update(decision_id=1,now_ns=message['now_ns'],submissions=[],service_finished=True)
                    line=json.dumps(response)+'\n';transcript.write(line);transcript.flush();child.stdin.write(line.encode());child.stdin.flush()
                child.stdin.close()
                code=child.wait(timeout=3500)
                assert code==0 and not buffer and child.stdout.read()==b''
        if preparation and not one_round:
            report=read(out/'execution/resource-preparation.json')
            assert report['preparation_completed'] and not report['business_activated'] and report['simulation_ns']==0
            assert report['ranks']==128 and report['fabric_transfers']==3741764 and report['local_transfers']==25930
            assert report['validated_nodes']==7655238 and report['timing_nodes']==0 and report['observed_ports']==16128
            assert not (out/'execution/result.json').exists()
            save(out/'result.json',report)
            return
        report=read(out/'execution/result.json');effective=read(out/'execution/effective_backend.json')
        assert report['normal_exit'] and report['network_drain']['idle'] and report['network_initializations']==1
        assert report['record_storage']['fields']['transfers']['records']==(3741764 if one_round else 0)
        assert len(list((out/'execution/ports').iterdir()))==ports
        assert len(effective['topology']['nodes'])==1216 and effective['fabric_audit']['status']=='matched_before_business_activation'
        if one_round:
            fields=report['record_storage']['fields'];life=report['resource_lifecycle']
            assert fields['node_execution']['records']==7655238 and fields['local_deliveries']['records']==25930
            assert life['resident_graphs_at_exit']==life['resident_transfers_at_exit']==life['adapter_transfers_at_exit']==0
            assert life['retired_graphs']==1 and life['provenance']['resident_packets']==life['provenance']['resident_transfers']==0
            assert all(row['same_network_objects'] for row in report['boundary_resources'])
            assert all(row[k]==0 for row in report['rank_resources'] for k in ('compute_slots','send_slots','pending_events','pending_tasks','stale_callbacks'))
            # This is the protocol driver's completion of one frozen graph,
            # explicitly not configured-service request/64-step completion.
            os.link(shared/'deployment.json',out/'deployment.json')
            save(out/'service_result.json',dict(schema_version='moe-ub-protocol-resource-front/1',service_finished=True,
                scope='one_frozen_target_graph',configured_service_executed=False,target_64_steps_completed=False,
                graph_resources=report['graph_resources']))
        save(out/'result.json',dict(passed=True,network_counts=audit['counts'],observed_ports=ports,
            fabric_audit_status=effective['fabric_audit']['status'],
            fabric_audit_file=dict(path='execution/effective_backend.json',sha256=file_sha256(out/'execution/effective_backend.json')),
            initial_transport_counts=report['initial_tp_counts'],
            compressed_record_bytes=report['record_storage']['compressed_bytes'],business_graphs_executed=int(one_round),
            finish_ns=report.get('service_finished_ns'),target_64_steps_completed=False,
            record_counts={k:v['records'] for k,v in report['record_storage']['fields'].items()},
            complete_target_service_validated=False,hardware_calibration='unvalidated_reference'))
    finally:
        if child is not None:
            if child.poll() is None:child.kill()
            code=child.wait();child.stdout.close()
            if not child.stdin.closed:child.stdin.close()
            save(out/'command.json',dict(argv=command,cwd=str(root),started_utc=started,
                environment_overrides={'MOE_UB_HOST_PREPARATION_TRACE':'1'} if one_round else {},
                ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=code))


if __name__=='__main__':main()
