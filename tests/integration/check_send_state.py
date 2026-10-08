"""Actual scheduler counts, passive execution, bounded capture and public reuse."""
import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import yaml

from moe_ub_cosim.observation import local_time
from moe_ub_cosim.record_storage import iter_result
from moe_ub_cosim.research_storage import stored_rows


def read(path):return json.loads(path.read_text())
def save(path,value):
    if path.exists():assert read(path)==value,('Refuse to replace existing test input',path)
    else:path.write_text(json.dumps(value,indent=2)+'\n')
    return path


def oracle(run,slots):
    execution=run/'raw/execution';report=read(execution/'result.json')
    nodes={(n['graph_id'],n['rank_id'],n['node_id']):n for n in iter_result(execution,'node_execution')}
    intervals=defaultdict(list);sends={};graphs=0;planned={}
    for path in sorted((run/'raw/graphs').glob('*/plan.json')):
        plan=read(path);graph=plan['graph_id'];graphs+=1
        assert plan['input']['cann_profile']['aiv_lanes']==2
        planned.update((p['transfer_id'],dict(**p,graph_id=graph)) for p in plan['transfers'])
        for rank_graph in plan['rank_graphs']:
            rank=rank_graph['rank_id']
            for node in rank_graph['nodes']:
                if node['kind']!='SEND':continue
                key=graph,rank,node['node_id'];n=nodes[key];sends[key]=n
                ready=max([plan['ready_ns']]+[nodes[graph,rank,d]['end_ns'] for d in node['deps']])
                assert ready<=n['start_ns']<=n['end_ns']
                intervals[rank].append((ready,n['start_ns'],n['end_ns']))
    # Independently confirm which callback ends each actual SEND occupancy.
    matched=set();early_data=loads=0
    for t in iter_result(execution,'transfers'):
        p=planned[t['transfer_id']];key=p['graph_id'],t['src_rank'],p['send_node_id']
        n=sends[key];assert n['start_ns']==t['send_start_ns'] and n['end_ns']==t['send_done_ns']
        assert t['send_done_ns']>=t['transaction_done_ns']>=t['data_ready_ns']
        early_data+=t['data_ready_ns']<t['send_done_ns'];loads+=t['operation']=='LOAD';matched.add(key)
    for t in iter_result(execution,'local_deliveries'):
        p=planned[t['transfer_id']];key=p['graph_id'],t['src_rank'],p['send_node_id'];n=sends[key]
        assert n['start_ns']==n['end_ns']==t['send_callback_ns'];matched.add(key)
    assert matched==set(sends)
    changes=defaultdict(dict);raw=list(iter_result(execution,'send_state'))
    for row in raw:changes[row['rank_id']][row['time_ns']]=row
    area=active_area=0;idle_compute=False
    for rank,rows in changes.items():
        times=sorted(set(rows)|{t for interval in intervals[rank] for t in interval})
        state=None
        for a,b in zip(times,times[1:]):
            if a in rows:state=rows[a]
            assert state is not None
            ready=sum(r<=a<s for r,s,e in intervals[rank]);active=sum(s<=a<e for r,s,e in intervals[rank])
            assert (state['ready_send_count'],state['active_send_count'])==(ready,active),(rank,a,state,ready,active)
            assert not ready or active==slots
            area+=ready*(b-a);active_area+=active*(b-a)
            if ready==active==0 and any(n['rank_id']==rank and n['kind']=='COMP' and
                    n['start_ns']<=a<n['end_ns'] for n in nodes.values()):idle_compute=True
    # This input has two send lanes: one slot creates pending work, while two
    # slots admit both lanes. Check the zero-wait case explicitly as well.
    assert (area>0 if slots==1 else area==0)
    assert early_data>0 and loads>0 and idle_compute
    assert max(r['active_send_count'] for r in raw)==slots
    assert any(a['time_ns']==b['time_ns'] for a,b in zip(raw,raw[1:]))
    return dict(graphs=graphs,send_nodes=len(sends),state_records=len(raw),send_wait_node_ns=area,
        occupied_slot_ns=active_area,early_data_before_sender_release=early_data,load_transfers=loads,
        peak_active_sends=slots,compute_without_ready_sends=True,simulation_end_ns=report['simulation_end_ns'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--resume-completed',action='store_true',help='Reuse successful recorded commands; recheck their results and never retry a failed command')
    args=parser.parse_args();root=Path(__file__).resolve().parents[2];out=args.output_dir.resolve()
    if args.resume_completed:assert out.is_dir() and not (out/'summary.json').exists()
    else:out.mkdir(parents=True,exist_ok=False)
    example=root/'examples/send_state';binary=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    commands=read(out/'commands.json') if args.resume_completed else []
    completed={c['label']:c for c in commands}
    def cli(label,*words,fail=False):
        argv=list(map(str,[sys.executable,'-m','moe_ub_cosim',*words]));start=time.monotonic()
        if label in completed:
            previous=completed[label]
            assert previous['argv']==argv and previous['cwd']==str(root) and previous['exit_code']==0 and not fail
            return
        entry=dict(label=label,argv=argv,cwd=str(root),started_utc=datetime.now(timezone.utc).isoformat())
        r=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=400)
        for stream in ('stdout','stderr'):(out/f'{label}.{stream}.log').write_text(getattr(r,stream))
        commands.append(dict(**entry,exit_code=r.returncode,wall_seconds=time.monotonic()-start))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        assert (r.returncode!=0) if fail else r.returncode==0,(label,r.stdout[-2000:],r.stderr[-2000:])
    def config(name):
        c=read(example/name)
        for k in ('record_policy','export_policy'):
            if k in c:c[k]=str((example/c[k]).resolve())
        if 'backend' in c:c['backend']=str(binary)
        c['host']['artifact_roots']=[str(out)]
        return c
    source=yaml.safe_load((example/'scenario.yaml').read_text())
    for k in ('service','layer','system'):source[k]=str((example/source[k]).resolve())
    source['backend']['reference_file']=str((example/source['backend']['reference_file']).resolve())
    source['network']['fabric_file']=str((example/source['network']['fabric_file']).resolve())
    results={};configs={};observations={}
    for slots in (1,2):
        scenario=deepcopy(source);scenario['execution']['send_slots_per_rank']=slots
        if slots==1:
            service=read(Path(source['service']))
            for request in service['requests']:
                request['generated_tokens']=2;request['decode_routes']*=2
            scenario['service']=str(save(out/'two-round-service.json',service))
        scenario_path=out/f'scenario-{slots}.yaml';scenario_path.write_text(yaml.safe_dump(scenario,sort_keys=False))
        resolved=out/f'resolved-{slots}'
        cli(f'resolve-{slots}','resolve','--config',scenario_path,'--backend',binary,'--output',resolved)
        for enabled in (False,True):
            label=f's{slots}-'+('on' if enabled else 'off');observation=read(example/'observation.json')
            if not enabled:observation.pop('send_state');observation['schema_version']='moe-ub-observation-config/3'
            if slots==1:
                observation['record_provenance']=False;observation['recording']['truth_categories']=[]
            c=config('run.json');c['inputs']={k:str(resolved/(k+'.json' if k!='network' else k)) for k in ('service','layer','system','network')}
            c['observation']=str(save(out/f'{label}-observation.json',observation));configs[label]=c;observations[label]=observation
            cli(label,'run','--config',save(out/f'{label}-run.json',c),'--output',out/label)
            cli('validate-'+label,'validate','--run',out/label)
        a,b=(out/f's{slots}-{flag}' for flag in ('off','on'))
        ar,br=(read(p/'raw/execution/result.json') for p in (a,b))
        assert ar['normal_exit'] and br['normal_exit'] and ar['network_drain']['idle'] and br['network_drain']['idle']
        assert ar['simulation_end_ns']==br['simulation_end_ns']
        assert 'send_state' not in ar['record_storage']['fields'] and 'send_state_capture' not in ar
        for field in ('node_execution','transfers','local_deliveries','native_starts','native_completions','endpoint_ops','operation_stages'):
            assert list(iter_result(a/'raw/execution',field))==list(iter_result(b/'raw/execution',field)),(slots,field)
        for port in (a/'raw/execution/ports').iterdir():
            assert read(port/'observation-audit.json')==read(b/'raw/execution/ports'/port.name/'observation-audit.json')
        for table in ('endpoint_ops.jsonl','endpoint_stages.jsonl','port_windows.csv','queue_windows.csv'):
            assert list(stored_rows(a/'observed',table))==list(stored_rows(b/'observed',table)),(slots,table)
        results[f'slots_{slots}']=oracle(b,slots)
    assert results['slots_1']['graphs']==2 and results['slots_2']['graphs']==1
    # Validate a physically isolated observed directory, with no raw/truth siblings.
    shutil.copytree(out/'s2-on/observed',out/'isolated/observed')
    cli('isolated-observed','validate','--observed',out/'isolated/observed')
    cli('freeze','slice','create','--from-run',out/'s2-on','--output',out/'slice')
    cli('inspect','slice','inspect','--slice',out/'slice','--verify')
    sampled=deepcopy(observations['s2-on'])
    for clock in sampled['clocks']:
        if clock['device_type']=='rank':clock.update(offset_ns=-19,drift_ppb=-12345)
    for switch in sampled['switches']:switch['period_ns']*=10
    sample=config('sample.json');sample.pop('period_ns');sample['observation']=str(save(out/'sample-observation.json',sampled))
    cli('sample','sample','--slice',out/'slice','--config',save(out/'sample.json',sample),'--output',out/'sample')
    expected=list(stored_rows(out/'s2-on/observed','endpoint_send_state.jsonl'))
    clocks={c['device_id']:c for c in sampled['clocks'] if c['device_type']=='rank'}
    for row in expected:row['time_local_ns']=local_time(row['time_local_ns'],clocks[row['rank_id']])
    assert expected==list(stored_rows(out/'sample/observed','endpoint_send_state.jsonl'))
    cli('replay','replay','--slice',out/'slice','--config',save(out/'replay.json',config('replay.json')),'--output',out/'replay')
    cli('validate-replay','validate','--run',out/'replay')
    assert list(stored_rows(out/'s2-on/observed','endpoint_send_state.jsonl'))==list(stored_rows(out/'replay/observed','endpoint_send_state.jsonl'))
    cli('freeze-old','slice','create','--from-run',out/'s2-off','--output',out/'old-slice')
    cli('reject-upgrade','sample','--slice',out/'old-slice','--config',out/'sample.json','--output',out/'old-upgrade',fail=True)
    assert not (out/'old-upgrade').exists()
    tiny=deepcopy(observations['s2-on']);tiny['send_state'].update(max_records_per_rank=2,max_total_records=6)
    limited=deepcopy(configs['s2-on']);limited['observation']=str(save(out/'limited-observation.json',tiny))
    cli('reject-record-limit','run','--config',save(out/'limited-run.json',limited),'--output',out/'limited',fail=True)
    assert not read(out/'limited/manifest.json')['normal_exit']
    assert 'Send-state record budget exceeded' in (out/'limited/raw/backend.stderr.log').read_text()
    save(out/'summary.json',dict(passed=True,cases=results,execution_and_packet_bytes_times_unchanged=True,
        independent_dependency_and_callback_oracle=True,cross_graph_continuity=True,truth_disabled_capture=True,
        observed_only_validation=True,local_clock_sampling=True,frozen_replay_identical=True,
        old_capture_upgrade_rejected=True,record_limit_rejected=True))
    print('PASS actual send-state counts, passivity, graph reuse, local clocks, sample/replay and limits')


if __name__=='__main__':main()
