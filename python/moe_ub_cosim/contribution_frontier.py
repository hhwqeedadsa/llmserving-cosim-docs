"""Read-only all-contribution readiness and buffer lifetime diagnosis.

Recorded arrival maxima are compared on ONE fixed execution. This is not a
counterfactual rerun, a runtime recovery policy, or an application consumer.
"""
import csv
import hashlib
import json
from pathlib import Path

from .scaleout_rdma import checked_json, save
from .scaleout_limits import limits
from .scaleout_topology import integer, require


def call_frontier(row, messages, helper_results, buffers):
    """Independently join every expected bundle, preserving all final ties."""
    binding=row['binding'];state=row['state']
    bundles={b['id']:b for b in binding['bundles']}
    expected={b['id']:b['experts'] for b in row['spec']['bundles']}
    require(len(bundles)==len(binding['bundles'])==len(expected)==len(row['spec']['bundles']) and
            bundles and set(bundles)==set(expected)==set(state['winners']),
            'frontier has duplicate/missing expected bundle')
    require(all(b['experts']==expected[key] for key,b in bundles.items()),'frontier bundle expert identity differs')
    require(sorted(e for experts in expected.values() for e in experts)==row['spec']['expected_experts'] and
            row['spec']['expected_experts']==sorted(set(row['spec']['expected_experts'])) and
            state['accepted_experts']==row['spec']['expected_experts'],'frontier expert cover differs')
    details=[]
    for bid,bundle in sorted(bundles.items()):
        original='original.'+bid;attempts=state['attempts']
        require(original in attempts,'frontier lacks original attempt')
        original_time=attempts[original]['result_ready_ns'];integer(original_time,'original result time',0,10**9)
        candidates={name:a['result_ready_ns'] for name,a in attempts.items() if a['bundle']==bid and a['result_ready_ns'] is not None}
        require(candidates and all(type(value) is int and value>=0 for value in candidates.values()),
                'invalid contribution result readiness')
        earliest=min(candidates.values());winner=state['winners'][bid]
        require(winner['attempt'] in candidates and candidates[winner['attempt']]==winner['time_ns']==earliest,
                'frontier winner differs from actual first result')
        mid=bundle['combine_message'] if winner['attempt']==original else helper_results.get((row['call'],winner['attempt']))
        if bundle['combine_message'] is not None:
            require(bundle['combine_message'] in messages,'original result lacks actual transport message')
            m=messages[bundle['combine_message']]
            require(m['src']==bundle['rank'] and m['dst']==binding['source_rank'] and
                    m['receive_ready_ns']<=original_time,'original result does not match transport binding')
        elif winner['attempt']==original:
            require(bundle['rank']==binding['source_rank'],'remote result missing physical message')
        if winner['attempt']!=original:require(mid in messages,'helper result lacks actual transport message')
        if mid is not None:
            require(messages[mid]['dst']==binding['source_rank'] and messages[mid]['receive_ready_ns']<=earliest,
                    'winner result does not match destination transport')
        details.append(dict(call=row['call'],bundle=bid,owner=bundle['rank'],experts=bundle['experts'],
            original_ready_ns=original_time,winner_attempt=winner['attempt'],winner_ready_ns=earliest,
            winner_message=mid,individual_advance_ns=original_time-earliest))
    original=max(d['original_ready_ns'] for d in details);chosen=max(d['winner_ready_ns'] for d in details)
    require(state['logical_complete_ns']==chosen and chosen<=original,'full contribution readiness differs')
    for d in details:
        d['original_is_last']=d['original_ready_ns']==original
        d['winner_is_last']=d['winner_ready_ns']==chosen
        mid=d['winner_message'];buffer=buffers.get(mid)
        d['buffer_evidence']='local_no_message' if mid is None else ('captured' if buffer else 'not_captured')
        d['buffer_released_ns']=None if buffer is None else buffer['target_released_ns']
        d['winner_buffer_live_at_join']=None;d['minimum_extra_hold_ns']=None
        if buffer is not None:
            require(buffer['receive_ready_ns']<=d['winner_ready_ns'] and buffer['id']==mid,
                    'winner buffer differs from actual receive')
            release=buffer['target_released_ns'];integer(release,'winner buffer release',0,10**9)
            # Same ns alone does not identify callback order; leave it unknown.
            d['winner_buffer_live_at_join']=True if chosen<release else False if chosen>release else None
            d['minimum_extra_hold_ns']=max(0,chosen-release)
    return dict(call=row['call'],graph_id=binding['graph_id'],source_rank=binding['source_rank'],slot=binding['slot'],
        layer=binding['logical_layer'],decode_step=row['spec']['invocation']['decode_step'],
        original_all_ready_ns=original,winner_all_ready_ns=chosen,recorded_frontier_advance_ns=original-chosen,
        original_last_bundles=[d['bundle'] for d in details if d['original_is_last']],
        winner_last_bundles=[d['bundle'] for d in details if d['winner_is_last']],bundles=details)


