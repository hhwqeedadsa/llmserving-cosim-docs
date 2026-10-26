"""Bounded hash-admitted archive and offline analysis from a copied source root."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path,PurePosixPath
import subprocess
import sys
import tarfile


def sha(data):return hashlib.sha256(data).hexdigest()
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def admit(path):
    assert path.stat().st_size<=32*2**20;blobs={};total=0
    with tarfile.open(path,'r:gz') as archive:
        for member in archive:
            name=PurePosixPath(member.name)
            assert member.isfile() and not name.is_absolute() and '..' not in name.parts and '\\' not in member.name
            assert str(name)==member.name and member.name not in ('','.') and member.name not in blobs
            assert 0<=member.size<=16*2**20 and len(blobs)<2048;total+=member.size;assert total<96*2**20
            blobs[member.name]=archive.extractfile(member).read();assert len(blobs[member.name])==member.size
    assert not any(str(parent) in blobs for name in blobs for parent in PurePosixPath(name).parents if str(parent)!='.')
    manifest=json.loads(blobs.pop('manifest.json'));assert manifest['schema']=='modern-network-levers-checkpoint/1'
    assert set(blobs)==set(manifest['files'])
    for name,data in blobs.items():assert len(data)==manifest['files'][name]['bytes'] and sha(data)==manifest['files'][name]['sha256']
    return blobs,manifest


def create(repo,stage,output,outer):
    assert not output.exists() and json.loads((stage/'verification.json').read_text())['passed'];files={}
    for path in stage.rglob('*'):
        rel=path.relative_to(stage)
        if not path.is_file() or rel.parts[0]=='restored' or path==output:continue
        if any(part.endswith('-monitor') for part in rel.parts) and not (stage/rel.parts[0]/'probe.json').exists():continue
        assert not path.is_symlink();files['evidence/'+str(rel)]=path
    for path in outer.iterdir():
        if path.is_file():files['evidence/driver-monitor/'+path.name]=path
    for path in (repo/'experiments/modern_network_levers').glob('*'):
        if path.is_file() and path.suffix in ('.py','.md'):files['source/'+str(path.relative_to(repo))]=path
    report='docs/reviews/联合仿真的两个创新候选与网络收益判别_20261026.md'
    files['source/'+report]=repo/report
    entries={name:dict(bytes=path.stat().st_size,sha256=sha(path.read_bytes())) for name,path in files.items()}
    assert len(entries)<2048 and sum(e['bytes'] for e in entries.values())<96*2**20
    assert max(e['bytes'] for e in entries.values())<=16*2**20
    manifest=dict(schema='modern-network-levers-checkpoint/1',files=entries,native_runtime_bundled=False,
        network_phase_closed=True,actual_networks=3,scope='offline analysis reproduction; runtime/source identities retained, no native rebuild or hardware accuracy claim')
    with tarfile.open(output,'x:gz',compresslevel=6) as archive:
        for name,path in sorted(files.items()):
            body=path.read_bytes();assert sha(body)==entries[name]['sha256']
            info=tarfile.TarInfo(name);info.size=len(body);info.mode=0o644;archive.addfile(info,io.BytesIO(body))
        body=(json.dumps(manifest,indent=2)+'\n').encode();info=tarfile.TarInfo('manifest.json');info.size=len(body)
        archive.addfile(info,io.BytesIO(body))
    admit(output);save(output.with_suffix('.receipt.json'),dict(sha256=sha(output.read_bytes()),members=len(entries)+1,
        compressed_bytes=output.stat().st_size,uncompressed_bytes=sum(e['bytes'] for e in entries.values())+len(body)))


def restore(archive,output):
    assert not output.exists();blobs,manifest=admit(archive);output.mkdir()
    for name,body in blobs.items():
        path=output/name;path.parent.mkdir(exist_ok=True,parents=True);path.write_bytes(body)
    source=output/'source';evidence=output/'evidence';empty=output/'empty-cwd';empty.mkdir()
    env=os.environ|{'PYTHONPATH':str(source),'PYTHONDONTWRITEBYTECODE':'1'}
    command=[sys.executable,'-B','-m','experiments.modern_network_levers.check','--stage',str(evidence)]
    result=subprocess.run(command,cwd=empty,env=env,capture_output=True,text=True,timeout=60)
    save(output/'replay-command.json',dict(command=command,cwd=str(empty),exit_code=result.returncode,stdout=result.stdout,stderr=result.stderr))
    assert result.returncode==0,result.stderr
    identical={name:(evidence/name).read_bytes()==blobs['evidence/'+name] for name in ('summary.json','verification.json')}
    assert all(identical.values())
    save(output/'recovery.json',dict(passed=True,archive_sha256=sha(archive.read_bytes()),members_hash_verified_before_writes=True,
        copied_research_source_root=True,empty_cwd=True,byte_identical=identical,additional_network_executions=0,
        native_runtime_required=False,scope=manifest['scope']))


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    c=sub.add_parser('create')
    for name in ('repository','stage','output','outer-monitor'):c.add_argument('--'+name,type=Path,required=True)
    r=sub.add_parser('restore')
    for name in ('archive','output'):r.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    if a.action=='create':create(a.repository.resolve(),a.stage.resolve(),a.output.resolve(),a.outer_monitor.resolve())
    else:restore(a.archive.resolve(),a.output.resolve())


if __name__=='__main__':main()
