"""Prepare a relocatable same-host runtime and execute one fresh public workflow."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')


def verify_payload(repo,s,payload,libs):
    original=json.loads((s/'network/healthy/result.json').read_text())
    replayed=json.loads((payload/'fresh-network/result.json').read_text())
    native={str((payload/'lib'/lib.name).resolve()) for lib in libs}
    assert {str(Path(p).resolve()) for p in replayed['loaded_libraries'] if '/libns3.' in p}==native
    for lib in libs:assert sha(lib)==sha(payload/'lib'/lib.name)
    replayed['loaded_libraries']=original['loaded_libraries'];assert replayed==original
    commands=json.loads((payload/'commands.json').read_text())
    save(payload/'replay.json',dict(passed=True,public_commands=commands,new_source_root=True,empty_cwd=True,
        complete_result_equal_except_library_paths=True,eight_libraries_loaded_from_package=True,
        scope='same Linux host; portable incremental binary, not a clean native build or cross-machine guarantee'))
    for f in (repo/'fixtures/selective_repeat').glob('*.py'):shutil.copyfile(f,payload/'source'/f.name)
    save(payload/'manifest.json',dict(schema_version='moe-selective-runtime/1',
        files={str(f.relative_to(payload)):dict(bytes=f.stat().st_size,sha256=sha(f))
               for f in sorted(payload.rglob('*')) if f.is_file() and f.name!='manifest.json'}))
    print(json.dumps(dict(passed=True,loaded_libraries=8,network_executions_total=12)))


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage','libraries','fabric_archive'):p.add_argument('--'+name.replace('_','-'),type=Path,required=True)
    p.add_argument('--audit-existing',action='store_true',help='Check already completed fresh results without executing any command')
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();payload=s/'portable'
    libs=sorted(a.libraries.glob('libns3*.so'));assert len(libs)==8
    if a.audit_existing:
        assert not (payload/'replay.json').exists()
        verify_payload(repo,s,payload,libs);return
    payload.mkdir();(payload/'bin').mkdir();(payload/'lib').mkdir()
    shutil.copytree(repo/'python',payload/'python',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(s/'inputs',payload/'inputs');shutil.copytree(s/'topology',payload/'topology')
    (payload/'source').mkdir()
    for f in (repo/'fixtures/selective_repeat').glob('*.py'):shutil.copyfile(f,payload/'source'/f.name)
    shutil.copytree(s/'network',payload/'network')
    for lib in libs:
        shutil.copyfile(lib,payload/'lib'/lib.name);assert sha(lib)==sha(payload/'lib'/lib.name)
    cmd=['c++',str(s/'objects/stream_probe.o'),str(s/'objects/persistent_streams.o'),str(a.fabric_archive),
         '-Wl,--disable-new-dtags','-Wl,-rpath,$ORIGIN/../lib','-Wl,--no-as-needed',*map(str,libs),'-o',str(payload/'bin/stream-probe')]
    monitor=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(s/'portable-link-monitor'),
         '--rss-bytes',str(2**30),'--address-bytes',str(2**31),'--artifact-bytes',str(64*2**20),
         '--wall-seconds','60','--artifact-root',str(payload),'--min-free-bytes',str(8*2**30),'--',*cmd]
    subprocess.run(monitor,cwd=repo,check=True,timeout=65)
    empty=payload/'empty-cwd';empty.mkdir();commands=[]
    env=dict(os.environ,PYTHONPATH=str(payload/'python'),PYTHONDONTWRITEBYTECODE='1')
    for label,tail in [
        ('topology',['topology','--config',payload/'inputs/topology.json','--output',payload/'fresh-topology']),
        ('network',['rdma-stream-probe','--topology',payload/'fresh-topology','--protocol',payload/'inputs/healthy-protocol.json',
                    '--config',payload/'inputs/healthy-work.json','--backend',payload/'bin/stream-probe','--output',payload/'fresh-network'])]:
        command=[sys.executable,'-B','-m','moe_ub_cosim','scaleout',*map(str,tail)]
        with (payload/(label+'.stdout.log')).open('w') as stdout,(payload/(label+'.stderr.log')).open('w') as stderr:
            r=subprocess.run(command,cwd=empty,env=env,stdout=stdout,stderr=stderr,timeout=60)
        commands.append(dict(command=command,cwd=str(empty),exit_code=r.returncode));save(payload/'commands.json',commands)
        assert r.returncode==0
    verify_payload(repo,s,payload,libs)


if __name__=='__main__':main()
