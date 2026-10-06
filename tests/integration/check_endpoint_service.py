"""Real preparation costs, directed start-time rates, frozen replay and coverage."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import yaml

from moe_ub_cosim.record_storage import iter_result
from moe_ub_cosim.frozen_replay import network_input


def read(path):return json.loads(path.read_text())
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n');return path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[2]
    out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    binary=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    runtime=binary.with_name('ns3.44-cosim-native-routing-test')
    fixture=root/'fixtures/m13_endpoint_service';example=root/'examples/first_round'
    c=yaml.safe_load((fixture/'scenario.yaml').read_text())
    for key in ('service','layer','system'):c[key]=str((fixture/c[key]).resolve())
    c['backend']['reference_file']=str((fixture/c['backend']['reference_file']).resolve())
    c['network']['fabric_file']=str((fixture/c['network']['fabric_file']).resolve())
    commands=[]
    def invoke(label,argv,fail=False):
        argv=list(map(str,argv));started=datetime.now(timezone.utc).isoformat()
        result=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=360)
        for stream in ('stdout','stderr'):(out/f'{label}.{stream}.log').write_text(getattr(result,stream))
        commands.append(dict(label=label,argv=argv,cwd=str(root),started_utc=started,exit_code=result.returncode))
        save(out/'commands.json',commands)
        assert (result.returncode!=0) if fail else result.returncode==0,(label,result.returncode,result.stderr)
        return result
    def cli(label,*words,fail=False):return invoke(label,[sys.executable,'-m','moe_ub_cosim',*words],fail)

    reports={};graphs={};durations={}
    for label in ('baseline','prepared','limited'):
        chosen=deepcopy(c)
        if label=='baseline':
            layers=read(Path(c['layer']))
            for g in layers['groups']:
                for layer in g['layers']:layer.pop('endpoint_prepare')
            chosen['layer']=str(save(out/'baseline-layers.json',layers))
        if label!='limited':chosen['port_service']['intervals']=[]
        path=out/f'{label}.yaml';path.write_text(yaml.safe_dump(chosen,sort_keys=False))
        invoke('resolve-'+label,[sys.executable,'-m','moe_ub_cosim.scenario','resolve','--input',path,
            '--backend',binary,'--output-dir',out/('resolved-'+label)])
        config=read(example/'run.json');resolved=out/('resolved-'+label)
        config['inputs']={name:str(resolved/(name+'.json' if name!='network' else name)) for name in ('service','layer','system','network')}
        for key in ('record_policy','export_policy'):config[key]=str(example/config[key])
        config['observation']=str(fixture/'observation.json');config['backend']=str(binary)
        cli(label,'run','--config',save(out/f'{label}-run.json',config),'--output',out/label)
        raw=out/label/'raw';report=read(raw/'execution/result.json');reports[label]=report
        assert report['normal_exit'] and report['network_drain']['idle'] and report['execution_budget_ns']==1000000
        assert read(out/label/'manifest.json')['coverage']['observation_complete']
        graph=read(next((raw/'graphs').glob('*/plan.json')));graphs[label]=graph
        events={(r['rank_id'],r['node_id']):r for r in iter_result(raw/'execution','node_execution',report)}
        cost={}
        for g in graph['rank_graphs']:
            for node in g['nodes']:
                e=events[g['rank_id'],node['node_id']]
                assert all(e['start_ns']>=events[g['rank_id'],d]['end_ns'] for d in node['deps'])
                if node['kind']=='COMP':
                    assert e['end_ns']-e['start_ns']==node['duration_ns']
                    cost[g['rank_id'],node['node_id']]=node['duration_ns']
        durations[label]=cost
        for t in iter_result(raw/'execution','transfers',report):
            assert t['committed_bytes']==t['bytes'] and t['send_callbacks']==t['receive_callbacks']==1
        cli('validate-'+label,'validate','--run',out/label)
    assert graphs['prepared']==graphs['limited']
    assert durations['prepared']==durations['limited']
    expected=deepcopy(durations['baseline']);changed=0
    for child in graphs['baseline']['layers']:
        local=Counter(t['src_rank'] for t in child['input']['tokens'])
        experts=Counter(child['input']['expert_placement'][e] for t in child['input']['tokens'] for e in t['expert_ids'])
        for g in graphs['baseline']['rank_graphs']:
            for n in g['nodes']:
                if n.get('layer_id')!=child['input']['layer_id']:continue
                extra=(500*local[2] if g['rank_id']==2 and n['name']=='dispatch-quant-pack' else
                       (1400 if n['layer_id']==2 else 700)*experts[12] if g['rank_id']==12 and n['name']=='expert-result-pack' else 0)
                if extra:expected[g['rank_id'],n['node_id']]+=extra;changed+=1
    assert changed and expected==durations['prepared']
    assert reports['prepared']['simulation_end_ns']>reports['baseline']['simulation_end_ns']
    assert reports['limited']['simulation_end_ns']>reports['prepared']['simulation_end_ns']
    effective=read(out/'limited/raw/execution/effective_backend.json')
    assert effective['live_port_service']['intervals'][0]['base_rate_bps']==400000000000
    assert effective['live_port_service']['intervals'][0]['rate_bps']==1000000000

    # Physical packet starts exactly on a boundary, and serialization spanning
    # its end. The response direction remains independently at the base rate.
    network=out/'physical-network';shutil.copytree(out/'resolved-baseline/network',network)
    # Isolate rate service with fixed routes. Under spray, changed ACK/credit
    # timing legitimately changes worker interleaving and salt-to-message order.
    attributes=(network/'network_attribute.txt').read_text()
    key='default ns3::UbLdstApi::UsePacketSpray "true"'
    assert key in attributes
    (network/'network_attribute.txt').write_text(attributes.replace(key,'default ns3::UbLdstApi::UsePacketSpray "false"'))
    invoke('physical-base',[runtime,network,out/'physical-base',0,2,1])
    base=read(out/'physical-base/result.json')
    first=min((p for p in base['paths'] if p['node']==0),key=lambda p:p['time_ns'])
    start=first['time_ns'];port=first['port']
    services=dict(schema_version='moe-ub-port-service/1',execution_end_ns=1000000,
                  intervals=[dict(node_id=0,port_id=port,start_ns=start,end_ns=start+1,rate_bps=1000000000)])
    service_file=save(out/'physical-service.json',services)
    invoke('physical-limited',[runtime,network,out/'physical-limited',0,2,1,service_file])
    actual=read(out/'physical-limited/result.json');selected=[p for p in actual['paths'] if p['node']==0 and p['port']==port]
    at_boundary=[p for p in selected if p['time_ns']==start]
    assert len(at_boundary)==1 and at_boundary[0]['rate_bps']==1000000000
    assert at_boundary[0]['tx_duration_ns']>1
    restored=[p for p in selected if p['time_ns']>=start+1]
    assert restored and all(p['rate_bps']==400000000000 for p in restored)
    assert all(p['rate_bps']==400000000000 for p in actual['paths'] if p['node']!=0 or p['port']!=port)
    assert Counter((p['node'],p['port'],p['packet_bytes'],p['opcode']) for p in base['paths'])==Counter(
        (p['node'],p['port'],p['packet_bytes'],p['opcode']) for p in actual['paths'])
    assert base['completions']!=actual['completions']

    # Replay starts the same graph from zero and accepts only enumerated changes.
    cli('freeze','slice','create','--from-run',out/'prepared','--output',out/'slice')
    replay=read(example/'replay.json');replay.update(backend=str(binary),export_policy=str(example/'export-policy.json'))
    cli('replay-identical','replay','--slice',out/'slice','--config',save(out/'replay.json',replay),'--output',out/'replay')
    old=reports['prepared'];new=read(out/'replay/raw/execution/result.json')
    for name in old['record_storage']['fields']:
        assert list(iter_result(out/'prepared/raw/execution',name,old))==list(iter_result(out/'replay/raw/execution',name,new)),name
    assert old['simulation_end_ns']==new['simulation_end_ns']
    shutil.copytree(out/'slice/inputs/network',out/'override')
    override=read(out/'override/backend.json');override['port_service']=deepcopy(c['port_service'])
    override['attributes']['ns3::UbLdstApi::UsePacketSpray']='false';save(out/'override/backend.json',override)
    observation=read(fixture/'observation.json');observation['switches'][0]['end_local_ns']=1000
    replay.update(schema_version='moe-ub-replay/2',network=str(out/'override'),max_simulation_ns=1000000,
                  observation=str(save(out/'partial-observation.json',observation)))
    cli('replay-partial','replay','--slice',out/'slice','--config',save(out/'partial-replay.json',replay),'--output',out/'partial')
    partial=read(out/'partial/manifest.json');assert partial['normal_exit'] and not partial['coverage']['observation_complete']
    cli('reject-partial-comparison','validate','--run',out/'partial',fail=True)
    cli('accept-declared-partial','validate','--run',out/'partial','--allow-partial-observation')
    observation['switches'][0]['end_local_ns']=1000000;save(out/'expanded-observation.json',observation)
    replay['observation']=str(out/'expanded-observation.json')
    cli('replay-expanded','replay','--slice',out/'slice','--config',save(out/'expanded-replay.json',replay),'--output',out/'expanded')
    cli('validate-expanded','validate','--run',out/'expanded')
    assert read(out/'expanded/manifest.json')['simulation_end_ns']==partial['simulation_end_ns']
    command=read(out/'expanded/raw/command.json');assert set(command['network']['overrides'])=={'port_service','ns3::UbLdstApi::UsePacketSpray'}
    # A too-small explicit execution budget must preserve a failure, not pass.
    bounded=deepcopy(override);bounded['port_service'].update(execution_end_ns=100,intervals=[])
    shutil.copytree(out/'override',out/'timeout-network');save(out/'timeout-network/backend.json',bounded)
    short=deepcopy(replay);short.update(network=str(out/'timeout-network'),max_simulation_ns=100)
    observation['switches'][0].update(end_local_ns=100,period_ns=100);short['observation']=str(save(out/'short-observation.json',observation))
    cli('timeout','replay','--slice',out/'slice','--config',save(out/'short-replay.json',short),'--output',out/'timeout',fail=True)
    failure=read(out/'timeout/raw/execution/failure.json');assert not failure['normal_exit'] and 'max_simulation_ns' in failure['error']
    rejected=0
    for label,mutate in [('overlap',lambda x:x['port_service']['intervals'].append(deepcopy(x['port_service']['intervals'][0]))),
                         ('nonpositive',lambda x:x['port_service']['intervals'][0].update(rate_bps=0)),
                         ('outside',lambda x:x['port_service']['intervals'][0].update(end_ns=1000001))]:
        bad=deepcopy(override);mutate(bad)
        invoke(label,[binary,'--check-backend',save(out/(label+'.json'),bad)],fail=True);rejected+=1
    bad=deepcopy(override);bad['execution']['chunk_bytes']=2048
    shutil.copytree(out/'override',out/'illegal');save(out/'illegal/backend.json',bad)
    try:network_input(out/'slice/inputs/network',out/'illegal-copy',out/'illegal',binary)
    except ValueError:rejected+=1
    else:raise AssertionError('arbitrary override accepted')
    save(out/'summary.json',dict(passed=True,actual_cost_nodes=changed,
        cases={k:dict(simulation_end_ns=v['simulation_end_ns'],physical_packets=v['network_drain']['started_packets']) for k,v in reports.items()},
        physical_boundary=at_boundary[0],restored_packets=len(restored),replay_identical=True,
        partial_observation_rejected=True,expanded_replay_completed=True,execution_timeout_rejected=True,
        invalid_backend_or_replay_rejected=rejected))
    print('PASS endpoint costs, actual start-time port service, restricted frozen replay and execution/observation coverage')


if __name__=='__main__':main()
