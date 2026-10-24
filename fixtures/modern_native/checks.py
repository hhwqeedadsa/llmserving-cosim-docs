"""Preserve evidence mutations, public rejects and the real size limitation."""
import argparse
from copy import deepcopy
from pathlib import Path
import subprocess
from collections import Counter
from fixtures.modern_native.stage import load,save
from fixtures.modern_native.audit import audit


def main():
    p=argparse.ArgumentParser()
    for key in ('repository','stage'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();root=s/'synthetic-healthy';rejected=[]
    original={name:load(root/name) for name in ('result.json','modern-execution.json','modern-plan.json','initial.json')}
    mutations={
        'fake_visible_callback':('modern-execution.json',lambda m:next(e for e in m['events'] if e['kind']=='visible').update(native_event='completed')),
        'missing_self':('modern-execution.json',lambda m:next(t for t in m['native_tasks'].values() if t.get('path')=='gin_self').update(modern_operation='missing')),
        'wrong_target_bytes':('result.json',lambda r:r['transactions']['target_events'][0].update(body_hex='0000000000000002')),
        'early_modern_complete':('modern-execution.json',lambda m:next(e for e in m['events'] if e['kind']=='local_complete').update(time_ns=0)),
        'corrupt_source':('result.json',lambda r:next(e for e in r['transactions']['source_events'] if e['kind']=='captured').update(hex='01')),
        'wrong_atomic_return':('result.json',lambda r:next(o for o in r['transactions']['operations'] if o['verb']=='fetch_add').update(returned_value=900)),
        'wrong_MRT1_magic':('result.json',lambda r:r['transactions']['transport']['source_payload_packets'][0].update(hex='00'+r['transactions']['transport']['source_payload_packets'][0]['hex'][2:])),
        'wrong_destination_nic':('initial.json',lambda i:next(c for c in i['connections'] if c['src']!=c['dst']).update(destination_nic=2)),
        'missing_zero_count':('modern-plan.json',lambda p:p['operations'].remove(next(o for o in p['operations'] if o['kind']=='expert_count_block' and set(o['values'])=={-1}))),
        'wrong_allocated_slot':('modern-execution.json',lambda m:next(e for e in m['events'] if e['kind']=='slot_allocate').update(slot_value=99)),
    }
    for label,(filename,change) in mutations.items():
        damaged=deepcopy(original[filename]);change(damaged)
        try:audit(root,{filename:damaged})
        except (AssertionError,KeyError,ValueError,StopIteration):rejected.append(label)
        else:raise AssertionError('corrupted evidence accepted: '+label)
    # Keep out-of-order packet arrivals separate from actually accepted bytes.
    fault=load(s/'synthetic-fault/result.json');damaged=deepcopy(fault)
    next(w for w in damaged['transactions']['transport']['work'] if w['stream']==2 and w['begin']==40)['accepted_ns']=24332
    try:audit(s/'synthetic-fault',{'result.json':damaged})
    except AssertionError:rejected.append('out_of_order_arrival_is_not_acceptance')
    else:raise AssertionError('early out-of-order acceptance was not rejected')
    out=s/'public-reject';out.mkdir();commands=[]
    cfg=load(s/'inputs/synthetic.json')
    changes={
        'GPU_visibility':lambda c:c['provider'].update(target_visibility='gpu_visible'),
        'self_timing':lambda c:c['provider']['self_service'].update(apply_ns=1000001),
        'missing_expert_cost':lambda c:c['costs'].pop('expert_ns'),
        'wrong_nic':lambda c:c['deployment'][0]['devices'][0].update(nic=3),
        'repeated_rank':lambda c:c['deployment'][1].update(rank=0),
    }
    for label,change in changes.items():
        config=deepcopy(cfg);change(config);path=out/(label+'.json');save(path,config);output=out/(label+'-unexpected')
        argv=['python3','-B','-m','moe_ub_cosim','scaleout','modern-deepep-native','--topology',str(s/'topology'),
            '--protocol',str(repo/'examples/modern_deepep_native/rdma.json'),'--config',str(path),
            '--backend',str(s/'runtime/run-transaction-session'),'--output',str(output)]
        r=subprocess.run(argv,cwd=repo,capture_output=True,text=True,timeout=5)
        (out/(label+'.stdout')).write_text(r.stdout);(out/(label+'.stderr')).write_text(r.stderr)
        assert r.returncode!=0 and not output.exists();commands.append(dict(label=label,command=argv,cwd=str(repo),exit_code=r.returncode))
    save(s/'checks.json',dict(passed=True,independent_tamper_rejects=rejected,public_rejects_before_output_and_native=commands,additional_network_executions=0))
    failed=load(s/'four-routes/result.json');state=load(s/'four-routes/modern-execution.json')
    assert not failed['passed'] and failed['session']['decisions']==256 and 'session decision budget exceeded' in failed['execution_error']
    assert state['runtime']['fired_events']==437 and state['runtime']['total_events']==566 and not state['runtime']['all_application_complete']
    analysis={}
    for label in ('synthetic-healthy','synthetic-fault','four-routes'):
        import json
        lines=[json.loads(l) for l in (s/label/'control.jsonl').read_text().splitlines()];pairs=list(zip(lines[::2],lines[1::2]));unused=[]
        for native,frontend in pairs:
            e=native['message']['events'];cmd=frontend['message']
            if e and not any(x['event'] in ('submitted','local_started','applied','completed') for x in e):
                assert not cmd['submissions'] and not cmd['finish'];unused.append(native['message']['decision_id'])
        analysis[label]=dict(actual_decisions=len(pairs),unused_only_decisions=unused,
            transition_counts=dict(Counter(e['event'] for n,c in pairs for e in n['message']['events'])))
    save(s/'limitation.json',dict(four_request_full_call_passed=False,actual_network_executed=True,
        failure='native session hard-coded 256 decisions, not network deadlock',finish_ns=failed['finish_ns'],
        fired_modern_events=437,total_modern_events=566,maximum_decisions_unchanged=256,
        enlarged_budgets=False,failed_network_reexecuted=False,transcript_analysis=analysis,
        next_requirement='bounded dependency-driven callback wakeups with retained full history; do not silently raise this phase budget or delay independent application work'))


if __name__=='__main__':main()
