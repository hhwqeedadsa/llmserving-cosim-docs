"""Budgeted M16 execution of a rebuilt, frozen full-model graph (or resource prefix).

Uses the production cosim/1 replay driver, one shared network and actual native
callbacks. No historical packet timestamps, scheduler calls or graph mutation.
Launch through host_probe; archive/import commands remain separate operations.
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess

from moe_ub_cosim.frozen_replay import run
from moe_ub_cosim.generate import export_bootstrap
from moe_ub_cosim.plan_io import file_sha256
from moe_ub_cosim.replay_catalog import rebind
from moe_ub_cosim.route_evidence import read_json
from moe_ub_cosim.user_io import read,save,inventory


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('prepared','graph','backend','baseline-network','config','output-dir'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();root=Path(__file__).resolve().parents[1];out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    settings=read(a.config);prepared=read(a.prepared);_,bundle=read_json(prepared['bundle']);first=bundle['configs'][0]
    binary=a.backend.resolve();network=out/'inputs/network';network.parent.mkdir();shutil.copytree(a.baseline_network,network)
    backend=read(network/'backend.json');catalog=subprocess.run([str(binary),'--backend-catalog'],capture_output=True,text=True,check=True)
    migration=rebind(backend,json.loads(catalog.stdout));backend['scenario_id']='m16-native-target'
    reference=__import__('yaml').safe_load((root/'examples/native_first_round/backend-reference.yaml').read_text())
    profiles=reference['ns3_backend']['transaction_profiles'];backend['profiles']=dict(dispatch=profiles['native_store'],combine=profiles['native_load'])
    for key,value in settings['native_attributes'].items():
        assert key in backend['attributes'];backend['attributes'][key]=value
        backend['origins']['attributes'][key].append(dict(source='m16_explicit_native_reference',value=value))
    assert backend['attributes']['ns3::UbSwitch::FlowControl']=='CBFC'
    assert backend['execution']==dict(chunk_bytes=8192,send_slots_per_rank=1,seed=1,run=1)
    save(network/'backend.json',backend)
    save(out/'network-input.json',dict(source=str(a.baseline_network.resolve()),inventory=inventory(a.baseline_network),catalog_migration=migration,
        new_profiles=backend['profiles'],native_attributes=settings['native_attributes']))
    shutil.copyfile(root/'fixtures/m1_compute/system.json',out/'inputs/system.json')
    shared=out/'shared';shared.mkdir();(shared/'graph').mkdir()
    links=[]
    for source in sorted(a.graph.resolve().rglob('*')):
        assert not source.is_symlink();target=shared/'graph'/source.relative_to(a.graph.resolve())
        if source.is_dir():target.mkdir()
        else:
            assert source.is_file();os.link(source,target)
            links.append(dict(source=str(source),target=str(target),bytes=source.stat().st_size))
    save(out/'graph-links.json',links)
    ranks=first['rank_ids'];group={k:deepcopy(first[k]) for k in ('group_id','rank_ids','rank_to_ub_node','expert_placement','deployment','batch_limits')}
    group.update(layer_configs=[{k:deepcopy(v) for k,v in c.items() if k not in ('graph_id','wave_id','ready_ns','tokens')} for c in bundle['configs']],
        model_spec=bundle['model'],full_model_profile=bundle['profile'])
    save(shared/'deployment.json',dict(schema_version='moe-ub-service-deployment/5',groups=[group]))
    save(shared/'record-storage.json',settings['record_policy']);export_bootstrap(shared/'bootstrap',ranks)
    observation=settings['observation'];assert len(observation['switches'])==21
    manifest=dict(run_id='m16-native-first-round',capture_end_ns=settings['max_simulation_ns'],ranks=ranks,observation=observation,
        random=dict(seed=1,run=1),submissions=[dict(graph_id=first['graph_id'],wave_id=first['wave_id'],rank_ids=ranks,
            workload_prefix='graph/layer',semantic_manifest='graph/plan.json')])
    save(out/'driver-input.json',manifest);save(out/'observation.json',observation)
    scope=dict(kind='rebuilt_native_first_graph',prepared=dict(path=str(a.prepared.resolve()),sha256=file_sha256(a.prepared)),
        configured_service_executed=False,target_64_steps_completed=False,blocks=len(bundle['model']['layers']),
        resource_prefix_only=len(bundle['model']['layers'])!=61,active_ranks=len(ranks),requests=len(first['tokens']),
        expected_usage=prepared['expected_usage'],source_evidence=bundle['source_evidence'],selected_switch_ports=42)
    save(out/'scope.json',scope);del bundle
    config=dict(backend=str(binary),backend_timeout_seconds=settings['backend_timeout_seconds'])
    target=out/'actual-inputs';target.mkdir()
    result=run(out,manifest,shared,out/'raw',target,config)
    for name in ('deployment.json','record-storage.json'):
        assert read(shared/name)==read(out/'raw'/name)
    expected=prepared['expected_usage'];fields=result['record_storage']['fields'];life=result['resource_lifecycle']
    assert result['network_initializations']==1 and result['normal_exit'] and result['network_drain']['idle']
    assert fields['transfers']['records']==expected['fabric_transfers'] and fields['node_execution']['records']==expected['nodes']
    assert fields['local_deliveries']['records']==expected['local_transfers']
    assert life['resident_graphs_at_exit']==life['resident_transfers_at_exit']==life['adapter_transfers_at_exit']==0
    assert life['provenance']['resident_packets']==life['provenance']['resident_transfers']==life['provenance']['pending_native_requests']==0
    assert all(r[k]==0 for r in result['rank_resources'] for k in ('compute_slots','send_slots','pending_events','pending_tasks','stale_callbacks'))
    save(out/'result.json',dict(passed=True,scope=scope,simulation_end_ns=result['simulation_end_ns'],network_drain=result['network_drain'],
        record_counts={k:v['records'] for k,v in fields.items()},provenance=life['provenance'],record_bytes=result['record_storage']['compressed_bytes']))
    print('PASS real native graph execution, unique records, one network and resource retirement')


if __name__=='__main__':main()
