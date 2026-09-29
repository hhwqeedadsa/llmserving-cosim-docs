"""Check one real scheduler P/D lifecycle and UB KV transfer, including failures."""
import argparse
from collections import Counter
from copy import deepcopy
import csv
import datetime
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from check_delay import read, sha
from check_service_boundaries import check_sparse_graph
from check_variants import rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2];build=args.build_dir.resolve();out=args.output_dir.resolve()
    out.mkdir(parents=True,exist_ok=False)
    fixture=root/'fixtures/m4_pd_handoff';binary=build/'output/cosim/ns3.44-cosim-service'
    system=root/'fixtures/m1_compute/system.json';config=read(fixture/'service.json');layer=read(fixture/'layer.json')
    commands=[];cases={};failures={}
    def invoke(label,argv,stdin=None):
        start=datetime.datetime.now(datetime.timezone.utc).isoformat()
        result=subprocess.run([str(v) for v in argv],cwd=root,input=stdin,capture_output=True,text=True,timeout=90)
        for stream in ('stdout','stderr'):(out/(label+'.'+stream+'.log')).write_text(getattr(result,stream))
        if stdin is not None:(out/(label+'.stdin.log')).write_text(stdin)
        commands.append(dict(label=label,argv=[str(v) for v in argv],cwd=str(root),started_utc=start,
            finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),exit_code=result.returncode))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        return result
    def command(label,c,network,observed=True,backend=binary):
        path=out/(label+'.input.json');path.write_text(json.dumps(c,indent=2)+'\n')
        argv=[sys.executable,'-m','moe_ub_cosim.service','--input',path,'--layer',fixture/'layer.json',
            '--backend',backend,'--system',system,'--network',network,'--output-dir',out/label]
        if observed is not None:
            obs=read(fixture/'observation.json');obs['record_provenance']=observed
            path=out/(label+'.observation.json');path.write_text(json.dumps(obs)+'\n');argv+=['--observation',path]
        return argv
    slow=out/'slow-network';shutil.copytree(fixture/'network',slow)
    links=list(csv.DictReader((slow/'topology.csv').open()))
    target=next(row for row in links if row['nodeId1']=='4')
    assert target['delay']=='20ns';target['delay']='2020ns'
    with (slow/'topology.csv').open('w') as file:
        writer=csv.DictWriter(file,fieldnames=links[0].keys(),lineterminator='\n');writer.writeheader();writer.writerows(links)
    for label,observed in (('on',True),('off',False),('absent',None),('repeat',True),('slow',True),('fp8',True),('arrival',True)):
        c=deepcopy(config)
        if label=='fp8':c['kv_dtype']='fp8'
        if label=='arrival':c['request']['arrival_ns']=9000
        network=slow if label=='slow' else fixture/'network'
        result=invoke(label,command(label,c,network,observed));assert result.returncode==0,(label,result.stderr)
        path=out/label;front=read(path/'service_result.json');report=read(path/'execution/result.json')
        manifest=read(path/'manifest.json')
        assert manifest['normal_exit'] and manifest['backend_exit_code']==0 and report['normal_exit'] and front['service_finished']
        assert manifest['kv_source']=='synthetic_prefill_then_ub_handoff'
        assert report['network_initializations']==1 and report['loaded_ranks_before_activation']==8
        assert report['bootstrap']==dict(finished_ranks=8,time_ns=0,business_nodes=0)
        assert front['scheduler_calls']==3 and [w['role'] for w in front['waves']]==['P','KV','D','D']
        assert front['pending']==front['waiting']==front['running']==front['inflight']==[0]*8
        assert front['done']==[0,0,0,0,1,0,0,0]
        request=front['requests'][0]
        assert request['status']=='FINISHED' and request['pd_state']=='FINISHED' and request['preemptions']==0
        assert request['num_computed_tokens']==6 and request['num_tokens_reached']==request['target_tokens']==7
        assert len(request['intertoken_latencies_ns'])==2
        waves=front['waves'];prefill,handoff,decode,last=waves
        assert prefill['submitted_ns']==c['request']['arrival_ns']
        assert prefill['finished_ns']==handoff['submitted_ns']<handoff['finished_ns']==decode['submitted_ns']
        assert decode['finished_ns']==last['submitted_ns']<last['finished_ns']==front['finished_ns']
        assert request['ttft_ns']==prefill['finished_ns']-c['request']['arrival_ns']
        assert sum(request['intertoken_latencies_ns'])==front['finished_ns']-prefill['finished_ns']
        assert report['service_finished_ns']==front['finished_ns']<=report['simulation_end_ns']<1000000
        assert report['network_drain']['idle'] and report['network_drain']['started_packets']==report['network_drain']['arrived_packets']
        assert report['active_wqes']==[0]*8 and report['csv_completed_tasks']==0
        assert all(r['finished'] and not r['remaining_nodes'] and all(r[k]==0 for k in
            ('cpu_slots','compute_slots','send_slots','pending_events','pending_tasks','stale_callbacks')) for r in report['rank_resources'])
        plans=[read(path/f"graphs/{w['graph_id']}/plan.json") for w in waves]
        assert [t['token_index'] for t in prefill['tokens']]==list(range(4))
        assert [t['expert_ids'] for t in prefill['tokens']]==c['request']['prefill_routes']
        assert [w['tokens'][0]['token_index'] for w in waves[2:]]==[4,5]
        for p in (plans[0],plans[2],plans[3]):check_sparse_graph(p,report,path/'execution')
        kv=plans[1];expected_kv=4096 if c['kv_dtype']=='bfloat16' else 2048
        assert kv['kv_payload_bytes']==expected_kv and sum(t['payload_bytes'] for t in kv['transfers'])==expected_kv
        pieces=2 if label=='fp8' else 3
        assert Counter(t['kv_component'] for t in kv['transfers'])=={'K':pieces,'V':pieces}
        cursor=0
        for t in kv['transfers']:
            assert t['pair_byte_range']==[cursor,cursor+t['payload_bytes']]
            assert t['pair_payload_bytes']==expected_kv and t['sources'][0]['token_range']==[0,4]
            assert t['sources'][0]['head_range']==[0,8] and t['sources'][0]['component'] in ('K','V')
            cursor+=t['payload_bytes']
        assert cursor==expected_kv
        actual={t['transfer_id']:t for t in report['transfers']}
        native_writes={t['task']:t for t in report['target_writes']}
        node_events={(e['graph_id'],e['rank_id'],e['node_id']):e for e in report['node_execution']}
        assert len(actual)==len(report['wqe_starts'])==len(report['wqe_completions'])==len(native_writes)==sum(len(p['transfers']) for p in plans)
        for p in plans:
            loads=[g for g in report['graph_loads'] if g['graph_id']==p['graph_id']]
            activation=[g for g in report['activation_order'] if g['graph_id']==p['graph_id']]
            assert [g['rank_id'] for g in loads]==[g['rank_id'] for g in activation]==p['rank_ids']
            assert all(g['time_ns']==p['ready_ns'] for g in loads+activation)
            for t in p['transfers']:
                got=actual[t['transfer_id']]
                assert got['submitted_tasks']==got['send_callbacks']==got['receive_callbacks']==1
                assert got['bytes']==got['committed_bytes']==t['payload_bytes']==native_writes[t['ub_task_id']]['bytes']
                assert not got['context_active'] and got['send_done_ns']==got['transaction_done_ns']
                assert got['data_ready_ns']==native_writes[t['ub_task_id']]['time_ns']
                assert got['receive_done_ns']==max(got['data_ready_ns'],got['receive_start_ns'])
                for rank,side,prefix in ((t['src_rank'],'send','send'),(t['dst_rank'],'recv','receive')):
                    event=node_events[p['graph_id'],rank,t[side+'_node_id']]
                    assert event['start_ns']==got[prefix+'_start_ns'] and event['end_ns']==got[prefix+'_done_ns']
        kv_actual=[actual[t['transfer_id']] for t in kv['transfers']]
        kv_data=max(t['data_ready_ns'] for t in kv_actual);kv_tx=max(t['transaction_done_ns'] for t in kv_actual)
        assert kv_data<=kv_tx==handoff['finished_ns']==decode['submitted_ns']
        assert all(e['start_ns']>=kv_tx for e in report['node_execution'] if e['graph_id']==decode['graph_id'])
        for rank in range(8):
            completion=next(g for g in report['graph_completions'] if g['graph_id']==kv['graph_id'] and g['rank_id']==rank)
            end=kv_tx if rank==0 else kv_data if rank==4 else handoff['submitted_ns']
            assert completion['time_ns']==end
        raw=(path/'execution/log/log.log').read_text()
        assert len(re.findall(r'(?:issue|callback),sys->id=\d+, tick=\d+, node->id=\d+,',raw))==2*len(report['node_execution'])
        assert len(report['graph_completions'])==20
        assert all(n['kind'] in ('SEND','RECV') for g in kv['rank_graphs'] for n in g['nodes'])
        records=front['memory_events'];events={r['event']:r for r in records}
        assert events['destination_reserved']['time_ns']==handoff['submitted_ns']
        block_bytes=expected_kv//4*c['block_size']
        for event in ('destination_reserved','handoff_submitted','handoff_complete'):
            states=events[event]['ranks'];assert states[0]['kv_used_bytes']==states[4]['kv_used_bytes']==block_bytes
            assert states[4]['running']==[] and states[4]['inflight']==0
        assert events['source_released']['time_ns']==kv_tx and events['source_released']['ranks'][0]['kv_used_bytes']==0
        assert events['decode_admitted']['ranks'][4]['kv_used_bytes']==block_bytes
        assert all(r['kv_used_bytes']==0 for r in events['finished']['ranks'])
        for event in records:
            for r in event['ranks']:
                assert r['used_bytes']==r['weight_bytes']+r['workspace_bytes']+r['communication_buffer_bytes']+r['kv_used_bytes']<=r['total_bytes']
                assert r['kv_used_bytes']==r['request_blocks']*r['kv_block_bytes']
                assert r['request_blocks']<=r['kv_capacity_blocks']
        protocol=rows(path/'protocol.jsonl')
        notifications=[m for m in protocol if m['type']=='DECIDE' and m['completed']]
        assert [m['completed'][0]['kind'] for m in notifications]==['WAVE_DONE','KV_DONE','WAVE_DONE','WAVE_DONE']
        assert notifications[1]['now_ns']==kv_tx and notifications[1]['available_rank_ids']==list(range(8))
        assert all(m['input_exhausted'] and not m['service_finished'] for m in protocol if m['type']=='COMMIT' and m['submissions'])
        assert len(protocol)==2*front['decisions']==2*report['decisions']
        observed_path=path/'execution/observed'
        if observed is not None:
            counters=rows(observed_path/'port_counters.jsonl');endpoints=rows(observed_path/'endpoint_ops.jsonl')
            assert len(endpoints)==32 and sum(e['kind']=='KV_TRANSFER' for e in endpoints)==8
            assert all(not set(e)&{'graph_id','request_id','transfer_id','operation_id','token_index'} for e in counters+endpoints)
            if observed:
                truth=rows(path/'execution/truth/port_byte_truth.jsonl')
                assert len(truth)==len(counters)
                for a,b in zip(counters,truth):assert a['counted_packet_bytes']==sum(b[k] for k in
                    ('business_payload_bytes','application_metadata_bytes','data_protocol_overhead_bytes','control_packet_bytes','unattributed_bytes'))
                registry=rows(path/'execution/truth/transfer_registry.jsonl')
                assert sum(t['payload_bytes'] for t in registry if t['operation_id']==kv['operations'][0]['operation_id'])==expected_kv
            else:assert not (path/'execution/truth').exists()
        else:assert not observed_path.exists()
        libraries={Path(v).resolve() for v in report['loaded_libraries'] if Path(v).name.startswith('libns3')}
        assert len(libraries)==10 and all(v.parent==build/'output/lib' for v in libraries)
        cases[label]=dict(front=front,report=report,plans=plans,protocol=protocol,kv_data_ready_ns=kv_data,kv_transaction_done_ns=kv_tx)
        print(f'PASS {label}: P done {prefill["finished_ns"]}, KV ready/done {kv_data}/{kv_tx}, D starts {decode["submitted_ns"]}, finish {front["finished_ns"]}',flush=True)
    normal=cases['on']
    def hashes(path):return {str(p.relative_to(path)):sha(p) for p in path.rglob('*') if p.is_file()}
    for label in ('off','absent','repeat'):
        assert cases[label]==normal,label
        assert hashes(out/label/'execution/network/runlog')==hashes(out/'on/execution/network/runlog')
    assert hashes(out/'off/execution/observed')==hashes(out/'on/execution/observed')
    delayed=cases['slow']
    assert delayed['front']['waves'][0]==normal['front']['waves'][0]
    assert delayed['kv_transaction_done_ns']>normal['kv_transaction_done_ns']
    assert delayed['front']['waves'][2]['submitted_ns']-normal['front']['waves'][2]['submitted_ns']==delayed['kv_transaction_done_ns']-normal['kv_transaction_done_ns']
    assert [p['transfers'] for p in normal['plans']]==[p['transfers'] for p in delayed['plans']]
    for label,key,value in (('layout','destination_layout','other'),('policy','kv_handoff_policy','async'),('dtype','kv_dtype','float32')):
        c=deepcopy(config);c[key]=value
        result=invoke('bad-'+label,command('bad-'+label,c,fixture/'network',None));assert result.returncode!=0
        assert not (out/('bad-'+label)/'execution').exists()
        failures[label]=dict(exit_code=result.returncode,error=result.stderr.splitlines()[-1])
    c=deepcopy(config);c['block_size']=1;c['request']['prompt_tokens']=5;c['request']['prefill_routes'].append([0,1])
    for rank in (4,5,6,7):
        c['npu_memory_bytes'][rank]=normal['front']['memory_events'][0]['ranks'][rank]['weight_bytes']+c['workspace_bytes'][rank]+c['communication_buffer_bytes'][rank]+1024
    result=invoke('capacity',command('capacity',c,fixture/'network',None))
    assert result.returncode!=0 and 'total D group capacity' in result.stderr and not (out/'capacity/execution').exists()
    failures['capacity']=dict(exit_code=result.returncode,error=result.stderr.splitlines()[-1])
    commits=[m for m in normal['protocol'] if m['type']=='COMMIT']
    for label in ('kv-size','kv-q','kv-range','last-member','partial-handoff','busy-handoff'):
        shared=out/(label+'-input')
        shutil.copytree(out/'on/bootstrap',shared/'bootstrap');shutil.copytree(out/'on/graphs',shared/'graphs')
        shutil.copyfile(out/'on/deployment.json',shared/'deployment.json')
        plan_path=shared/'graphs/g1/plan.json';plan=read(plan_path)
        if label=='kv-size':plan['transfers'][0]['payload_bytes']+=1
        if label=='kv-q':plan['transfers'][0]['kv_component']='Q'
        if label=='kv-range':plan['transfers'][1]['component_byte_range']=[0,768]
        if label=='last-member':(shared/'graphs/g1/layer.7.et').write_bytes(b'\x80')
        plan_path.write_text(json.dumps(plan,indent=2)+'\n')
        messages=deepcopy(commits[:2])
        if label=='partial-handoff':messages[1]['submissions'][0]['rank_ids']=[0,1,2,3]
        if label=='busy-handoff':
            messages=messages[:1];messages[0]['submissions'].append(deepcopy(commits[1]['submissions'][0]))
        result=invoke(label,[binary,config['run_id'],shared,system,fixture/'network','-',out/label,'1000000'],
            ''.join(json.dumps(m)+'\n' for m in messages))
        assert result.returncode==1 and not (out/label/'result.json').exists(),(label,result.stderr)
        failure=read(out/label/'failure.json')
        assert failure['now_ns']==(0 if label=='busy-handoff' else normal['front']['waves'][0]['finished_ns'])
        assert 'graph_id=g1' not in (out/label/'log/log.log').read_text()
        failures[label]=dict(exit_code=result.returncode,error=failure['error'],now_ns=failure['now_ns'])
    proxy=out/'crash-proxy.py'
    proxy.write_text('#!'+sys.executable+'\n'+'''import json,os,signal,subprocess,sys
from pathlib import Path
binary=Path(__file__).with_name('real-backend.path').read_text()
child=subprocess.Popen([binary,*sys.argv[1:]],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
try:
    for _ in range(2):
        print(child.stdout.readline().rstrip(),flush=True)
        child.stdin.write(sys.stdin.readline());child.stdin.flush()
    pending=json.loads(child.stdout.readline())
    child.terminate();code=child.wait(timeout=10)
    Path(sys.argv[-2],'crash-test.json').write_text(json.dumps(dict(pending_decision=pending,backend_exit_code=code)))
    os.kill(os.getpid(),signal.SIGTERM)
finally:
    if child.poll() is None:child.kill();child.wait()
''')
    proxy.chmod(0o700);(out/'real-backend.path').write_text(str(binary))
    result=invoke('crash',command('crash',config,fixture/'network',None,proxy))
    manifest=read(out/'crash/manifest.json');proof=read(out/'crash/execution/crash-test.json');failure=read(out/'crash/failure.json')
    assert result.returncode!=0 and not manifest['normal_exit'] and manifest['backend_exit_code']==proof['backend_exit_code']==-15
    assert proof['pending_decision']['completed'][0]['kind']=='KV_DONE'
    assert failure['active_graphs']=={'handoff':'g1'} and failure['requests'][0]['pd_state']=='KV_INFLIGHT'
    assert failure['memory_events'][-1]['event']=='handoff_submitted'
    assert failure['memory_events'][-1]['ranks'][0]['kv_used_bytes']==failure['memory_events'][-1]['ranks'][4]['kv_used_bytes']==16384
    assert not (out/'crash/service_result.json').exists()
    failures['crash']=dict(exit_code=-15,unacknowledged_completion_ns=proof['pending_decision']['now_ns'],source_kv_retained=True)
    summary=dict(passed=True,actual_runs=len(cases),failures=failures,
        cases={name:dict(finished_ns=r['front']['finished_ns'],prefill_done_ns=r['front']['waves'][0]['finished_ns'],
            kv_data_ready_ns=r['kv_data_ready_ns'],kv_transaction_done_ns=r['kv_transaction_done_ns'],
            decode_start_ns=r['front']['waves'][2]['submitted_ns'],kv_bytes=r['plans'][1]['kv_payload_bytes'],
            writes=len(r['report']['transfers']),business_bytes=sum(t['bytes'] for t in r['report']['transfers'])) for name,r in cases.items()},
        source_hashes={str(p.relative_to(root)):sha(p) for folder in ('backend','python/moe_ub_cosim','fixtures/m4_pd_handoff')
            for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts})
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print('PASS: one prefill, source retention, destination reservation, actual KV completion, decode gating, atomic graph failures and passive observation')


if __name__=='__main__':main()
