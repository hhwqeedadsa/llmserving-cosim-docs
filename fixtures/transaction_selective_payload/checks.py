"""Offline strict audit, immutable build inputs and reference package preparation."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage','byte_reference'):p.add_argument('--'+name.replace('_','-'),type=Path,required=True)
    a=p.parse_args();s=a.stage.resolve();out=s/'checks';out.mkdir()
    cmd=[sys.executable,'-B',str(a.repository/'fixtures/transaction_selective_payload/audit.py'),'--stage',str(s),'--output',str(out/'verification.json')]
    done=subprocess.run(cmd,cwd=a.repository,check=True,capture_output=True,text=True,timeout=10)
    save(out/'command.json',dict(command=cmd,cwd=str(a.repository),exit_code=done.returncode))
    assert (out/'verification.json').read_bytes()==(s/'verification.json').read_bytes()
    build=load(s/'build.json')
    for name,digest in build['inputs'].items():assert sha(Path(name))==digest
    for name,digest in build['binaries'].items():assert sha(s/'bin'/name)==digest
    ref=s/'reference';ref.mkdir();old=a.byte_reference.resolve();r=load(old/'network/healthy/result.json')
    assert sha(old/'topology/topology.json')==sha(s/'topology/topology.json')
    identities={}
    for name in r['loaded_libraries']:
        lib=Path(name)
        if not lib.name.startswith('libns3'):continue
        assert sha(lib)==sha(s/'lib'/lib.name);identities[lib.name]=sha(lib)
    assert len(identities)==8
    files={}
    for source,name in [(old/'network/healthy/result.json','result.json'),(old/'inputs/healthy-work.json','work.json'),
                        (old/'inputs/healthy-protocol.json','protocol.json'),(old/'network/healthy/inputs.json','original-command.json')]:
        shutil.copyfile(source,ref/name);files[name]=dict(original_path=str(source),sha256=sha(source))
    save(ref/'identity.json',dict(fixed_eight_library_hashes=identities,files=files,topology_sha256=sha(s/'topology/topology.json'),
        scope='immutable prior pure-byte SR result; no new execution at reference stage'))
    save(s/'checks.json',dict(passed=True,strict_audit_byte_equal=True,build_inputs_and_three_binaries_unchanged=True,
        byte_reference_uses_same_libraries_and_topology=True,no_network=True))


if __name__=='__main__':main()
