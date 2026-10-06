"""Prepare the M16 native target from the hash-checked M10 first-entry bundle.

No downloads, model execution or simulation. Preserve all source tokens, costs,
layers and physical placement; rebuild graphs after the explicit native mapping.
Run through host_probe. The prefix is for resource measurement, never acceptance.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from moe_ub_cosim.full_model import model_structure
from moe_ub_cosim.plan_io import file_sha256
from moe_ub_cosim.resources import full_usage,check_graph
from moe_ub_cosim.route_evidence import read_json


def save(path,value):path.write_text(json.dumps(value,separators=(',',':'))+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepared',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--prefix-moe-blocks',type=int,choices=range(1,59),default=1,
                   help='Leading MoE blocks in the explicitly partial resource measurement (default: 1)')
    a=p.parse_args();out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    prepared=json.loads(a.prepared.read_text());_,bundle=read_json(prepared['bundle'])
    assert len(bundle['configs'])==58 and len(bundle['model']['layers'])==61
    assert prepared['source_files']==1024 and prepared['selected_records']==59392 and prepared['source_entry']==1
    assert prepared['expected_usage']==full_usage(bundle['configs'],bundle['model'])
    budget=dict(schema_version='moe-ub-graph-resources/1',max_graph_transfers=5000000,max_graph_nodes=10000000,
        max_plan_bytes=16*2**30,max_run_graphs=1,max_run_transfers=5000000,max_run_nodes=10000000)
    for c in bundle['configs']:
        assert c['template']=='cann_a3_v850_fullmesh_v1' and len(c['rank_ids'])==128 and len(c['tokens'])==1024
        c.update(template='cann_a3_v850_native_v1',native_profile=dict(dispatch='STORE',combine='LOAD',ready_notice_bytes=32),resource_budget=budget)
    expected=check_graph(full_usage(bundle['configs'],bundle['model']),budget)
    assert expected['fabric_transfers']==prepared['expected_usage']['fabric_transfers']+2*prepared['independent_counts']['cross_chip_contributions']
    assert expected['local_transfers']==prepared['expected_usage']['local_transfers']
    bundle['source_evidence']['native_mapping']=dict(dispatch='STORE',combine='LOAD',ready_notice_bytes=32,
        original_prepared=dict(path=str(a.prepared.resolve()),sha256=file_sha256(a.prepared)),
        graph_changes='native request direction, data-ready dependencies and real readiness messages; regeneration required')
    for label,value in [('full',bundle),('prefix',deepcopy(bundle))]:
        if label=='prefix':
            value['configs']=value['configs'][:a.prefix_moe_blocks];value['provenance']=value['provenance'][:a.prefix_moe_blocks]
            value['model']['layers']=value['model']['layers'][:3+a.prefix_moe_blocks];value['profile']['layers']=value['profile']['layers'][:3+a.prefix_moe_blocks]
            value['profile']['model_structure']=model_structure(value['model'])
            value['source_evidence']['resource_prefix_only']=True
        slot=out/label;slot.mkdir();save(slot/'bundle.json',value)
        save(slot/'prepared.json',dict(schema_version='moe-ub-full-round-prepared/1',
            bundle=dict(path=str(slot/'bundle.json'),sha256=file_sha256(slot/'bundle.json')),
            expected_usage=check_graph(full_usage(value['configs'],value['model']),budget)))
    save(out/'summary.json',dict(passed=True,simulation_executed=False,expected_usage=expected,
        original_prepared=str(a.prepared.resolve()),original_bundle=prepared['bundle'],
        selected_source_files=1024,selected_records=59392,full_blocks=61,active_ranks=128,
        business_bytes_unchanged=prepared['independent_counts']['fabric_business_bytes'],
        target_hardware_calibrated=False,budget=budget))
    print(json.dumps(expected))


if __name__=='__main__':main()
