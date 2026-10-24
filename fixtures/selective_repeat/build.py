"""Incremental adapter acceptance build using explicit existing, read-only native inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage','native_include','native_lib','fabric_archive'):
        p.add_argument('--'+name.replace('_','-'),type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve()
    assert stage.is_dir();objects=stage/'objects';objects.mkdir();binary=stage/'bin';binary.mkdir()
    libs=sorted(a.native_lib.glob('libns3*.so'));assert len(libs)==8
    identity={str(p.resolve()):sha(p) for p in [a.fabric_archive,*libs]}
    def run(label,cmd):
        monitor=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(stage/(label+'-monitor')),
          '--rss-bytes',str(2**30),'--address-bytes',str(2**31),'--artifact-bytes',str(64*2**20),
          '--wall-seconds','60','--artifact-root',str(stage),'--min-free-bytes',str(8*2**30),'--',*map(str,cmd)]
        subprocess.run(monitor,cwd=repo,check=True,timeout=65)
    flags=['c++','-std=c++20','-O0','-DNDEBUG','-DNS3_BUILD_PROFILE_RELEASE','-D__LINUX__','-Wall','-Wextra',
           '-I'+str(a.native_include),'-I'+str(repo/'backend/rdma'),
           '-I'+str(repo/'third_party/LLMServingSim/astra-sim/extern/helper')]
    for name in ('persistent_streams','stream_probe'):
        run('compile-'+name,[*flags,'-c',repo/'backend/rdma'/(name+'.cc'),'-o',objects/(name+'.o')])
    run('link',['c++',objects/'stream_probe.o',objects/'persistent_streams.o',a.fabric_archive,
        '-Wl,--disable-new-dtags','-Wl,-rpath,'+str(a.native_lib.resolve()),'-Wl,--no-as-needed',*libs,
        '-o',binary/'stream-probe'])
    run('compile-oracle',['c++','-std=c++20','-O0','-I'+str(repo/'backend/rdma'),
        repo/'tests/unit/selective_repeat_test.cc','-o',binary/'state-oracle'])
    run('state-oracle',[binary/'state-oracle'])
    run('python-input',[sys.executable,'-B','-m','unittest','discover','-s','tests/unit','-p','scaleout*streams_test.py'])
    run('python-selective',[sys.executable,'-B','-m','unittest','discover','-s','tests/unit','-p','scaleout_selective_repeat_test.py'])
    for name,digest in identity.items():assert sha(Path(name))==digest
    (stage/'build.json').write_text(json.dumps(dict(inputs=identity,
        binary=dict(path=str(binary/'stream-probe'),sha256=sha(binary/'stream-probe')),
        scope='incremental adapter build and CPU/input checks, not a clean native build'),indent=2)+'\n')


if __name__=='__main__':main()