def diagnose(calls, result, waves, events):
    transport=result['service_transport'];messages={m['id']:m for m in transport['messages']}
    buffers={m['id']:m for m in transport.get('message_buffers',{}).get('messages',[])}
    attempts=result['service'].get('inflight_helpers',{}).get('attempts',[])
    helpers={};extra=0
    for attempt in attempts:
        c=attempt['candidate'];key=c['call'],'helper.'+c['bundle']
        require(key not in helpers,'ambiguous helper result mapping');helpers[key]=c['result_message']
        for key in ('input_message','result_message'):
            require(c[key] in messages,'helper byte overhead lacks physical message')
            extra+=messages[c[key]]['bytes']
    tasks={(t['graph_id'],t['rank'],t['node']):t for t in result['service']['shared_compute']['tasks'] if t['kind']=='original'}
    require(tasks,'frontier requires actual shared-compute start evidence')
    node_times={}
    for e in events:
        if e['kind'] in ('node_issue','node_complete'):
            key=e['graph_id'],e['rank'],e['node'],e['kind']
            require(key not in node_times,'duplicate native node time');node_times[key]=e['time_ns']
    phase={(r['graph_id'],r['id']):r for r in result['service']['phase_completion']['gates']}
    rows=[];seen=set()
    for call in calls:
        require(call['call'] not in seen,'duplicate contribution call');seen.add(call['call'])
        row=call_frontier(call,messages,helpers,buffers)
        graph=row['graph_id'];wave=waves[graph];lid=row['layer'];src=row['source_rank']
        require(call['spec']['invocation']['decode_step']==wave['decode_step'],'frontier decode identity differs')
        nodes={(int(rank),n['id']):n for rank,ns in wave['rank_nodes'].items() for n in ns}
        by_name={(rank,n['name']):n for (rank,_),n in nodes.items()}
        merge=by_name[src,f'L{lid}.merge'];merge_task=tasks[graph,src,merge['id']]
        row.update(merge_ready_ns=merge_task['ready_ns'],merge_start_ns=merge_task['start_ns'],
            merge_done_ns=merge_task['done_ns'],winner_to_merge_start_ns=merge_task['start_ns']-row['winner_all_ready_ns'])
        require(row['winner_to_merge_start_ns']>=0,'merge consumed before every contribution arrived')
        gate=phase[graph,f'L{lid}.dispatch'];members={m['rank']:m['node'] for m in gate['members']}
        for d,b in zip(row['bundles'],sorted(call['binding']['bundles'],key=lambda b:b['id'])):
            require(d['bundle']==b['id'],'bundle order differs')
            rank=b['rank'];expert=nodes[rank,b['expert_node']];task=tasks[graph,rank,b['expert_node']]
            gate_node=members[rank]
            require(gate_node in expert['deps'],'expert lacks declared Dispatch gate')
            gate_time=node_times[graph,rank,gate_node,'node_complete']
            other=max((node_times[graph,rank,node,'node_complete'] for node in expert['deps'] if node!=gate_node),default=0)
            original=call['state']['attempts']['original.'+b['id']]
            require(task['ready_ns']==node_times[graph,rank,b['expert_node'],'node_issue'] and
                    task['done_ns']>=task['start_ns']>=task['ready_ns']>=max(other,gate_time,original['input_ready_ns']) and
                    gate_time==gate['released_ns'],
                    'expert FIFO or phase completion evidence differs')
            d.update(input_ready_ns=original['input_ready_ns'],expert_ready_ns=task['ready_ns'],
                expert_start_ns=task['start_ns'],expert_done_ns=task['done_ns'],dispatch_gate_ns=gate_time,
                other_dependencies_ready_ns=other,gate_strictly_last_dependency=gate_time>other,
                expert_issue_wait_after_input_ns=task['ready_ns']-original['input_ready_ns'],
                expert_fifo_wait_ns=task['start_ns']-task['ready_ns'])
        rows.append(row)
    require(rows,'no contribution calls to diagnose')
    details=[d for r in rows for d in r['bundles']]
    summary=dict(schema_version='moe-contribution-frontier/1',calls=len(rows),bundles=len(details),
        calls_with_recorded_frontier_advance=sum(r['recorded_frontier_advance_ns']>0 for r in rows),
        max_recorded_frontier_advance_ns=max(r['recorded_frontier_advance_ns'] for r in rows),
        helper_winner_bundles=sum(d['winner_attempt'].startswith('helper.') for d in details),
        helper_actual_logical_bytes=extra,
        captured_winners_released_before_join=sum(d['winner_buffer_live_at_join'] is False for d in details),
        max_minimum_extra_hold_ns=max((d['minimum_extra_hold_ns'] or 0 for d in details),default=0),
        bundle_uses_of_strict_dispatch_gate=sum(d['gate_strictly_last_dependency'] for d in details),
        native_network_executed_by_diagnosis=False,
        scope='recorded same-execution readiness maxima and actual lifetimes; not a counterfactual rerun or measured application speedup',
        timing='nanoseconds; per-call maxima, never summed latency across calls',
        buffer_scope='current scope-based release is valid without consumers; early release here identifies a future consumer lease requirement')
    return summary,rows


