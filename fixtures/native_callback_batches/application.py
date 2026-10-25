"""Independent full application/memory check with explicit physical capture limits.

Application assertions retained from fixtures/modern_native/audit.py; full wire
oracle is replaced by the separately labeled source/work/prefix audit.
"""
from collections import Counter,defaultdict
import hashlib
from fixtures.native_callback_batches.stage import load
from fixtures.modern_native.audit import memory_audit
from fixtures.native_callback_batches.transport import transport_audit

def qp_context(sm,warp,sms,contexts,warps,notify):
    if contexts==1:return 0
    first=int(notify)
    candidates=list(range(first+sm,contexts,sms))
    return candidates[warp%len(candidates)] if sms<=contexts-first else first+(sm*warps+warp)%(contexts-first)


def application_audit(root,overrides=None):
    def read(name):return overrides[name] if overrides and name in overrides else load(root/name)
    c=read('input.json');config=c['modern'];x=config['execution'];ranks=x['ranks'];cap=x['max_tokens_per_rank'];experts=config['model']['experts'];local=experts//ranks
    result=read('result.json');modern=read('modern-execution.json');initial=read('initial.json');plan=read('modern-plan.json')
    assert result['passed'] and result['session']['finished'] and result['drained'] and modern['runtime']['all_events_fired']
    nodes={n['id']:n for n in modern['native_operations']};tasks={int(i):t for i,t in modern['native_tasks'].items()}
    callbacks={(e['operation'],e['event']):e['time_ns'] for e in result['session']['events']};assert len(callbacks)==len(result['session']['events'])
    log=modern['events'];events={e['id']:e for e in plan['events']};ops={o['id']:o for o in plan['operations']};fired={};byoperation=defaultdict(dict)
    assert len(log)==len(events) and len(nodes)==result['completed_operations']+result['completed_local_work']
    direct=[]
    for row in (loadline for loadline in map(__import__('json').loads,(root/'control.jsonl').read_text().splitlines())):
        if row['direction']=='frontend':direct.extend(row['message']['submissions'])
    assert direct==modern['native_operations']
    group_next=Counter();slots={}
    for row in log:
        name=row['event'];event=events[name];assert name not in fired and all(d in fired and fired[d]['time_ns']<=row['time_ns'] for d in event['deps'])
        assert row['kind']==event['kind']
        if row['origin']=='native_callback':
            key=row['native_operation'],row['native_event'];assert key in callbacks and callbacks[key]==row['native_time_ns']<=row['time_ns']
            task=tasks[key[0]]
            if 'operation' in event:
                assert task['modern_operation']==event['operation'];byoperation[event['operation']][event['kind']]=row
                native=nodes[key[0]];localnode=native['kind']=='local'
                expected=('local_started' if localnode else 'submitted') if event['kind']=='issued' else 'completed' if localnode or event['kind']=='local_complete' else 'applied'
                assert row['native_event']==expected
            else:assert task['event']==name and row['native_event']=='completed'
        else:
            assert row['origin']=='zero_cost_control' and event['kind'] not in ('issued','accepted','visible','local_complete','token_prepared','counts_ready','barrier_ready','expert_stage_done','local_expert_reduction_done')
        if event['kind']=='slot_allocate':
            group=tuple(event['allocation_group']);assert row['slot_value']==group_next[group];group_next[group]+=1;slots[name]=row['slot_value']
        fired[name]=row
    assert set(fired)==set(events) and set(byoperation)==set(ops) and all(len(v)==4 for v in byoperation.values())
    memory=memory_audit(result,initial,nodes);wire=transport_audit(result,initial,nodes)
    expected_names=set();pairs={};groups={}
    for ci,call in enumerate(config['calls']):
        counts=Counter();pair=Counter()
        for src,tokens in enumerate(call['tokens']):
            for token,choices in enumerate(tokens):
                counts.update((src,e) for e in choices)
                for dst in set(e//local for e in choices):
                    pair[src,dst]+=1;groups[ci,src,token,dst]=[i for i,e in enumerate(choices) if e//local==dst]
        pairs[ci]=pair
        for phase in ('dispatch','combine'):
            for boundary in ('entry','exit'):
                expected_names.update(f'm{ci}.{phase}.{boundary}.s{s}.d{d}' for s in range(ranks) for d in range(ranks))
        for src in range(ranks):
            for dst in range(ranks):
                expected_names.update((f'm{ci}.dispatch.rank_count.s{src}.d{dst}',f'm{ci}.dispatch.expert_count.s{src}.d{dst}'))
                op=ops[f'm{ci}.dispatch.rank_count.s{src}.d{dst}'];assert op['value']==-pair[src,dst]-1
                op=ops[f'm{ci}.dispatch.expert_count.s{src}.d{dst}'];assert op['values']==[-counts[src,e]-1 for e in range(dst*local,(dst+1)*local)]
    for ci,src,token,dst in groups:
        expected_names.update((f'm{ci}.dispatch.data.s{src}.t{token}.d{dst}',f'm{ci}.combine.data.s{dst}.d{src}.t{token}'))
    assert set(ops)==expected_names
    conns={c['id']:c for c in initial['connections']};bindings={b['transaction_connection']:b for b in modern['bindings']};physical={d['rank']:d['node'] for d in c['deployment']}
    for name,op in ops.items():
        row=byoperation[name]['issued'];node=nodes[row['native_operation']];src,dst,ci=op['src'],op['dst'],op['call']
        assert node['rank']==physical[src] if op['path']=='local' else conns[node['connection']]['src']==physical[src] and conns[node['connection']]['dst']==physical[dst]
        if op['kind']=='barrier_signal':expected_path='gin_self' if src==dst else 'rdma';expected_context=0;key=2;address=32*2**20+8*src
        elif op['kind']=='rank_count':expected_path='local' if src==dst else 'rdma';expected_context=0;key=1;address=4096+49216+8*src
        elif op['kind']=='expert_count_block':expected_path='gin_self' if src==dst else 'rdma';expected_context=0;key=1;address=4096+49216+8*(ranks+src*local)
        else:
            expected_path='local' if src==dst else 'rdma';key=1;h=config['model']['hidden_size'];k=config['model']['top_k']
            metadata=8*k+(4+4*k if op['phase']=='dispatch' else 0);nbytes=((2*h+((metadata+31)//32*32)+63)//64)*64
            assert nbytes==op['operand_bytes']
            if op['phase']=='dispatch':
                slot=slots[op['slot_variable']];address=4096+16*2**20+(src*cap+slot)*nbytes
                channel=op['token']%(x['num_sms']*x['dispatch_warps']);sm=channel%x['num_sms'];warp=channel//x['num_sms']
                expected_context=qp_context(sm,warp,x['num_sms'],x['call_contexts'],x['dispatch_warps'],True)
                marker_name=f"dispatch:{ci}:{src}:{op['token']}"
            else:
                positions=groups[ci,dst,op['token'],src];plane=src if ranks<=k else positions[0]
                address=4096+16*2**20+(plane*cap+op['token'])*nbytes
                index=sum(pairs[ci][s,src] for s in range(dst))+slots[op['binding']['slot_variable']]
                per=(sum(pairs[ci][s,src] for s in range(ranks))+x['num_sms']*x['combine_warps']-1)//(x['num_sms']*x['combine_warps'])
                sm=(index//per)%x['num_sms'];warp=(index//per)//x['num_sms']
                expected_context=qp_context(sm,warp,x['num_sms'],x['call_contexts'],x['combine_warps'],False)
                marker_name=name
            marker=hashlib.sha256(('modern-native-marker/1:'+marker_name).encode()).digest()+bytes(nbytes-32)
            if op['path']!='local':assert memory['source_bytes'][node['id']]==marker
            elif op['phase']=='dispatch':assert memory['copy_bytes'][node['id']]==marker
            else:
                reduction=name.replace('.combine.data.s','.combine.s')+'.reduced'
                helper=next(i for i,t in tasks.items() if t.get('event')==reduction)
                assert memory['copy_bytes'][helper]==marker and nodes[helper]['copy']['target']==dict(key=key,address=address)
        assert op['path']==expected_path and row['binding']['context_index']==expected_context
        if op['path']!='local':
            connection=conns[node['connection']];binding=bindings[node['connection']]
            assert (node['key'],node['address'])==(key,address)
            assert (binding['source_rank'],binding['peer_rank'],binding['context_index'])==(src,dst,expected_context)
            assert ('self_service' in connection)==(op['path']=='gin_self')
        elif 'copy' in node:assert node['copy']['target']==dict(key=key,address=address)
    assert not modern['runtime']['memory_state']['active_source_leases'] and modern['runtime']['memory_state']['nonzero_count_elements']==0
    assert len(modern['runtime']['memory_state']['completed_barriers'])==4*ranks*len(config['calls'])
    for signal in modern['runtime']['memory_state']['signals']:assert signal['value']==signal['shadow']==4*len(config['calls'])
    return dict(passed=True,modern_operations=len(ops),modern_events=len(events),native_remote_operations=result['completed_operations'],
        native_local_operations=result['completed_local_work'],paths=dict(Counter(o['path'] for o in ops.values())),
        calls=len(config['calls']),ranks=ranks,decisions=modern['decisions'],finish_ns=result['finish_ns'],
        actual_target_applications=len(memory['targets']),**wire,scope='functional byte/event acceptance, not numerical model or calibrated GPU/provider performance')

