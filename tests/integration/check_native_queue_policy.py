"""Fixed-budget STORE queues: real protocol oracle and fresh public A3 workflow."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import yaml
from moe_ub_cosim.native_queue import checked_rows, checked_requests
from moe_ub_cosim.record_storage import iter_result
from moe_ub_cosim.user_io import read, save, timestamp


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build-dir',type=Path,required=True)
    p.add_argument('--previous-backend',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();root=Path(__file__).resolve().parents[2]
    out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    binary=a.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    oracle=binary.with_name('ns3.44-cosim-native-queue-policy-test')
    example=root/'examples/a3_segment';commands=[];summaries={};executions={}
    def invoke(label,argv,fail=False):
        argv=list(map(str,argv));started=timestamp()
        r=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=300)
        for stream in ('stdout','stderr'):(out/(label+'.'+stream+'.log')).write_text(getattr(r,stream))
        commands.append(dict(argv=argv,cwd=str(root),started_utc=started,exit_code=r.returncode))
        save(out/'commands.json',commands)
        assert (r.returncode!=0) if fail else r.returncode==0,(label,r.stderr)
        return r
    def cli(label,*args,**kwargs):return invoke(label,[sys.executable,'-m','moe_ub_cosim',*args],**kwargs)
    for label,policy,producers,quota,pool in (
        ('default','default',1,32,256),('fifo','fifo',1,32,256),('single','producer_round_robin',1,32,256),
        ('multi','producer_round_robin',3,32,256),('pool','producer_round_robin',3,32,2),
        ('worker','producer_round_robin',3,2,256),('missing','producer_round_robin',0,32,256),
        ('unknown','unknown',3,32,256)):
        invoke('protocol-'+label,[oracle,root/'third_party/ns-3-ub/scratch/2nodes_single-tp',
            out/('protocol-'+label),policy,producers,quota,pool],fail=label in ('missing','unknown'))
        if label not in ('missing','unknown'):summaries[label]=read(out/('protocol-'+label)/'result.json')
    assert summaries['default']==summaries['fifo']==summaries['single']
    settings=yaml.safe_load((example/'scenario-round-robin.yaml').read_text())
    for key in ('service','layer','system'):settings[key]=str(example/settings[key])
    settings['network']['fabric_file']=str(example/settings['network']['fabric_file'])
    settings['backend']['reference_file']=str(example/settings['backend']['reference_file'])
    # Resolve against each actual binary; old backend has no new policy attribute.
    for label,policy,capture,cores,zero in (
        ('old',None,False,2,False),('fifo-off','fifo',False,2,False),('fifo-audit','fifo',True,2,False),
        ('rr','producer_round_robin',True,2,False),('rr-off','producer_round_robin',False,2,False),
        ('zero','producer_round_robin',True,2,True),('one-fifo','fifo',True,1,False),
        ('one-rr','producer_round_robin',True,1,False)):
        backend=a.previous_backend.resolve() if label=='old' else binary
        scenario=deepcopy(settings);attrs=scenario['backend']['attributes']
        if policy is None:attrs.pop('ns3::UbLdstThread::StoreQueuePolicy')
        else:attrs['ns3::UbLdstThread::StoreQueuePolicy']=policy
        # Exact revision binding for both binaries, retaining the reference profiles.
        catalog=json.loads(invoke(label+'-catalog',[backend,'--backend-catalog']).stdout)
        ref=yaml.safe_load(Path(scenario['backend']['reference_file']).read_text())
        ref['ns3_backend']['revision']=catalog['ub_revision']
        reference=out/(label+'-reference.yaml');reference.write_text(yaml.safe_dump(ref,sort_keys=False))
        scenario['backend']['reference_file']=str(reference)
        layers=read(Path(settings['layer']))
        for layer in layers['groups'][0]['layers']:
            layer['cann_profile']['platform_aiv_cores']=cores
            if zero:
                # Positive per-token cost 1 split across Top-2 creates a
                # zero-duration core work item without invalidating the model.
                layer['compute_profile_ns']['attention_route_pack']=1
        if zero:
            service=read(Path(settings['service']))
            for layer in service['groups'][0]['full_model_profile']['layers']:
                if 'dispatch_quant_pack' in layer['per_token_ns']:
                    layer['per_token_ns']['dispatch_quant_pack']=1
            save(out/(label+'-service.json'),service);scenario['service']=str(out/(label+'-service.json'))
        save(out/(label+'-layers.json'),layers);scenario['layer']=str(out/(label+'-layers.json'))
        config=out/(label+'-scenario.yaml');config.write_text(yaml.safe_dump(scenario,sort_keys=False))
        cli(label+'-resolve','resolve','--config',config,'--backend',backend,'--output',out/(label+'-resolved'))
        source=read(example/'source-run-round-robin.json')
        source['backend']=str(backend);source['host']['artifact_roots']=[str(out)]
        source['inputs']={k:str(out/(label+'-resolved')/(k+'.json' if k!='network' else k))
                          for k in ('service','layer','system','network')}
        source['observation']=str(example/('queue-observation.json' if capture else 'observation.json'))
        for key in ('record_policy','export_policy'):source[key]=str(example/source[key])
        save(out/(label+'-source.json'),source)
        segment=read(example/'segment-round-robin.json');segment['source_run']=label+'-source.json'
        save(out/(label+'-segment.json'),segment)
        cli(label+'-prepare','segment','prepare','--config',out/(label+'-segment.json'),'--output',out/(label+'-input'))
        cli(label+'-run','run','--config',out/(label+'-input/run.json'),'--output',out/label)
        cli(label+'-validate','validate','--run',out/label)
        execution=out/label/'raw/execution';executions[label]=execution;report=read(execution/'result.json')
        if capture:
            rows=list(checked_rows(execution,report));requests=list(checked_requests(execution,report))
            assert requests and all(type(r['identity']['producer']) is int and 0<=r['identity']['producer']<cores for r in requests)
            assert all(r['store_capacity']==32 and r['request_pool_capacity']==256 and r['thread_id']==0 for r in rows)
            if policy=='producer_round_robin':assert all(r['store_queue_policy']==policy for r in rows)
            assert not any('native_request' in str(p) for p in (out/label/'observed').rglob('*'))
        summaries[label+'-public']=dict(simulation_end_ns=report['simulation_end_ns'],packets=report['network_drain']['started_packets'],
                                        native_tasks=report['record_storage']['fields']['transfers']['records'])
    fields=('planned_transfers','transfers','local_deliveries','node_execution','endpoint_ops','operation_registry',
            'native_starts','native_completions','operation_stages')
    for x,y in [('old','fifo-off'),('fifo-off','fifo-audit'),('rr','rr-off'),('one-fifo','one-rr')]:
        for field in fields:assert list(iter_result(executions[x],field))==list(iter_result(executions[y],field)),(x,y,field)
        for path in (executions[x]/'ports').glob('*/observation-audit.json'):
            assert read(path)==read(executions[y]/path.relative_to(executions[x])),(x,y,path)
    def ranges(label):
        return sorted((r['identity']['graph_id'],r['identity']['transfer_id'],r['identity']['producer'],r['offset'],r['bytes'])
                      for r in checked_requests(executions[label],read(executions[label]/'result.json')))
    assert ranges('fifo-audit')==ranges('rr')
    # An old frozen input can acquire the explicit FIFO default, then use the
    # same sealed A3 plan with the single policy override. No graph rewrite.
    cli('freeze-old','segment','freeze','--from-run',out/'old','--output',out/'old-slice')
    replay=read(example/'replay.json');replay.update(schema_version='moe-ub-replay/2',max_simulation_ns=1000000,
        backend=str(binary),export_policy=str(example/'export-policy.json'))
    replay['host']['artifact_roots']=[str(out)]
    replay['observation']=str(example/'queue-observation.json')
    save(out/'replay.json',replay)
    cli('replay-fifo','replay','--slice',out/'old-slice','--config',out/'replay.json','--output',out/'replay-fifo')
    shutil.copytree(out/'old-slice/inputs/network',out/'override-network')
    network=read(out/'override-network/backend.json');network['attributes']['ns3::UbLdstThread::StoreQueuePolicy']='producer_round_robin'
    save(out/'override-network/backend.json',network);replay['network']=str(out/'override-network');save(out/'replay.json',replay)
    cli('replay-rr','replay','--slice',out/'old-slice','--config',out/'replay.json','--output',out/'replay-rr')
    for label,original in [('replay-fifo','fifo-audit'),('replay-rr','rr')]:
        cli(label+'-validate','validate','--run',out/label)
        for field in (*fields,'native_queue','native_requests'):
            assert list(iter_result(out/label/'raw/execution',field))==list(iter_result(executions[original],field)),(label,field)
    save(out/'summary.json',dict(passed=True,cases=summaries,legacy_fifo_execution_and_wire_equal=True,
        passive_audit_equal=True,all_store_ranges_equal=True,old_sealed_plan_replay=True,
        independent_protocol_order_capacity_release=True,missing_identity_rejected=True))
    print('PASS fixed-budget native queues, byte ranges, callbacks, default FIFO and public replay')


if __name__=='__main__':main()
