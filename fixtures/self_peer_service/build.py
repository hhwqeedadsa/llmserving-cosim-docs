"""Compile three changed consumers, link two programs using fixed eight libs."""
import argparse
from pathlib import Path
import shutil
from fixtures.self_peer_service.stage import load,save,sha,run


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage'):p.add_argument('--'+k,type=Path,required=True)
    p.add_argument('--resume-validation-fix',action='store_true')
    p.add_argument('--link-current-objects',action='store_true')
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();old=Path('/dev/shm/moe-application-windows-20261024')
    runtime=s/'runtime';objects=s/'objects'
    resume=a.resume_validation_fix or a.link_current_objects
    if not resume:
        for directory in (runtime,objects,runtime/'lib'):directory.mkdir(exist_ok=False)
    lib_source=Path('/dev/shm/moe-context-transactions-20261024/runtime/lib');libs=sorted(lib_source.glob('*.so'));assert len(libs)==8
    identity=dict(libraries={str(p):sha(p) for p in libs},sources={str(p.relative_to(repo)):sha(p) for p in (repo/'backend/rdma').iterdir() if p.suffix in ('.cc','.hh')},unchanged_inputs={})
    if not resume:
        for src in libs:shutil.copyfile(src,runtime/'lib'/src.name)
    elif a.resume_validation_fix:
        frozen=load(s/'resume-validation-fix.json')
        assert sha(objects/'remote_transactions.o')==frozen['successful_core_object_sha256']
        assert sha(repo/'backend/rdma/remote_transactions.cc')==frozen['core_source_sha256']
        assert sha(repo/'backend/rdma/remote_transactions.hh')==frozen['core_header_sha256']
    prior=load(old/'build-records.json')
    if a.link_current_objects:
        frozen=load(s/'link-current-objects.json')
        assert len(frozen['objects'])==3 and all(sha(objects/n)==h for n,h in frozen['objects'].items())
        assert all(sha(repo/n)==h for n,h in frozen['sources'].items())
    for name in ('remote_transactions','transaction_probe','transaction_session'):
        if a.link_current_objects:continue
        if a.resume_validation_fix and name=='remote_transactions':continue
        cmd=next(r['command'] for r in prior if '-c' in r['command'] and any(x.endswith('/'+name+'.cc') for x in r['command'])).copy()
        for flag in ('-o','-MT','-MF'):cmd[cmd.index(flag)+1]=str(objects/(name+'.o'+('.d' if flag=='-MF' else '')))
        run(repo,s,'compile-'+name+('-validation-fix' if a.resume_validation_fix and name=='transaction_probe' else ''),cmd)
    current_libs=sorted((runtime/'lib').glob('*.so'))
    native_prior=load(Path('/dev/shm/moe-nic-wakeup-20261024/build.json'))
    persistent=next(Path(p) for p in native_prior['inputs'] if p.endswith('persistent_streams.o'))
    archive=next(Path(p) for p in native_prior['inputs'] if p.endswith('.a'))
    for path in (persistent,archive):
        assert sha(path)==native_prior['inputs'][str(path)];identity['unchanged_inputs'][str(path)]=sha(path)
    for name in ('backend/rdma/persistent_streams.cc','backend/rdma/persistent_streams.hh'):
        assert sha(repo/name)==native_prior['platform_sources'][name]
    for name in ('transaction-probe','transaction-session'):
        cmd=next(r['command'] for r in prior if '-c' not in r['command'] and '-o' in r['command'] and r['command'][r['command'].index('-o')+1].endswith(name))
        parts=[]
        for arg in cmd:
            if arg.endswith('.o'):
                replacement=objects/Path(arg).name
                if replacement.exists():parts.append(replacement)
                else:
                    assert Path(arg).name=='persistent_streams.o';parts.append(persistent)
        run(repo,s,'link-'+name+('-current-baseline' if a.link_current_objects else ''),['c++',*parts,archive,'-Wl,--no-as-needed',*current_libs,'-o',runtime/name])
        launcher=runtime/('run-'+name)
        launcher.write_text('#!/bin/sh\nset -eu\nbase=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
            'exec /lib64/ld-linux-x86-64.so.2 --inhibit-rpath "" --library-path "$base/lib" "$base/'+name+'" "$@"\n');launcher.chmod(0o755)
    assert all(sha(Path(p))==h for p,h in identity['unchanged_inputs'].items())
    assert all(sha(Path(p))==h==sha(runtime/'lib'/Path(p).name) for p,h in identity['libraries'].items())
    identity['built_runtime']={str(p.relative_to(runtime)):sha(p) for p in runtime.rglob('*') if p.is_file()};save(s/'build.json',identity)


if __name__=='__main__':main()
