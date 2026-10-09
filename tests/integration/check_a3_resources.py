"""Audit A3 local work, real completion events and operator boundaries."""
import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys

from moe_ub_cosim.record_storage import iter_result


def read(path):
    return json.loads(path.read_text())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[2]
    output=args.output_dir.resolve();output.mkdir(parents=True,exist_ok=False)
    binary=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    loader=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-plan-load-probe'
    fixture=root/'fixtures/m19_a3_resources';example=root/'examples/first_round'
    commands=[];summaries={}

    def save(path,value):
        path.write_text(json.dumps(value,indent=2)+'\n');return path

    def invoke(label,command,expected=0):
        command=list(map(str,command));started=datetime.now(timezone.utc).isoformat()
        result=subprocess.run(command,cwd=root,capture_output=True,text=True,timeout=240)
        for stream in ('stdout','stderr'):
            (output/f'{label}.{stream}.log').write_text(getattr(result,stream))
        commands.append(dict(command=command,cwd=str(root),started_utc=started,
            ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=result.returncode,log_prefix=str(output/label)))
        save(output/'commands.json',commands)
        assert result.returncode==expected,(label,result.returncode,result.stderr)

    from moe_ub_cosim.generate import export_plan
    from moe_ub_cosim.plan import build_plan
    single=build_plan(read(fixture/'layer.json'))
    export_plan(single,output/'single')
    invoke('single-load',[loader,output/'single','--compare-retention'])
    for mutation in ('missing-wait','wrong-core','wrong-operation','unknown-field'):
        path=output/mutation;shutil.copytree(output/'single',path);plan=deepcopy(single)
        if mutation=='wrong-operation':
            next(t for t in plan['transfers'] if 'ub_task_id' in t)['operation']='LOAD'
        elif mutation=='unknown-field':plan['unvalidated_resource']=True
        elif mutation=='missing-wait':
            next(n for g in plan['rank_graphs'] for n in g['nodes'] if n.get('event')=='data_ready')['deps']=[]
        else:
            next(n for g in plan['rank_graphs'] for n in g['nodes'] if n['kind']=='COMP')['resource_id']='gpu_compute'
        save(path/'plan.json',plan);invoke(mutation,[loader,path],1)

    for label in ('baseline','partial'):
        service=read(fixture/'service.json');layers=read(fixture/'layers.json')
        if label=='partial':
            service['requests']=[r for r in service['requests'] if r['rank_id']==2]
            assert len(service['requests'])==2
            for index,request in enumerate(service['requests']):
                request['decode_routes']=[{str(l['layer_id']):[2,3] if index==0 else [4,5]
                    for l in layers['groups'][0]['layers']}]
            for layer in layers['groups'][0]['layers']:
                layer['endpoint_prepare']=dict(schema_version='moe-ub-endpoint-prepare/1',
                    relation='additional_to_existing_once',ranks=[dict(rank_id=r,
                    dispatch_ns_per_local_token=0,combine_ns_per_expert_input=50000 if r==12 else 0)
                    for r in layer['rank_ids']])
        cp=save(output/f'{label}.service.json',service);lp=save(output/f'{label}.layers.json',layers)
        invoke(label,[sys.executable,'-m','moe_ub_cosim.service','--input',cp,'--layer',lp,
            '--backend',binary,'--system',example/'system.json','--network',example/'network',
            '--output-dir',output/label,'--record-storage',example/'record-policy.json',
            '--max-simulation-ns','1000000','--wall-timeout-seconds','180'])
        run=output/label;execution=run/'execution';report=read(execution/'result.json')
        front=read(run/'service_result.json')
        assert front['service_finished'] and report['normal_exit'] and report['network_initializations']==1
        assert report['network_drain']['idle'] and report['network_drain']['started_packets']==report['network_drain']['arrived_packets']
        assert all(row[k]==0 for row in report['rank_resources'] for k in ('compute_slots','send_slots','pending_events','pending_tasks','stale_callbacks'))
        assert all(not r['request_blocks'] and r['kv_used_bytes']==0 for r in front['memory_final'])
        actual={r['transfer_id']:r for r in iter_result(execution,'transfers',report)}
        local={r['transfer_id']:r for r in iter_result(execution,'local_deliveries',report)}
        events={(r['graph_id'],r['rank_id'],r['node_id']):r for r in iter_result(execution,'node_execution',report)}
        overlaps=partial=read_before_retire=0;work=bytes_=0
        for wave in front['waves']:
            graph_dir=run/'graphs'/wave['graph_id'];plan=read(graph_dir/'plan.json');gid=plan['graph_id']
            invoke(label+'-load-'+gid,[loader,graph_dir,'--compare-retention'])
            assert plan['communication_template']=='cann_a3_v850_store_resources_v1'
            for t in plan['transfers']:
                if 'ub_task_id' in t:
                    row=actual[t['transfer_id']]
                    assert t['operation']=='STORE' and row['bytes']==row['committed_bytes']==t['payload_bytes']
                    assert row['submitted_tasks']==row['send_callbacks']==row['receive_callbacks']==1
                    assert row['data_ready_ns']<row['transaction_done_ns'] and not row['context_active']
                    bytes_+=t['payload_bytes']
                else:
                    row=local[t['transfer_id']]
                    assert row['send_callbacks']==row['recv_callbacks']==1 and not row['active']
            for graph in plan['rank_graphs']:
                rank=graph['rank_id'];intervals=defaultdict(list);cores=[]
                get=lambda n:events[gid,rank,n]
                for node in graph['nodes']:
                    event=get(node['node_id'])
                    assert all(event['start_ns']>=get(d)['end_ns'] for d in node['deps'])
                    if node['kind']=='COMP':
                        assert event['end_ns']-event['start_ns']==node['duration_ns']
                        work+=node['duration_ns'];intervals[node['resource_id']].append(event)
                        if node['resource_id'].startswith('aiv:'):cores.append((node,event))
                    if node['kind']=='EVENT':
                        if node['event']=='barrier':assert event['start_ns']==event['end_ns']
                        elif node['transfer_id'] in actual:
                            transfer=actual[node['transfer_id']]
                            if node['event']=='data_ready':
                                assert event['end_ns']==max(event['start_ns'],transfer['data_ready_ns'])
                            else:
                                assert transfer['send_start_ns']<=event['end_ns']<=transfer['transaction_done_ns']
                                read_before_retire+=event['end_ns']<transfer['transaction_done_ns']
                        else:
                            assert event['end_ns']==max(event['start_ns'],local[node['transfer_id']]['ready_ns'])
                for rows in intervals.values():
                    rows.sort(key=lambda r:r['start_ns'])
                    assert all(a['end_ns']<=b['start_ns'] for a,b in zip(rows,rows[1:]))
                for i,(a,x) in enumerate(cores):
                    overlaps+=sum(a['resource_id']!=b['resource_id'] and
                        max(x['start_ns'],y['start_ns'])<min(x['end_ns'],y['end_ns']) for b,y in cores[i+1:])
                for phase in graph['layer_phases']:
                    finish=get(phase['phase_nodes']['finish'])
                    reductions=phase['phase_members'].get('reduce',[])
                    assert all(get(n)['end_ns']<=finish['end_ns'] for n in reductions)
                    if len(reductions)==2 and rank==2:
                        early=get(reductions[0])['start_ns']
                        late=[get(t['recv_node_id'])['end_ns'] for t in plan['transfers']
                            if t['dst_rank']==rank and t['kind']=='combine' and t['sources'] and
                            t['sources'][0]['source_record_index']==1 and
                            t['operation_id'].split('.')[-2]=='L'+str(phase['layer_id'])]
                        partial+=bool(late and early<max(late))
        assert overlaps>0 and read_before_retire>0
        if label=='partial':assert partial>0
        summaries[label]=dict(simulation_end_ns=report['simulation_end_ns'],fabric_payload_bytes=bytes_,
            compute_work_ns=work,overlapping_core_pairs=overlaps,partial_token_starts=partial,
            local_reads_before_protocol_retirement=read_before_retire,graphs=len(front['waves']))
    save(output/'summary.json',dict(passed=True,cases=summaries))
    print('PASS: A3 independent cores, actual copy events, partial Token readiness, and released resources')


if __name__=='__main__':main()
