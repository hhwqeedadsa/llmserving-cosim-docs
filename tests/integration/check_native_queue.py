"""Public fresh run/replay, branch occupancy oracle and passive native diagnostics."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import yaml

from moe_ub_cosim.native_queue import checked_rows
from moe_ub_cosim.record_storage import iter_result
from moe_ub_cosim.research_storage import stored_rows


def read(p): return json.loads(p.read_text())
def save(p, value): p.write_text(json.dumps(value, indent=2)+'\n'); return p


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build-dir',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();root=Path(__file__).resolve().parents[2]
    out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    binary=a.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    example=root/'examples/first_round';fixture=root/'fixtures/m13_endpoint_service'
    commands=[];summaries={};runs={}
    def cli(label,*words,fail=False):
        argv=[sys.executable,'-m','moe_ub_cosim',*map(str,words)]
        started=datetime.now(timezone.utc).isoformat()
        result=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=300)
        for stream in ('stdout','stderr'):(out/f'{label}.{stream}.log').write_text(getattr(result,stream))
        commands.append(dict(argv=argv,cwd=str(root),started_utc=started,exit_code=result.returncode))
        save(out/'commands.json',commands)
        assert (result.returncode!=0) if fail else result.returncode==0,(label,result.stderr)
    scenario=yaml.safe_load((fixture/'scenario.yaml').read_text())
    for key in ('service','layer','system'):scenario[key]=str((fixture/scenario[key]).resolve())
    layers=read(Path(scenario['layer']))
    for group in layers['groups']:
        for layer in group['layers']:layer['native_profile']['combine']='STORE'
    scenario['layer']=str(save(out/'layers.json',layers))
    scenario['backend']['reference_file']=str((fixture/scenario['backend']['reference_file']).resolve())
    scenario['network']['fabric_file']=str((fixture/scenario['network']['fabric_file']).resolve())
    scenario['backend']['profile_by_role']['combine']='native_store'
    scenario['execution'].update(chunk_bytes=8192,send_slots_per_rank=2)
    attrs=scenario['backend']['attributes']
    attrs.update({'ns3::UbLdstThread::StoreRequestSize':4096,'ns3::UbLdstThread::LoadResponseSize':4096})
    base=read(root/'fixtures/m14_operation_queue/observation.json')
    base['schema_version']='moe-ub-observation-config/3'
    base['recording']=dict(schema_version='moe-ub-observation-recording/1',mode='aggregate',detail_ranges=[],
        truth_categories=['business_data','attached_metadata','dispatch_count','combine_flag'],
        max_detail_events_per_port=100000,max_window_attribution_entries=4096)
    policy=dict(schema_version='moe-ub-native-queue-config/1',rank_ids=[2,7,12],end_ns=1000000,
                max_records=100000,max_snapshot_items=1024)
    for label,threads,quota,pool in [('off',1,1,256),('quota',1,1,256),('pool',3,32,1),
                                    ('split',48,32,256),('boundary',1,1,256),('overflow',1,1,256)]:
        attrs.update({'ns3::UbLdstInstance::ThreadNum':threads,'ns3::UbLdstThread::StoreOutstanding':quota,
                      'ns3::UbLdstApi::RequestIdCount':pool})
        path=out/(label+'-scenario.yaml');path.write_text(yaml.safe_dump(scenario,sort_keys=False))
        cli(label+'-resolve','resolve','--config',path,'--backend',binary,'--output',out/(label+'-resolved'))
        obs=deepcopy(base)
        if label!='off':
            obs.update(schema_version='moe-ub-observation-config/5',native_queue=deepcopy(policy))
        if label=='boundary':obs['native_queue']['end_ns']=5000
        if label=='overflow':obs['native_queue']['max_records']=6
        config=read(example/'run.json');config['backend']=str(binary)
        config['host']['artifact_roots']=[str(out)]
        config['inputs']={k:str(out/(label+'-resolved')/(k+'.json' if k!='network' else k))
                          for k in ('service','layer','system','network')}
        for key in ('record_policy','export_policy'):config[key]=str(example/config[key])
        config['observation']=str(save(out/(label+'-observation.json'),obs))
        cli(label,'run','--config',save(out/(label+'-run.json'),config),'--output',out/label,fail=label=='overflow')
        if label=='overflow':
            assert not read(out/label/'manifest.json')['normal_exit']
            error=(out/label/'raw/backend.stderr.log').read_text()
            assert any(f'Native queue {kind} budget exceeded' in error for kind in ('identity','record'))
            continue
        cli(label+'-validate','validate','--run',out/label)
        execution=out/label/'raw/execution';report=read(execution/'result.json');runs[label]=execution
        assert report['normal_exit'] and report['network_drain']['idle']
        if label=='off':continue
        rows=list(checked_rows(execution,report))
        assert rows==list(stored_rows(out/label/'truth','native_queue.jsonl'))
        assert not any('native_queue' in p.name for p in (out/label/'observed').iterdir())
        events=Counter(r['event'] for r in rows)
        assert events['initial']==events['final']==threads*3
        # Independent issue/release ownership conservation, including the shared
        # pool across workers. Snapshot metadata is checked against these events.
        pending={};issued=Counter();released=Counter()
        for r in rows:
            affected=r['affected'];rank=r['rank_id']
            if r['event'] in ('request_issued','response_released'):
                key=(rank,affected['task_id'],affected['thread_id'],affected['offset'],affected['bytes'])
                if r['event']=='request_issued':
                    assert key not in pending;pending[key]=True;issued[affected['identity']['transfer_id']]+=1
                else:
                    assert key in pending;del pending[key];released[affected['identity']['transfer_id']]+=1
            actual={(rank,t['task_id'],t['thread_id'],t['offset'],t['bytes']) for t in r['pending']}
            assert actual=={k for k in pending if k[0]==rank},(label,r['event'])
        if label!='boundary':assert not pending and issued==released
        if label=='quota':assert events['outstanding_blocked'] and not events['request_pool_blocked']
        if label=='pool':assert events['request_pool_blocked'] and not events['outstanding_blocked']
        if label=='boundary':assert report['native_queue_capture']['end_ns']==5000
        summaries[label]=dict(events=dict(events),simulation_end_ns=report['simulation_end_ns'],issued=dict(issued))
    for label in ('quota','boundary'):
        assert read(runs[label]/'result.json')['simulation_end_ns']==read(runs['off']/'result.json')['simulation_end_ns']
        for field in ('node_execution','transfers','local_deliveries','native_starts','native_completions','endpoint_ops'):
            assert list(iter_result(runs[label],field))==list(iter_result(runs['off'],field)),(label,field)
        assert read(runs[label]/'ports/4-4/observation-audit.json')==read(runs['off']/'ports/4-4/observation-audit.json')
    flags=[t['transfer_id'] for t in iter_result(runs['quota'],'planned_transfers')
           if t['payload_bytes']==32 and t['transfer_id'] in summaries['quota']['issued']]
    assert flags and all(summaries['quota']['issued'][t]==1 and summaries['split']['issued'][t]==32 for t in flags)
    cli('freeze','slice','create','--from-run',out/'quota','--output',out/'slice')
    replay=read(example/'replay.json');replay.update(backend=str(binary),export_policy=str(example/'export-policy.json'))
    replay['host']['artifact_roots']=[str(out)]
    cli('replay','replay','--slice',out/'slice','--config',save(out/'replay.json',replay),'--output',out/'replay')
    assert list(stored_rows(out/'quota/truth','native_queue.jsonl'))==list(stored_rows(out/'replay/truth','native_queue.jsonl'))
    save(out/'summary.json',dict(passed=True,cases=summaries,passive_execution_and_wire=True,
        independent_request_conservation=True,worker_split_changes_status_requests=[1,32],replay_identical=True,
        overflow_rejected=True))
    print('PASS native queue branch oracle, passive execution, fragmentation confound, bounded truth and replay')


if __name__=='__main__':main()
