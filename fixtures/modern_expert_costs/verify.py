"""Independently check declared expert duration, causal gates and actual frames."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from fixtures.modern_selective.verify import verify as application_verify


def load(p):return json.loads(p.read_bytes())


def verify(root,overrides=None):
    def read(name):return overrides[name] if overrides and name in overrides else load(root/name)
    cfg=read('input.json');plan=read('modern-plan.json');state=read('modern-execution.json')
    result=read('result.json');ranks=cfg['modern']['execution']['ranks'];calls=len(cfg['modern']['calls'])
    if cfg['schema_version']=='moe-modern-native-input/1':
        expected=[[cfg['costs']['expert_ns']]*ranks for _ in range(calls)]
        assert 'expert_stage' not in state
    else:
        assert cfg['schema_version']=='moe-modern-native-input/2' and 'expert_ns' not in cfg['costs']
        stage=cfg['expert_stage'];assert stage['mode']=='per_call_rank_declared_ns/1'
        assert stage==state['expert_stage'] and type(stage['source']) is str and stage['source'].strip()
        expected=stage['durations_ns']
    assert len(expected)==calls and all(len(row)==ranks for row in expected)
    nodes={n['id']:n for n in state['native_operations']}
    tasks={int(i):t for i,t in state['native_tasks'].items()}
    logs={e['event']:e for e in state['events']}
    events={e['id']:e for e in plan['events']}
    callbacks={(e['operation'],e['event']):e['time_ns'] for e in result['session']['events']}
    assert len(callbacks)==len(result['session']['events'])
    wanted={f'm{call}.r{rank}.experts_done' for call in range(calls) for rank in range(ranks)}
    assert {e['id'] for e in events.values() if e['kind']=='expert_stage_done'}==wanted
    actual={task['event']:identity for identity,task in tasks.items()
        if task['role']=='event' and task['event'] in wanted}
    assert set(actual)==wanted and len(actual)==calls*ranks
    rows=[]
    for call in range(calls):
        for rank in range(ranks):
            name=f'm{call}.r{rank}.experts_done';identity=actual[name];node=nodes[identity]
            pred=f'm{call}.dispatch.r{rank}.complete'
            assert events[name]['rank']==rank and events[name]['deps']==[pred]
            assert node['kind']=='local' and node['duration_ns']==expected[call][rank]
            started=callbacks[identity,'local_started'];completed=callbacks[identity,'completed']
            admitted=callbacks[identity,'local_admitted']
            assert logs[pred]['time_ns']<=admitted<=started
            assert completed-started==expected[call][rank]
            event=logs[name]
            assert event['native_operation']==identity and event['native_event']=='completed'
            assert event['native_time_ns']==completed and event['time_ns']>=completed
            begin=f'm{call}.combine.r{rank}.begin'
            assert events[begin]['deps']==[name] and logs[begin]['time_ns']>=event['time_ns']
            for op in plan['operations']:
                if op['call']==call and op['phase']=='combine' and op['src']==rank:
                    assert logs[op['events']['issued']]['time_ns']>=completed
            rows.append(dict(call=call,rank=rank,operation=identity,declared_ns=expected[call][rank],
                dispatch_complete_ns=logs[pred]['time_ns'],admitted_ns=admitted,started_ns=started,
                completed_ns=completed,combine_begin_ns=logs[begin]['time_ns']))
    assert not state['hardware_calibrated'] and not state['numeric_expert_computation'] and not state['gpu_visibility_modeled']
    app=application_verify(root,overrides)
    assert app['capture_complete'] and app['omitted_events']==0
    return dict(passed=True,expert_stages=rows,application=app,hardware_calibrated=False,
        numeric_expert_computation=False,declared_duration_not_observed_profile=True)


def corruptions(root):
    state=load(root/'modern-execution.json')
    identity=next(int(i) for i,t in state['native_tasks'].items()
        if t.get('event')=='m0.r0.experts_done')
    probes=[]
    v=deepcopy(state);next(n for n in v['native_operations'] if n['id']==identity)['duration_ns']+=1
    probes.append(('duration',{'modern-execution.json':v}))
    v=load(root/'result.json');next(e for e in v['session']['events']
        if e['operation']==identity and e['event']=='completed')['time_ns']+=1
    probes.append(('actual_completion',{'result.json':v}))
    v=load(root/'modern-plan.json');next(e for e in v['events']
        if e['id']=='m0.r0.experts_done')['deps']=[]
    probes.append(('dispatch_prerequisite',{'modern-plan.json':v}))
    v=deepcopy(state);v['expert_stage']['source']='tampered source description'
    probes.append(('declared_source',{'modern-execution.json':v}))
    for name,overrides in probes:
        try:verify(root,overrides)
        except (AssertionError,ValueError,KeyError):continue
        raise AssertionError('accepted corruption '+name)
    return [name for name,_ in probes]


def compare(left,right,*,matrix_metadata=False,library_roots=False):
    a,b=load(left/'result.json'),load(right/'result.json')
    if library_roots:
        # Preserve list order and every other field. Only package root changes.
        for value in (a,b):
            value['loaded_libraries']=[Path(n).name if Path(n).name.startswith('libns3.') else n
                for n in value['loaded_libraries']]
    assert a==b,'actual network state differs'
    a,b=load(left/'modern-execution.json'),load(right/'modern-execution.json')
    if matrix_metadata:assert b.pop('expert_stage')==load(right/'input.json')['expert_stage']
    assert a==b,'modern execution differs'
    assert (left/'control.jsonl').read_bytes()==(right/'control.jsonl').read_bytes()
    assert (left/'modern-plan.json').read_bytes()==(right/'modern-plan.json').read_bytes()
    return dict(passed=True,whole_native_state=True,control_byte_identical=True,
        modern_state_identical_except_declared_matrix=matrix_metadata,
        library_root_mapping_only=library_roots)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--corruptions',action='store_true')
    a=p.parse_args();assert not a.output.exists()
    report=verify(a.run)
    if a.corruptions:report['corruptions_rejected']=corruptions(a.run)
    a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