def inventory(directory):
    return {str(p.relative_to(directory)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob('*')) if p.is_file()}


def execute(directory,output):
    from .scaleout_verify import verify
    directory=Path(directory).resolve();output=Path(output).resolve()
    require(not output.exists() and not output.is_relative_to(directory),'frontier output must be new and outside input run')
    config=checked_json(directory/'input.json',2**20)
    require('contribution_tracking' in config and 'inflight_helpers' in config,
            'frontier requires the explicit contribution/helper shared-compute service')
    before=inventory(directory);receipt=verify(directory)
    protocol=checked_json(directory/'rdma.json',65536)
    result=checked_json(directory/'result.json',limits(protocol)['result_json_bytes'])
    maximum=config['contribution_tracking']['max_report_bytes'];path=directory/'contribution_calls.jsonl'
    require(path.stat().st_size<=maximum,'contribution input exceeds declared byte budget')
    calls=[]
    with path.open() as stream:
        for line in stream:
            require(len(line.encode())<=maximum,'oversized contribution row');calls.append(json.loads(line))
            require(len(calls)<=config['contribution_tracking']['max_invocations'],'contribution call budget exceeded')
    waves={w['graph_id']:checked_json(directory/'graphs'/w['graph_id']/'wave.json',16*2**20) for w in result['service']['waves']}
    events=[]
    with (directory/'execution_events.jsonl').open() as stream:
        for line in stream:events.append(json.loads(line))
    summary,rows=diagnose(calls,result,waves,events)
    require(inventory(directory)==before,'frontier diagnosis changed saved input run')
    summary.update(input_run_verified=receipt['passed'],input_files_unchanged=len(before),
        gpu_count=result['gpu_count'],expert_parallel_sizes=[len(g) for g in config['parallel']['groups']],
        input_identity={name:before[name] for name in ('input.json','result.json','contribution_calls.jsonl','execution_events.jsonl')},
        hardware_calibrated=False)
    output.mkdir(parents=True)
    save(output/'summary.json',summary);save(output/'calls.json',rows)
    columns=['call','bundle','owner','winner_attempt','original_ready_ns','winner_ready_ns','original_is_last',
             'winner_is_last','individual_advance_ns','input_ready_ns','expert_ready_ns','expert_start_ns',
             'dispatch_gate_ns','other_dependencies_ready_ns','gate_strictly_last_dependency','expert_fifo_wait_ns',
             'buffer_evidence','buffer_released_ns','winner_buffer_live_at_join','minimum_extra_hold_ns']
    with (output/'bundles.csv').open('x',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns,extrasaction='ignore');writer.writeheader()
        for row in rows:writer.writerows(row['bundles'])
    return summary
