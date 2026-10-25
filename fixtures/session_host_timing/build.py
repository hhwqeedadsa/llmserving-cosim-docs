"""Compile only the session consumer; fixed objects and libraries stay immutable."""
import argparse
from pathlib import Path
import shutil
import subprocess
from fixtures.session_host_timing.stage import load,save,sha


def prepare(repo,stage):
    old=Path('/dev/shm/moe-native-wakeup-20261024')
    identity=load(old/'runtime-identity.json')
    fixed={**identity['unchanged_objects'],**identity['libraries']}
    assert all(sha(Path(path))==value for path,value in fixed.items())
    runtime=stage/'runtime';runtime.mkdir();(runtime/'lib').mkdir();(stage/'objects').mkdir()
    for path,value in identity['libraries'].items():
        target=runtime/'lib'/Path(path).name;shutil.copyfile(path,target);assert sha(target)==value
    shutil.copyfile(old/'runtime/run-transaction-session',runtime/'run-transaction-session')
    (runtime/'run-transaction-session').chmod(0o755)
    compile=load(old/'build-commands.json')['compile']
    for flag in ('-o','-MT','-MF'):
        compile[compile.index(flag)+1]=str(stage/'objects'/('transaction_session.o'+('.d' if flag=='-MF' else '')))
    link=['c++',stage/'objects/transaction_session.o',*identity['unchanged_objects'],'-Wl,--no-as-needed',
        *sorted((runtime/'lib').glob('*.so')),'-o',runtime/'transaction-session']
    pure=['c++','-std=c++20','-O0','-I'+str(repo/'backend/rdma'),
        '-I'+str(repo/'third_party/LLMServingSim/astra-sim/extern/helper'),
        repo/'tests/unit/session_host_timing_disabled_test.cc','-o',stage/'objects/disabled-timing-test']
    save(stage/'build-commands.json',dict(compile=compile,link=list(map(str,link)),pure=list(map(str,pure))))
    sources={str(p.relative_to(repo)):sha(p) for p in (repo/'backend/rdma').glob('*.hh')}
    for name in ('backend/rdma/transaction_session.cc','python/moe_ub_cosim/modern_native.py',
                 'python/moe_ub_cosim/transaction_session.py','python/moe_ub_cosim/host_timing.py'):
        sources[name]=sha(repo/name)
    save(stage/'identity.json',dict(unchanged_objects=identity['unchanged_objects'],
        libraries={str(runtime/'lib'/Path(path).name):value for path,value in identity['libraries'].items()},
        previous_executable_sha256=identity['files']['transaction-session'],sources=sources,
        actual_native_commit=identity['actual_native_commit'],dependencies_rebuilt=False,clean_build=False))
    inputs=stage/'inputs';inputs.mkdir()
    for name in ('synthetic-healthy.json','synthetic-healthy-protocol.json','four-routes.json','four-routes-protocol.json','topology.json'):
        shutil.copyfile(old/'inputs'/name,inputs/name)
    original=stage/'original';original.mkdir()
    for case in ('default-healthy','wake-healthy','four-routes'):
        target=original/case;target.mkdir()
        for name in ('result.json','modern-execution.json','input.json','initial.json','protocol.json','topology.json','control.jsonl'):
            source=old/case/name
            if source.exists():shutil.copyfile(source,target/name)
    shutil.copytree(old/'original-routes',stage/'original-routes')
    shutil.copyfile(old/'route-provenance.json',stage/'route-provenance.json')
    shutil.copyfile(old/'failure-analysis.json',stage/'previous-failure.json')
    save(stage/'inputs.json',dict(files={str(p.relative_to(stage)):sha(p) for root in (inputs,original,stage/'original-routes')
        for p in root.rglob('*') if p.is_file()},route_provenance=sha(stage/'route-provenance.json')))


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--action',choices=('prepare','compile','link','pure'),required=True);a=p.parse_args()
    repo=a.repository.resolve();stage=a.stage.resolve()
    if a.action=='prepare':prepare(repo,stage)
    else:
        identity=load(stage/'identity.json')
        assert all(sha(Path(path))==value for path,value in {**identity['unchanged_objects'],**identity['libraries']}.items())
        assert all(sha(repo/path)==value for path,value in identity['sources'].items())
        subprocess.run(load(stage/'build-commands.json')[a.action],cwd=repo,check=True,timeout=55)
        if a.action=='pure':subprocess.run([stage/'objects/disabled-timing-test'],check=True,timeout=2)
        if a.action=='link':
            save(stage/'runtime-identity.json',dict(**identity,files={str(p.relative_to(stage/'runtime')):sha(p)
                for p in (stage/'runtime').rglob('*') if p.is_file()}))


if __name__=='__main__':main()
