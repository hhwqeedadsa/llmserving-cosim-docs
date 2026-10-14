"""Fresh public resolution/run/replay and passive CBFC fact validation."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import sys
import yaml
from moe_ub_cosim.network_facts import checked_rows
from moe_ub_cosim.record_storage import iter_result
from moe_ub_cosim.research_storage import stored_rows

def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n');return p

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    a=parser.parse_args();root=Path(__file__).resolve().parents[2]
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);backend=a.backend.resolve()
    commands=[];reports={};summaries={}
    def cli(label,*args,fail=False):
        argv=[sys.executable,'-m','moe_ub_cosim',*map(str,args)]
        started=datetime.now(timezone.utc).isoformat()
        with (out/(label+'.stdout.log')).open('w') as so,(out/(label+'.stderr.log')).open('w') as se:
            done=subprocess.run(argv,cwd=root,stdout=so,stderr=se,timeout=300)
        commands.append(dict(argv=argv,cwd=str(root),started_utc=started,exit_code=done.returncode))
        save(out/'commands.json',commands)
        assert bool(done.returncode)==fail,(label,done.returncode)
    f=root/'fixtures/m13_endpoint_service';example=root/'examples/first_round'
    scenario=yaml.safe_load((f/'scenario.yaml').read_text())
    for k in ('service','layer','system'):scenario[k]=str((f/scenario[k]).resolve())
    scenario['backend']['reference_file']=str((f/scenario['backend']['reference_file']).resolve())
    scenario['network']['fabric_file']=str((f/scenario['network']['fabric_file']).resolve())
    attrs=scenario['backend']['attributes']
    attrs.update({'ns3::UbLdstThread::StoreRequestSize':4096,'ns3::UbLdstThread::LoadResponseSize':4096,
                  'ns3::UbPort::CbfcInitCreditCell':64,'ns3::UbPort::CbfcCtrlCrdRtrThldCell':32})
    (out/'scenario.yaml').write_text(yaml.safe_dump(scenario,sort_keys=False))
    cli('resolve','resolve','--config',out/'scenario.yaml','--backend',backend,'--output',out/'resolved')
    obs=read(root/'fixtures/m14_operation_queue/observation.json')
    obs.update(schema_version='moe-ub-observation-config/3',recording=dict(schema_version='moe-ub-observation-recording/1',
        mode='aggregate',detail_ranges=[],truth_categories=['business_data','attached_metadata','dispatch_count','combine_flag'],
        max_detail_events_per_port=100000,max_window_attribution_entries=4096))
    ports=[dict(node_id=4,port_id=i) for i in range(5)]
    for label,end,limit in [('off',None,0),('on',1000000,200000),('boundary',3000,200000),('overflow',1000000,12)]:
        observation=deepcopy(obs)
        if end:observation.update(schema_version='moe-ub-observation-config/6',network_facts=dict(
            schema_version='moe-ub-network-facts/1',ports=ports,end_ns=end,max_records=limit))
        config=read(example/'run.json');config['backend']=str(backend)
        config['host']['artifact_roots']=[str(out)]
        config['inputs']={k:str(out/'resolved'/(k+'.json' if k!='network' else k)) for k in ('service','layer','system','network')}
        for k in ('record_policy','export_policy'):config[k]=str(example/config[k])
        config['observation']=str(save(out/(label+'-observation.json'),observation))
        cli(label,'run','--config',save(out/(label+'-run.json'),config),'--output',out/label,fail=label=='overflow')
        if label=='overflow':
            assert not read(out/label/'manifest.json')['normal_exit']
            assert 'Network facts budget exceeded' in (out/label/'raw/backend.stderr.log').read_text()
            continue
        cli(label+'-validate','validate','--run',out/label)
        execution=out/label/'raw/execution';report=read(execution/'result.json');reports[label]=report
        assert report['normal_exit'] and report['network_drain']['idle']
        if label=='off':continue
        rows=list(checked_rows(execution,report));events=Counter(r['event'] for r in rows)
        assert rows==list(stored_rows(out/label/'truth','network_facts.jsonl'))
        assert not any('network_facts' in p.name for p in (out/label/'observed').iterdir())
        assert events['initial']==events['final']==5 and events['tx'] and events['credit_decision']
        assert any(r['event']=='credit' and r['data']['delta_cells']>0 for r in rows)
        summaries[label]=dict(events=dict(events),simulation_end_ns=report['simulation_end_ns'],
            blocked_probes=sum(r['event']=='credit_decision' and r['data']['blocked'] for r in rows),
            capture_end_ns=report['network_facts_capture']['end_ns'])
        assert report['simulation_end_ns']==reports['off']['simulation_end_ns']
        for field in ('node_execution','transfers','local_deliveries','native_starts','native_completions','endpoint_ops'):
            assert list(iter_result(execution,field))==list(iter_result(out/'off/raw/execution',field)),field
        assert read(execution/'ports/4-4/observation-audit.json')==read(out/'off/raw/execution/ports/4-4/observation-audit.json')
    cli('freeze','slice','create','--from-run',out/'on','--output',out/'slice')
    replay=read(example/'replay.json');replay.update(backend=str(backend),export_policy=str(example/'export-policy.json'))
    replay['host']['artifact_roots']=[str(out)]
    cli('replay','replay','--slice',out/'slice','--config',save(out/'replay.json',replay),'--output',out/'replay')
    assert list(stored_rows(out/'on/truth','network_facts.jsonl'))==list(stored_rows(out/'replay/truth','network_facts.jsonl'))
    public=root/'examples/native_first_round'
    scenario=yaml.safe_load((public/'scenario.yaml').read_text())
    for k in ('service','layer','system'):scenario[k]=str((public/scenario[k]).resolve())
    scenario['backend']['reference_file']=str((public/scenario['backend']['reference_file']).resolve())
    scenario['network']['fabric_file']=str((public/scenario['network']['fabric_file']).resolve())
    (out/'public-scenario.yaml').write_text(yaml.safe_dump(scenario,sort_keys=False))
    cli('public-resolve','resolve','--config',out/'public-scenario.yaml','--backend',backend,'--output',out/'public-input')
    config=read(public/'run-network.json');config['backend']=str(backend);config['host']['artifact_roots']=[str(out)]
    config['inputs']={k:str(out/'public-input'/(k+'.json' if k!='network' else k)) for k in ('service','layer','system','network')}
    for k in ('observation','record_policy','export_policy'):config[k]=str((public/config[k]).resolve())
    cli('public-run','run','--config',save(out/'public-run.json',config),'--output',out/'public-run')
    cli('public-validate','validate','--run',out/'public-run')
    public_report=read(out/'public-run/raw/execution/result.json')
    assert sum(1 for _ in checked_rows(out/'public-run/raw/execution',public_report))>10
    save(out/'summary.json',dict(passed=True,cases=summaries,passive_execution_and_wire=True,
        overflow_rejected=True,replay_identical=True,observed_private_separation=True,fresh_public_example=True))
    print('PASS bounded CBFC network facts, passive execution/wire, fresh public run/export/replay')

if __name__=='__main__':main()
