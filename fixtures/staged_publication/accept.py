"""Execute only the eight frozen functional conditions; never a research driver."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from fixtures.staged_publication.verify import load, save, sha, verify, corruptions

ROOT = Path(__file__).resolve().parents[2]


def transcript(path): return [json.loads(line) for line in Path(path).read_text().splitlines()]


def worker(stage, case):
    from moe_ub_cosim.transaction_session import NativeTransactionSession
    config=load(stage/'inputs'/(case+'.json'));out=stage/case;out.mkdir()
    submitted=False;prepared=set();published=set();seen=set();rejections=0
    expected_failure=case in ('tail','source-conflict','native-invalid')
    try:
        with NativeTransactionSession(stage/'topology/topology.json',stage/'inputs/rdma.json',
                dict(config,operations=[]),stage/'runtime/run-transaction-session',out) as s:
            while (event:=s.receive()) is not None:
                seen.update((e['operation'],e['event']) for e in event['events'])
                if not submitted:
                    s.commit(config['operations']);submitted=True;continue
                if case=='native-invalid':
                    assert (0,'submitted') in seen
                    command=dict(type='COMMIT',decision_id=event['decision_id'],now_ns=event['now_ns'],finish=False,
                        submissions=[dict(id=20,kind='local',rank=0,duration_ns=0,after=[])],prepare_operations=[0,999])
                    save(out/'rejected-native-command.json',command)
                    s.child.stdin.write((json.dumps(command)+'\n').encode());s.child.stdin.flush();s.pending=None
                    s.receive();raise AssertionError('native accepted invalid stage command')
                ready=[op['id'] for op in config['operations'] if op['kind']=='remote' and
                       (op['id'],'submitted') in seen and op['id'] not in prepared]
                rings=[op['id'] for op in config['operations'] if op['kind']=='remote' and not op['aggregate_requests'] and
                       (op['id'],'prepared') in seen and op['id'] not in published]
                if case=='credit' and ready and not rejections:
                    before=(deepcopy(s.config),deepcopy(s.pending),s.sequence,s.transcript.tell())
                    for extra in (dict(prepare_operations=[0,999]),dict(publish_operations=[0]),
                                  dict(prepare_operations=[0,0]),dict(prepare_operations=[True])):
                        try:s.commit([],alarms=[dict(id=7,delay_ns=1)],**extra)
                        except ValueError:rejections+=1
                        else:raise AssertionError('Python accepted invalid stage command')
                        assert before==(s.config,s.pending,s.sequence,s.transcript.tell()) and s._alarms=={}
                    save(out/'python-rejections.json',dict(rejected=rejections,pending_preserved=True))
                s.commit([],prepare_operations=ready or None,publish_operations=rings or None,
                         finish=event['drained'])
                prepared.update(ready);published.update(rings)
    except ValueError as error:
        if not expected_failure:raise
        assert (out/'result.json').is_file(),str(error)
        save(out/'expected-failure.json',dict(message=str(error)))
    result=load(out/'result.json')
    assert result['passed'] is (not expected_failure)


def prepare(stage,build_dir):
    build=load(build_dir/'backend.json');provenance=load(build_dir/'provenance.json')
    assert build['passed'] and all(sha(ROOT/n)==h for n,h in provenance['sources'].items())
    runtime=stage/'runtime';runtime.mkdir();(runtime/'lib').mkdir()
    for key,name in (('transaction_probe','transaction-probe'),('transaction_session','transaction-session')):
        shutil.copyfile(build[key],runtime/name);(runtime/name).chmod(0o755)
        wrapper=runtime/('run-'+name)
        wrapper.write_text('#!/usr/bin/env python3\nimport os,sys\nfrom pathlib import Path\n'
            'r=Path(__file__).resolve().parent\nos.environ["LD_LIBRARY_PATH"]=str(r/"lib")\n'
            f'b=str(r/"{name}")\nos.execv(b,[b,*sys.argv[1:]])\n');wrapper.chmod(0o755)
    assert len(build['linked_ns3_libraries'])==8
    for path in build['linked_ns3_libraries']:shutil.copyfile(path,runtime/'lib'/Path(path).name)
    identity={str(p.relative_to(runtime)):sha(p) for p in sorted(runtime.rglob('*')) if p.is_file()}
    save(stage/'runtime-identity.json',identity);shutil.copytree(runtime,stage/'moved-runtime')
    source=stage/'source';source.mkdir()
    for directory in ('python/moe_ub_cosim','fixtures/staged_publication','examples/scaleout_staged_publication'):
        shutil.copytree(ROOT/directory,source/directory,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in provenance['sources']:
        dst=source/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dst)
    for name in ('deps.lock.json','docs/tasks/staged_publication_spec.md'):
        dst=source/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dst)
    save(stage/'source-identity.json',{str(p.relative_to(source)):sha(p) for p in sorted(source.rglob('*')) if p.is_file()})
    inputs=stage/'inputs';inputs.mkdir();(stage/'empty-cwd').mkdir()
    sample=ROOT/'examples/scaleout_staged_publication'
    for name in ('work.json','topology.json','rdma.json'):shutil.copyfile(sample/name,inputs/name)
    main=load(inputs/'work.json');configs={}
    credit=deepcopy(main);credit['operations']=credit['operations'][:2];credit['connections'][0]['queue_depth']=1
    configs['credit']=credit
    tail=deepcopy(main);tail['operations']=tail['operations'][:1];tail['operations'][0]['aggregate_requests']=True
    configs['tail']=tail
    conflict=deepcopy(tail);conflict['operations'].append(dict(id=10,kind='local',rank=0,duration_ns=0,
        copy=dict(source=dict(key=7,address=4160,bytes=8),target=dict(key=7,address=4096)),
        after=[dict(operation=0,event='prepared')]))
    configs['source-conflict']=conflict
    invalid=deepcopy(main);invalid['operations']=invalid['operations'][:1];configs['native-invalid']=invalid
    explicit=deepcopy(main);explicit['operations']=explicit['operations'][:2];explicit['submission_model']['mode']='explicit_prefix/1'
    configs['explicit']=explicit
    legacy=deepcopy(explicit);legacy.pop('submission_model')
    for op in legacy['operations']:op.pop('aggregate_requests')
    configs['legacy']=legacy
    for name,value in configs.items():save(inputs/(name+'.json'),value)
    save(stage/'input-identity.json',{p.name:sha(p) for p in sorted(inputs.iterdir())})


def execute(stage,resume=False):
    commands=load(stage/'commands.json') if resume else []
    if resume:
        assert [r['name'] for r in commands]==['topology','01-main'] and all(r['exit_code']==0 for r in commands)
        assert load(stage/'main/result.json')['passed'] and not (stage/'credit').exists()
        assert all(sha(stage/'inputs'/n)==h for n,h in load(stage/'input-identity.json').items())
    env=dict(os.environ,PYTHONPATH=str(ROOT/'python')+':'+str(ROOT),PYTHONDONTWRITEBYTECODE='1')
    def invoke(name,args,*,custom_env=None):
        args=list(map(str,args));cwd=stage/'empty-cwd'
        with (stage/(name+'.stdout.log')).open('w') as out,(stage/(name+'.stderr.log')).open('w') as err:
            result=subprocess.run(args,cwd=cwd,env=custom_env or env,stdout=out,stderr=err,timeout=55)
        commands.append(dict(name=name,command=args,cwd=str(cwd),exit_code=result.returncode))
        save(stage/'commands.json',commands);assert result.returncode==0,name
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout']
    if not resume:invoke('topology',cli+['topology','--config',stage/'inputs/topology.json','--output',stage/'topology'])
    # Admission rejection must create no output and launch no marker backend.
    for name in ('rdma-transaction-probe','rdma-transaction-session'):
        from moe_ub_cosim.scaleout_transactions import probe
        from moe_ub_cosim.transaction_session import run
        try:(probe if name.endswith('probe') else run)(stage/'topology',stage/'inputs/rdma.json',stage/'inputs/work.json',
            stage/'runtime/run-transaction-session',stage/(name+'-must-not-exist'))
        except ValueError as error:assert 'controller using NativeTransactionSession' in str(error)
        else:raise AssertionError('static frontend accepted staged mode')
        assert not (stage/(name+'-must-not-exist')).exists()
    sample=ROOT/'examples/scaleout_staged_publication/run.py'
    def demo(source,backend,out):return [sys.executable,'-B',source,'--topology',stage/'topology',
        '--protocol',stage/'inputs/rdma.json','--config',stage/'inputs/work.json','--backend',backend,'--output',out]
    if not resume:invoke('01-main',demo(sample,stage/'runtime/run-transaction-session',stage/'main'))
    checks={}
    def check(case,config):
        value=verify(load(stage/case/'result.json'),load(stage/'inputs'/config),load(stage/'topology/topology.json'),
            load(stage/'inputs/rdma.json'),transcript(stage/case/'control.jsonl'))
        checks[case]=value;save(stage/'verification-progress.json',checks)
    check('main','work.json')
    main=load(stage/'main/result.json');rows={o['id']:o for o in main['transactions']['operations']}
    assert rows[1]['descriptor_ready_ns']==0<rows[1]['prepared_ns']==rows[4]['completed_ns']
    assert rows[4]['completed_ns']==rows[0]['descriptor_ready_ns']==rows[0]['prepared_ns']
    assert checks['main']['no_op_rings']==2
    for index,case in enumerate(('credit','tail','source-conflict','native-invalid'),2):
        invoke(f'{index:02d}-{case}',[sys.executable,'-B','-m','fixtures.staged_publication.accept','--stage',stage,'--worker',case])
        result=load(stage/case/'result.json');ops=result['transactions']['operations']
        if case=='credit':
            check(case,case+'.json');a,b=ops
            assert b['descriptor_ready_ns']==b['prepared_ns']==a['completed_ns']>0
        elif case=='tail':
            assert result['finish_ns']==load(stage/'inputs/rdma.json')['limits']['max_sim_ns']
            assert len(ops)==1 and ops[0]['prepared_ns']==0 and ops[0]['published_ns']==-1
            assert not result['transactions']['transport']['source_packets']
        elif case=='source-conflict':
            assert 'execution_error' in result and len(result['transactions']['active_source_leases'])==1
            assert not result['transactions']['transport']['source_packets']
        else:
            assert 'provider stage identity' in result['execution_error']
            assert len(result['session']['registrations'])==len(ops)==1 and ops[0]['descriptor_ready_ns']==-1
            assert not result['transactions']['transport']['source_packets']
        checks[case]=checks.get(case,dict(passed=True,expected_failure=True));save(stage/'verification-progress.json',checks)
    for index,case in ((6,'legacy'),(7,'explicit')):
        invoke(f'{index:02d}-{case}',cli+['rdma-transaction-probe','--topology',stage/'topology','--protocol',stage/'inputs/rdma.json',
            '--config',stage/'inputs'/(case+'.json'),'--backend',stage/'runtime/run-transaction-probe','--output',stage/case])
    old=load(stage/'legacy/result.json');explicit=load(stage/'explicit/result.json');normalized=deepcopy(explicit)
    normalized['transactions'].pop('submission')
    for row in normalized['transactions']['operations']:row.pop('prepared_ns')
    assert normalized==old
    moved_env=dict(env,PYTHONPATH=str(stage/'source/python')+':'+str(stage/'source'))
    invoke('08-moved',demo(stage/'source/examples/scaleout_staged_publication/run.py',stage/'moved-runtime/run-transaction-session',stage/'moved'),custom_env=moved_env)
    def normalize(result):
        result=deepcopy(result);paths=result['loaded_libraries']
        for path in paths:
            if Path(path).name.startswith('libns3.'):
                assert sha(path)==load(stage/'runtime-identity.json')['lib/'+Path(path).name]
        result['loaded_libraries']=sorted(Path(p).name if Path(p).name.startswith('libns3.') else p for p in paths)
        return result
    assert normalize(main)==normalize(load(stage/'moved/result.json'))
    check('moved','work.json')
    rejected=corruptions(main,load(stage/'inputs/work.json'),load(stage/'topology/topology.json'),
        load(stage/'inputs/rdma.json'),transcript(stage/'main/control.jsonl'))
    assert all(sha(stage/'runtime'/n)==sha(stage/'moved-runtime'/n)==h for n,h in load(stage/'runtime-identity.json').items())
    assert all(sha(stage/'inputs'/n)==h for n,h in load(stage/'input-identity.json').items())
    save(stage/'verification.json',dict(passed=True,network_conditions=8,checks=checks,legacy_equal=True,
        moved_full_result_equal_except_verified_library_paths=True,corruptions_rejected=rejected,static_frontends_rejected=2))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--worker',choices=('credit','tail','source-conflict','native-invalid'))
    p.add_argument('--prepare',action='store_true');p.add_argument('--build-dir',type=Path)
    p.add_argument('--continue-after-checker-fix',action='store_true',help='Only after successful main execution and an offline checker correction; no network rerun')
    a=p.parse_args();stage=a.stage.resolve()
    if a.worker:worker(stage,a.worker)
    elif a.prepare:prepare(stage,(a.build_dir or stage/'build').resolve())
    else:execute(stage,a.continue_after_checker_fix)


if __name__=='__main__':main()
