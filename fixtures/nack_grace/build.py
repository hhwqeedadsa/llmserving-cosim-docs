"""Incremental header-consumer build. Fixed Fabric/native libraries stay immutable."""
import argparse
from pathlib import Path
from stage import load,save,sha,run


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage','baseline','libraries','native-include'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();old=a.baseline.resolve()
    assert not (s/'objects').exists()
    identity=load(old/'build.json');archive=old/'objects/liblive-fabric.a'
    libs=sorted(a.libraries.resolve().glob('libns3*.so'))
    assert len(libs)==8 and all(identity['libraries'][str(p)]==sha(p) for p in libs)
    assert sha(old/'bin/stream-probe')==identity['binaries']['stream-probe']
    # Fabric implementation and layout have not changed since this archive build.
    for name in ('fabric.hh','fabric.cc','astra_adapter.hh','astra_adapter.cc','recovery.cc'):
        rel='backend/rdma/'+name
        assert sha(repo/rel)==identity['sources'][rel]
    objects=s/'objects';objects.mkdir();binary=s/'bin';binary.mkdir()
    inputs={str(p):sha(p) for p in [archive,old/'bin/stream-probe',*libs]}
    flags=['c++','-std=c++20','-O0','-DNDEBUG','-DNS3_BUILD_PROFILE_RELEASE','-D__LINUX__','-Wall','-Wextra',
        '-I'+str(a.native_include),'-I'+str(repo/'backend/rdma'),'-I'+str(repo/'third_party/LLMServingSim/astra-sim/extern/helper')]
    for name in ('persistent_streams','remote_transactions','stream_probe','transaction_probe','transaction_session'):
        run(repo,s,'compile-'+name,[*flags,'-c',repo/'backend/rdma'/(name+'.cc'),'-o',objects/(name+'.o')])
    for name in ('stream-probe','transaction-probe','transaction-session'):
        objs=[objects/(name.replace('-','_')+'.o')]
        if name.startswith('transaction'):objs.append(objects/'remote_transactions.o')
        run(repo,s,'link-'+name,['c++',*objs,objects/'persistent_streams.o',archive,'-Wl,--disable-new-dtags',
            '-Wl,-rpath,'+str(a.libraries.resolve()),'-Wl,--no-as-needed',*libs,'-o',binary/name])
    assert all(sha(p)==h for p,h in inputs.items())
    save(s/'build.json',dict(inputs=inputs,binaries={p.name:sha(p) for p in binary.iterdir()},
        sources={str(p.relative_to(repo)):sha(p) for p in (repo/'backend/rdma').iterdir() if p.suffix in ('.cc','.hh')},
        libraries={str(p):sha(p) for p in libs},scope='incremental PersistentStreams and all header consumers; immutable Fabric and eight ns-3 libraries; not a clean dependency build'))

if __name__=='__main__':main()
