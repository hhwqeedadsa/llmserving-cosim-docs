"""Compile the legacy driver and stream adapter against an immutable runtime."""
from pathlib import Path
import shutil
import subprocess

from .native_identity import verify_native_source
from .scaleout_rdma import ROOT, checked_json, digest, monitored, save
from .deepep_plan import require


def build(runtime,identity,output):
    runtime,identity,output=map(lambda p:Path(p).resolve(),(runtime,identity,output))
    require(not output.exists() and output.parent.is_dir(),'shared driver build requires a new directory')
    require(shutil.disk_usage(output.parent).free>=8*2**30,'build volume below 8GiB reserve')
    native=verify_native_source(ROOT);old=checked_json(runtime/'provenance.json',2*2**20)
    expected=checked_json(identity,2**20)
    require(old['native_commit']==expected['native_commit']==native,'prebuilt native identity differs from lock')
    # These units/headers are recompiled here. The Fabric archive and its layout
    # must keep their recorded sources, and the old runtime is never mutated.
    rebuilt={'backend/rdma/legacy_deepep_probe.cc','backend/rdma/persistent_streams.cc',
             'backend/rdma/persistent_streams.hh','backend/rdma/legacy_execution_validation.hh'}
    for name,wanted in old['sources'].items():
        if name not in rebuilt:
            require((ROOT/name).is_file() and digest(ROOT/name)==wanted,'prebuilt source mismatch: '+name)
    hashes=expected['library_hashes']
    require(isinstance(hashes,dict) and len(hashes)==8 and
            all(name.startswith('libns3.42-') and name.endswith('.so') and '/' not in name for name in hashes),
            'expected eight pinned native libraries')
    archive=runtime/'output/lib/libmoe-rdma-fabric.a'
    require(digest(archive)==expected['fabric_archive_sha256'],'prebuilt Fabric archive hash mismatch')
    libraries=[runtime/'output/lib'/name for name in hashes]
    require(all(path.is_file() and digest(path)==hashes[path.name] for path in libraries),'native library hash mismatch')
    compiler=shutil.which('c++');require(compiler,'existing C++ compiler required')
    output.mkdir();lib=output/'runtime/lib';lib.mkdir(parents=True)
    for source in libraries+[archive]:shutil.copyfile(source,lib/source.name)
    common=[compiler,'-DNS3_BUILD_PROFILE_RELEASE','-D__LINUX__','-DNDEBUG','-Wall','-Wpedantic','-O3','-std=c++20',
            '-fPIE','-fno-semantic-interposition','-I'+str(runtime/'output/include'),
            '-I'+str(ROOT/'backend/rdma'),'-I'+str(ROOT/'third_party/LLMServingSim/astra-sim/extern/helper')]
    commands=[];objects=[]
    sources={str(path.relative_to(ROOT)):digest(path) for path in sorted((ROOT/'backend/rdma').glob('*')) if path.is_file()}
    budget=dict(wall_seconds=240,rss_bytes=2*2**30,address_bytes=4*2**30,artifact_bytes=512*2**20)
    for name in ('legacy_deepep_probe','persistent_streams'):
        obj=output/(name+'.o');objects.append(obj)
        command=[*common,'-c',str(ROOT/'backend/rdma'/(name+'.cc')),'-o',str(obj)];commands.append(command)
    binary=output/'moe-rdma-legacy-deepep'
    commands.append([compiler,*map(str,objects),str(lib/archive.name),'-Wl,--no-as-needed',
        *[str(lib/path.name) for path in libraries],'-ldl','-Wl,-rpath,$ORIGIN/runtime/lib','-o',str(binary)])
    save(output/'build-inputs.json',dict(native_commit=native,runtime=str(runtime),identity_sha256=digest(identity),
        original_sources=old['sources'],sources=sources,recompiled_old_paths=sorted(rebuilt),
        fabric_archive_sha256=digest(archive),library_hashes=hashes,commands=commands,ns3_builds=0))
    for i,command in enumerate(commands):monitored(command,output/f'compile-{i}',[output],runtime=budget)
    linked=subprocess.run(['ldd',str(binary)],capture_output=True,text=True,timeout=10,check=True).stdout
    (output/'ldd.txt').write_text(linked);actual={}
    for line in linked.splitlines():
        if 'libns3' in line and '=>' in line:
            name,right=line.split('=>',1);path=Path(right.split()[0]).resolve()
            require(path.parent==lib,'new legacy driver loaded another runtime');actual[name.strip()]=digest(path)
    require(actual==hashes and 'not found' not in linked,'loaded runtime differs from pinned hashes')
    result=dict(passed=True,legacy_deepep=str(binary),backend_sha256=digest(binary),sources=sources,
        native_commit=native,library_hashes=hashes,ns3_builds=0,
        scope='two platform units compiled; immutable existing Fabric/ns-3 libraries copied and hash checked; no network run')
    save(output/'backend.json',result);return result
