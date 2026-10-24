"""Necessary repair build after idle-stream close crash; successful networks stay saved."""
import argparse
from pathlib import Path
from stage import run,load,save,sha


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage','native-include'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();build=load(s/'build-v1.json')
    assert all(sha(p)==h for p,h in build['inputs'].items())
    obj=s/'objects';binary=s/'bin';libs=[Path(p) for p in build['libraries']]
    archive=next(Path(p) for p in build['inputs'] if p.endswith('.a'))
    flags=['c++','-std=c++20','-O0','-DNDEBUG','-DNS3_BUILD_PROFILE_RELEASE','-D__LINUX__','-Wall','-Wextra',
        '-I'+str(a.native_include),'-I'+str(repo/'backend/rdma'),'-I'+str(repo/'third_party/LLMServingSim/astra-sim/extern/helper')]
    run(repo,s,'compile-idle-log-fixed',[*flags,'-c',repo/'backend/rdma/persistent_streams.cc','-o',obj/'persistent_streams.o'])
    for name in ('stream-probe','transaction-probe','transaction-session'):
        objs=[obj/(name.replace('-','_')+'.o')]
        if name.startswith('transaction'):objs.append(obj/'remote_transactions.o')
        run(repo,s,'link-idle-fixed-'+name,['c++',*objs,obj/'persistent_streams.o',archive,
            '-Wl,--disable-new-dtags','-Wl,-rpath,'+str(libs[0].parent),'-Wl,--no-as-needed',*libs,'-o',binary/name])
    build['sources']['backend/rdma/persistent_streams.cc']=sha(repo/'backend/rdma/persistent_streams.cc')
    build['binaries']={p.name:sha(p) for p in binary.iterdir()}
    build['repair']='idle grace log has high=0/deadline=-1 when no published prefix; v1 evidence retained'
    save(s/'build.json',build)

if __name__=='__main__':main()
