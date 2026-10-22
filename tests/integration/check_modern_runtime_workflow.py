"""Public transcript workflow with an independent, untimed functional oracle.

No runtime import, GPU, provider or timing model. This test deliberately supplies
callbacks; they must not be used as research/network simulation observations.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def limits():
    resource.setrlimit(resource.RLIMIT_AS,(2*2**30,2*2**30))
    resource.setrlimit(resource.RLIMIT_CPU,(30,30))


def oracle(plan, config):
    events = {e['id']:e for e in plan['events']}
    ops = {o['id']:o for o in plan['operations']}
    slots = {s['id']:s for s in plan['slots']}
    done, values, actions = set(), {}, []
    counts = defaultdict(int)
    token_slots, owner_slots = defaultdict(set), defaultdict(set)
    for s in slots.values():
        token_slots[s['call'],s['src'],s['token']].add(s['id'])
        owner_slots[s['call'],s['dst']].add(s['id'])
    x = config['execution']
    returns = [o for o in ops.values() if o['phase']=='combine' and o['kind']=='token_data']
    reduced_ops = {events[o['events']['issued']]['deps'][0]:o for o in returns}

    def receive(op):
        b=op['binding'];index=b['source_rank_prefix']+values[b['slot_variable']]
        width=(b['owner_received_tokens']+x['num_sms']*x['combine_warps']-1)//(x['num_sms']*x['combine_warps'])
        channel=index//width
        return (op['call'],op['src'],channel),index

    while len(done)<len(events):
        enabled=[]
        for identity,e in events.items():
            if identity in done or not set(e['deps'])<=done:continue
            if e['kind']=='issued':
                op=ops[e['operation']]
                if op['phase']=='dispatch' and op['kind']=='token_data':
                    if not token_slots[op['call'],op['src'],op['token']]<=done:continue
            if identity in reduced_ops:
                op=reduced_ops[identity]
                if not owner_slots[op['call'],op['src']]<=done:continue
                channel,index=receive(op)
                previous=[o for o in returns if (o['call'],o['src'])==(op['call'],op['src'])
                          and receive(o)[0]==channel and receive(o)[1]<index]
                if any(o['events']['issued'] not in done for o in previous):continue
            enabled.append(identity)
        assert enabled,'independent oracle deadlocked'
        identity=max(enabled)  # Deterministic TEST policy, never a GPU policy.
        if identity in slots:
            s=slots[identity];group=(s['call'],s['src'],s['dst'])
            values[identity]=counts[group];counts[group]+=1
        done.add(identity);actions.append(identity)
    return actions,values,{o['id']:receive(o) for o in returns}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve()
    assert not out.exists() and shutil.disk_usage(out.parent).free>=8*2**30+8*2**20
    out.mkdir();start=time.monotonic();commands=[]
    env=dict(os.environ,PYTHONPATH=str(ROOT/'python'),PYTHONDONTWRITEBYTECODE='1')
    shutil.copy2(ROOT/'examples/modern_deepep_partial_order/input.json',out/'input.json')

    def cli(name,argv,expected=0):
        remaining=59-(time.monotonic()-start);assert remaining>0
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*argv]
        began=time.monotonic()
        with (out/(name+'.stdout')).open('w') as stdout,(out/(name+'.stderr')).open('w') as stderr:
            result=subprocess.run(command,cwd=out,env=env,stdout=stdout,stderr=stderr,
                                  timeout=min(15,remaining),preexec_fn=limits)
        commands.append(dict(argv=command,cwd=str(out),exitcode=result.returncode,
                             expected_exitcode=expected,wall_seconds=time.monotonic()-began))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        assert result.returncode==expected,name
        return json.loads((out/(name+'.stdout')).read_text()) if not expected else None

    cli('plan',['modern-deepep-plan','--config','input.json','--output','plan'])
    config=json.loads((out/'input.json').read_text());plan=json.loads((out/'plan/plan.json').read_text())
    actions,slots,channels=oracle(plan,config)
    def transcript(name,events):
        (out/(name+'.json')).write_text(json.dumps(dict(schema_version='moe-modern-deepep-actions/1',events=events))+'\n')
    def replay(name,source,expected=0):
        return cli(name,['modern-deepep-replay','--config','input.json','--actions',source+'.json',
                         '--output',name+'.json'],expected)
    transcript('actions',actions);replay('full','actions');replay('repeat','actions')
    assert (out/'full.json').read_bytes()==(out/'repeat.json').read_bytes()
    result=json.loads((out/'full.json').read_text())
    assert result['all_events_fired'] and result['all_application_complete']
    assert result['allocated_slots']==slots and result['fired_events']==len(actions)==1076
    assert [r['event'] for r in result['log']]==actions
    operations={o['id']:o for o in plan['operations']}
    checked=0
    for row in result['log']:
        if row['kind']!='issued':continue
        op=operations[row['operation']]
        if op['id'] not in channels:continue
        (call,owner,channel),index=channels[op['id']]
        assert row['receive_index']==index
        x=config['execution'];sm=channel%x['num_sms'];warp=channel//x['num_sms']
        available=x['call_contexts']
        if available==1:context=0
        elif x['num_sms']<=available:
            per_sm=available//x['num_sms']+int(sm<available%x['num_sms'])
            context=sm+(warp%per_sm)*x['num_sms']
        else:context=(sm*x['combine_warps']+warp)%available
        b=row['binding'];assert (b['sm'],b['rotated_warp'])==(sm,warp)
        assert b['context_index']==context and b['connection']==context%x['connections']
        devices=x['local_devices'][owner]
        assert b['local_device']==devices[b['connection']%len(devices)]
        checked+=1
    transcript('empty',[]);replay('initial','empty')
    initial=json.loads((out/'initial.json').read_text())
    assert initial['fired_events']==0 and len(initial['ready_events'])==4
    prefix=actions[:actions.index('m0.slot.s0.d1.t1')]
    transcript('prefix',prefix);replay('prefix-state','prefix')
    state=json.loads((out/'prefix-state.json').read_text())
    assert 'm0.slot.s0.d1.t1' in state['ready_events']
    assert 'm0.slot.s0.d1.t1' not in state['allocated_slots']
    for name,bad in [('early',['m0.slot.s0.d1.t0']),('duplicate',[actions[0],actions[0]]),
                     ('unknown',['unknown']),('unsatisfied',prefix+['m0.combine.r0.complete'])]:
        transcript(name+'-actions',bad);replay(name,name+'-actions',1)
        assert not (out/(name+'.json')).exists()
    moved=out/'moved';moved.mkdir()
    shutil.copy2(out/'input.json',moved/'input.json');shutil.copy2(out/'actions.json',moved/'actions.json')
    cli('moved',['modern-deepep-replay','--config','moved/input.json','--actions','moved/actions.json',
                 '--output','moved/result.json'])
    assert (moved/'result.json').read_bytes()==(out/'full.json').read_bytes()
    usage=resource.getrusage(resource.RUSAGE_CHILDREN)
    total=sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    assert total<=8*2**20 and usage.ru_maxrss<=2**20 and time.monotonic()-start<60
    paths=['python/moe_ub_cosim/modern_deepep_runtime.py','python/moe_ub_cosim/modern_deepep_plan.py',
           'python/moe_ub_cosim/cli.py','tests/integration/check_modern_runtime_workflow.py',
           'tests/unit/modern_deepep_runtime_test.py','examples/modern_deepep_partial_order/input.json']
    verified=dict(passed=True,public_commands=len(commands),expected_rejections=4,event_count=len(actions),
        allocated_slots=len(slots),independently_checked_combine_bindings=checked,
        repeated_output_byte_identical=True,relocated_output_byte_identical=True,
        fresh_cwd=True,simulated_packets=False,queues_executed=False,hardware_calibrated=False,
        wall_seconds=time.monotonic()-start,peak_child_rss_kib=usage.ru_maxrss,output_bytes=total,
        source_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in paths})
    (out/'verification.json').write_text(json.dumps(verified,indent=2)+'\n');print(json.dumps(verified))


if __name__=='__main__':main()
