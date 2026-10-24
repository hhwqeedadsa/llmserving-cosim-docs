"""Isolated incremental build of changed carrier and its four consumers."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage','native_include','fabric_archive','libraries'):
        p.add_argument('--'+name.replace('_','-'),type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();objects=s/'objects';objects.mkdir();binary=s/'bin';binary.mkdir()
    libs=sorted(a.libraries.glob('libns3*.so'));assert len(libs)==8
    identity={str(p.resolve()):sha(p) for p in [a.fabric_archive,*libs]}
    def run(label,cmd):
        subprocess.run([sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(s/(label+'-monitor')),
            '--wall-seconds','60','--rss-bytes',str(2**30),'--address-bytes',str(2**31),'--artifact-bytes',str(64*2**20),
            '--artifact-root',str(objects),'--artifact-root',str(binary),'--min-free-bytes',str(8*2**30),'--',*map(str,cmd)],cwd=repo,check=True,timeout=65)
    flags=['c++','-std=c++20','-O0','-DNDEBUG','-DNS3_BUILD_PROFILE_RELEASE','-D__LINUX__','-Wall','-Wextra',
        '-I'+str(a.native_include),'-I'+str(repo/'backend/rdma'),'-I'+str(repo/'third_party/LLMServingSim/astra-sim/extern/helper')]
    for name in ('persistent_streams','remote_transactions','transaction_probe','transaction_session','stream_probe'):
        run('compile-'+name,[*flags,'-c',repo/'backend/rdma'/(name+'.cc'),'-o',objects/(name+'.o')])
    for name in ('transaction-probe','transaction-session','stream-probe'):
        objs=[objects/(name.replace('-','_')+'.o')]
        if name.startswith('transaction'):objs.append(objects/'remote_transactions.o')
        run('link-'+name,['c++',*objs,objects/'persistent_streams.o',a.fabric_archive,'-Wl,--disable-new-dtags',
            '-Wl,-rpath,$ORIGIN/../lib','-Wl,--no-as-needed',*libs,'-o',binary/name])
    run('python-regression',[sys.executable,'-B','-m','unittest','discover','-s','tests/unit','-p','*transaction*test.py'])
    for name,digest in identity.items():assert sha(Path(name))==digest
    (s/'build.json').write_text(json.dumps(dict(inputs=identity,binaries={p.name:sha(p) for p in binary.iterdir()},scope='incremental adapters, not a clean dependency build'),indent=2)+'\n')


if __name__=='__main__':main()
