"""Reuse fixed transport/fabric/libraries; recompile only session and its core."""
import argparse
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
from fixtures.native_callback_batches.stage import load,save,sha


def prepare(repo,stage):
    old=Path('/dev/shm/moe-incremental-progress-20261025')
    identity=load(old/'identity.json')
    assert all(sha(Path(p))==h for p,h in identity['unchanged_objects'].items())
    runtime=stage/'runtime';runtime.mkdir(exist_ok=True);(runtime/'lib').mkdir(exist_ok=True);(stage/'objects').mkdir(exist_ok=True)
    for path in (old/'runtime/lib').iterdir():shutil.copyfile(path,runtime/'lib'/path.name)
    for name in ('run-transaction-session',):
        shutil.copyfile(old/'runtime'/name,runtime/name);(runtime/name).chmod(0o755)
    commands={}
    for name in ('remote_transactions','transaction_session'):
        command=deepcopy(load(old/'build-commands.json')[name])
        for flag in ('-o','-MT','-MF'):
            command[command.index(flag)+1]=str(stage/'objects'/(name+'.o'+('.d' if flag=='-MF' else '')))
        command[-1]=str(repo/'backend/rdma'/(name+'.cc'));commands[name]=command
    commands['link']=list(map(str,['c++',stage/'objects/transaction_session.o',stage/'objects/remote_transactions.o',
        *identity['unchanged_objects'],'-Wl,--no-as-needed',*sorted((runtime/'lib').glob('*.so')),'-o',runtime/'transaction-session']))
    # Pure admission/storage tests need only the pinned json include, no ns-3.
    includes=[flag for flag in commands['transaction_session'] if flag.startswith('-I')]
    commands['unit-compile']=['c++','-std=c++17','-O0',*includes,'-I'+str(repo/'backend/rdma'),
        str(repo/'tests/unit/session_callback_batches_test.cc'),'-o',str(stage/'batch-test')]
    save(stage/'build-commands.json',commands)
    save(stage/'identity.json',dict(identity,sources={str(p.relative_to(repo)):sha(p) for p in
        [*sorted((repo/'backend/rdma').glob('*.hh')),repo/'backend/rdma/remote_transactions.cc',repo/'backend/rdma/transaction_session.cc']},
        prior_runtime_sha256=sha(old/'runtime/transaction-session')))
    shutil.copytree(old/'inputs',stage/'inputs');shutil.copytree(old/'original-routes',stage/'original-routes')
    shutil.copyfile(old/'route-provenance.json',stage/'route-provenance.json')
    (stage/'original').mkdir()
    for label in ('default-healthy','wake-fault','four-routes'):
        target=stage/'original'/label;target.mkdir()
        for name in ('initial.json','input.json','protocol.json','topology.json','result.json','modern-execution.json','control.jsonl'):
            shutil.copyfile(old/label/name,target/name)
    save(stage/'inputs.json',{str(p.relative_to(stage)):sha(p) for root in
        (stage/'inputs',stage/'original',stage/'original-routes') for p in root.rglob('*') if p.is_file()})


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--action',required=True);a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve()
    if a.action=='prepare':prepare(repo,stage);return
    identity=load(stage/'identity.json')
    assert all(sha(repo/name)==digest for name,digest in identity['sources'].items())
    assert all(sha(Path(name))==digest for name,digest in identity['unchanged_objects'].items())
    subprocess.run(load(stage/'build-commands.json')[a.action],cwd=repo,check=True,timeout=55)
    if a.action=='link':
        save(stage/'runtime-identity.json',dict(files={str(p.relative_to(stage/'runtime')):sha(p)
            for p in (stage/'runtime').rglob('*') if p.is_file()},**identity))


if __name__=='__main__':main()
