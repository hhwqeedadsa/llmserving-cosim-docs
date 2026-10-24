"""Build the pre-change HEAD consumers with the SAME fixed carrier and libraries."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    p=argparse.ArgumentParser()
    for key in ('repository','stage','native_include','fabric_archive','persistent_object'):
        p.add_argument('--'+key.replace('_','-'),type=Path,required=True)
    p.add_argument('--baseline',default='fd3b714f140c871169aa288a543c7d2677dd80cf')
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve()
    ref=s/'reference-source';ref.mkdir();objects=s/'reference-objects';objects.mkdir()
    commit=subprocess.check_output(['git','rev-parse',a.baseline],cwd=repo,text=True).strip()
    need='fd3b714f140c871169aa288a543c7d2677dd80cf';assert commit==need
    files=subprocess.check_output(['git','ls-tree','-r','--name-only',commit,'backend'],cwd=repo,text=True).splitlines()
    identity={}
    for name in files:
        if not name.endswith(('.hh','.cc')):continue
        data=subprocess.check_output(['git','show',commit+':'+name],cwd=repo)
        dest=ref/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
        identity[name]=hashlib.sha256(data).hexdigest()
    libs=sorted((s/'lib').glob('libns3*.so'));assert len(libs)==8
    def run(label,cmd):
        subprocess.run([sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(s/(label+'-monitor')),
            '--wall-seconds','60','--rss-bytes',str(2**30),'--address-bytes',str(2**31),
            '--artifact-bytes',str(64*2**20),'--artifact-root',str(objects),'--artifact-root',str(s/'bin'),
            '--min-free-bytes',str(8*2**30),'--',*map(str,cmd)],cwd=repo,check=True,timeout=65)
    flags=['c++','-std=c++20','-O0','-DNDEBUG','-DNS3_BUILD_PROFILE_RELEASE','-D__LINUX__',
        '-I'+str(a.native_include),'-I'+str(ref/'backend/rdma'),
        '-I'+str(repo/'third_party/LLMServingSim/astra-sim/extern/helper')]
    for name in ('remote_transactions','transaction_probe'):
        run('reference-'+name,[*flags,'-c',ref/'backend/rdma'/(name+'.cc'),'-o',objects/(name+'.o')])
    run('reference-link',['c++',objects/'transaction_probe.o',objects/'remote_transactions.o',
        a.persistent_object,a.fabric_archive,'-Wl,--disable-new-dtags','-Wl,-rpath,$ORIGIN/../lib',
        '-Wl,--no-as-needed',*libs,'-o',s/'bin/reference-probe'])
    (s/'reference-build.json').write_text(json.dumps(dict(commit=commit,source_hashes=identity,
        same_carrier_and_fabric=True,library_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in libs},
        binary_sha256=hashlib.sha256((s/'bin/reference-probe').read_bytes()).hexdigest()),indent=2)+'\n')


if __name__=='__main__':main()
