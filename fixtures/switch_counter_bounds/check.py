"""Complete bounded native object acceptance using the already built libraries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')


def check(repo,output):
    from moe_ub_cosim.host_probe import probe
    output.mkdir();old=repo/'runs/switch_counter_storage_20261027'
    identity=json.loads((old/'identity.json').read_bytes())
    build=json.loads((old/'build/backend.json').read_bytes())
    assert build['native_commit']==identity['native_commit']
    include=old/'build/output/include';lib=old/'build/output/lib'
    for name,digest in identity['library_hashes'].items():assert sha(lib/name)==digest
    native=repo/'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3'
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=native,text=True).strip()==build['native_commit']
    assert not subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=native,text=True).strip()
    source=repo/'fixtures/switch_counter_storage/native_check.cc';binary=output/'native-check'
    command=['/usr/bin/c++','-std=c++20','-O2','-I'+str(include),str(source),'-L'+str(lib),
        '-Wl,-rpath,'+str(lib),'-lns3.42-point-to-point','-lns3.42-applications',
        '-lns3.42-internet','-lns3.42-network','-lns3.42-core','-o',str(binary)]
    compiled=probe(command,output/'compile-monitor',rss_bytes=2**30,address_bytes=2*2**30,
        artifact_bytes=64*2**20,wall_seconds=30,artifact_roots=[output],min_free_bytes=8*2**30)
    assert compiled['status']=='completed' and not compiled['remaining_live_group_pids']
    empty=output/'empty-cwd';empty.mkdir()
    cases=('port','legacy','zero','too_many','twice','bad_in','bad_out','bad_priority')
    def execute(executable,environment,prefix,relocated_lib=None):
        results={}
        for case in cases:
            value=subprocess.run([executable,case],cwd=empty,env=environment,capture_output=True,timeout=10)
            (output/(prefix+'-'+case+'.stdout')).write_bytes(value.stdout)
            (output/(prefix+'-'+case+'.stderr')).write_bytes(value.stderr)
            expected=0 if case in ('port','legacy') else -signal.SIGABRT
            assert value.returncode==expected,(case,value.returncode,value.stderr)
            diagnostic=None
            if expected:
                diagnostic='switch byte counter read out of bounds' if case.startswith('bad_') else 'invalid or repeated switch byte counter initialization'
                assert diagnostic.encode() in value.stderr,(case,value.stderr)
            if relocated_lib:
                paths=re.findall(rb'calling init:\s*([^\r\n]*libns3[^\r\n]*)',value.stderr)
                assert {Path(p.decode().strip()) for p in paths}=={relocated_lib/n for n in identity['library_hashes']},paths
            results[case]=dict(exit_code=value.returncode,expected_diagnostic=diagnostic)
        return results
    original=execute(binary,dict(os.environ),'original')
    moved=output/'moved';moved.mkdir();moved_lib=moved/'lib';moved_lib.mkdir()
    assert sum((lib/n).stat().st_size for n in identity['library_hashes'])+binary.stat().st_size<48*2**20
    for name,digest in identity['library_hashes'].items():
        shutil.copyfile(lib/name,moved_lib/name);assert sha(moved_lib/name)==digest
    shutil.copyfile(binary,moved/'native-check');(moved/'native-check').chmod(0o755)
    environment=dict(os.environ,LD_LIBRARY_PATH=str(moved_lib),LD_DEBUG='libs')
    relocated=execute(moved/'native-check',environment,'relocated',moved_lib)
    assert original==relocated
    save(output/'verification.json',dict(passed=True,source_sha256=sha(source),binary_sha256=sha(binary),
        original=original,relocated=relocated,native_commit=identity['native_commit'],
        library_hashes=identity['library_hashes'],object_conditions=8,object_executions=16,
        network_runs=0,ns3_builds=0,standalone_compiles=1,HTTP=0,GPU=0,
        prior_failure_preserved=True,prior_acceptance_record_rewritten=False,
        all_relocated_native_initializers_loaded_from_copied_libraries=True))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('repo','output'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();check(a.repo.resolve(),a.output.resolve())
