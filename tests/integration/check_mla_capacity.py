"""MLA capacity decisions on native Scheduler and actual shared ASTRA/UB execution."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys


def read(path):return json.loads(path.read_text())
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n');return path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[2];out=args.output_dir.resolve()
    out.mkdir(parents=True,exist_ok=False)
    fixture=root/'fixtures/m10_mla_capacity';base=read(fixture/'service.json')
    layers=read(root/'fixtures/m10_cann/layers.json');evidence=read(fixture/'evidence.json')
    network=root/'fixtures/m3_shared_bottleneck/network'
    binary=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    commands=[];cases={}

    def run(label,c=base,l=layers,net=network):
        cp=save(out/f'{label}.service.json',c);lp=save(out/f'{label}.layers.json',l)
        argv=[str(x) for x in (sys.executable,'-m','moe_ub_cosim.service','--input',cp,'--layer',lp,
            '--backend',binary,'--system',root/'fixtures/m1_compute/system.json','--network',net,
            '--output-dir',out/label,'--max-simulation-ns',2000000)]
        start=datetime.now(timezone.utc).isoformat()
        p=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=120)
        for stream in ('stdout','stderr'):(out/f'{label}.{stream}.log').write_text(getattr(p,stream))
        commands.append(dict(label=label,argv=argv,cwd=str(root),started_utc=start,
                             ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=p.returncode))
        save(out/'commands.json',commands);assert p.returncode==0,(label,p.stderr)
        path=out/label;front=read(path/'service_result.json');execution=read(path/'execution/result.json')
        manifest=read(path/'manifest.json');audits=read(path/'input_evidence.json')['groups']
        assert manifest['capacity_model_schema']=='moe-ub-model/2'
        assert manifest['mla_kernel_validated'] is manifest['quantized_kernels_executed'] is False
        assert front['service_finished'] and execution['normal_exit'] and execution['network_initializations']==1
        assert execution['network_drain']['idle'] and execution['network_drain']['started_packets']==execution['network_drain']['arrived_packets']
        assert all(b['same_network_objects'] for b in execution['boundary_resources'])
        assert all(r[k]==0 for r in execution['rank_resources'] for k in ('compute_slots','send_slots','pending_events','pending_tasks','stale_callbacks'))
        assert all(not r['request_blocks'] and r['kv_used_bytes']==0 for r in front['memory_final'])
        for event in front['memory_events']:
            for rank in event['ranks']:
                used=sum(rank['request_blocks'].values())
                assert used+rank['free_blocks']==rank['kv_capacity_blocks']
                assert rank['kv_used_bytes']==used*1536
                assert rank['used_bytes']==rank['non_kv_bytes']+rank['kv_used_bytes']
        for group in audits.values():
            assert group['model']['executed_layer_ids']==[1,2,3] and group['model']['unexecuted_layer_ids']==[0]
            assert group['model']['communication_buffer_minimum_bytes']==6304512
            assert group['model']['compute_validation']=='reference_stage_costs_only'
            assert all(r['kv_bytes_per_token']==384 and r['kv_block_bytes']==1536 for r in group['model']['ranks'])
        assert front['route_records_consumed']==front['route_records_expected']==36
        transfers={t['transfer_id']:t for t in execution['transfers']}
        business=0;contributions=0
        for wave in front['waves']:
            plan=read(path/f"graphs/{wave['graph_id']}/plan.json")
            events={(e['rank_id'],e['node_id']):e for e in execution['node_execution'] if e['graph_id']==wave['graph_id']}
            assert wave['finished_ns']==max(e['time_ns'] for e in execution['graph_completions'] if e['graph_id']==wave['graph_id'])
            for child in plan['layers']:
                contributions+=len(child['contributions'])
            for graph in plan['rank_graphs']:
                for node in graph['nodes']:
                    e=events[graph['rank_id'],node['node_id']]
                    assert all(e['start_ns']>=events[graph['rank_id'],d]['end_ns'] for d in node['deps'])
            for t in plan['transfers']:
                if 'ub_task_id' not in t:continue
                x=transfers[t['transfer_id']];business+=t['business_payload_bytes']
                assert x['bytes']==x['committed_bytes']==t['payload_bytes']
                assert x['submitted_tasks']==x['receive_callbacks']==x['send_callbacks']==1
                assert x['data_ready_ns']<x['transaction_done_ns']==x['send_done_ns']
                assert events[t['src_rank'],t['send_node_id']]['end_ns']==x['send_done_ns']
                assert events[t['dst_rank'],t['recv_node_id']]['end_ns']==max(x['data_ready_ns'],x['receive_start_ns'])
        assert contributions==72 and transfers
        cases[label]=dict(finished_ns=front['finished_ns'],waves=len(front['waves']),writes=len(transfers),
            payload_bytes=sum(t['bytes'] for t in transfers.values()),business_bytes=business,
            capacity_blocks={r['rank_id']:r['kv_capacity_blocks'] for r in front['memory_final']},
            route_records=36,contributions=contributions)
        return front,execution

    primary=run('reference')
    assert cases['reference']['writes']==305 and cases['reference']['payload_bytes']==120316
    assert cases['reference']['business_bytes']==113664 and cases['reference']['finished_ns']==131530
    # Two requests need two blocks each: ceil separately, never ceil the sum.
    first=next(e for e in primary[0]['batch_events'] if e['event']=='scheduled' and e['rank_id']==2)
    assert first['request_ids']==[201,202]
    snapshots=[r for e in primary[0]['memory_events'] for r in e['ranks'] if r['rank_id']==2]
    assert any(r['request_blocks']=={'201':2,'202':2} and r['kv_used_bytes']==6144 for r in snapshots)
    tight=deepcopy(base)
    for g in tight['groups']:
        for row in g['memory']['ranks']:row['device_bytes']-=5*1536
    waited=run('tight',tight)[0]
    assert all(v==3 for v in cases['tight']['capacity_blocks'].values())
    assert any(e.get('reason')=='kv_capacity' and e['request_id']==202 for e in waited['memory_events'])
    release={e['request_id']:e['time_ns'] for e in waited['memory_events'] if e['event']=='release'}
    admit={e['request_id']:e['time_ns'] for e in waited['memory_events'] if e['event']=='admit'}
    assert admit[202]==release[201] and admit[203]==release[202]
    # Same device budget, matrix storage INT8 vs BF16. Neither executes a quantized kernel.
    floating=deepcopy(tight)
    for g in floating['groups']:
        g['model_spec']['weight_storage']['mla'].update(dtype='bfloat16',quantization='none',scale_dtype=None)
        for row in g['memory']['ranks']:row['device_bytes']+=2836736
        g['memory']['max_kv_blocks_per_rank']=4096
    floated=run('bf16-projections',floating)[0]
    compact=deepcopy(floating)
    for g in compact['groups']:g['model_spec']['weight_storage']['mla']=deepcopy(base['groups'][0]['model_spec']['weight_storage']['mla'])
    packed=run('int8-projections-same-budget',compact)[0]
    assert all(v==3 for v in cases['bf16-projections']['capacity_blocks'].values())
    assert all(v==1849 for v in cases['int8-projections-same-budget']['capacity_blocks'].values())
    assert next(e for e in floated['batch_events'] if e['event']=='scheduled' and e['rank_id']==2)['request_ids']==[201]
    assert next(e for e in packed['batch_events'] if e['event']=='scheduled' and e['rank_id']==2)['request_ids']==[201,202]
    assert any(e.get('reason')=='kv_capacity' for e in floated['memory_events'])
    assert not any(e.get('reason')=='kv_capacity' for e in packed['memory_events'])
    window=deepcopy(evidence);limited=deepcopy(layers)
    window['service']['groups'][1]['scheduling']['max_num_batched_tokens']=1
    for layer in limited['groups'][1]['layers']:layer['batch_limits']['max_num_batched_tokens']=1
    observed=run('window-resident',window,limited)[0]
    assert cases['window-resident']['business_bytes'] > 0
    scheduled=next(e for e in observed['batch_events'] if e['event']=='scheduled' and e['rank_id']==20)
    assert scheduled['request_ids']==[401] and scheduled['resident_request_ids']==[401,402]
    rank=next(r for r in scheduled['ranks'] if r['rank_id']==20)
    assert rank['request_blocks']=={'401':1,'402':2} and rank['kv_used_bytes']==4608
    assert all(r['status']=='OBSERVATION_ENDED' and r['target_tokens'] is None and not r['eos_known'] for r in observed['requests'])
    assert observed['requests_completed']==[] and len(observed['requests_censored'])==7 and sum(observed['done'])==0
    assert Counter(e['request_id'] for e in observed['memory_events'] if e['event']=='observation_cleanup')==Counter(r['request_id'] for r in observed['requests'])
    assert not any(e['event']=='release' for e in observed['memory_events'])
    window['service']['groups'][0]['memory']=deepcopy(tight['groups'][0]['memory'])
    censored=run('window-wait',window,limited)[0]
    cleanup={e['request_id']:e['time_ns'] for e in censored['memory_events'] if e['event']=='observation_cleanup'}
    admit={e['request_id']:e['time_ns'] for e in censored['memory_events'] if e['event']=='admit'}
    assert admit[202]==cleanup[201] and admit[203]==cleanup[202]
    slow=out/'slow-network';shutil.copytree(network,slow)
    p=slow/'topology.csv';p.write_text(p.read_text().replace('10Gbps','1Gbps'))
    slower=run('slow',net=slow)[0]
    assert cases['slow']['finished_ns']>cases['reference']['finished_ns']
    for name in ('ep0','ep1'):
        assert [w for w in slower['waves'] if w['group_id']==name][1]['submitted_ns'] > [w for w in primary[0]['waves'] if w['group_id']==name][1]['submitted_ns']
    save(out/'summary.json',dict(passed=True,cases=cases,backend_reused=True,full_model_execution=False,hardware_calibrated=False))
    print('PASS: MLA tensor budget -> unique native pool -> real UB completion, admission, release and window cleanup')


if __name__=='__main__':main()
