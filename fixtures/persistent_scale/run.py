"""Bounded small/default/128/1024 progression; no retry or budget escalation."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def load(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage','old_backend'):p.add_argument('--'+name.replace('_','-'),type=Path,required=True)
    p.add_argument('--continue-after-small-audit',action='store_true')
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();commands=[]
    if a.continue_after_small_audit:
        commands=load(s/'commands.json')
        assert commands[-1]['label']=='small-audit' and commands[-1]['exit_code']!=0 and sum(x.get('network',False) for x in commands)==3
    else:
        (s/'network').mkdir();(s/'topology').mkdir()
    def run(label,cmd,network=False):
        before=sum(x.get('network',False) for x in commands);assert not network or before<5
        argv=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(s/(label+'-monitor')),
            '--wall-seconds','60','--rss-bytes',str(4*2**30),'--address-bytes',str(8*2**30),'--artifact-bytes',str(64*2**20),
            '--artifact-root',str(s/'inputs'),'--artifact-root',str(s/'topology'),'--artifact-root',str(s/'network'),'--min-free-bytes',str(8*2**30),'--',*map(str,cmd)]
        done=subprocess.run(argv,cwd=repo,timeout=65)
        commands.append(dict(label=label,command=argv,cwd=str(repo),exit_code=done.returncode,network=network));save(s/'commands.json',commands)
        assert done.returncode==0,label
    if not a.continue_after_small_audit:
        run('prepare',[sys.executable,'-B',repo/'fixtures/persistent_scale/prepare.py','--repository',repo,'--stage',s])
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout']
    if not a.continue_after_small_audit:
        for n in (8,128,1024):run('topology-'+str(n),[*cli,'topology','--config',s/'inputs'/(str(n)+'-topology.json'),'--output',s/'topology'/str(n)])
    else:
        run('small-audit-corrected',[sys.executable,'-B',repo/'fixtures/persistent_scale/audit.py','--result',s/'network/small/result.json',
            '--work',s/'inputs/8-work.json','--output',s/'network/small/audit.json'])
    for label,n,work,proto,backend in [('default-old',8,'legacy','legacy',a.old_backend),('default-new',8,'legacy','legacy',s/'bin/stream-probe'),
            ('small',8,'8','8',s/'bin/stream-probe'),('medium',128,'128','128',s/'bin/stream-probe'),('target',1024,'1024','1024',s/'bin/stream-probe')]:
        if a.continue_after_small_audit and label in ('default-old','default-new','small'):continue
        run(label,[*cli,'rdma-stream-probe','--topology',s/'topology'/str(n),'--protocol',s/'inputs'/(proto+'-protocol.json'),
            '--config',s/'inputs'/(work+'-work.json'),'--backend',backend,'--output',s/'network'/label],True)
        r=load(s/'network'/label/'result.json');assert r['passed'] and r['drained']
        # Check this completed case offline before proceeding to a larger size.
        if label not in ('default-old','default-new'):
            run(label+'-audit',[sys.executable,'-B',repo/'fixtures/persistent_scale/audit.py','--result',s/'network'/label/'result.json',
                '--work',s/'inputs'/(work+'-work.json'),'--output',s/'network'/label/'audit.json'])
    save(s/'run.json',dict(passed=True,network_executions=5,scope='capacity program, not complete MoE inference'))


if __name__=='__main__':main()
