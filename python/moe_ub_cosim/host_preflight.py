"""Offline host-size evidence, separate from admission or simulation readiness."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .plan import require
from .route_evidence import read_json,validate_manifest,validate_raw


def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def ref(p):return dict(path=str(p),sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest())
def empty():return dict(same_rank=0,same_chip=0,cross_chip=0,sources=0,destinations=0,local_sources=0,local_destinations=0)


def add_token(row,src,experts,placement,chips,positions):
    row['sources']|=1<<positions[src]
    for expert in experts:
        dst=placement[expert];row['destinations']|=1<<positions[dst]
        if dst==src:row['same_rank']+=1
        elif chips[src]==chips[dst]:
            row['same_chip']+=1;row['local_sources']|=1<<positions[src];row['local_destinations']|=1<<positions[dst]
        else:row['cross_chip']+=1


def usage(c,row):
    """Independent protocol arithmetic; never calls production resources.cann_usage."""
    ranks=c['rank_ids'];owners=c['expert_placement'];chips={r['rank_id']:r['chip_id'] for r in c['deployment']['ranks']}
    near_status=sum(src!=dst and chips[src]==chips[dst] for src in ranks for dst in owners)
    far_status=sum(chips[src]!=chips[dst] for src in ranks for dst in owners)
    h=c['hidden_dim'];chunk=c['chunk_bytes'];dispatch=((h+31)//32)*32+44
    remote_chunks=(dispatch+chunk-1)//chunk+(2*h+chunk-1)//chunk+1
    near=near_status+3*row['same_chip'];far=far_status+remote_chunks*row['cross_chip']
    local=row['sources'].bit_count();compute=len(ranks)+2*local+2*row['destinations'].bit_count()
    if c['compute_profile_ns']['next_layer']:compute+=local
    prep=c['deployment']['local_prepare_ns_per_vector']
    if prep['dispatch']:compute+=row['local_sources'].bit_count()
    if prep['combine']:compute+=row['local_destinations'].bit_count()
    return dict(transfers=near+far,fabric_transfers=far,local_transfers=near,nodes=2*(near+far)+compute,
        same_rank_contributions=row['same_rank'],same_chip_contributions=row['same_chip'],cross_chip_contributions=row['cross_chip'],
        fabric_business_bytes=row['cross_chip']*3*h,
        fabric_payload_bytes=far_status*32+row['cross_chip']*(dispatch+2*h+32))


def target_inputs(config):
    require(config['schema_version']=='moe-ub-host-preflight/1','Unsupported host preflight input')
    values={k:read_json(config[k])[1] for k in ('manifest','requests','capacity','fabric','layer_template','scenario_contract')}
    m=values['manifest'];validate_manifest(m)
    model=values['capacity']['model_spec'];fabric=values['fabric'];group=fabric['groups'][0]
    ranks=group['rank_ids'];require(ranks==list(range(128)) and len(m['files'])==len(values['requests'])==1024,'Not the complete target workload')
    require(model['hidden_size']==m['model']['hidden_dim']==7168 and len(model['layers'])==len(m['layers'])==61,'Target model mismatch')
    for actual,source in zip(model['layers'],m['layers']):
        require(actual['layer_id']==source['layer_id'] and actual['kind']==source['kind'],'Source/model layer mismatch')
        if actual['kind']=='moe':require(actual['num_experts']==source['num_experts']==256 and actual['top_k']==source['top_k']==8,'Source/model experts mismatch')
    require(config['chunk_bytes']==8192 and config['send_slots_per_rank']==1,'Probe must preserve selected target chunk/slots')
    c=deepcopy(values['layer_template']['groups'][0]['layers'][0]);c.update(graph_id='host-probe-g0',wave_id='host-probe-w0',
        layer_id=3,rank_ids=ranks,rank_to_ub_node=group['rank_to_ub_node'],hidden_dim=7168,top_k=8,tokens=[],
        expert_placement=[r for r in ranks for _ in range(2)],chunk_bytes=config['chunk_bytes'],send_slots_per_rank=config['send_slots_per_rank'],
        batch_limits=dict(max_num_seqs=8,max_num_batched_tokens=8))
    require(all(l['expert_placement']==c['expert_placement'] for l in model['layers'] if l['kind']=='moe'),'Target placement differs across layers')
    c['deployment']['ranks']=[dict(rank_id=r,die_id=f'die-{r}',chip_id=f'chip-{r//2}',server_id=f'server-{r//16}') for r in ranks]
    c['cann_profile']['window_bytes']=values['scenario_contract']['memory']['cann_data_window_bytes_per_layer']
    # This is a bound for one offline layer probe, not permission to run a target service.
    c['resource_budget']=dict(schema_version='moe-ub-graph-resources/1',max_graph_transfers=100000,
        max_graph_nodes=250000,max_plan_bytes=2**30,max_run_graphs=1,max_run_transfers=100000,max_run_nodes=250000)
    return values,c


def count_target(config,output):
    values,c=target_inputs(config);manifest=values['manifest'];requests=values['requests'];output.mkdir(parents=True,exist_ok=False)
    ranks=c['rank_ids'];positions={r:i for i,r in enumerate(ranks)};chips={r['rank_id']:r['chip_id'] for r in c['deployment']['ranks']}
    rows=[[empty() for _ in range(58)] for _ in range(64)];seen=set();first=[];provenance=[];source_bytes=0
    for index,(item,q) in enumerate(zip(manifest['files'],requests)):
        require(q==dict(request_id=index+1,rank_id=index%128,arrival_ns=0,file_id=item['file_id'],first_decode_entry=1,decode_steps=64,target_total_tokens=None),
            'Target request order/window mapping mismatch')
        path,raw=read_json(item['file']);require(item['file']['sha256'] not in seen,'Repeated source content');seen.add(item['file']['sha256'])
        prompt,steps=validate_raw(raw,manifest['layers']);require(steps>=64,'Source window too short');source_bytes+=path.stat().st_size
        for step in range(64):
            for lid in range(3,61):add_token(rows[step][lid-3],q['rank_id'],raw[step+1][str(lid)][0],c['expert_placement'],chips,positions)
        experts=raw[1]['3'][0]
        first.append(dict(request_id=str(q['request_id']),src_rank=q['rank_id'],phase='decode',token_index=prompt,layer_id=3,expert_ids=experts))
        provenance.append(dict(request_id=str(q['request_id']),phase='decode',token_index=prompt,layer_id=3,expert_ids=experts,
            source_file=deepcopy(item['file']),file_id=item['file_id'],source_entry=1,source_layer_id=3,source_token_row=0,decode_index=0,
            source_index_convention='entry_0_prefill_entry_1_first_decode'))
        del raw
        if (index+1)%128==0:print('Counted source files:',index+1,flush=True)
    per_layer=[];per_step=[]
    for step,layer_rows in enumerate(rows):
        subtotal=Counter()
        for lid,row in enumerate(layer_rows,3):
            value=usage(c,row);subtotal.update(value);per_layer.append(dict(step=step,layer_id=lid,**value))
        # Positive reference costs create 4 head, 3*8 dense and 58*10 outer MoE nodes per active rank.
        subtotal['nodes']+=128*(4+3*8+58*10);per_step.append(dict(step=step,**subtotal))
    totals={k:sum(row[k] for row in per_step) for k in per_step[0] if k!='step'}
    shape=values['fabric']['shape'];devices=shape['servers']*shape['chips_per_server']*shape['dies_per_chip']
    switch_ports=shape['planes']*(devices+2*shape['servers']*shape['l2_per_plane'])
    # Preserve the historical preflight's explicitly versioned storage assumption.
    # Current backends share provenance; these fields reproduce the old blocker,
    # not a current-backend estimate. Runtime ownership is in provenance-audit.json.
    first_writes=per_step[0]['fabric_transfers'];writes=totals['fabric_transfers']
    report=dict(schema_version='moe-ub-target-host-counts/1',input=config,source_files=1024,source_bytes=source_bytes,
        route_records=1024*64*58,rounds=64,per_round=per_step,total=totals,
        nodes_basis='all reference compute costs positive, local prepare enabled; no calibrated duration implied',
        all_switch_egress_ports=switch_ports,port_inventory_policy='all_target_switch_egress_for_preflight_not_a_hidden_subset',
        descriptor_storage_revision='4545074',descriptor_storage_model='historical_per_port_copies_not_current_backend',
        descriptor_copies_per_port=2,descriptor_key_bytes=4,
        first_round_all_ports_descriptor_key_lower_bound_bytes=2*4*switch_ports*first_writes,
        run_all_ports_descriptor_key_lower_bound_bytes=2*4*switch_ports*writes,
        packet_ledger_lower_bound='each registered WRITE creates at least one data packet; exact segmentation count not inferred',
        unknown=['target UB execution peak RSS','full graph allocation/export bytes','actual packet counts',
                 'actual 64-round completion time','final window count and raw/public output bytes'],
        target_scale_executed=False,hardware_calibration='unvalidated_reference',runtime_ready=False)
    c['tokens']=first;save(output/'first-layer.json',c);save(output/'first-layer-provenance.json',provenance)
    save(output/'layer-counts.json',per_layer);save(output/'counts.json',report)
    return report
