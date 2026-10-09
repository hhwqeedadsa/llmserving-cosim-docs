"""A3 finite buffers, partial Token readiness, physical egress and late preparation."""
import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys

from moe_ub_cosim.segment import select
from moe_ub_cosim.observation import backend_observation
from moe_ub_cosim.record_storage import iter_result
from moe_ub_cosim.user_io import read,save


def queue_area(rows):
    state={};total=0;peak=0
    for row in rows:
        key=row['port'],row['kind'];previous,value=state.get(key,(0,0))
        total+=(row['time_ns']-previous)*value;state[key]=(row['time_ns'],row['bytes']);peak=max(peak,row['bytes'])
    assert all(v==0 for _,v in state.values())
    return total,peak


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--build-dir',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
    root=Path(__file__).resolve().parents[2];out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    binaries=a.build_dir.resolve()/'output/cosim';binary=binaries/'ns3.44-cosim-service';example=root/'examples/a3_segment';commands=[]
    def invoke(label,cmd):
        cmd=list(map(str,cmd));result=subprocess.run(cmd,cwd=root,capture_output=True,text=True,timeout=240)
        for stream in ('stdout','stderr'):(out/f'{label}.{stream}.log').write_text(getattr(result,stream))
        commands.append(dict(command=cmd,cwd=str(root),exit_code=result.returncode,ended_utc=datetime.now(timezone.utc).isoformat()))
        save(out/'commands.json',commands);assert result.returncode==0,(label,result.stderr)
    invoke('resolve',[sys.executable,'-m','moe_ub_cosim','resolve','--config',example/'scenario.yaml','--backend',binary,'--output',out/'resolved'])
    base_service=read(out/'resolved/service.json');base_layers=read(out/'resolved/layer.json');summaries={};cases={}
    for label in ('buffers','partial','early','late','late-observation-off'):
        service=deepcopy(base_service);layers=deepcopy(base_layers)
        request=service['requests'][0];requests=[]
        routes=([[4,5]]*3+[[0,1]]*3 if label=='buffers' else [[2,3],[4,5]] if label=='partial' else [[4,5]])
        for i,experts in enumerate(routes):
            r=deepcopy(request);r['request_id']=i+1;r['rank_id']=2
            r['decode_routes']=[{str(l['layer_id']):experts for l in layers['groups'][0]['layers']}];requests.append(r)
        service['requests']=requests
        limits=dict(max_num_seqs=max(2,len(requests)),max_num_batched_tokens=max(2,len(requests)))
        service['groups'][0]['scheduling']=limits
        for l in layers['groups'][0]['layers']:
            l['batch_limits']=limits
            if label in ('partial','late','late-observation-off'):
                l['endpoint_prepare']=dict(schema_version='moe-ub-endpoint-prepare/1',relation='additional_to_existing_once',
                    ranks=[dict(rank_id=r,dispatch_ns_per_local_token=10000 if label.startswith('late') and r==2 else 0,
                        combine_ns_per_expert_input=50000 if label=='partial' and r==12 else 0) for r in l['rank_ids']])
        service,layers=select(service,layers,1,1,0)
        cp=out/(label+'.service.json');lp=out/(label+'.layers.json');save(cp,service);save(lp,layers)
        network=out/(label+'-network');shutil.copytree(out/'resolved/network',network)
        if label=='buffers':
            c=read(network/'backend.json');c['attributes']['ns3::UbLdstApi::RequestIdCount']='1';c['attributes']['ns3::UbLdstThread::StoreRequestSize']='128';save(network/'backend.json',c)
        cmd=[sys.executable,'-m','moe_ub_cosim.service','--input',cp,'--layer',lp,'--backend',binary,
            '--network',network,'--system',example/'system.json','--record-storage',example/'record-policy.json',
            '--max-simulation-ns','1000000','--wall-timeout-seconds','180','--output-dir',out/label]
        if label!='late-observation-off':
            op=out/(label+'.observation.json');save(op,backend_observation(read(example/'observation.json'),[2,7,12],1000000));cmd+=['--observation',op]
        invoke(label,cmd)
        run=out/label;execution=run/'execution';report=read(execution/'result.json');plan=read(run/'graphs/g0/plan.json')
        assert report['normal_exit'] and report['network_drain']['idle'] and report['network_drain']['started_packets']==report['network_drain']['arrived_packets']
        assert all(row[k]==0 for row in report['rank_resources'] for k in ('compute_slots','send_slots','pending_events','pending_tasks','stale_callbacks'))
        events={(r['rank_id'],r['node_id']):r for r in iter_result(execution,'node_execution',report)}
        transfers={r['transfer_id']:r for r in iter_result(execution,'transfers',report)}
        for t in plan['transfers']:
            if 'ub_task_id' in t:
                row=transfers[t['transfer_id']]
                assert row['bytes']==row['committed_bytes']==t['payload_bytes'] and row['send_callbacks']==row['receive_callbacks']==row['submitted_tasks']==1
        for g in plan['rank_graphs']:
            for n in g['nodes']:
                row=events[g['rank_id'],n['node_id']]
                assert all(row['start_ns']>=events[g['rank_id'],d]['end_ns'] for d in n['deps'])
        cases[label]=(plan,events,transfers,report)
        summaries[label]=dict(simulation_end_ns=report['simulation_end_ns'],fabric_transfers=len(transfers),
            fabric_bytes=sum(t['bytes'] for t in transfers.values()),physical_packets=report['network_drain']['started_packets'])
    plan,events,actual,_=cases['buffers'];g=next(g for g in plan['rank_graphs'] if g['rank_id']==2);nodes={n['node_id']:n for n in g['nodes']}
    waits=independent=0
    for n in g['nodes']:
        if n['name']!='dispatch-quant-pack':continue
        for d in n['deps']:
            dep=nodes[d]
            if dep['name']!='dispatch-buffer-reusable':continue
            base=max((events[2,x]['end_ns'] for x in n['deps'] if x!=d),default=0);released=events[2,d]['end_ns']
            assert events[2,n['node_id']]['start_ns']>=released
            if released>base:
                waits+=1
                independent+=sum(x.get('resource_id')!=n.get('resource_id') and x['kind']=='COMP' and
                    base<=events[2,x['node_id']]['start_ns']<released for x in g['nodes'])
    assert waits>0 and independent>0,(waits,independent)
    summaries['buffers'].update(actual_buffer_waits=waits,other_core_progress_during_wait=independent)
    plan,events,_,_=cases['partial'];g=next(g for g in plan['rank_graphs'] if g['rank_id']==2)
    reductions=g['layer_phases'][0]['phase_members']['reduce'];assert len(reductions)==2
    late=max(events[2,t['recv_node_id']]['end_ns'] for t in plan['transfers'] if t['dst_rank']==2 and t['kind']=='combine' and t['sources'] and t['sources'][0]['source_record_index']==1)
    assert events[2,reductions[0]]['start_ns']<late
    finish=events[2,g['layer_phases'][0]['phase_nodes']['finish']]['end_ns']
    assert finish>=max(events[2,n]['end_ns'] for n in reductions)
    summaries['partial'].update(first_reduction_ns=events[2,reductions[0]]['start_ns'],second_token_ready_ns=late,operator_complete_ns=finish)
    early=cases['early'];late=cases['late'];shifts=[];latencies=[]
    for t in early[0]['transfers']:
        if 'ub_task_id' not in t or t['kind']!='dispatch' or t['message_role']!='data':continue
        x=early[2][t['transfer_id']];y=late[2][t['transfer_id']]
        shifts.append(y['send_start_ns']-x['send_start_ns'])
        latencies.append((x['data_ready_ns']-x['send_start_ns'],y['data_ready_ns']-y['send_start_ns']))
    assert shifts and set(shifts)=={5000},shifts
    # Empty ranks still publish their required Dispatch counts. Moving vector
    # preparation can remove a small overlap with those messages; low competing
    # traffic does not imply a time-translation invariant network. The added
    # delay must be entirely before submission, with no added network interval.
    assert all(y<=x for x,y in latencies),latencies
    assert cases['late'][1:3]==cases['late-observation-off'][1:3]
    assert summaries['late']==summaries['late-observation-off']
    summaries['late'].update(pre_submit_delay_ns=5000,dispatch_delivery_latencies_ns=latencies,observation_off_identical=True)
    # Hand-checkable two STORE flows, with one shared vs two distinct slow egress ports.
    star=out/'star';star.mkdir();shutil.copyfile(out/'resolved/network/network_attribute.txt',star/'network_attribute.txt')
    (star/'node.csv').write_text('nodeId,nodeType,portNum,allocationDelay,forwardDelay\n0..3,DEVICE,1,1ns,0ns\n4,SWITCH,4,1ns,0ns\n')
    (star/'topology.csv').write_text('nodeId1,portId1,nodeId2,portId2,bandwidth,delay\n'+''.join(f'{i},0,4,{i},400Gbps,20ns\n' for i in range(4)))
    (star/'routing_table.csv').write_text('nodeId,dstNodeId,dstPortId,outPorts,metrics\n'+''.join(f'{i},{j},0,0,1\n' for i in range(4) for j in range(4) if i!=j)+''.join(f'4,{j},0,{j},1\n' for j in range(4)))
    egress={}
    for label,shared,detail in [('shared',1,1),('separate',0,1),('shared-off',1,0)]:
        invoke(label,[binaries/'ns3.44-cosim-a3-egress-test',star,out/label,shared,detail]);r=read(out/label/'result.json');egress[label]=r
        assert sum(dict(r['delivered_bytes']).values())==16384
        if detail:
            packets=r['packets'];assert len(packets)==4
            assert all(p['rate_bps']==1000000000 and p['duration_ns']==8*p['bytes'] for p in packets)
            ports=defaultdict(list)
            for packet in packets:ports[packet['port']].append(packet)
            assert sorted(map(len,ports.values()))==([4] if shared else [2,2])
            for rows in ports.values():assert all(b['time_ns']==x['time_ns']+x['duration_ns'] for x,b in zip(rows,rows[1:]))
            area,peak=queue_area(r['queues']);summaries[label]=dict(queue_byte_ns=area,queue_peak_bytes=peak,
                latest_ready_ns=max(dict(r['ready_ns']).values()),store_packets=len(packets),wire_data_bytes=sum(p['bytes'] for p in packets))
    assert summaries['shared']['wire_data_bytes']==summaries['separate']['wire_data_bytes']
    assert summaries['shared']['latest_ready_ns']>summaries['separate']['latest_ready_ns']
    assert summaries['shared']['queue_byte_ns']>summaries['separate']['queue_byte_ns']>0
    assert all(egress['shared'][k]==egress['shared-off'][k] for k in ('delivered_bytes','ready_ns','done_ns','wire_and_time_hash','physical_packets'))
    save(out/'summary.json',dict(passed=True,cases=summaries,scope='platform_functional_checks_not_normal_business_experiments'))
    print('PASS: four A3 acceptance cases, conserved bytes/callbacks, and passive capture')


if __name__=='__main__':main()
