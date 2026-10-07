"""Evaluation checks that a paired run removes only its declared intervention."""
from copy import deepcopy
import hashlib
import json

from moe_ub_cosim.research_storage import stored_rows

from .common import digest, read, require


def deployment_parts(value):
    value=deepcopy(value); costs={}
    for group in value['groups']:
        for layer in group['layer_configs']:
            declaration=layer.pop('endpoint_prepare',None)
            for rank in layer['rank_ids']:
                selected=[] if declaration is None else [r for r in declaration['ranks'] if r['rank_id']==rank]
                require(len(selected)<=1,'Duplicate endpoint cost rank')
                costs[group['group_id'],layer['layer_id'],rank]=tuple(
                    selected[0][k] if selected else 0 for k in ('dispatch_ns_per_local_token','combine_ns_per_expert_input'))
    return value,costs


def structural_transfers(run):
    sha=hashlib.sha256(); count=0
    for row in stored_rows(run/'truth','transfer_registry.jsonl'):
        row.pop('execution_global_ns')
        sha.update((json.dumps(row,sort_keys=True,separators=(',',':'))+'\n').encode());count+=1
    return count,sha.hexdigest()


def check_pair(run, reference, case):
    manifests=[read(p/'manifest.json') for p in (run,reference)]
    require(manifests[0]['backend_binary']['sha256']==manifests[1]['backend_binary']['sha256'] and
            manifests[0]['software']['source_hashes']==manifests[1]['software']['source_hashes'],
            'Paired platform implementation or backend differs')
    parts=[deployment_parts(read(p/'raw/deployment.json')) for p in (run,reference)]
    require(parts[0][0]==parts[1][0] and parts[0][1].keys()==parts[1][1].keys(),
            'Paired model, placement, costs or deployment changed outside endpoint preparation')
    measured={k:tuple(a-b for a,b in zip(parts[0][1][k],parts[1][1][k])) for k in parts[0][1]
              if parts[0][1][k]!=parts[1][1][k]}
    components=case.get('evaluated_components',['endpoint_prepare','port_service'])
    expected={(parts[0][0]['groups'][0]['group_id'],r['layer_id'],r['rank_id']):
              (r['dispatch_ns_per_local_token'],r['combine_ns_per_expert_input'])
              for r in case['endpoint_prepare']} if 'endpoint_prepare' in components else {}
    expected={k:v for k,v in expected.items() if any(v)}
    require(measured==expected,'Actual endpoint differences do not match the declared comparison')
    backends=[read(p/'inputs/network/backend.json') for p in (run,reference)]
    services=[]
    for backend in backends:
        services.append(backend.pop('port_service',{'intervals':[]}))
        backend.pop('origins',None)
    require(backends[0]==backends[1],'Paired backend parameters changed outside port service')
    require(services[1]['intervals']==[], 'Current comparison requires a reference without a port intervention')
    require(services[0]['intervals']==(case['port_service'] if 'port_service' in components else []),
            'Actual port intervention differs from comparison')
    for name in ('system.json',):
        require(digest(run/'inputs'/name)==digest(reference/'inputs'/name),'Paired system settings differ')
    for name in ('node.csv','topology.csv','routing_table.csv','transport_channel.csv'):
        require(digest(run/'inputs/network'/name)==digest(reference/'inputs/network'/name),'Paired network input differs')
    same_slice=(manifests[0].get('source_slice_sha256') is not None and
                manifests[0].get('source_slice_sha256')==manifests[1].get('source_slice_sha256'))
    if not same_slice:
        require(structural_transfers(run)==structural_transfers(reference),
                'Paired request routing or transfer structure differs')
    return dict(passed=True,endpoint_cost_entries_changed=len(measured),
                port_intervals_changed=len(services[0]['intervals']),
                workload_check='same_verified_frozen_graphs' if same_slice else 'same_structural_transfer_digest')
