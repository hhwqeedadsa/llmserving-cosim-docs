"""Isolated incremental native objects/library; preserve all old runtimes."""
import argparse
from pathlib import Path
import shlex
import shutil
from fixtures.nic_wakeup.stage import load,save,sha,run


def main():
    p=argparse.ArgumentParser()
    for key in ('repository','stage','native-build','old-libraries','old-platform'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--resume-current-switch',action='store_true')
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();native=repo/'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3';build=a.native_build.resolve()
    inc=build.parent/'output/include';oldlibs=sorted(a.old_libraries.resolve().glob('libns3*.so'));assert len(oldlibs)==8
    identity=load(s/'initial-identity.json');assert all(sha(p)==identity['libraries'][str(p)] for p in oldlibs)
    lib=s/'lib';obj=s/'objects';binary=s/'bin'
    if not a.resume_current_switch:
        for directory in (lib,obj,binary):assert not directory.exists();directory.mkdir()
        for old in oldlibs:
            if 'point-to-point' not in old.name:shutil.copyfile(old,lib/old.name)
    commands=load(build/'compile_commands.json');objects={};inputs={str(p):sha(p) for p in oldlibs}
    for name in ('qbb-net-device','rdma-hw','switch-node'):
        if a.resume_current_switch and (obj/(name+'.o')).exists():
            objects[name]=obj/(name+'.o');assert objects[name].exists();continue
        record=next(c for c in commands if c['file']==str(native/'src/point-to-point/model'/(name+'.cc')))
        flags=shlex.split(record['command']);position=flags.index('-o');flags[position+1]=str(obj/(name+'.o'))
        run(repo,s,'compile-native-'+name,flags,cwd=build);objects[name]=obj/(name+'.o')
    ninja=(build/'build.ninja').read_text().splitlines()
    line=next(line for line in ninja if line.startswith('build ') and '/lib/libns3.42-point-to-point.so: CXX_SHARED' in line)
    files=line.split(': ',1)[1].split(' || ',1)[0].split()[1:];chosen=[]
    for name in files:
        path=build/name;replacement=objects.get(path.name.removesuffix('.cc.o'))
        if replacement:chosen.append(replacement)
        else:inputs[str(path)]=sha(path);chosen.append(path)
    assert len(chosen)==17
    libraries=sorted(p for p in lib.glob('*.so') if p.name!='libns3.42-point-to-point.so')
    suffix='-current-switch-excluding-output' if a.resume_current_switch else ''
    run(repo,s,'link-native-point-to-point'+suffix,['c++','-shared','-Wl,-soname,libns3.42-point-to-point.so',*chosen,
        '-Wl,--disable-new-dtags','-Wl,-rpath,'+str(lib),'-Wl,--no-as-needed',*libraries,'-o',lib/'libns3.42-point-to-point.so'])
    libs=sorted(lib.glob('*.so'));assert len(libs)==8
    old=a.old_platform.resolve();prior=load(old/'build.json');archive=next(Path(x) for x in prior['inputs'] if x.endswith('.a'))
    assert sha(archive)==prior['inputs'][str(archive)];inputs[str(archive)]=sha(archive)
    assert all(sha(repo/name)==value for name,value in prior['sources'].items())
    for name in ('stream-probe','transaction-probe','transaction-session'):
        parts=[old/'objects'/(name.replace('-','_')+'.o'),old/'objects/persistent_streams.o']
        if name.startswith('transaction'):parts.append(old/'objects/remote_transactions.o')
        for path in parts:inputs[str(path)]=sha(path)
        run(repo,s,'link-'+name+suffix,['c++',*parts,archive,'-Wl,--disable-new-dtags','-Wl,-rpath,'+str(lib),'-Wl,--no-as-needed',*libs,'-o',binary/name])
    flags=['c++','-std=c++20','-O0','-DNDEBUG','-DNS3_BUILD_PROFILE_RELEASE','-D__LINUX__','-Wall','-Wextra','-Werror',
        '-isystem',inc,'-I'+str(repo/'third_party/LLMServingSim/astra-sim/extern/helper')]
    run(repo,s,'compile-native-cpu-fixed',[*flags,repo/'tests/nic_wakeup_native.cc','-Wl,--disable-new-dtags',
        '-Wl,-rpath,'+str(lib),'-Wl,--no-as-needed',*libs,'-o',s/'native-cpu-fixed'])
    assert all(sha(Path(name))==value for name,value in inputs.items())
    save(s/'build.json',dict(inputs=inputs,libraries={str(p):sha(p) for p in libs},
        binaries={str(p.relative_to(s)):sha(p) for p in [*binary.iterdir(),s/'native-cpu-fixed']},
        platform_sources=prior['sources'],native_sources={str(p.relative_to(native)):sha(p)
            for p in (native/'src/point-to-point/model').glob('*') if p.suffix in ('.cc','.h')},
        scope='two changed native objects plus unchanged current switch object and point-to-point library; seven copied hash-identical libraries; relink existing same-layout three consumers; no clean build'))
    run(repo,s,'native-cpu-fixed',[s/'native-cpu-fixed','fixed',s/'native-cpu-fixed.json'])


if __name__=='__main__':main()
