"""Run sparse EP graphs and explicit service failures against real ASTRA/UB."""
import argparse
from collections import Counter
from copy import deepcopy
import datetime
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from check_delay import read, sha
from check_variants import rows


def check_sparse_graph(plan, report, execution):
    graph_id, ready = plan['graph_id'], plan['ready_ns']
    tokens, profile = plan['input']['tokens'], plan['input']['compute_profile_ns']
    expected = Counter()
    expert_counts = Counter()
    for token in tokens:
        destinations = {plan['input']['expert_placement'][e] for e in token['expert_ids']}
        for expert in token['expert_ids']: expert_counts[plan['input']['expert_placement'][expert]] += 1
        for dst in destinations - {token['src_rank']}:
            expected['dispatch',token['src_rank'],dst] += 8192
            expected['combine',dst,token['src_rank']] += 8192
    actual = Counter()
    for t in plan['transfers']: actual[t['kind'],t['src_rank'],t['dst_rank']] += t['payload_bytes']
    assert actual == expected
    assert plan['expert_input_counts'] == [expert_counts[r] for r in plan['rank_ids']]
    nodes = {(g['rank_id'],n['node_id']):n for g in plan['rank_graphs'] for n in g['nodes']}
    events = {(n['rank_id'],n['node_id']):n for n in report['node_execution'] if n['graph_id']==graph_id}
    transfers = {t['transfer_id']:t for t in report['transfers'] if t['graph_id']==graph_id}
    assert set(nodes) == set(events) and set(transfers) == {t['transfer_id'] for t in plan['transfers']}
    for t in plan['transfers']:
        got = transfers[t['transfer_id']]
        assert got['submitted_tasks'] == got['send_callbacks'] == got['receive_callbacks'] == 1
        assert not got['context_active'] and got['committed_bytes'] == t['payload_bytes']
        assert got['send_done_ns'] == got['transaction_done_ns']
        assert got['receive_done_ns'] == max(got['data_ready_ns'],got['receive_start_ns'])
        for rank,side,prefix in ((t['src_rank'],'send','send'),(t['dst_rank'],'recv','receive')):
            node = events[rank,t[side+'_node_id']]
            assert node['start_ns'] == got[prefix+'_start_ns'] and node['end_ns'] == got[prefix+'_done_ns']
    completions = {e['rank_id']:e['time_ns'] for e in report['graph_completions'] if e['graph_id']==graph_id}
    assert set(completions) == set(plan['rank_ids'])
    for graph in plan['rank_graphs']:
        rank, phase = graph['rank_id'], graph['phase_nodes']
        local = [t for t in tokens if t['src_rank']==rank]
        assert next(b for b in plan['instance_batches'] if b['rank_id']==rank)['request_ids'] == [t['request_id'] for t in local]
        def compute(key, duration, start):
            if duration == 0:
                assert phase[key] is None
                return start
            node = events[rank,phase[key]]
            assert node['kind']=='COMP' and node['start_ns']==start and node['end_ns']==start+duration
            return node['end_ns']
        def communicate(kind,start):
            ends=[start]
            for t in plan['transfers']:
                if t['kind'] != kind: continue
                got=transfers[t['transfer_id']]
                if t['src_rank']==rank:
                    assert got['send_start_ns']>=start; ends.append(got['send_done_ns'])
                if t['dst_rank']==rank:
                    assert got['receive_start_ns']==start; ends.append(got['receive_done_ns'])
            return max(ends)
        prepared=compute('prepare',len(local)*profile['attention_route_pack'],ready)
        dispatched=communicate('dispatch',prepared)
        computed=compute('expert',expert_counts[rank]*profile['expert_per_input'],dispatched)
        merged=compute('local_merge',bool(expert_counts[rank])*profile['expert_local_merge'],computed)
        combined=communicate('combine',merged)
        finished=compute('finish',len(local)*profile['combine_unpack_merge'],combined)
        complete=compute('next_layer',len(local)*profile['next_layer'],finished)
        assert completions[rank]==complete
        if not local and not expert_counts[rank]: assert graph['nodes']==[] and complete==ready
    for key,node in nodes.items():
        assert all(events[key]['start_ns']>=events[key[0],d]['end_ns'] for d in node['deps'])
    raw='\n'.join(s for s in (execution/'log/log.log').read_text().splitlines() if s.endswith(', graph_id='+graph_id))
    traces=re.findall(r'(issue|callback),sys->id=(\d+), tick=(\d+), node->id=(\d+),',raw)
    assert len(traces)==2*len(nodes) and len({(a,b,d) for a,b,c,d in traces})==len(traces)
    sends=Counter()
    for action,rank,now,node in traces:
        key=int(rank),int(node)
        assert events[key]['start_ns' if action=='issue' else 'end_ns']==int(now)
        if nodes[key]['kind']=='SEND':
            sends[int(rank)]+=1 if action=='issue' else -1
            assert 0<=sends[int(rank)]<=plan['send_slots_per_rank']
    assert not any(sends.values())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    build,out=args.build_dir.resolve(),args.output_dir.resolve(); out.mkdir(parents=True,exist_ok=False)
    binary=build/'output/cosim/ns3.44-cosim-service'
    fixture=root/'fixtures/m3_service_boundaries'
    layer=root/'fixtures/m2_single_layer/layer.json'; system=root/'fixtures/m1_compute/system.json'
    network=root/'fixtures/m2_single_layer/network'
    commands=[]; results={}; rejected={}
    def invoke(label,argv,input_text=None):
        start=datetime.datetime.now(datetime.timezone.utc).isoformat()
        result=subprocess.run(argv,cwd=root,input=input_text,capture_output=True,text=True,timeout=90)
        for stream in ('stdout','stderr'): (out/(label+'.'+stream+'.log')).write_text(getattr(result,stream))
        if input_text is not None: (out/(label+'.stdin.log')).write_text(input_text)
        commands.append(dict(label=label,argv=[str(v) for v in argv],cwd=str(root),started_utc=start,
            finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),exit_code=result.returncode))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        return result
    def command(label,config,selected_network=network,backend=binary,observed=True,extra=()):
        input_file=out/(label+'.input.json'); input_file.write_text(json.dumps(config,indent=2)+'\n')
        argv=[sys.executable,'-m','moe_ub_cosim.service','--input',input_file,'--layer',layer,
            '--backend',backend,'--system',system,'--network',selected_network,'--output-dir',out/label]
        if observed is not None:
            obs=read(root/'fixtures/m2_single_layer/observation.json')
            obs.update(window_end_ns=1000000,record_provenance=observed)
            if len(config.get('groups',[]))==2: obs.update(switch_id=8,port_id=2)
            path=out/(label+'.observation.json');path.write_text(json.dumps(obs)+'\n');argv+=['--observation',path]
        return [str(v) for v in argv+list(extra)]
    def run(label,config,selected_network=network,observed=True):
        result=invoke(label,command(label,config,selected_network,observed=observed))
        assert result.returncode==0,(label,result.stderr)
        path=out/label;front=read(path/'service_result.json');report=read(path/'execution/result.json')
        assert read(path/'manifest.json')['normal_exit'] and report['normal_exit'] and front['service_finished']
        rank_count=4*len(config.get('groups',[{}]))
        assert report['network_initializations']==1 and report['loaded_ranks_before_activation']==rank_count
        assert report['bootstrap']==dict(finished_ranks=rank_count,time_ns=0,business_nodes=0)
        assert front['pending']==front['waiting']==front['running']==front['inflight']==[0]*rank_count
        assert len(front['requests'])==len(config['requests']) and sum(front['done'])==len(config['requests'])
        assert report['active_wqes']==[0]*rank_count and report['csv_completed_tasks']==0
        assert all(r['finished'] and not r['remaining_nodes'] and all(r[k]==0 for k in
            ('cpu_slots','compute_slots','send_slots','pending_events','pending_tasks','stale_callbacks')) for r in report['rank_resources'])
        assert report['network_drain']['idle'] and report['network_drain']['started_packets']==report['network_drain']['arrived_packets']
        assert front['finished_ns']==report['service_finished_ns']<=report['simulation_end_ns']<1000000
        assert len(report['wqe_starts'])==len(report['wqe_completions'])==len(report['transfers'])
        assert len({t['transfer_id'] for t in report['transfers']})==len(report['transfers'])
        source={r['request_id']:r for r in config['requests']};occurrences=Counter();plans=[];prior={}
        for wave in front['waves']:
            plan=read(path/f"graphs/{wave['graph_id']}/plan.json");plans.append(plan)
            check_sparse_graph(plan,report,path/'execution')
            assert wave['submitted_ns']==plan['ready_ns']
            assert wave['finished_ns']==max(e['time_ns'] for e in report['graph_completions'] if e['graph_id']==wave['graph_id'])
            assert wave['submitted_ns']>=prior.get(wave['group_id'],0);prior[wave['group_id']]=wave['finished_ns']
            for token in wave['tokens']:
                identity=int(token['request_id']);r=source[identity];step=occurrences[identity]
                assert token['src_rank']==r['rank_id'] and token['expert_ids']==r['decode_routes'][step]
                assert token['token_index']==r['prompt_tokens']+r['generated_tokens']-1+step
                assert wave['submitted_ns']>=r['arrival_ns'];occurrences[identity]+=1
        assert occurrences==Counter({k:len(r['decode_routes']) for k,r in source.items()})
        assert front['scheduler_calls']==sum(occurrences.values())
        for request in front['requests']:
            r=source[request['request_id']]
            assert request['status']=='FINISHED' and request['preemptions']==0
            assert request['num_tokens_reached']==request['target_tokens']
            assert len(request['intertoken_latencies_ns'])==len(r['decode_routes'])
            assert sum(request['intertoken_latencies_ns'])==request['latest_completion_ns']-r['arrival_ns']
        protocol=rows(path/'protocol.jsonl')
        assert len(protocol)==front['decisions']*2==report['decisions']*2
        if config['requests']: assert any(m['type']=='COMMIT' and m['input_exhausted'] and not m['service_finished'] for m in protocol)
        assert protocol[-1]['service_finished'] and protocol[-1]['submissions']==[]
        if observed is not None:
            counters=rows(path/'execution/observed/port_counters.jsonl')
            endpoint=rows(path/'execution/observed/endpoint_ops.jsonl')
            assert len(endpoint)==8*len(plans)
            assert all(not set(r)&{'graph_id','request_id','transfer_id','operation_id','token_index'} for r in counters+endpoint)
            if observed:
                truth=rows(path/'execution/truth/port_byte_truth.jsonl')
                assert len(truth)==len(counters)
                for c,t in zip(counters,truth): assert c['counted_packet_bytes']==sum(t[k] for k in
                    ('business_payload_bytes','application_metadata_bytes','data_protocol_overhead_bytes','control_packet_bytes','unattributed_bytes'))
            else: assert not (path/'execution/truth').exists()
        else: assert not (path/'execution/observed').exists()
        libraries={Path(p).resolve() for p in report['loaded_libraries'] if Path(p).name.startswith('libns3')}
        assert len(libraries)==10 and all(p.parent==build/'output/lib' for p in libraries)
        results[label]=dict(front=front,report=report,protocol=protocol,plans=plans)
        print(f"PASS {label}: {len(front['waves'])} waves, {len(report['transfers'])} writes, {front['finished_ns']} ns",flush=True)
        return results[label]
    sparse=read(fixture/'sparse.json');normal=run('sparse',sparse)
    for label,observed in (('off',False),('absent',None),('repeat',True)):
        other=run(label,sparse,observed=observed)
        assert other==normal,label
        def hashes(path):return {str(p.relative_to(path)):sha(p) for p in path.rglob('*') if p.is_file()}
        assert hashes(out/label/'execution/network/runlog')==hashes(out/'sparse/execution/network/runlog')
        if observed is not None:assert hashes(out/label/'execution/observed')==hashes(out/'sparse/execution/observed')
    assert [len(p['transfers']) for p in normal['plans']]==[4,0,2]
    assert sum(t['bytes'] for t in normal['report']['transfers'])==49152
    uneven=read(fixture/'uneven.json')
    mixed=run('uneven',uneven,root/'fixtures/m3_dynamic_arrivals/network')
    a=[w for w in mixed['front']['waves'] if w['group_id']=='ep0'];b=[w for w in mixed['front']['waves'] if w['group_id']=='ep1']
    assert len(a)==4 and len(b)==1 and b[0]['finished_ns']<a[-1]['finished_ns']
    assert [sorted(int(t['request_id']) for t in w['tokens']) for w in a]==[[101],[101,102],[101],[201]]
    assert all(e['active_graph_id']=='g0' for e in mixed['front']['arrivals'] if e['request_id'] in (102,201))
    local=deepcopy(sparse);local['requests'][0]['decode_routes']=[[0,1]]
    only_local=run('local-only',local)
    assert not only_local['report']['transfers'] and only_local['report']['network_drain']['started_packets']==0
    empty=deepcopy(sparse);empty['requests']=[];nothing=run('empty',empty)
    assert nothing['front']['finished_ns']==0 and nothing['front']['waves']==[] and nothing['report']['network_drain']['started_packets']==0
    # Direct protocol failure inputs are retained, including mid-run EOF.
    first=deepcopy(normal['protocol'][1])
    bad_graph=out/'bad-graph';shutil.copytree(out/'sparse/bootstrap',bad_graph/'bootstrap')
    shutil.copytree(out/'sparse/graphs/g0',bad_graph/'graphs/g0')
    (bad_graph/'graphs/g0/layer.3.et').write_bytes(b'\x80')
    cases={'initial-eof':('',out/'sparse'), 'mid-eof':(json.dumps(first)+'\n',out/'sparse'),
           'no-work':(json.dumps(dict(first,submissions=[]))+'\n',out/'sparse'),
           'bad-empty-member':(json.dumps(first)+'\n',bad_graph)}
    for label,(text,shared) in cases.items():
        target=out/label
        result=invoke(label,[str(binary),sparse['run_id'],str(shared),str(system),str(network),'-',str(target),'1000000'],text)
        assert result.returncode==1 and not (target/'result.json').exists()
        failure=read(target/'failure.json');assert not failure['normal_exit'] and failure['ranks']
        if label=='mid-eof':assert failure['now_ns']==normal['front']['waves'][0]['finished_ns']>0 and 'EOF' in failure['error']
        if label=='no-work':assert failure['now_ns']==0 and 'deadlock' in failure['error']
        if label=='bad-empty-member':assert failure['now_ns']==0 and 'layer.3.et' in failure['error'] and not failure['transfers']
        rejected[label]=dict(exit_code=result.returncode,now_ns=failure['now_ns'],error=failure['error'])
    # Missing activation cannot be configured in the production executable.
    original=read(root/'fixtures/m3_service/service.json')
    for label,backend,extra in (
        ('deadlock',build/'output/cosim/ns3.44-cosim-service-deadlock-test',()),
        ('bound',binary,('--max-simulation-ns','5200'))):
        result=invoke(label,command(label,original,backend=backend,observed=None,extra=extra))
        front=read(out/label/'manifest.json');failure=read(out/label/'execution/failure.json')
        assert result.returncode!=0 and not front['normal_exit'] and front['backend_exit_code']==1
        assert not (out/label/'service_result.json').exists() and read(out/label/'failure.json')['active_graphs']
        if label=='deadlock':
            assert 'Service deadlock' in failure['error'] and failure['now_ns']<1000000
            assert failure['network']['idle'] and not failure['future_wakeup_pending']
            assert all(r['pending_adapter_events']==0 for r in failure['ranks'])
            assert any(t['src_rank']==3 and not t['send_registered'] and t['receive_registered'] for t in failure['transfers'])
        else:assert failure['now_ns']==5200 and 'max_simulation_ns' in failure['error']
        rejected[label]=dict(exit_code=front['backend_exit_code'],now_ns=failure['now_ns'],error=failure['error'])
    # Kill the real backend after its first wave, before forwarding that DECIDE.
    proxy=out/'crash-proxy.py'
    proxy.write_text('#!'+sys.executable+'\n'+'''import json,os,signal,subprocess,sys
from pathlib import Path
binary=Path(__file__).with_name('real-backend.path').read_text()
child=subprocess.Popen([binary,*sys.argv[1:]],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
try:
    print(child.stdout.readline().rstrip(),flush=True)
    child.stdin.write(sys.stdin.readline());child.stdin.flush()
    pending=json.loads(child.stdout.readline())
    child.terminate();code=child.wait(timeout=10)
    Path(sys.argv[-2],'crash-test.json').write_text(json.dumps(dict(pending_decision=pending,backend_exit_code=code)))
    os.kill(os.getpid(),signal.SIGTERM)
finally:
    if child.poll() is None: child.kill();child.wait()
''')
    proxy.chmod(0o700);(out/'real-backend.path').write_text(str(binary))
    result=invoke('crash',command('crash',sparse,backend=proxy,observed=None))
    manifest=read(out/'crash/manifest.json');proof=read(out/'crash/execution/crash-test.json')
    assert result.returncode!=0 and manifest['backend_exit_code']==proof['backend_exit_code']==-15
    assert not manifest['normal_exit'] and 'EOF' in manifest['error'] and proof['pending_decision']['completed']
    assert proof['pending_decision']['now_ns']==normal['front']['waves'][0]['finished_ns']
    assert read(out/'crash/failure.json')['active_graphs']=={'ep0':'g0'}
    rejected['crash']=dict(exit_code=-15,now_ns=proof['pending_decision']['now_ns'])
    summary=dict(passed=True,actual_runs=len(results),failures=rejected,
        cases={name:dict(waves=len(r['front']['waves']),finished_ns=r['front']['finished_ns'],
            writes=len(r['report']['transfers']),bytes=sum(t['bytes'] for t in r['report']['transfers']),
            scheduler_calls=r['front']['scheduler_calls'],wave_times=[dict(group_id=w['group_id'],
                start_ns=w['submitted_ns'],finish_ns=w['finished_ns']) for w in r['front']['waves']]) for name,r in results.items()},
        source_hashes={str(p.relative_to(root)):sha(p) for folder in ('backend','python/moe_ub_cosim') for p in (root/folder).glob('*') if p.is_file()})
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print('PASS: zero-Token experts, empty graphs, uneven requests, input exhaustion, passive observation and explicit failures')


if __name__=='__main__':main()
