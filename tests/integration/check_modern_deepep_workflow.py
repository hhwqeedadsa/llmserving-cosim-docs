"""Fresh public modern operation workflow; source-only and bounded, no CUDA."""
import argparse
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


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve()
    assert not out.exists() and shutil.disk_usage(out.parent).free>=8*2**30+8*2**20
    out.mkdir();started=time.monotonic();commands=[]
    env=dict(os.environ,PYTHONPATH=str(ROOT/'python'),PYTHONDONTWRITEBYTECODE='1')
    shutil.copy2(ROOT/'examples/modern_deepep_partial_order/input.json',out/'input.json')
    def cli(name,argv,expected=0):
        remaining=59-(time.monotonic()-started)
        assert remaining>0
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*argv]
        before=time.monotonic()
        with (out/(name+'.stdout')).open('w') as stdout,(out/(name+'.stderr')).open('w') as stderr:
            result=subprocess.run(command,cwd=out,env=env,stdout=stdout,stderr=stderr,
                                  timeout=min(15,remaining),preexec_fn=limits)
        commands.append(dict(argv=command,cwd=str(out),exitcode=result.returncode,
                             expected_exitcode=expected,wall_seconds=time.monotonic()-before))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        assert result.returncode==expected,name
        return json.loads((out/(name+'.stdout')).read_text()) if expected==0 else None
    generated=cli('generate',['modern-deepep-plan','--config','input.json','--output','plan'])
    checks={}
    for name,held in [('normal',None),('zero_block','m0.dispatch.expert_count.s2.d1.visible'),
                      ('local_completion','m0.dispatch.data.s0.t0.d1.local_complete'),
                      ('target_visibility','m0.dispatch.data.s0.t0.d1.visible'),
                      ('final_signal_tail','m1.combine.exit.s0.d1.local_complete')]:
        argv=['modern-deepep-check','--plan','plan','--output',name+'.json']
        if held:argv+=['--withhold',held]
        checks[name]=cli(name,argv)
    assert checks['normal']['all_completions_reachable']
    assert checks['final_signal_tail']['all_completions_reachable']
    for name in ('zero_block','local_completion','target_visibility'):
        assert all(f'm1.combine.r{r}.complete' in checks[name]['blocked_completions'] for r in range(4))
    cli('unknown',['modern-deepep-check','--plan','plan','--output','unknown.json',
                   '--withhold','missing-event'],expected=1)
    assert not (out/'unknown.json').exists()
    shutil.copytree(out/'plan',out/'relocated')
    portable=cli('relocated',['modern-deepep-check','--plan','relocated','--output','portable.json'])
    assert portable==checks['normal']
    cli('repeat',['modern-deepep-plan','--config','input.json','--output','repeat'])
    for name in ('input.json','plan.json','manifest.json'):
        assert (out/'plan'/name).read_bytes()==(out/'repeat'/name).read_bytes(),name
    path=out/'relocated/plan.json';path.write_bytes(path.read_bytes()+b' ')
    cli('tampered',['modern-deepep-check','--plan','relocated','--output','bad.json'],expected=1)
    assert not (out/'bad.json').exists()
    shutil.copytree(out/'plan',out/'wrong-scope')
    path=out/'wrong-scope/manifest.json';manifest=json.loads(path.read_text());manifest['simulated_packets']=True
    path.write_text(json.dumps(manifest))
    cli('wrong-scope',['modern-deepep-check','--plan','wrong-scope','--output','wrong-scope.json'],expected=1)
    assert not (out/'wrong-scope.json').exists()
    config=json.loads((out/'input.json').read_text());config['execution']['mode']='hybrid'
    (out/'unsupported.json').write_text(json.dumps(config))
    cli('unsupported',['modern-deepep-plan','--config','unsupported.json','--output','unsupported'],expected=1)
    assert not (out/'unsupported').exists()
    usage=resource.getrusage(resource.RUSAGE_CHILDREN)
    total=sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    assert total<=8*2**20 and usage.ru_maxrss<=2**20 and time.monotonic()-started<60
    result=dict(passed=True,public_commands=len(commands),expected_rejections=4,
        event_count=generated['event_count'],operation_count=generated['operation_count'],slot_count=generated['slot_count'],
        three_repeat_outputs_byte_identical=True,portable_check_identical=True,fresh_cwd=True,
        network_execution=False,slots_resolved=False,hardware_calibrated=False,
        wall_seconds=time.monotonic()-started,peak_child_rss_kib=usage.ru_maxrss,output_bytes=total,
        source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
            (ROOT/'python/moe_ub_cosim/modern_deepep_plan.py',ROOT/'examples/modern_deepep_partial_order/input.json')})
    (out/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':
    main()
