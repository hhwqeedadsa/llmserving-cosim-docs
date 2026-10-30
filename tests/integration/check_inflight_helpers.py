"""Independent shared-resource and original-gate audit; no network executions."""
import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def load(path):return json.loads(path.read_bytes())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):
    with path.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')
def frames(path):
    return [json.loads(line[len('MOE_RDMA_EVENT '):]) for line in path.read_text().splitlines()
            if line.startswith('MOE_RDMA_EVENT ')]


def inspect(run):
    r=load(run/'result.json');c=load(run/'input.json');f=load(run/'frontend.json')
    assert r['passed'] and r['gpu_count']==128 and r['drained'] and r['service']['service_finished']
    assert r['packet_capture']['complete'] and not r['packet_capture']['omitted_events']
    waves={h['graph_id']:load(run/'graphs'/h['graph_id']/'wave.json') for h in f['history']}
    graph_nodes={(g,int(rank),n['id']):n for g,w in waves.items() for rank,rows in w['rank_nodes'].items() for n in rows}
    raw=frames(run/'native.stdout.log');by_node={};original={};observed=0;last=-1;decisions=[];life=[]
    saved=load(run/'helper_control.json');pending=None
    for frame in raw:
        kind=frame['type'];time=frame.get('time_ns',frame.get('now_ns'))
        assert time>=last;last=time
        if kind=='OBSERVE':
            observed+=1
            if frame['kind'].startswith('node_'):
                key=frame['graph_id'],frame['rank'],frame['node']
                by_node.setdefault(key,{})[frame['kind']]=time
            if frame['kind'].startswith('message_'):
                original.setdefault(frame['message'],{})[frame['kind']]=time
        elif kind=='SOURCE_ACK_TIMEOUT':
            assert frame['observed_events']==observed
            pending=frame
        elif kind=='HELPER_DECIDE':
            assert pending and frame['now_ns']==pending['time_ns'] and frame['observed_events']==observed
            assert frame['message']==pending['message'] and frame['notice_sequence']==pending['sequence']
            assert frame['decision_id']==len(decisions) and saved[len(decisions)]['event']==frame
            reply=saved[len(decisions)]['reply']
            assert set(reply)=={'type','decision_id','now_ns','attempts'} and reply['type']=='HELPER_COMMIT'
            assert reply['now_ns']==time and reply['decision_id']==len(decisions)
            decisions.append(reply);pending=None
        elif kind=='HELPER_OBSERVE':
            assert frame['observed_events']==observed and frame['sequence']==len(life)
            life.append(frame)
    assert len(saved)==len(decisions)
    assert life==[json.loads(line) for line in (run/'helper_events.jsonl').read_text().splitlines()]
    # Independent FIFO schedule from cumulative admission order.
    tasks=r['service']['shared_compute']['tasks'];end=defaultdict(int);original_tasks={};helper_tasks={}
    assert r['service']['shared_compute']['empty'] and len({t['key'] for t in tasks})==len(tasks)
    for sequence,task in enumerate(sorted(tasks,key=lambda t:t['sequence'])):
        rank=task['rank'];assert task['sequence']==sequence and 0<=rank<128
        assert task['start_ns']==max(end[rank],task['ready_ns'])
        assert task['done_ns']==task['start_ns']+task['duration_ns']
        end[rank]=task['done_ns']
        if task['kind']=='original':
            key=task['graph_id'],rank,task['node'];node=graph_nodes[key];event=by_node[key]
            assert node['kind']=='COMP' and task['duration_ns']==node['duration_ns']
            assert task['ready_ns']==event['node_issue'] and task['done_ns']==event['node_complete']
            original_tasks[key]=task
        else:helper_tasks[task['helper_id'],task['kind']]=task
    assert set(original_tasks)=={key for key,node in graph_nodes.items() if node['kind']=='COMP'}
    messages={m['id']:m for m in r['service_transport']['messages']}
    streams={s['id']:s for s in c['transport']['streams']}
    catalog={row['id']:row for w in waves.values() for row in w['helper_candidates']}
    attempts=r['service']['inflight_helpers']['attempts'];verified=[]
    packet_rows=[dict(zip(r['packet_capture']['columns'],row)) for row in r['packet_capture']['events']]
    for attempt in attempts:
        row=attempt['candidate'];id=attempt['id'];assert row==catalog[id]
        assert len({row['source'],row['owner'],row['provider']})==3
        assert row['provider'] not in waves[row['graph_id']]['rank_ids']
        owner_group=next(g for g in c['parallel']['groups'] if row['owner'] in g)
        replica_group=next(g for g in c['parallel']['groups'] if row['provider'] in g)
        assert 'expert_to_group_rank' not in c['route_replay']  # This public example uses contiguous ownership.
        width=c['model']['experts']//len(owner_group)
        assert all(owner_group[e//width]==row['owner'] and replica_group[e//width]==row['provider'] for e in row['experts'])
        token=next(t for t in waves[row['graph_id']]['routes'][row['logical_layer']]['tokens']
                   if t['rank']==row['source'] and row['call'].endswith('.s'+str(t['slot'])))
        assert row['experts']==sorted(e for e in token['experts'] if owner_group[e//width]==row['owner'])
        states={e['kind']:e['time_ns'] for e in life if e['id']==id};assert len(states)==12
        assert states['accepted']==states['input_post']==next(x['now_ns'] for x in decisions if id in x['attempts'])
        details={}
        for prefix,src,dst in [('input',row['source'],row['provider']),('result',row['provider'],row['source'])]:
            m=messages[row[prefix+'_message']];stream=streams[m['stream']]
            assert m['src']==src and m['dst']==dst and m['bytes']==row[prefix+'_bytes']
            assert stream['src']==src and stream['dst']==dst and stream['source_nic']==stream['destination_nic']==1
            assert m['ready_ns']==states[prefix+'_post'] and m['receive_ready_ns']==states[prefix+'_ready'] and m['ack_ns']==states[prefix+'_ack']
            sent=[p for p in packet_rows if p['message_id']==m['stream'] and p['event']==0 and p['node']==src and
                  p['ip_protocol']==17 and m['begin']<=p['sequence']<m['end']]
            received=[p for p in packet_rows if p['message_id']==m['stream'] and p['event']==1 and p['node']==dst and
                      p['ip_protocol']==17 and m['begin']<=p['sequence']<m['end']]
            assert len(sent)==len(received)==1 and sent[0]['data_payload_bytes']==received[0]['data_payload_bytes']==m['bytes']
            assert received[0]['time_ns']==m['receive_ready_ns']
            details[prefix]=dict(message=m['id'],stream=m['stream'],bytes=m['bytes'],source_packets=1,destination_packets=1,
                                 source_nic=1,destination_nic=1,ready_ns=m['ready_ns'],receive_ns=m['receive_ready_ns'],ack_ns=m['ack_ns'])
        for kind,ready,begin,done,cost in [('helper_experts','input_ready','compute_begin','compute_done','compute_ns'),
                                        ('helper_reduce','compute_done','reduce_begin','reduce_done','reduce_ns')]:
            task=helper_tasks[id,kind]
            assert task['rank']==row['provider'] and task['ready_ns']==states[ready] and task['start_ns']==states[begin]
            assert task['done_ns']==states[done] and task['duration_ns']==row[cost]
        calls=[json.loads(line) for line in (run/'contribution_calls.jsonl').read_text().splitlines()]
        call=next(x for x in calls if x['call']==row['call']);state=call['state']
        assert state['winners'][row['bundle']]==dict(attempt='helper.'+row['bundle'],time_ns=states['result_ready'])
        late=[e for e in state['events'] if e['kind']=='result' and e['attempt']=='original.'+row['bundle']]
        assert len(late)==1 and late[0]['outcome']=='redundant'
        assert state['declared_call_retirable'] and state['all_started_attempts_retired']
        assert states['retired']==max(states['input_ack'],states['result_ack'],states['result_ready'])
        original_result=original[row['combine_message']]['message_recv_complete']
        verified.append(dict(candidate=row,lifecycle=states,packets=details,original_result_ns=original_result,
            bundle_earlier_ns=original_result-states['result_ready'],call_logical_complete_ns=state['logical_complete_ns'],
            winners=state['winners'],original_graph_retired_ns=state['attempts']['original.'+row['bundle']]['retired_ns']))
    assert len(helper_tasks)==2*len(attempts) and len(life)==12*len(attempts)
    # Every actual expert node still depends on the EP Dispatch gate. This is
    # intentionally not replaced by a fast auxiliary-result counter.
    g=waves['g0.w0'];gate=next(x for x in r['service']['phase_completion']['gates'] if x['graph_id']=='g0.w0' and x['id']=='L0.dispatch')
    for rank,rows in g['rank_nodes'].items():
        node=next(x for x in rows if x['name']=='L0.experts');member=next(x['node'] for x in gate['members'] if x['rank']==int(rank))
        assert member in node['deps'] and by_node['g0.w0',int(rank),node['id']]['node_issue']>=gate['released_ns']
    call=next(json.loads(line) for line in (run/'contribution_calls.jsonl').read_text().splitlines()
              if json.loads(line)['call']=='g0.w0.l0.r0.s0')
    return dict(application_finished_ns=max(h['finished_ns'] for h in f['history']),network_drained_ns=r['finish_ns'],
        critical_call_logical_complete_ns=call['state']['logical_complete_ns'],
        fault_drops=r['fault_drops'],original_messages=len(original),actual_messages=len(messages),packet_events=len(packet_rows),
        compute_tasks=len(tasks),original_compute_tasks=len(original_tasks),dispatch_gate_ns=gate['released_ns'],
        declared_payload_bytes=sum(m['bytes'] for m in messages.values()),attempts=verified)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('root','reference','backend','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();root=args.root.resolve();output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    cases={mode:inspect(root/mode) for mode in ('normal','local','helper')}
    normal,local,helper=(load(root/mode/'result.json') for mode in ('normal','local','helper'))
    assert cases['helper']['application_finished_ns']==cases['local']['application_finished_ns']
    assert cases['helper']['critical_call_logical_complete_ns']==cases['local']['critical_call_logical_complete_ns']
    assert cases['helper']['attempts'][0]['call_logical_complete_ns']==89808
    # Fairness: only helper policy differs; original graph work identical.
    configs=[load(root/mode/'input.json') for mode in ('normal','local','helper')]
    for config in configs:config['inflight_helpers']['policy']='normalized'
    assert configs[0]==configs[1]==configs[2]
    assert load(root/'local/rdma.json')==load(root/'helper/rdma.json')
    assert local['service']['phase_completion']==helper['service']['phase_completion']
    assert local['service']['message_callbacks']==helper['service']['message_callbacks']
    assert (root/'local/execution_events.jsonl').read_bytes()==(root/'helper/execution_events.jsonl').read_bytes()
    matches={}
    for mode in ('normal','local'):
        current=load(root/mode/'result.json');ref=load(args.reference.resolve()/mode/'result.json')
        assert current['finish_ns']==ref['finish_ns']
        for key in ('waves','source_ack_watchdog'):
            assert current['service'][key]==ref['service'][key]
        old_callbacks={m['id']:m for m in ref['service']['message_callbacks']};deltas=[]
        for row in current['service']['message_callbacks']:
            old=old_callbacks[row['id']]
            assert {k:v for k,v in row.items() if not k.endswith('_ns')}=={k:v for k,v in old.items() if not k.endswith('_ns')}
            deltas.extend(dict(message=row['id'],field=k,delta_ns=row[k]-old[k]) for k in row if k.endswith('_ns') and row[k]!=old[k])
        def node_times(path):
            return {(f['graph_id'],f['rank'],f['node'],f['kind']):f['time_ns']
                    for f in frames(path/'native.stdout.log') if f['type']=='OBSERVE' and f['kind'].startswith('node_')}
        new_nodes,old_nodes=node_times(root/mode),node_times(args.reference.resolve()/mode)
        assert set(new_nodes)==set(old_nodes)
        node_deltas=[dict(graph=k[0],rank=k[1],node=k[2],kind=k[3],delta_ns=new_nodes[k]-old_nodes[k]) for k in new_nodes if new_nodes[k]!=old_nodes[k]]
        assert max((abs(row['delta_ns']) for row in deltas+node_deltas),default=0)<=1
        if mode=='local':
            assert not deltas and not node_deltas and current['packet_capture']==ref['packet_capture']
            assert current['service_transport']['messages']==ref['service_transport']['messages']
        matches[mode]=dict(original_packets_equal=current['packet_capture']==ref['packet_capture'],
            original_messages_equal=current['service_transport']['messages']==ref['service_transport']['messages'],
            execution_events_byte_identical=(root/mode/'execution_events.jsonl').read_bytes()==(args.reference.resolve()/mode/'execution_events.jsonl').read_bytes(),
            callback_deltas=deltas,node_time_deltas=node_deltas,finished_ns=current['finish_ns'],
            note='shared FIFO introduces additional same-time events; normal ordering differs, with two ACK callbacks shifted by one ns')
    commands=[]
    def command(argv,ok):
        r=subprocess.run(list(map(str,argv)),capture_output=True,text=True,timeout=30)
        commands.append(dict(argv=list(map(str,argv)),exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr))
        assert (r.returncode==0)==ok,r.stderr
        return r
    def check(path,ok):return command([sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-check','--run',path],ok)
    relocated=output/'relocated';shutil.copytree(root/'helper',relocated);check(relocated,True)
    originals={name:(relocated/name).read_bytes() for name in ('result.json','helper_control.json','helper_events.jsonl','native.stdout.log')}
    rejected={}
    for mutation in ('reply-time','candidate-cost','transport-arrival','compute-rank','missing-lifecycle','duplicate-lifecycle'):
        for name,raw in originals.items():(relocated/name).write_bytes(raw)
        if mutation=='reply-time':
            value=load(relocated/'helper_control.json');value[0]['reply']['now_ns']-=1
            (relocated/'helper_control.json').write_text(json.dumps(value))
        elif mutation in ('candidate-cost','transport-arrival','compute-rank'):
            value=load(relocated/'result.json');candidate=value['service']['inflight_helpers']['attempts'][0]['candidate']
            if mutation=='candidate-cost':candidate['compute_ns']+=1
            elif mutation=='transport-arrival':
                next(m for m in value['service_transport']['messages'] if m['id']==candidate['result_message'])['receive_ready_ns']-=1
            else:next(t for t in value['service']['shared_compute']['tasks'] if t['kind']=='helper_experts')['rank']=0
            (relocated/'result.json').write_text(json.dumps(value))
        else:
            rows=originals['helper_events.jsonl'].decode().splitlines(keepends=True)
            raw=originals['native.stdout.log'].decode().splitlines(keepends=True)
            index=next(i for i,line in enumerate(raw) if '"kind":"result_ready"' in line and 'HELPER_OBSERVE' in line)
            if mutation=='missing-lifecycle':
                rows=[line for line in rows if json.loads(line)['kind']!='result_ready'];raw.pop(index)
            else:
                target=next(line for line in rows if json.loads(line)['kind']=='result_ready');rows.append(target);raw.insert(index,raw[index])
            (relocated/'helper_events.jsonl').write_text(''.join(rows));(relocated/'native.stdout.log').write_text(''.join(raw))
        rejected[mutation]=check(relocated,False).stderr
    for name,raw in originals.items():(relocated/name).write_bytes(raw)
    native_rejected={};declaration=load(root/'helper/inflight-helpers-config.json')
    for mutation in ('overlap','incomplete-group','bool-budget','insufficient-events','bad-placement'):
        value=deepcopy(declaration)
        if mutation=='overlap':value['groups'][1][0]=0
        elif mutation=='incomplete-group':value['groups'][0].pop()
        elif mutation=='bool-budget':value['configuration']['max_compute_tasks']=True
        elif mutation=='insufficient-events':value['configuration']['max_events']=1
        else:value['expert_to_group_rank']=[[True]*256]
        path=output/(mutation+'.json');save(path,value)
        r=command([args.backend.resolve(),output/'missing-topology',output/'missing-rdma',output/'missing-system',output/'missing-bootstrap',
                   output/'never-result','--inflight-helpers',path],False)
        assert 'cannot open' not in r.stderr and not (output/'never-result').exists()
        native_rejected[mutation]=r.stderr
    hashed={str(path):sha(path) for mode in ('normal','local','helper') for path in (root/mode).rglob('*') if path.is_file()}
    revision=subprocess.run(['git','rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
    extra=cases['helper']['attempts'][0]
    report=dict(passed=True,execution_commit=revision,cases=cases,
        reference_matches=matches,same_resources_and_original_work=True,negative_checks=rejected,
        native_pre_fabric_rejections=native_rejected,original_hashes=hashed,
        findings=dict(application_gain_ns=cases['local']['application_finished_ns']-cases['helper']['application_finished_ns'],
            bundle_earlier_ns=extra['bundle_earlier_ns'],
            extra_payload_bytes=cases['helper']['declared_payload_bytes']-cases['local']['declared_payload_bytes'],
            extra_compute_ns=sum(t['duration_ns'] for t in helper['service']['shared_compute']['tasks'] if t['kind']!='original'),
            call_gain_ns=cases['local']['critical_call_logical_complete_ns']-cases['helper']['critical_call_logical_complete_ns'],
            reason='rank8 healthy input still waits original EP Dispatch gate; original graph and callbacks retained'),
        scope='artificial functional routes; actual 128-endpoint Clos and shared resources; no application recovery benefit')
    save(output/'verification.json',report);save(output/'commands.json',commands)
    print(json.dumps(dict(passed=True,cases=cases,negative_checks=len(rejected),native_rejections=len(native_rejected))))


if __name__=='__main__':main()
