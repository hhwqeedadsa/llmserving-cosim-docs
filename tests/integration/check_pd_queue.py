"""Queued KV capacity, native UB contention, graph ownership and passive output."""
import argparse
from collections import Counter
from copy import deepcopy
import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys

from check_delay import read,sha
from check_service_boundaries import check_sparse_graph
from check_shared_bottleneck import audit_queue
from check_variants import rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[2]
    build,out=args.build_dir.resolve(),args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    fixture=root/'fixtures/m4_pd_queue';base=read(fixture/'service.json')
    binary=build/'output/cosim/ns3.44-cosim-service';commands=[];results={};failures={}
    def invoke(label,argv,stdin=None):
        start=datetime.datetime.now(datetime.timezone.utc).isoformat()
        result=subprocess.run([str(x) for x in argv],cwd=root,input=stdin,capture_output=True,text=True,timeout=90)
        for stream in ('stdout','stderr'):(out/(label+'.'+stream+'.log')).write_text(getattr(result,stream))
        if stdin is not None:(out/(label+'.stdin.log')).write_text(stdin)
        commands.append(dict(label=label,argv=[str(x) for x in argv],cwd=str(root),exit_code=result.returncode,
            started_utc=start,finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n');return result
    def command(label,c,observed=True):
        p=out/(label+'.input.json');p.write_text(json.dumps(c,indent=2)+'\n')
        argv=[sys.executable,'-m','moe_ub_cosim.service','--input',p,'--layer',fixture/'layer.json','--backend',binary,
            '--system',root/'fixtures/m1_compute/system.json','--network',fixture/'network','--output-dir',out/label]
        if observed is not None:
            p=out/(label+'.observation.json');p.write_text(json.dumps(dict(read(fixture/'observation.json'),record_provenance=observed))+'\n')
            argv+=['--observation',p]
        return argv
    def run(label,c,observed=True):
        result=invoke(label,command(label,c,observed));assert result.returncode==0,(label,result.stderr)
        path=out/label;front=read(path/'service_result.json');report=read(path/'execution/result.json')
        assert front['service_finished'] and report['normal_exit'] and read(path/'manifest.json')['normal_exit']
        assert report['network_initializations']==1 and report['loaded_ranks_before_activation']==12
        assert front['waiting']==front['pending']==front['running']==front['inflight']==[0]*12
        assert front['handoff_queue']==[] and report['active_wqes']==[0]*12 and report['csv_completed_tasks']==0
        assert all(b['same_network_objects'] for b in report['boundary_resources'])
        assert report['network_drain']['idle'] and report['network_drain']['started_packets']==report['network_drain']['arrived_packets']
        assert report['service_finished_ns']==front['finished_ns']<=report['simulation_end_ns']<1000000
        for r in front['requests']:
            assert r['pd_state']==r['status']=='FINISHED' and r['num_tokens_reached']==r['target_tokens'] and r['preemptions']==0
        plans={w['graph_id']:read(path/f"graphs/{w['graph_id']}/plan.json") for w in front['waves']}
        wave_by_graph={w['graph_id']:w for w in front['waves']}
        for r in front['requests']:
            rid=r['request_id'];source=next(x for x in c['requests']+c['independent_requests'] if x['request_id']==rid)
            used=[w for w in front['waves'] if rid in w['request_ids']]
            if 'prefill_rank' in source:
                prefill=next(w for w in used if w['role']=='P')
                assert r['ttft_ns']==prefill['finished_ns']-source['arrival_ns']
                assert len(r['intertoken_latencies_ns'])==source['output_tokens']-1
                assert sum(r['intertoken_latencies_ns'])==used[-1]['finished_ns']-prefill['finished_ns']
            else:
                assert len(r['intertoken_latencies_ns'])==len(source['decode_routes'])
                assert sum(r['intertoken_latencies_ns'])==used[-1]['finished_ns']-source['arrival_ns']
        for w in front['waves']:
            p=plans[w['graph_id']];assert p['ready_ns']==w['submitted_ns']
            assert w['finished_ns']==max(e['time_ns'] for e in report['graph_completions'] if e['graph_id']==w['graph_id'])
            assert [e['rank_id'] for e in report['graph_loads'] if e['graph_id']==w['graph_id']]==p['rank_ids']
            assert [e['rank_id'] for e in report['activation_order'] if e['graph_id']==w['graph_id']]==p['rank_ids']
            if w['role']!='KV':check_sparse_graph(p,report,path/'execution')
        for a in front['waves']:
            for b in front['waves']:
                if a['submitted_ns']<b['submitted_ns']<a['finished_ns']:
                    assert not set(a['rank_ids'])&set(b['rank_ids']), 'Overlapping owned rank contexts'
        transfers={t['transfer_id']:t for p in plans.values() for t in p['transfers']}
        actual={t['transfer_id']:t for t in report['transfers']};writes={t['task']:t for t in report['target_writes']}
        assert set(actual)==set(transfers) and len(actual)==len(writes)==len(report['wqe_starts'])==len(report['wqe_completions'])
        for tid,t in transfers.items():
            x=actual[tid];assert x['submitted_tasks']==x['send_callbacks']==x['receive_callbacks']==1
            assert x['bytes']==x['committed_bytes']==t['payload_bytes']==writes[t['ub_task_id']]['bytes']
            assert x['data_ready_ns']==writes[t['ub_task_id']]['time_ns']
            assert x['send_done_ns']==x['transaction_done_ns'] and x['receive_done_ns']==max(x['receive_start_ns'],x['data_ready_ns'])
            assert not x['context_active']
        kv=[w for w in front['waves'] if w['role']=='KV']
        assert [w['request_ids'][0] for w in kv]==[e['request_id'] for e in front['memory_events'] if e['event']=='handoff_enqueued']
        for w in kv:
            rid=w['request_ids'][0];source=next(r for r in c['requests'] if r['request_id']==rid)
            p=plans[w['graph_id']];events={e['event']:e for e in front['memory_events'] if e['request_id']==rid}
            assert p['kv_payload_bytes']==source['prompt_tokens']*1024==sum(t['payload_bytes'] for t in p['transfers'])
            assert w['finished_ns']==max(actual[t['transfer_id']]['transaction_done_ns'] for t in p['transfers'])
            assert events['destination_reserved']['time_ns']==w['submitted_ns']
            assert events['source_released']['time_ns']==events['decode_admitted']['time_ns']==w['finished_ns']
            first=min(d['submitted_ns'] for d in front['waves'] if d['role']=='D' and rid in d['request_ids'])
            assert first>=w['finished_ns']
            for event in ('destination_reserved','handoff_submitted','handoff_complete'):
                assert str(rid) in events[event]['ranks'][source['prefill_rank']]['request_blocks']
                assert str(rid) in events[event]['ranks'][source['decode_rank']]['request_blocks']
            assert str(rid) not in events['source_released']['ranks'][source['prefill_rank']]['request_blocks']
        for event in front['memory_events']:
            for r in event['ranks']:
                assert r['used_bytes']==r['weight_bytes']+r['workspace_bytes']+r['communication_buffer_bytes']+r['kv_used_bytes']<=r['total_bytes']
                assert r['kv_used_bytes']==sum(r['request_blocks'].values())*r['kv_block_bytes']
                assert sum(r['request_blocks'].values())+r['free_blocks']==r['kv_capacity_blocks']
        protocol=rows(path/'protocol.jsonl')
        for i in range(0,len(protocol),2):
            decide,commit=protocol[i:i+2];now=decide['now_ns']
            assert decide['decision_id']==commit['decision_id']==i//2 and now==commit['now_ns']
            for e in decide['completed']:
                w=wave_by_graph[e['graph_id']]
                assert w['finished_ns']==now and e['kind']==('KV_DONE' if w['role']=='KV' else 'WAVE_DONE')
        record=dict(front=front,report=report,plans=plans,protocol=protocol,path=path)
        if observed is not None:
            queue=audit_queue(path/'execution',1,12,6);record['queue']=queue
            counters=rows(path/'execution/observed/port_counters.jsonl')
            if observed:
                ranges=rows(path/'execution/truth/packet_ranges.jsonl');record['ranges']=ranges
                crossing={tid for tid,t in transfers.items() if t['src_rank']%2==0 and t['dst_rank']%2==1}
                assert {r['transfer_id'] for r in ranges}==crossing
                for tid in crossing:
                    parts=sorted((r for r in ranges if r['transfer_id']==tid),key=lambda r:r['task_byte_range'][0]);cursor=0
                    for part in parts:assert part['task_byte_range'][0]==cursor;cursor=part['task_byte_range'][1]
                    assert cursor==transfers[tid]['payload_bytes']
                kinds={t['operation_id']:wave_by_graph[g]['role'] for g,p in plans.items() for t in p['transfers']}
                truth=rows(path/'execution/truth/port_byte_truth.jsonl');windows=[]
                for counter,t in zip(counters,truth):
                    amount=Counter()
                    for p in ranges:
                        if counter['local_window_start_ns']<=p['sim_time_ns']<counter['local_window_end_ns']:
                            amount[kinds[p['operation_id']]]+=p['task_byte_range'][1]-p['task_byte_range'][0]
                    assert sum(amount.values())==t['business_payload_bytes']
                    assert counter['counted_packet_bytes']==sum(t[k] for k in ('business_payload_bytes','application_metadata_bytes','data_protocol_overhead_bytes','control_packet_bytes','unattributed_bytes'))
                    windows.append(dict(start_ns=counter['local_window_start_ns'],bytes_by_role=dict(amount)))
                record['windows']=windows;record['packet_roles']={p['packet_uid']:kinds[p['operation_id']] for p in ranges}
                (out/(label+'.windows.json')).write_text(json.dumps(windows,indent=2)+'\n')
            else:assert not (path/'execution/truth').exists()
        else:assert not (path/'execution/observed').exists()
        libraries={Path(x).resolve() for x in report['loaded_libraries'] if Path(x).name.startswith('libns3')}
        assert len(libraries)==10 and all(p.parent==build/'output/lib' for p in libraries)
        results[label]=record;print(f'PASS {label}: {len(kv)} handoffs, {len(actual)} writes, finish {front["finished_ns"]}',flush=True)
        return record
    solo=deepcopy(base);solo['independent_requests']=[]
    ep=deepcopy(base);ep['requests']=[]
    staggered=deepcopy(base)
    for r in staggered['independent_requests']:r['arrival_ns']=600000
    run('queue',solo);run('independent',ep);shared=run('overlap',base);run('staggered',staggered)
    roomy=deepcopy(solo);roomy['npu_memory_bytes'][5]+=16384;run('roomy',roomy)
    late=deepcopy(solo);r=deepcopy(late['requests'][0]);r.update(request_id=503,arrival_ns=130000);late['requests'].append(r);run('late',late)
    for label,observed in (('off',False),('absent',None),('repeat',True)):
        other=run(label,base,observed)
        for key in ('front','report','plans','protocol'):assert other[key]==shared[key],(label,key)
        def hashes(path):return {str(p.relative_to(path)):sha(p) for p in path.rglob('*') if p.is_file()}
        assert hashes(other['path']/'execution/network/runlog')==hashes(shared['path']/'execution/network/runlog')
        if observed is not None:assert hashes(other['path']/'execution/observed')==hashes(shared['path']/'execution/observed')
    for name in ('queue','overlap','staggered','late'):
        front=results[name]['front'];waits=[e for e in front['memory_events'] if e['event']=='capacity_wait']
        assert waits and any(e['request_id']==502 for e in waits)
        first_done=max(w['finished_ns'] for w in front['waves'] if w['role']=='D' and 501 in w['request_ids'])
        second=next(w for w in front['waves'] if w['role']=='KV' and w['request_ids']==[502])
        assert second['submitted_ns']==first_done
    roomy=results['roomy']['front']['waves']
    assert [w['role'] for w in roomy[:3]]==['P','KV','KV']
    late=results['late'];third=next(w for w in late['front']['waves'] if w['role']=='P' and 503 in w['request_ids'])
    second=next(w for w in late['front']['waves'] if w['role']=='KV' and w['request_ids']==[502])
    assert third['submitted_ns']==second['finished_ns']>130000
    def first_kv(case):return next(w for w in results[case]['front']['waves'] if w['role']=='KV')
    a,b=first_kv('queue'),first_kv('overlap')
    assert a['submitted_ns']==b['submitted_ns'] and b['finished_ns']>a['finished_ns']
    assert first_kv('staggered')['finished_ns']==a['finished_ns']
    for case in ('queue','overlap'):
        w=first_kv(case);front=results[case]['front']
        assert next(x for x in front['waves'] if x['role']=='D')['submitted_ns']==w['finished_ns']
    assert any('KV' in w['bytes_by_role'] and 'EP' in w['bytes_by_role'] for w in shared['windows'])
    assert not any('KV' in w['bytes_by_role'] and 'EP' in w['bytes_by_role'] for w in results['staggered']['windows'])
    # Same old business packet queued across the KV graph submission boundary.
    preserved=[p for p in shared['queue']['packets'] if shared['packet_roles'].get(p['packet_uid'])=='EP' and p['enqueue_ns']<b['submitted_ns']<p['dequeue_ns']]
    assert preserved,'No independent queued packet survives handoff activation'
    waits={label:[p['wait_ns'] for p in results[label]['queue']['packets'] if results[label]['packet_roles'].get(p['packet_uid'])=='KV'] for label in ('queue','overlap')}
    assert sum(waits['overlap'])/len(waits['overlap'])>sum(waits['queue'])/len(waits['queue'])
    comparison=[]
    for left,right in zip(results['queue']['plans'][a['graph_id']]['transfers'],shared['plans'][b['graph_id']]['transfers']):
        def actual(case,tid):return next(t for t in results[case]['report']['transfers'] if t['transfer_id']==tid)
        st,ot=actual('queue',left['transfer_id']),actual('overlap',right['transfer_id'])
        assert {k:v for k,v in left.items() if k not in ('transfer_id','ub_task_id','comm_tag','operation_id')}=={k:v for k,v in right.items() if k not in ('transfer_id','ub_task_id','comm_tag','operation_id')}
        comparison.append(dict(chunk_index=left['chunk_index'],solo=st,overlap=ot))
    for label,mutate in (('capacity',lambda c:c['requests'][0].update(prompt_tokens=16,prefill_routes=[[0,1]]*16)),
                        ('mapping',lambda c:c['groups'][2]['rank_ids'].__setitem__(0,7)),
                        ('duplicate',lambda c:c['requests'][1].update(request_id=501)),
                        ('buffer',lambda c:c['communication_buffer_bytes'].__setitem__(11,1))):
        c=deepcopy(base);mutate(c);result=invoke('bad-'+label,command('bad-'+label,c,None))
        assert result.returncode!=0 and not (out/('bad-'+label)/'execution').exists()
        failures[label]=dict(exit_code=result.returncode,error=result.stderr.splitlines()[-1])
    commits=[m for m in shared['protocol'] if m['type']=='COMMIT'];kv_index=next(i for i,m in enumerate(commits) if any(s['graph_id']==b['graph_id'] for s in m['submissions']))
    for label in ('bad-kv','last-member','partial-group','busy-group'):
        directory=out/(label+'-input');shutil.copytree(shared['path']/'bootstrap',directory/'bootstrap')
        shutil.copytree(shared['path']/'graphs',directory/'graphs');shutil.copyfile(shared['path']/'deployment.json',directory/'deployment.json')
        messages=deepcopy(commits[:kv_index+1]);target=next(s for s in messages[-1]['submissions'] if s['graph_id']==b['graph_id'])
        if label=='bad-kv':
            p=directory/'graphs'/b['graph_id']/'plan.json';d=read(p);d['transfers'][0]['payload_bytes']+=1;p.write_text(json.dumps(d))
        if label=='last-member':(directory/'graphs'/b['graph_id']/'layer.7.et').write_bytes(b'\x80')
        if label=='partial-group':target['rank_ids'].pop()
        if label=='busy-group':messages[0]['submissions'].append(target);messages=messages[:1]
        result=invoke(label,[binary,base['run_id'],directory,root/'fixtures/m1_compute/system.json',fixture/'network','-',out/label,'1000000'],''.join(json.dumps(m)+'\n' for m in messages))
        assert result.returncode==1 and not (out/label/'result.json').exists()
        failure=read(out/label/'failure.json');failures[label]=dict(exit_code=result.returncode,error=failure['error'])
        assert ', graph_id='+b['graph_id']+'\n' not in (out/label/'log/log.log').read_text()
    summary=dict(passed=True,actual_runs=len(results),failures=failures,first_kv_finish_shift_ns=b['finished_ns']-a['finished_ns'],
        first_kv_transfers=comparison,preserved_packets=preserved,
        kv_fifo_mean_wait_ns={k:sum(v)/len(v) for k,v in waits.items()},
        cases={label:dict(finished_ns=r['front']['finished_ns'],writes=len(r['report']['transfers']),
            business_bytes=sum(t['bytes'] for t in r['report']['transfers']),waves=r['front']['waves'],
            capacity_waits=sum(e['event']=='capacity_wait' for e in r['front']['memory_events'])) for label,r in results.items()},
        source_hashes={str(p.relative_to(root)):sha(p) for folder in ('backend','python/moe_ub_cosim','fixtures/m4_pd_queue') for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts})
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print('PASS: FIFO capacity waits, source retention, destination reservation, real shared KV/EP queue and completion feedback')


if __name__=='__main__':main()
