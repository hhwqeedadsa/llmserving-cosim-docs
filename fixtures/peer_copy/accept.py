"""One fresh runtime, four frozen public networks; stop at any failed condition."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from fixtures.peer_copy.verify import load, save, sha, verify, corruptions
from moe_ub_cosim.host_probe import disk_usage


def runtime_check(root, identity):
    assert all(sha(root/name) == digest for name, digest in identity['files'].items())


def source_check(root):
    assert all(sha(root/name) == digest for name,digest in load(root/'source-manifest.json').items())


def library_check(result, runtime, identity):
    paths = [Path(p) for p in result['loaded_libraries'] if Path(p).name.startswith('libns3.')]
    expected = {Path(n).name: h for n,h in identity['files'].items() if n.startswith('lib/')}
    assert len(paths) == len(expected) == 8 and {p.name for p in paths} == set(expected)
    assert all(p.parent == runtime/'lib' and sha(p) == expected[p.name] for p in paths)


def normalize_libraries(result):
    result = deepcopy(result)
    result['loaded_libraries'] = sorted(Path(p).name if Path(p).name.startswith('libns3.') else p
                                        for p in result['loaded_libraries'])
    return result


def prepare(repo, stage):
    backend = load(stage/'build/backend.json'); provenance = load(stage/'build/provenance.json')
    assert backend['passed'] and backend['compiler_options'] == load(stage/'build/compiler-options.json')
    assert all(sha(repo/n) == h for n,h in provenance['sources'].items())
    runtime = stage/'runtime'; runtime.mkdir(); (runtime/'lib').mkdir(); files = {}
    for key, name in (('transaction_probe','transaction-probe'),('transaction_session','transaction-session')):
        shutil.copyfile(backend[key], runtime/name); (runtime/name).chmod(0o755)
        wrapper = runtime/('run-'+name)
        wrapper.write_text('#!/usr/bin/env python3\nimport os, sys\nfrom pathlib import Path\n'
            'root = Path(__file__).resolve().parent\nos.environ["LD_LIBRARY_PATH"] = str(root/"lib")\n'
            f'binary = str(root/"{name}")\nos.execv(binary, [binary, *sys.argv[1:]])\n')
        wrapper.chmod(0o755)
    assert len(backend['linked_ns3_libraries']) == 8
    for path in backend['linked_ns3_libraries']: shutil.copyfile(path, runtime/'lib'/Path(path).name)
    files = {str(p.relative_to(runtime)):sha(p) for p in sorted(runtime.rglob('*')) if p.is_file()}
    identity = dict(files=files, sources=provenance['sources'], compiler_options=backend['compiler_options'])
    save(stage/'runtime-identity.json', identity); runtime_check(runtime,identity)
    shutil.copytree(runtime, stage/'migrated-runtime'); runtime_check(stage/'migrated-runtime',identity)
    source = stage/'source'; source.mkdir()
    paths = {p for p in (repo/'python/moe_ub_cosim').rglob('*.py')}
    paths.update((repo/'fixtures/peer_copy').glob('*.py'))
    paths.update(repo/n for n in provenance['sources'])
    paths.update(repo/n for n in ('deps.lock.json','AGENTS.md','docs/development.md','docs/tasks/peer_copy_spec.md','docs/user_guide.md','docs/architecture.md','docs/record_storage.md','backend/README.md'))
    paths.update((repo/'examples/scaleout_peer_copy').glob('*'))
    for p in sorted(paths):
        if not p.is_file(): continue
        target = source/p.relative_to(repo); target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(p,target)
    save(source/'source-manifest.json',{str(p.relative_to(source)):sha(p) for p in sorted(source.rglob('*')) if p.is_file()})
    source_check(source)
    inputs = stage/'inputs'; inputs.mkdir(); (stage/'empty-cwd').mkdir()
    for p in (repo/'examples/scaleout_peer_copy').glob('*.json'): shutil.copyfile(p,inputs/p.name)
    fixed = load(inputs/'work.json')
    invalid = []
    for name, change in (
        ('cross-server',lambda c:c['operations'][0]['copy']['target'].update(owner=4)),
        ('same-gpu',lambda c:c['operations'][0]['copy']['target'].update(owner=0)),
        ('fixed-delay',lambda c:c['operations'][0].update(duration_ns=1)),
        ('missing-model',lambda c:c.pop('peer_copy_model')),
        ('source-owner',lambda c:c['operations'][0]['copy']['source'].update(owner=1)),
        ('out-of-bounds',lambda c:c['operations'][0]['copy']['target'].update(address=8192)),
        ('unknown-gpu',lambda c:c['operations'][0]['copy']['target'].update(owner=31))):
        value=deepcopy(fixed);change(value); path=inputs/('invalid-'+name+'.json');save(path,value);invalid.append(path)
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout']
    commands=[]
    def add(label,args,network=False,expected=0,migrated=False):
        commands.append(dict(label=label,argv=list(map(str,args)),network=network,expected=expected,migrated=migrated))
    for key in ('rdma-transaction-probe','rdma-transaction-session'): add('help-'+key,[*cli,key,'--help'])
    for key,name in (('topology','topology.json'),('without-peer-topology','without-peer-topology.json')):
        add(key,[*cli,'topology','--config',inputs/name,'--output',stage/key])
    def args(kind,config,output,*,topology='topology',migrated=False):
        native='transaction-session' if kind.endswith('session') else 'transaction-probe'
        return [*cli,kind,'--topology',stage/topology,'--protocol',inputs/'rdma.json',
                '--config',config,'--backend',stage/('migrated-runtime' if migrated else 'runtime')/('run-'+native),'--output',stage/output]
    for path in invalid: add(path.stem,args('rdma-transaction-probe',path,'rejected-'+path.stem),expected=1)
    add('peer',args('rdma-transaction-probe',inputs/'work.json','peer'),network=True)
    add('session',args('rdma-transaction-session',inputs/'work.json','session'),network=True)
    add('without-peer',args('rdma-transaction-probe',inputs/'without-peer.json','without-peer',topology='without-peer-topology'),network=True)
    add('migrated',args('rdma-transaction-probe',inputs/'work.json','migrated',migrated=True),network=True,migrated=True)
    save(stage/'frozen-commands.json',commands)
    save(stage/'preparation.json',dict(passed=True,inputs={p.name:sha(p) for p in sorted(inputs.iterdir())},
        runtime_identity_sha256=sha(stage/'runtime-identity.json'),command_ledger_sha256=sha(stage/'frozen-commands.json'),
        network_limit=4,builds=1,downloads=0,gpu_execution=False,source_files=len(paths)))
    print('Frozen two topology generations, seven admission rejections and four networks.')


def budget(stage, reserve=65):
    probes=[load(p) for p in stage.rglob('probe.json') if '/monitor/' in str(p) or '/logs/' in str(p) or '/execution/' in str(p)]
    # Including nested receipts is deliberately conservative.
    assert sum(p['wall_seconds'] for p in probes)+reserve+10 < 2400
    assert max(disk_usage(stage).values())+2*2**20 < 2**31
    assert shutil.disk_usage(stage).free >= 8*2**30


def execute(repo,stage):
    prepared=load(stage/'preparation.json');identity=load(stage/'runtime-identity.json')
    assert sha(stage/'frozen-commands.json') == prepared['command_ledger_sha256']
    assert all(sha(stage/'inputs'/n)==h for n,h in prepared['inputs'].items())
    assert not (stage/'execution.json').exists()
    ledger=dict(state='started',closed=False,records=[],network_attempts=0);save(stage/'execution.json',ledger)
    try:
        for command in load(stage/'frozen-commands.json'):
            budget(stage);source_check(stage/'source')
            label=command['label'];migrated=command['migrated'];source=stage/'source' if migrated else repo
            runtime=stage/('migrated-runtime' if migrated else 'runtime');runtime_check(runtime,identity)
            if command['network']:
                assert ledger['network_attempts'] < 4;ledger['network_attempts'] += 1
            row=dict(command,exit_code=None,cwd=str(stage/'empty-cwd'));ledger['records'].append(row);save(stage/'execution.json',ledger)
            env=os.environ|{'PYTHONPATH':str(source/'python')+':'+str(source),'PYTHONDONTWRITEBYTECODE':'1'}
            argv=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',stage/'monitor'/label,
                '--rss-bytes',2**30,'--address-bytes',2**31,'--artifact-bytes',2**31,'--artifact-root',stage,
                '--wall-seconds',60,'--min-free-bytes',8*2**30,'--',*command['argv']]
            run=subprocess.run(list(map(str,argv)),cwd=stage/'empty-cwd',env=env,timeout=65)
            row['exit_code']=run.returncode;save(stage/'execution.json',ledger)
            receipt=load(stage/'monitor'/label/'probe.json')
            assert run.returncode == receipt['child_exit_code'] == command['expected']
            assert not receipt['remaining_live_group_pids']
            if command['expected']:
                assert not (stage/('rejected-'+label)).exists()
            if command['network']:
                result=load(stage/label/'result.json');library_check(result,runtime,identity)
                config=load(stage/'inputs'/('without-peer.json' if label=='without-peer' else 'work.json'))
                topology=load(stage/('without-peer-topology' if label=='without-peer' else 'topology')/'topology.json')
                report=verify(result,config,topology,load(stage/'inputs/rdma.json'))
                if label=='peer':report['corruptions_rejected']=corruptions(result,config,topology,load(stage/'inputs/rdma.json'))
                save(stage/(label+'-verification.json'),report)
                if label=='session':
                    first=load(stage/'peer/result.json')
                    assert all(first[k] == result[k] for k in ('local_work','transactions','messages','intra_host_links','finish_ns'))
                    row['probe_session_core_equal']=True
                if label=='migrated':
                    assert normalize_libraries(load(stage/'peer/result.json')) == normalize_libraries(result)
                    row['full_result_equal_except_verified_library_paths_and_order']=True
            save(stage/'execution.json',ledger);print(label,'passed',flush=True)
        ledger.update(state='completed',closed=True)
    except BaseException as error:
        ledger.update(state='failure_preserved_remaining_networks_closed',closed=True,error=repr(error));raise
    finally:save(stage/'execution.json',ledger)


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('prepare','run'))
    p.add_argument('--repository',type=Path,required=True);p.add_argument('--stage',type=Path,required=True);a=p.parse_args()
    (prepare if a.action=='prepare' else execute)(a.repository.resolve(),a.stage.resolve())
