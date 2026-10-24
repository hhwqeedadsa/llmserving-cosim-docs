"""Prepare one changed consumer; never build or modify a dependency."""
import argparse
from pathlib import Path
import shutil
import subprocess
from fixtures.native_wakeup.stage import load, save, sha


def prepare(repo, stage):
    old=Path('/dev/shm/moe-self-peer-service-20261024')
    identity=load(old/'build.json');frozen=load(old/'link-current-objects.json')
    runtime=stage/'runtime';runtime.mkdir();(runtime/'lib').mkdir();(stage/'objects').mkdir()
    inputs={str(old/'objects/remote_transactions.o'):frozen['objects']['remote_transactions.o'],**identity['unchanged_inputs']}
    assert all(sha(Path(path))==value for path,value in inputs.items())
    for name,value in identity['sources'].items():
        if name!='backend/rdma/transaction_session.cc':assert sha(repo/name)==value,name
    for path,value in identity['libraries'].items():
        source=old/'runtime/lib'/Path(path).name;assert sha(source)==value
        shutil.copyfile(source,runtime/'lib'/source.name)
    launcher=old/'runtime/run-transaction-session';assert sha(launcher)==identity['built_runtime'][launcher.name]
    shutil.copyfile(launcher,runtime/launcher.name);(runtime/launcher.name).chmod(0o755)
    records=load(old/'commands.json')
    compile=next(r['command'].copy() for r in records if r['label']=='compile-transaction_session')
    for flag in ('-o','-MT','-MF'):
        compile[compile.index(flag)+1]=str(stage/'objects'/('transaction_session.o'+('.d' if flag=='-MF' else '')))
    link=['c++',stage/'objects/transaction_session.o',*[Path(path) for path in inputs],'-Wl,--no-as-needed',
          *sorted((runtime/'lib').glob('*.so')),'-o',runtime/'transaction-session']
    pure=['c++','-std=c++20','-O0','-I'+str(repo/'backend/rdma'),
          '-I'+str(repo/'third_party/LLMServingSim/astra-sim/extern/helper'),
          str(repo/'tests/unit/session_wakeups_test.cc'),'-o',str(stage/'objects/session-wakeups-test')]
    save(stage/'build-commands.json',dict(compile=compile,link=list(map(str,link)),pure=pure))
    save(stage/'build-inputs.json',dict(unchanged_objects=inputs,libraries={str(runtime/'lib'/Path(path).name):value for path,value in identity['libraries'].items()},
        previous_executable_sha256=identity['built_runtime']['transaction-session'],
        actual_native_commit='68a07f7d5e53bbe97d275af41d4964ad01b864f3',
        sources={name:sha(repo/name) for name in ('backend/rdma/transaction_session.cc','backend/rdma/session_wakeups.hh')},
        dependencies_rebuilt=False,clean_build=False))


def main():
    parser=argparse.ArgumentParser()
    for name in ('repository','stage'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--action',choices=('prepare','compile','link','pure'),required=True)
    args=parser.parse_args();repo=args.repository.resolve();stage=args.stage.resolve()
    if args.action=='prepare':prepare(repo,stage)
    else:
        before=load(stage/'build-inputs.json')
        assert all(sha(Path(path))==value for path,value in {**before['unchanged_objects'],**before['libraries']}.items())
        assert all(sha(repo/path)==value for path,value in before['sources'].items())
        subprocess.run(load(stage/'build-commands.json')[args.action],cwd=repo,check=True,timeout=55)
        if args.action=='link':
            save(stage/'runtime-identity.json',dict(**before,files={str(path.relative_to(stage/'runtime')):sha(path) for path in (stage/'runtime').rglob('*') if path.is_file()}))


if __name__=='__main__':main()
