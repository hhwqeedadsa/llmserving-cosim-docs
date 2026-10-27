"""Bounded public-CLI platform acceptance; no research timing comparisons."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from fixtures.modern_expert_costs.verify import compare, verify, corruptions
from moe_ub_cosim.host_probe import disk_usage


def load(p):return json.loads(p.read_bytes())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def identity_check(repo,runtime,identity):
    assert all(sha(runtime/name)==digest for name,digest in identity['files'].items())
    assert all(sha(repo/name)==digest for name,digest in identity['sources'].items())


def prepare(repo,stage,runtime,identity_path):
    identity=load(identity_path);identity_check(repo,runtime,identity)
    assert not (stage/'runtime').exists()
    shutil.copytree(runtime,stage/'runtime',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    save(stage/'runtime-identity.json',identity)
    identity_check(repo,stage/'runtime',identity)
    inputs=stage/'inputs';inputs.mkdir();(stage/'empty-cwd').mkdir()
    sample=repo/'examples/modern_deepep_native'
    for name in ('topology.json','rdma.json'):shutil.copyfile(sample/name,inputs/name)
    fixed=load(sample/'input-selective.json')
    skew=load(sample/'input-expert-costs.json')
    equal=deepcopy(skew)
    equal['expert_stage']['durations_ns']=[[fixed['costs']['expert_ns']]*2 for _ in range(2)]
    for name,cfg in (('fixed',fixed),('equal',equal),('skew',skew)):save(inputs/(name+'.json'),cfg)
    for name,change in (
        ('missing-call',lambda c:c['expert_stage'].update(durations_ns=[[1,2]])),
        ('missing-rank',lambda c:c['expert_stage'].update(durations_ns=[[1],[2]])),
        ('boolean',lambda c:c['expert_stage'].update(durations_ns=[[True,1],[2,3]])),
        ('negative',lambda c:c['expert_stage'].update(durations_ns=[[-1,1],[2,3]])),
        ('beyond-horizon',lambda c:c['expert_stage'].update(durations_ns=[[1000001,1],[2,3]])),
        ('two-sources',lambda c:c['costs'].update(expert_ns=1000)),
        ('wrong-version',lambda c:c.update(schema_version='moe-modern-native-input/1')),
        ('missing-source',lambda c:c['expert_stage'].pop('source'))):
        cfg=deepcopy(skew);change(cfg);save(inputs/('invalid-'+name+'.json'),cfg)
    # Compare the unmodified fixed-cost layout/controller with the previous source.
    # This is a declaration-only check, never presented as a network execution.
    baseline={}
    for module in ('modern_native_layout','modern_native'):
        raw=subprocess.check_output(['git','show','8c06c79:python/moe_ub_cosim/'+module+'.py'],cwd=repo)
        baseline[module]=hashlib.sha256(raw).hexdigest()
        (stage/('baseline-'+module+'.py')).write_bytes(raw)
    save(stage/'preparation.json',dict(passed=True,runtime_identity_sha256=sha(identity_path),
        runtime_source=str(runtime),baseline_sources=baseline,new_builds=0,new_downloads=0,
        input_files={p.name:sha(p) for p in inputs.iterdir()}))
    print('Prepared fixed/equal/skew inputs and eight explicit admission failures.')


def baseline_replay(stage):
    namespaces={}
    for module in ('modern_native_layout','modern_native'):
        raw=(stage/('baseline-'+module+'.py')).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==load(stage/'preparation.json')['baseline_sources'][module]
        space={'__name__':'moe_ub_cosim._old_'+module,'__package__':'moe_ub_cosim'}
        exec(compile(raw,'baseline-'+module,'exec'),space);namespaces[module]=space
    namespaces['modern_native']['ModernNativeLayout']=namespaces['modern_native_layout']['ModernNativeLayout']
    old=namespaces['modern_native']['ModernNativeController'](
        load(stage/'topology/topology.json'),load(stage/'inputs/rdma.json'),load(stage/'inputs/fixed.json'),callback_batches=True)
    pending=None;decisions=0
    for line in (stage/'fixed/control.jsonl').read_text().splitlines():
        row=json.loads(line)
        if row['direction']=='native':
            assert pending is None
            pending=old.advance_batches(row['message']);decisions+=1
        else:
            assert row['direction']=='frontend' and pending==row['message']['submissions']
            pending=None
    assert pending is None and old.snapshot()==load(stage/'fixed/modern-execution.json')
    return dict(passed=True,previous_source='8c06c79',actual_callback_replay=True,
        entire_submissions_and_snapshot_identical=True,physical_decisions=decisions,new_networks=0)


def execute(repo,stage,runtime,identity_path):
    assert stage.is_dir() and not (stage/'execution.json').exists()
    ledger=dict(state='started',closed=False,records=[],network_attempts=0)
    env=os.environ|{'PYTHONPATH':str(repo/'python')+':'+str(repo),'PYTHONDONTWRITEBYTECODE':'1'}
    start=time.monotonic()
    def command(label,argv,*,network=False,expected=0,cwd=None):
        if network:
            assert ledger['network_attempts']<3
            ledger['network_attempts']+=1
        probes=[load(p) for p in (stage/'monitor').glob('*/probe.json')]
        assert sum(p['wall_seconds'] for p in probes)+65<600
        assert max(disk_usage(stage).values())+2*2**20<256*2**20
        assert shutil.disk_usage(stage).free>=8*2**30
        row=dict(label=label,command=list(map(str,argv)),cwd=str(cwd or repo),network=network,
            expected_exit=expected,exit_code=None)
        ledger['records'].append(row);save(stage/'execution.json',ledger)
        wrapped=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',stage/'monitor'/label,
            '--rss-bytes',2**30,'--address-bytes',2**31,'--artifact-bytes',256*2**20,
            '--artifact-root',stage,'--wall-seconds',60,'--min-free-bytes',8*2**30,'--',*argv]
        result=subprocess.run(list(map(str,wrapped)),cwd=cwd or repo,env=env,timeout=65)
        row['exit_code']=result.returncode;save(stage/'execution.json',ledger)
        probe=load(stage/'monitor'/label/'probe.json')
        assert result.returncode==expected and probe['status']==('completed' if expected==0 else 'child_failed')
        assert probe['child_exit_code']==expected and not probe['remaining_live_group_pids']
        print(label,'passed',flush=True)
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout']
    module=[sys.executable,'-B','-m','fixtures.modern_expert_costs.accept']
    try:
        command('prepare',[*module,'prepare','--repository',repo,'--stage',stage,'--runtime',runtime,'--identity',identity_path])
        for pattern in ('modern_deepep_runtime_test.py','modern_deepep_windows_test.py','scaleout_transactions_test.py'):
            command(pattern.removesuffix('.py'),[sys.executable,'-B','-m','unittest','discover','-s','tests/unit','-p',pattern])
        command('dependencies',['bash','scripts/check_deps.sh'])
        command('help',[*cli,'modern-deepep-native','--help'])
        command('topology',[*cli,'topology','--config',stage/'inputs/topology.json','--output',stage/'topology'])
        base=[*cli,'modern-deepep-native','--topology',stage/'topology','--protocol',stage/'inputs/rdma.json',
            '--backend',stage/'runtime/run-transaction-session','--callback-batches']
        for path in sorted((stage/'inputs').glob('invalid-*.json')):
            target=stage/('rejected-'+path.stem)
            command(path.stem,[*base,'--config',path,'--output',target],expected=1,cwd=stage/'empty-cwd')
            assert not target.exists(),'invalid input created output or may have started native work'
        for label in ('fixed','equal','skew'):
            command(label,[*base,'--config',stage/'inputs'/(label+'.json'),'--output',stage/label],
                network=True,cwd=stage/'empty-cwd')
            argv=[sys.executable,'-B','-m','fixtures.modern_expert_costs.verify','--run',stage/label,
                '--output',stage/(label+'-verification.json')]
            if label=='skew':argv+=['--corruptions']
            command(label+'-verify',argv,cwd=stage/'empty-cwd')
            if label=='fixed':save(stage/'v1-baseline-replay.json',baseline_replay(stage))
            if label=='equal':save(stage/'equal-comparison.json',compare(stage/'fixed',stage/'equal',matrix_metadata=True))
        identity_check(repo,stage/'runtime',load(stage/'runtime-identity.json'))
        ledger.update(state='completed',closed=True)
    except BaseException as error:
        ledger.update(state='failure_preserved_remaining_networks_closed',closed=True,error=repr(error));raise
    finally:
        ledger['driver_wall_seconds']=time.monotonic()-start;save(stage/'execution.json',ledger)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('prepare','run'))
    for name in ('repository','stage','runtime','identity'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();args=[getattr(a,name).resolve() for name in ('repository','stage','runtime','identity')]
    if a.action=='prepare':prepare(*args)
    else:execute(*args)
