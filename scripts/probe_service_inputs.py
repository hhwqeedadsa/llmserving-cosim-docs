"""Validate the complete fixed target service input, without running its network."""
import argparse
from copy import deepcopy
import gc
import json
from pathlib import Path
import time

from moe_ub_cosim.full_model_service import FullModelEvidenceService
from moe_ub_cosim.full_model import model_structure
from moe_ub_cosim.host_preflight import target_inputs
from moe_ub_cosim.route_evidence import read_json
from moe_ub_cosim.plan_io import file_sha256
from moe_ub_cosim.generate import export_bootstrap


def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
def rss():
    return int(next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines()
                    if line.startswith('VmRSS:')))*1024


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--communication-reserve',type=Path)
    parser.add_argument('--isolated-composition',action='store_true')
    args=parser.parse_args();out=args.output_dir;out.mkdir(parents=True,exist_ok=False);start=time.monotonic()
    def event(phase,**details):
        row=dict(phase=phase,elapsed_seconds=time.monotonic()-start,rss_bytes=rss(),**details)
        with (out/'phases.jsonl').open('a') as stream:stream.write(json.dumps(row)+'\n')
        print(phase,flush=True)
    prepared=json.loads(args.prepared.read_text());_,bundle=read_json(prepared['bundle'])
    _,host=read_json(prepared['input']['host_input']);values,_=target_inputs(host)
    original=values['capacity']['model_spec'];model=deepcopy(original)
    # Explicitly bind the structural capacity reference to the route identity.
    # This changes only the name used for input compatibility, not dimensions,
    # precision, inventory, costs, calibration or source declarations.
    model['name']=values['manifest']['model']['name']
    assert dict(model,name=original['name'])==original
    profile=deepcopy(bundle['profile']);profile['model_structure']=model_structure(model)
    budget=dict(prepared['input']['graph_budget'],max_run_graphs=64,max_run_transfers=256000000,max_run_nodes=512000000)
    layers=[]
    for original_layer in bundle['configs']:
        layer=deepcopy(original_layer);layer['tokens']=layer['tokens'][:1];layer['resource_budget']=deepcopy(budget)
        layers.append(layer)
    name=layers[0]['group_id'];run='m10-target-resource-inputs'
    group=dict(group_id=name,model_spec=model,memory=deepcopy(values['capacity']['memory']),
        scheduling=deepcopy(layers[0]['batch_limits']),full_model_profile=profile)
    amendment=None
    if args.communication_reserve:
        from moe_ub_cosim.model_capacity import communication_minimum
        amendment=json.loads(args.communication_reserve.read_text())
        assert amendment['schema_version']=='moe-ub-communication-reserve-amendment/1'
        assert len(layers)==amendment['layer_count']
        bounds=[communication_minimum(layer) for layer in layers]
        assert set(bounds)=={amendment['minimum_bytes_per_layer']}
        assert sum(bounds)==amendment['effective_bytes_per_rank']
        for rank in group['memory']['ranks']:
            assert rank['device_bytes']==amendment['device_bytes_per_rank']
            assert rank['communication_buffer_bytes']==amendment['original_bytes_per_rank']
            rank['communication_buffer_bytes']=amendment['effective_bytes_per_rank']
        save(out/'communication-reserve.json',amendment)
    service=dict(schema_version='moe-ub-configured-service/4',run_id=run,mode='fixture',
        execution=dict(entry_mode='service',resource_budget=budget),model=model['name'],groups=[group],requests=[])
    config=dict(schema_version='moe-ub-evidence-service/1',run_id=run,service=service,
        route_manifest=deepcopy(host['manifest']),requests=deepcopy(values['requests']),compute_profiles=[])
    segments=dict(schema_version='moe-ub-layer-segments/1',groups=[dict(group_id=name,layers=layers)])
    save(out/'input.json',config);save(out/'layers.json',segments)
    save(out/'binding.json',dict(schema_version='moe-ub-target-input-binding/1',
        prepared=dict(path=str(args.prepared),sha256=file_sha256(args.prepared)),original_capacity=host['capacity'],
        original_reference_label=original['name'],bound_route_identity=model['name'],
        changed_model_fields=['name'],capacity_numbers_unchanged=amendment is None,
        communication_reserve_amendment=(dict(path=str(args.communication_reserve),
            sha256=file_sha256(args.communication_reserve)) if amendment else None),
        route_precision=values['manifest']['source']['precision'],modeled_precision='declared_INT8_reference',
        precision_equivalence_validated=False,hardware_calibration='unvalidated_reference',
        compute_basis=prepared['compute_basis'],observation_steps=64,simulation_executed=False))
    del bundle;gc.collect();event('service_input_begin')
    base_layer=FullModelEvidenceService.validate_layer;base_segment=FullModelEvidenceService.check_segment
    def layer_check(self,config,layer):
        result=base_layer(self,config,layer);event('layer_inputs_checked',layer_id=layer['layer_id'],requests=len(config['requests']))
        return result
    def segment_check(self,layers,group):
        event('placeholder_full_graph_begin');result=base_segment(self,layers,group)
        event('placeholder_full_graph_end');return result
    FullModelEvidenceService.validate_layer=layer_check;FullModelEvidenceService.check_segment=segment_check
    options={}
    if args.isolated_composition:
        from moe_ub_cosim.graph_storage import GraphStorage
        (out/'graphs').mkdir()
        options['graph_storage']=GraphStorage(out,dict(schema_version='moe-ub-graph-storage/1',
            max_recipe_bytes=64*2**20,max_archive_bytes=128*2**20,max_active_graph_bytes=12*2**30),1800)
    try:instance=FullModelEvidenceService(config,segments,**options)
    finally:
        FullModelEvidenceService.validate_layer=base_layer;FullModelEvidenceService.check_segment=base_segment
    event('service_input_end');gc.collect();event('service_input_after_gc')
    if args.isolated_composition:instance.graph_storage.finish()
    assert len(instance.requests)==1024 and len(instance.expected_routes)==3801088 and not instance.consumed
    save(out/'deployment.json',instance.deployment());save(out/'input_evidence.json',instance.evidence)
    export_bootstrap(out/'bootstrap',instance.ranks)
    save(out/'result.json',dict(passed=True,requests=len(instance.requests),ranks=len(instance.ranks),
        expected_routes=len(instance.expected_routes),consumed_routes=len(instance.consumed),
        model_identity=instance.config['model'],input_seconds=time.monotonic()-start,resident_rss_bytes=rss(),
        source_records=instance.route_records.storage_counts(),all_inputs_validated=True,
        isolated_composition=args.isolated_composition,
        backend_started=False,simulation_executed=False,hardware_calibration='unvalidated_reference'))
    event('complete')


if __name__=='__main__':main()
