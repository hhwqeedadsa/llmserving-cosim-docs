"""Public window replay with independent addresses and state accounting.

All callbacks here are functional TEST declarations, never network observations.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time

from check_modern_runtime_workflow import oracle, limits

ROOT=Path(__file__).resolve().parents[2]


def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();out=a.output.resolve();start=time.monotonic()
    assert not out.exists() and shutil.disk_usage(out.parent).free>=8*2**30+16*2**20
    out.mkdir();commands=[]
    env=dict(os.environ,PYTHONPATH=str(ROOT/'python'),PYTHONDONTWRITEBYTECODE='1')
    shutil.copy2(ROOT/'examples/modern_deepep_partial_order/input.json',out/'input.json')
    def cli(name,args,expected=0):
        remaining=59-(time.monotonic()-start);assert remaining>0
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*args];began=time.monotonic()
        with (out/(name+'.stdout')).open('w') as stdout,(out/(name+'.stderr')).open('w') as stderr:
            result=subprocess.run(command,cwd=out,env=env,stdout=stdout,stderr=stderr,
                timeout=min(15,remaining),preexec_fn=limits)
        commands.append(dict(argv=command,cwd=str(out),exitcode=result.returncode,
                             expected_exitcode=expected,wall_seconds=time.monotonic()-began))
        save(out/'commands.json',commands);assert result.returncode==expected,name
    def replay(name,source,expected=0):
        cli(name,['modern-deepep-window-replay','--config','input.json','--actions',source+'.json',
                  '--output',name+'.json'],expected)
    def actions(name,events):save(out/(name+'.json'),dict(schema_version='moe-modern-deepep-actions/1',events=events))
    cli('plan',['modern-deepep-plan','--config','input.json','--output','plan'])
    config=json.loads((out/'input.json').read_text());plan=json.loads((out/'plan/plan.json').read_text())
    sequence,slots,_=oracle(plan,config);actions('actions',sequence)
    replay('full','actions');replay('repeat','actions')
    assert (out/'full.json').read_bytes()==(out/'repeat.json').read_bytes()
    result=json.loads((out/'full.json').read_text());memory=result['memory_state']
    assert result['all_events_fired'] and result['all_application_complete'] and result['fired_events']==1076
    assert result['allocated_slots']==slots and memory['active_source_leases']=={}
    assert memory['nonzero_count_elements']==0 and len(memory['consumed_counts'])==8
    assert len(memory['signals'])==16 and all(s['value']==s['shadow']==8 for s in memory['signals'])
    assert result['windows']['main_window']['ep_buffer_base']==16*2**20
    ops={o['id']:o for o in plan['operations']};mapped={r['operation']:r for r in result['memory_log']}
    assert set(mapped)==set(ops) and len(mapped)==224
    counts=Counter();leases={};peak=0
    for event in sequence:
        row=next((o for o in ops.values() if o['events']['issued']==event),None)
        if row is not None:
            location=mapped[row['id']];target,source=location['target'],location['source']
            s,d=row['src'],row['dst'];assert target['owner']==d
            assert target['bytes']==row['operand_bytes']
            counts[location['path']]+=1
            if row['kind']=='barrier_signal':
                assert target['domain']=='provider_indexed_signals' and target['slot']==s
                assert target['context_index']==0 and 'byte_offset' not in target and source is None
            elif row['kind']=='rank_count':
                assert target['byte_offset']==49216+8*s and source is None
            elif row['kind']=='expert_count_block':
                assert target['byte_offset']==49216+8*(4+2*s)
                assert source['byte_offset']==24640+8*(4+2*d) and source['owner']==s
            elif row['phase']=='dispatch':
                expected=16*2**20+(4*s+slots[row['slot_variable']])*576
                assert target['byte_offset']==expected
                if s==d:assert source['domain']=='local_tma'
                else:assert source['byte_offset']==16*2**20+(16+row['token'])*576
            else:
                assert target['byte_offset']==16*2**20+(4*row['topk_positions'][0]+row['token'])*576
                if s==d:assert source['domain']=='local_reduction'
                else:assert source['byte_offset']==16*2**20+(8+4*d+row['token'])*576
            if row['path']!='local' and source is not None and source['domain']=='main_window':
                leases[row['id']]=source;peak=max(peak,len(leases))
        for op in ops.values():
            if op['events']['local_complete']==event:leases.pop(op['id'],None)
    assert not leases and peak==memory['peak_source_leases']
    assert counts==Counter(r['path'] for r in mapped.values()) and counts['gin_self']==40
    # Withhold one self bulk source completion. Its target is still consumed,
    # while its source read remains protected. Independent DAG descendants.
    held='m0.dispatch.expert_count.s2.d2.local_complete';blocked={held}
    for event in plan['events']:
        if set(event['deps']) & blocked:blocked.add(event['id'])
    actions('held-actions',[e for e in sequence if e not in blocked]);replay('held','held-actions')
    partial=json.loads((out/'held.json').read_text())
    assert held[:-15] in partial['memory_state']['active_source_leases']
    assert partial['memory_state']['nonzero_count_elements']==0
    assert not partial['all_application_complete']
    readers=['m0.dispatch.data.s0.t1.d1','m0.dispatch.data.s0.t1.d2']
    blocked={op+'.local_complete' for op in readers}
    for event in plan['events']:
        if set(event['deps']) & blocked:blocked.add(event['id'])
    actions('shared-source-actions',[e for e in sequence if e not in blocked]);replay('shared-source','shared-source-actions')
    shared=json.loads((out/'shared-source.json').read_text())['memory_state']['active_source_leases']
    assert set(shared)==set(readers) and shared[readers[0]]==shared[readers[1]]
    actions('empty',[]);replay('initial','empty')
    initial=json.loads((out/'initial.json').read_text())
    assert initial['fired_events']==0 and not initial['memory_state']['active_source_leases']
    for name,bad in [('early',['m0.dispatch.data.s0.t0.d1.visible']),('unknown',['no-event']),
                     ('duplicate',[sequence[0],sequence[0]])]:
        actions(name+'-actions',bad);replay(name,name+'-actions',1)
        assert not (out/(name+'.json')).exists()
    save(out/'bad-schema.json',dict(schema_version='bad',events=[]));replay('bad-schema-result','bad-schema',1)
    cli('overwrite',['modern-deepep-window-replay','--config','input.json','--actions','actions.json',
                     '--output','full.json'],1)
    moved=out/'moved';moved.mkdir()
    for name in ('input.json','actions.json'):shutil.copy2(out/name,moved/name)
    cli('moved',['modern-deepep-window-replay','--config','moved/input.json','--actions','moved/actions.json',
                 '--output','moved/result.json'])
    assert (out/'full.json').read_bytes()==(moved/'result.json').read_bytes()
    total=sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    usage=resource.getrusage(resource.RUSAGE_CHILDREN)
    assert total<=16*2**20 and usage.ru_maxrss<=2**20 and time.monotonic()-start<60
    paths=['python/moe_ub_cosim/modern_deepep_windows.py','python/moe_ub_cosim/cli.py',
           'tests/unit/modern_deepep_windows_test.py','tests/integration/check_modern_windows_workflow.py']
    verification=dict(passed=True,public_commands=len(commands),expected_rejections=5,
        events=1076,independently_mapped_operations=len(mapped),paths=dict(counts),
        independently_counted_peak_source_leases=peak,self_source_completion_withheld=True,
        concurrent_puts_sharing_one_source_independently_verified=2,
        consumed_count_generations=8,signal_value_and_shadow=8,full_outputs_byte_identical=True,
        relocated_output_byte_identical=True,fresh_cwd=True,output_bytes=total,
        peak_child_rss_kib=usage.ru_maxrss,wall_seconds=time.monotonic()-start,
        source_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in paths},
        network_executed=False,hardware_calibrated=False,transport_payload_verified=False)
    save(out/'verification.json',verification);print(json.dumps(verification))


if __name__=='__main__':main()
