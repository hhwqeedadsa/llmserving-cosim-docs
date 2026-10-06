"""Actual node/callback stages, independent per-nanosecond queue oracle and passivity."""
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import yaml

from moe_ub_cosim.observation import local_time,validate_observation
from moe_ub_cosim.record_storage import iter_result,iter_port_records
from moe_ub_cosim.research_storage import stored_rows


def read(path):return json.loads(path.read_text())
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n');return path


def queue_oracle(events,port,clock):
    # Small-scene oracle samples every integer local nanosecond, separately
    # including zero-duration after-update states in the peak. No production
    # accumulator/resampler is called.
    changes=defaultdict(list)
    for r in events:changes[local_time(r['time_ns'],clock)].append((r['voq_waiting_bytes'],r['fifo_waiting_bytes']))
    first,last=min(changes),max(changes);assert last-first<2000000
    state=(0,0);grids=[[],[]];peaks=[[],[]]
    for now in range(first,last+1):
        values=changes.get(now,[])
        if values:state=values[-1]
        for j in range(2):
            grids[j].append(state[j]);peaks[j].append(max([v[j] for v in values] or [state[j]]))
    assert state==(0,0)
    result={}
    for left in range(port['start_local_ns'],port['end_local_ns'],port['period_ns']):
        right=min(left+port['period_ns'],port['end_local_ns']);a=max(0,left-first);b=min(len(grids[0]),right-first)
        values={}
        for j,kind in enumerate(('voq_waiting','fifo_waiting')):
            area=sum(grids[j][a:b]);peak=max(peaks[j][a:b],default=0)
            values.update({kind+'_byte_ns':area,kind+'_mean_bytes':area/(right-left),kind+'_peak_bytes':peak})
        if any(values[k+'_peak_bytes'] for k in ('voq_waiting','fifo_waiting')):result[left]=values
    return result


