"""Hand-counted locality, causal local callbacks and mixed real UB execution."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    binaries=args.build_dir.resolve()/'output/cosim'
    fixture=root/'fixtures/m6_locality'
    system=root/'fixtures/m1_compute/system.json'
    network=root/'fixtures/m2_single_layer/network'
    commands=[];cases={};failures={}

    def save(path,value):
        path.write_text(json.dumps(value,indent=2)+'\n');return path

    def invoke(label,argv,expected=0,stdin=None):
        argv=[str(a) for a in argv];start=datetime.now(timezone.utc).isoformat()
        result=subprocess.run(argv,cwd=root,input=stdin,capture_output=True,text=True,timeout=90)
        for stream in ('stdout','stderr'):(out/f'{label}.{stream}.log').write_text(getattr(result,stream))
        if stdin is not None:(out/f'{label}.stdin.log').write_text(stdin)
        commands.append(dict(label=label,argv=argv,cwd=str(root),started_utc=start,
            ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=result.returncode))
        save(out/'commands.json',commands)
        assert result.returncode==expected,(label,result.returncode,result.stderr)
        return result

    def observation(label,mode):
        if mode is None:return '-'
        config=read(root/'fixtures/m2_single_layer/observation.json')
        config.update(window_end_ns=1000000,record_provenance=mode)
        return save(out/f'{label}.observation.json',config)

    def check_execution(plan,result,graph_id=None):
        events={(e['rank_id'],e['node_id']):e for e in result['node_execution']
                if graph_id is None or e.get('graph_id')==graph_id}
        local={t['transfer_id']:t for t in result['local_deliveries']}
        fabric={t['transfer_id']:t for t in result['transfers']}
        for graph in plan['rank_graphs']:
            rank=graph['rank_id'];intervals=[]
            for node in graph['nodes']:
                e=events[rank,node['node_id']]
                assert all(e['start_ns']>=events[rank,d]['end_ns'] for d in node['deps'])
                if node['kind']=='COMP':
                    assert e['end_ns']-e['start_ns']==node['duration_ns']
                    intervals.append((e['start_ns'],e['end_ns']))
            intervals.sort()
            assert all(a[1]<=b[0] for a,b in zip(intervals,intervals[1:]))
        for t in plan['transfers']:
            send=events[t['src_rank'],t['send_node_id']];recv=events[t['dst_rank'],t['recv_node_id']]
            if t['transport_profile_id']=='same-chip-local-reference':
                d=local[t['transfer_id']]
                assert t['transfer_id'] not in fabric and 'ub_task_id' not in t
                assert d['ready_ns']==send['start_ns']==send['end_ns']==d['send_callback_ns']
                assert d['recv_registered_ns']==recv['start_ns']
                assert recv['end_ns']==d['recv_callback_ns']==max(d['ready_ns'],recv['start_ns'])
                assert d['send_callbacks']==d['recv_callbacks']==1 and not d['active'] and not d['pending_events']
            else:
                d=fabric[t['transfer_id']]
                assert send['end_ns']==d['transaction_done_ns'] and recv['end_ns']>=d['data_ready_ns']
                assert d['bytes']==t['payload_bytes']
        assert len(result['wqe_starts'])==len(result['wqe_completions'])==len(result['transfers'])
        assert all(all(r[k]==0 for k in ('compute_slots','send_slots','pending_events','pending_tasks'))
                   for r in result['rank_resources'])

    def check_truth(execution,result):
        ids={t['transfer_id'] for t in result['transfers']}
        truth=rows(execution/'truth/transfer_registry.jsonl')
        assert {t['transfer_id'] for t in truth}==ids
        for observed,record in zip(rows(execution/'observed/port_counters.jsonl'),rows(execution/'truth/port_byte_truth.jsonl')):
            assert observed['counted_packet_bytes']==sum(record[k] for k in
                ('business_payload_bytes','application_metadata_bytes','data_protocol_overhead_bytes','control_packet_bytes','unattributed_bytes'))
            assert record['unattributed_bytes']==0
            if not ids:assert observed['counted_packet_bytes']==0

    def layer(label,c,mode=True,sequence=False):
        cp=save(out/f'{label}.input.json',c);plan_dir=out/f'{label}.plan'
        invoke(label+'-generate',[sys.executable,'-m','moe_ub_cosim.sequence' if sequence else 'moe_ub_cosim.generate',
                                '--input',cp,'--output-dir',plan_dir])
        invoke(label,[binaries/('ns3.44-cosim-sequence' if sequence else 'ns3.44-cosim-layer'),
                      plan_dir,system,network,observation(label,mode),out/label])
        result=read(out/label/'result.json')
        plans=[read(plan_dir/str(i)/'plan.json') for i in range(2)] if sequence else [read(plan_dir/'plan.json')]
        for p in plans:check_execution(p,result,p['graph_id'] if sequence else None)
        if mode is True:check_truth(out/label,result)
        finish=max(e['time_ns'] for e in result['graph_completions'])
        cases[label]=dict(writes=len(result['transfers']),bytes=sum(t['bytes'] for t in result['transfers']),
                         local_deliveries=len(result['local_deliveries']),finished_ns=finish)
        return result,plans

    mixed=read(fixture/'mixed-layer.json');local=read(fixture/'local-layer.json')
    normal,plans=layer('mixed-on',mixed)
    assert cases['mixed-on']['writes']==10 and cases['mixed-on']['bytes']==1280
    assert cases['mixed-on']['local_deliveries']==4 and plans[0]['expert_input_counts']==[2,5,3,2]
    assert Counter(c['locality'] for c in plans[0]['contributions'])==dict(same_rank=4,same_chip=2,cross_chip=5)
    prepare=[e for e in normal['node_execution'] if e['rank_id'] in (2,5) and e['node_id']==1]
    assert len(prepare)==2 and all(e['start_ns']==0 and e['end_ns']==100 for e in prepare)
    assert layer('mixed-off',mixed,False)[0]==normal
    assert layer('mixed-absent',mixed,None)[0]==normal
    for p in (out/'mixed-on/observed').iterdir():assert p.read_bytes()==(out/'mixed-off/observed'/p.name).read_bytes()
    pure,_=layer('local',local)
    assert cases['local']==dict(writes=0,bytes=0,local_deliveries=2,finished_ns=750)
    assert not pure['target_writes'] and pure['peak_wqes']==[0,0,0,0]
    slow=deepcopy(local);slow['deployment']['local_prepare_ns_per_vector']['dispatch']+=1000
    delayed,_=layer('local-slow',slow)
    assert cases['local-slow']['finished_ns']==1750
    assert delayed['local_deliveries'][0]['ready_ns']-pure['local_deliveries'][0]['ready_ns']==1000
    own=deepcopy(local);own['tokens'][0]['src_rank']=5
    own_result,_=layer('same-rank',own)
    assert not own_result['local_deliveries'] and not own_result['transfers']
    assert cases['same-rank']['finished_ns']==570
    zero=deepcopy(local);zero['deployment']['local_prepare_ns_per_vector']=dict(dispatch=0,combine=0)
    layer('zero-local-profile',zero)
    assert cases['zero-local-profile']['finished_ns']==570
    seq=dict(schema_version='moe-ub-sequence-fixture/1',steps=[deepcopy(local),deepcopy(local)])
    seq['steps'][1].update(graph_id='g-locality-next',wave_id='D.locality1');seq['steps'][1]['tokens'][0]['token_index']+=1
    result,_=layer('two-local-steps',seq,sequence=True)
    assert result['step_submissions'][1]['time_ns']==750 and cases['two-local-steps']['finished_ns']==1500
    assert cases['two-local-steps']['local_deliveries']==4
    legacy=deepcopy(mixed);legacy['schema_version']='moe-ub-layer-fixture/1';del legacy['deployment']
    old,_=layer('legacy-no-placement',legacy)
    assert len(old['transfers'])==14 and not old['local_deliveries'] and sum(t['bytes'] for t in old['transfers'])==1792

    config=read(fixture/'service.json');sl=read(fixture/'service-layer.json')
    def service(label,c,l,mode=True):
        cp=save(out/f'{label}.input.json',c);lp=save(out/f'{label}.layer.json',l)
        argv=[sys.executable,'-m','moe_ub_cosim.service','--input',cp,'--layer',lp,
              '--backend',binaries/'ns3.44-cosim-service','--system',system,'--network',network,'--output-dir',out/label]
        op=observation(label,mode)
        if mode is not None:argv+=['--observation',op]
        invoke(label,argv)
        front=read(out/label/'service_result.json');result=read(out/label/'execution/result.json')
        assert result['normal_exit']
        evidence=read(out/label/'input_evidence.json')
        assert [r['weight_bytes'] for r in evidence['model']['ranks']]==[255872,280448,231296]
        assert [r['kv_capacity_blocks'] for r in evidence['model']['ranks']]==[3,3,2]
        for wave in front['waves']:
            check_execution(read(out/label/'graphs'/wave['graph_id']/'plan.json'),result,wave['graph_id'])
        if mode is True:check_truth(out/label/'execution',result)
        assert result['network_drain']['idle']
        assert all(not r['request_blocks'] and r['kv_used_bytes']==0 for r in front['memory_final'])
        cases[label]=dict(waves=len(front['waves']),writes=len(result['transfers']),
            bytes=sum(t['bytes'] for t in result['transfers']),local_deliveries=len(result['local_deliveries']),finished_ns=front['finished_ns'])
        return front,result
    front,result=service('service-on',config,sl)
    assert cases['service-on']['waves']==6 and cases['service-on']['writes']==6
    assert cases['service-on']['local_deliveries']==18 and cases['service-on']['bytes']==1152
    assert service('service-off',config,sl,False)==(front,result)
    assert service('service-absent',config,sl,None)==(front,result)
    delayed_config=deepcopy(config);delayed_layer=deepcopy(sl)
    delayed_layer['deployment']['local_prepare_ns_per_vector']['dispatch']+=5000
    delayed_config['groups'][0]['deployment']=deepcopy(delayed_layer['deployment'])
    slow_front,slow_result=service('service-slow',delayed_config,delayed_layer)
    assert slow_front['waves'][1]['submitted_ns']>front['waves'][1]['submitted_ns']
    assert cases['service-slow']['finished_ns']>cases['service-on']['finished_ns']
    assert cases['service-slow']['writes']==6 and cases['service-slow']['local_deliveries']==18
    local_config=deepcopy(config);local_config['requests']=local_config['requests'][:1]
    lf,lr=service('service-local-only',local_config,sl)
    assert not lr['transfers'] and lr['network_drain']['started_packets']==lr['network_drain']['arrived_packets']==0
    assert len(lr['local_deliveries'])==8

    # Corrupt only executable contracts; backend must reject before creating a run.
    def bad_dependency(p):
        t=next(t for t in p['transfers'] if t['transport_profile_id']=='same-chip-local-reference')
        g=next(g for g in p['rank_graphs'] if g['rank_id']==t['src_rank'])
        next(n for n in g['nodes'] if n['node_id']==t['send_node_id'])['deps']=[]
    def bad_task(p):
        next(t for t in p['transfers'] if t['transport_profile_id']=='same-chip-local-reference')['ub_task_id']=1
    def overflow_profile(p):
        for target in (p['input']['deployment'],p['deployment']):
            target['local_prepare_ns_per_vector']['dispatch']=2**63-1
    mutations={
        'lost-local-dependency':bad_dependency,
        'local-ub-task':bad_task,
        'false-classification':lambda p:p['contributions'][1].update(locality='cross_chip'),
        'downgrade-schema':lambda p:p.update(schema_version='moe-ub-execution-plan/1'),
        'missing-delivery':lambda p:p['transfers'].pop(0),
        'duplicate-global-die':lambda p:p['input']['deployment']['ranks'][1].update(die_id='die-2'),
        'same-chip-different-server':lambda p:p['input']['deployment']['ranks'][0].update(server_id='server1'),
        'local-profile-overflow':overflow_profile,
    }
    for name,mutate in mutations.items():
        directory=out/(name+'.plan');shutil.copytree(out/'mixed-on.plan',directory)
        p=read(directory/'plan.json');mutate(p);save(directory/'plan.json',p)
        result=invoke('reject-'+name,[binaries/'ns3.44-cosim-layer',directory,system,network,'-',out/name],1)
        assert not (out/name).exists();failures[name]=result.stderr.splitlines()[-1]
    # Valid changed physical ownership must also fail the service's immutable run declaration.
    shared=out/'changed-deployment';shutil.copytree(out/'service-on',shared)
    shutil.rmtree(shared/'execution')
    p=read(shared/'graphs/g0/plan.json')
    for target in (p['deployment'],p['input']['deployment']):target['ranks'][0]['die_id']='replacement-die'
    save(shared/'graphs/g0/plan.json',p)
    commit=next(r for r in rows(out/'service-on/protocol.jsonl') if r.get('type')=='COMMIT')
    result=invoke('reject-changed-deployment',[binaries/'ns3.44-cosim-service',config['run_id'],shared,system,network,'-',shared/'execution',1000000],1,json.dumps(commit)+'\n')
    failure=read(shared/'execution/failure.json')
    assert failure['now_ns']==0 and not failure['transfers'] and not failure['local_deliveries']
    failures['changed-deployment']=result.stderr.splitlines()[-1]
    save(out/'summary.json',dict(passed=True,cases=cases,failures=failures))
    print('PASS: three locality classes, no local fabric traffic, causal callbacks, fixed steps and service lifecycle')


if __name__=='__main__':main()