def stage_oracle(execution,plan):
    report=read(execution/'result.json');nodes={(r['rank_id'],r['node_id']):r for r in iter_result(execution,'node_execution',report)}
    operations=list(iter_result(execution,'operation_registry',report));binding={}
    expected={};events=defaultdict(list)
    for op in operations:
        for e in op['endpoint_records']:binding[op['operation_id'],e['rank_id']]=e['rank_id'],e['local_record_id']
    def add(op,rank,stage,value):events[(*binding[op,rank],stage)].append(value)
    planned={t['transfer_id']:t for t in iter_result(execution,'planned_transfers',report)}
    for t in iter_result(execution,'transfers',report):
        op=planned[t['transfer_id']]['operation_id']
        add(op,t['src_rank'],'request_submit_first',t['send_start_ns'])
        add(op,t['src_rank'],'request_submit_last',t['send_start_ns'])
        add(op,t['src_rank'],'initiator_resources_complete',t['transaction_done_ns'])
        add(op,t.get('consumer_rank',t['dst_rank']),'data_ready',t['data_ready_ns'])
    for t in iter_result(execution,'local_deliveries',report):
        for stage,field in [('local_publish_first','ready_ns'),('local_publish_last','ready_ns'),('local_resources_complete','send_callback_ns')]:
            add(t['operation_id'],t['src_rank'],stage,t[field])
        add(t['operation_id'],t['dst_rank'],'data_ready',t['ready_ns'])
    for r in iter_result(execution,'endpoint_ops',report):expected[r['rank_id'],r['local_record_id'],'operation_complete']=(r['end_local_ns'],1)
    for op in operations:
        index=plan['communication_layer_ids'].index(op['layer_id'])
        for g in plan['rank_graphs']:
            rank=g['rank_id'];phase=g['layer_phases'][index]
            ids={'prepare':phase['phase_nodes']['prepare' if op['kind']=='dispatch' else 'local_merge']}
            if 'local_prepare_nodes' in phase:ids['local_prepare']=phase['local_prepare_nodes'][op['kind']]
            for prefix,node in ids.items():
                if node is None:continue
                for part in ('start','end'):add(op['operation_id'],rank,prefix+'_'+part,nodes[rank,node][part+'_ns'])
    for key,values in events.items():expected[key]=((min if key[-1].endswith('_first') else max)(values),len(values))
    actual={(r['rank_id'],r['local_record_id'],r['stage']):(r['time_ns'],r['event_count']) for r in iter_result(execution,'operation_stages',report)}
    assert actual==expected,(set(actual)^set(expected),[(k,v,expected.get(k)) for k,v in actual.items() if expected.get(k)!=v][:5])
    return len(actual)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    a=parser.parse_args();root=Path(__file__).resolve().parents[2];out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    binary=a.build_dir.resolve()/'output/cosim/ns3.44-cosim-service';example=root/'examples/first_round';fixture=root/'fixtures/m13_endpoint_service'
    commands=[]
    def invoke(label,argv,fail=False):
        argv=list(map(str,argv));started=datetime.now(timezone.utc).isoformat();begin=time.monotonic()
        r=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=360)
        for stream in ('stdout','stderr'):(out/f'{label}.{stream}.log').write_text(getattr(r,stream))
        commands.append(dict(label=label,argv=argv,cwd=str(root),started_utc=started,exit_code=r.returncode,wall_seconds=time.monotonic()-begin))
        save(out/'commands.json',commands);assert (r.returncode!=0) if fail else r.returncode==0,(label,r.stderr)
    def cli(label,*words,fail=False):invoke(label,[sys.executable,'-m','moe_ub_cosim',*words],fail)
    scenario=yaml.safe_load((fixture/'scenario.yaml').read_text())
    for key in ('service','layer','system'):scenario[key]=str((fixture/scenario[key]).resolve())
    scenario['backend']['reference_file']=str((fixture/scenario['backend']['reference_file']).resolve())
    scenario['network']['fabric_file']=str((fixture/scenario['network']['fabric_file']).resolve())
    (out/'scenario.yaml').write_text(yaml.safe_dump(scenario,sort_keys=False))
    invoke('resolve',[sys.executable,'-m','moe_ub_cosim.scenario','resolve','--input',out/'scenario.yaml','--backend',binary,'--output-dir',out/'resolved'])
    config=read(example/'run.json');config['backend']=str(binary)
    config['inputs']={k:str(out/'resolved'/(k+'.json' if k!='network' else k)) for k in ('service','layer','system','network')}
    for key in ('record_policy','export_policy'):config[key]=str(example/config[key])
    observation=read(root/'fixtures/m14_operation_queue/observation.json');reports={};cost={}
    for label in ('disabled','enabled','without-truth'):
        chosen=deepcopy(observation)
        if label=='disabled':chosen.pop('schema_version');chosen.pop('statistics')
        if label=='without-truth':chosen['record_provenance']=False
        config['observation']=str(save(out/(label+'-observation.json'),chosen))
        cli(label,'run','--config',save(out/(label+'-run.json'),config),'--output',out/label)
        cli('validate-'+label,'validate','--run',out/label)
        reports[label]=read(out/label/'raw/execution/result.json')
        assert reports[label]['normal_exit'] and reports[label]['network_drain']['idle']
        probe=read(out/label/'logs/resources-run/probe.json')
        cost[label]={k:probe[k] for k in ('wall_seconds','peak_sampled_group_rss_bytes','max_waited_child_rss_kib')}
        cost[label]['file_bytes']=sum(p.stat().st_size for p in (out/label).rglob('*') if p.is_file())
    base=reports['disabled']
    for label in ('enabled','without-truth'):
        assert reports[label]['simulation_end_ns']==base['simulation_end_ns']
        for field in ('node_execution','transfers','local_deliveries','native_starts','native_completions','endpoint_ops'):
            assert list(iter_result(out/'disabled/raw/execution',field))==list(iter_result(out/label/'raw/execution',field)),(label,field)
        assert read(out/label/'raw/execution/ports/4-4/observation-audit.json')==read(out/'disabled/raw/execution/ports/4-4/observation-audit.json')
    plan=read(next((out/'enabled/raw/graphs').glob('*/plan.json')))
    stages=stage_oracle(out/'enabled/raw/execution',plan)
    events=list(iter_port_records(out/'enabled/raw/execution',out/'enabled/raw/execution/ports/4-4','queue-state-events.jsonl'))
    def check_queue(directory,chosen):
        clocks,ports=validate_observation(chosen,[2,7,12],1000000);assert len(ports)==1
        expected=queue_oracle(events,ports[0],clocks['switch',4]);rows=list(stored_rows(directory/'observed','queue_windows.csv'))
        actual={r['local_window_start_ns']:{k:v for k,v in r.items() if k.startswith(('voq_','fifo_'))} for r in rows}
        assert actual==expected,(len(actual),len(expected))
        packets={r['local_window_start_ns'] for r in stored_rows(directory/'observed','port_windows.csv')}
        silent=sum(r['local_window_start_ns'] not in packets and (r['voq_waiting_byte_ns'] or r['fifo_waiting_byte_ns'])>0 for r in rows)
        return len(rows),silent
    count,silent=check_queue(out/'enabled',observation);assert silent>0
    assert list(stored_rows(out/'enabled/observed','endpoint_stages.jsonl'))==list(stored_rows(out/'without-truth/observed','endpoint_stages.jsonl'))
    cli('freeze','slice','create','--from-run',out/'enabled','--output',out/'slice')
    selected=deepcopy(observation);selected['clocks'][-1].update(offset_ns=-3,drift_ppb=100000000)
    selected['switches'][0].update(start_local_ns=2000,end_local_ns=34001,period_ns=263)
    initial=[r for r in events if local_time(r['time_ns'],selected['clocks'][-1])<=2000][-1]
    assert initial['voq_waiting_bytes'] or initial['fifo_waiting_bytes']
    sample=read(example/'sample.json');sample.pop('period_ns');sample.update(observation=str(save(out/'resample-observation.json',selected)),export_policy=str(example/'export-policy.json'))
    cli('sample','sample','--slice',out/'slice','--config',save(out/'sample.json',sample),'--output',out/'sample')
    cli('validate-sample','validate','--run',out/'sample','--allow-partial-observation')
    sampled_count,sampled_silent=check_queue(out/'sample',selected)
    replay=read(example/'replay.json');replay.update(backend=str(binary),export_policy=str(example/'export-policy.json'))
    cli('replay','replay','--slice',out/'slice','--config',save(out/'replay.json',replay),'--output',out/'replay')
    for name in ('port_windows.csv','queue_windows.csv','endpoint_stages.jsonl'):
        assert list(stored_rows(out/'enabled/observed',name))==list(stored_rows(out/'replay/observed',name)),name
    # Detailed packet history alone cannot be silently upgraded to queue data.
    cli('freeze-old','slice','create','--from-run',out/'disabled','--output',out/'old-slice')
    cli('reject-old-queues','sample','--slice',out/'old-slice','--config',out/'sample.json','--output',out/'old-upgrade',fail=True)
    tiny=deepcopy(observation);tiny['resource_budget']['max_event_bytes_per_port']=1
    config['observation']=str(save(out/'tiny-budget-observation.json',tiny))
    cli('reject-event-budget','run','--config',save(out/'tiny-budget-run.json',config),'--output',out/'tiny-budget',fail=True)
    failed=read(out/'tiny-budget/manifest.json');assert not failed['normal_exit']
    assert 'Observation event byte budget exceeded' in (out/'tiny-budget/raw/backend.stderr.log').read_text()
    save(out/'summary.json',dict(passed=True,simulation_end_ns=base['simulation_end_ns'],physical_packets=base['network_drain']['started_packets'],
        operation_stage_events=stages,queue_state_events=len(events),nonzero_queue_windows=count,silent_nonempty_windows=silent,
        sampled_nonzero_queue_windows=sampled_count,sampled_silent_nonempty_windows=sampled_silent,independent_nanosecond_oracle=True,
        execution_and_wire_time_unchanged=True,replay_identical=True,old_packet_only_upgrade_rejected=True,event_budget_rejected=True,resource_comparison=cost))
    print('PASS real operation stages, waiting-byte oracle, passive collection, public sample/replay and legacy refusal')


if __name__=='__main__':main()
