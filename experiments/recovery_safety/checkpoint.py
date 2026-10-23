"""Bounded evidence archive and an offline replay in a new source root/cwd."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[2]
REVIEW=Path('docs/review_artifacts/recovery_safety_20261023')
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')

def archive(run):
    dest=run/'checkpoint';dest.mkdir(exist_ok=False);items={}
    for p in run.rglob('*'):
        relative=p.relative_to(run)
        if relative.parts[0] in ('checkpoint','recovery') or relative.parts[0].endswith('-monitor'):continue
        if p.is_file():assert not p.is_symlink();items['raw/'+str(relative)]=p
    for path in ('experiments/recovery_safety','docs/review_artifacts/recovery_safety_20261023'):
        for p in (ROOT/path).rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts and p.name not in ('archive.json','recovery.json','closeout.json'):
                items['source/'+str(p.relative_to(ROOT))]=p
    report=ROOT/'docs/reviews/创新点筛选与MoE安全恢复候选_20261023.md'
    items['source/'+str(report.relative_to(ROOT))]=report
    for group in ('deepep_modern_20261022','nccl_gin_20261022'):
        base=ROOT/'data/research'/group;p=base/'tree.json';items['source/'+str(p.relative_to(ROOT))]=p
    for row in read(run/'sources/fixed-sources.json')['sources']:
        group='nccl_gin_20261022' if row['commit']=='12df1a11afad322be5a204a2db890161cbf8131d' else 'deepep_modern_20261022'
        p=ROOT/'data/research'/group/'sources'/row['path'];assert sha(p)==row['sha256']
        items['source/'+str(p.relative_to(ROOT))]=p
    manifest={name:dict(bytes=p.stat().st_size,sha256=sha(p)) for name,p in sorted(items.items())}
    size=sum(r['bytes'] for r in manifest.values());assert len(manifest)<=500 and size<=32*2**20
    target=dest/'checkpoint.tar.gz'
    with tarfile.open(target,'w:gz') as tf:
        for name,p in sorted(items.items()):tf.add(p,arcname=name,recursive=False)
    assert target.stat().st_size<=16*2**20
    with tarfile.open(target,'r:gz') as tf:
        assert len(tf.getmembers())==len(manifest)
        for member in tf:
            data=tf.extractfile(member).read();assert hashlib.sha256(data).hexdigest()==manifest[member.name]['sha256']
    save(dest/'members.json',manifest)
    summary=dict(passed=True,members=len(manifest),member_bytes=size,compressed_bytes=target.stat().st_size,
        sha256=sha(target),includes_binaries=False,storage='tmpfs_volatile',persistent_backup=False)
    save(dest/'archive.json',summary);save(ROOT/REVIEW/'archive.json',summary);print(json.dumps(summary))


def recover(run):
    dest=run/'recovery';dest.mkdir(exist_ok=False);manifest=read(run/'checkpoint/members.json')
    archive_file=run/'checkpoint/checkpoint.tar.gz';assert sha(archive_file)==read(run/'checkpoint/archive.json')['sha256']
    with tarfile.open(archive_file,'r:gz') as tf:
        assert len(tf.getmembers())==len(manifest)
        for member in tf:
            name=PurePosixPath(member.name);assert member.isfile() and not name.is_absolute() and '..' not in name.parts
            data=tf.extractfile(member).read();assert hashlib.sha256(data).hexdigest()==manifest[member.name]['sha256']
            p=dest/member.name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    source=dest/'source';cwd=dest/'empty-cwd';cwd.mkdir();new=dest/'new';new.mkdir();commands=[]
    env=dict(os.environ,PYTHONPATH='',PYTHONDONTWRITEBYTECODE='1')
    for name,cmd in [('model',[source/'experiments/recovery_safety/check_model.py','--output',new/'model']),
        ('verify',[source/'experiments/recovery_safety/verify.py','--summary',new/'model/summary.json','--output',new/'verification.json']),
        ('review',[source/'experiments/recovery_safety/build_review.py','--run',dest/'raw','--output',new/'review'])]:
        command=list(map(str,[sys.executable,*cmd]))
        with (new/(name+'.stdout')).open('w') as stdout,(new/(name+'.stderr')).open('w') as stderr:
            p=subprocess.run(command,cwd=cwd,env=env,stdout=stdout,stderr=stderr,timeout=20)
        commands.append(dict(command=command,cwd=str(cwd),exitcode=p.returncode,pythonpath=env['PYTHONPATH']))
        save(new/'commands.json',commands);assert p.returncode==0,name
    compared=[]
    for fresh,old in [(new/'model/summary.json',dest/'raw/model/summary.json'),
        (new/'verification.json',dest/'raw/verification.json'),
        *[(new/'review'/name,source/REVIEW/name) for name in ('summary.json','sources.json','inputs.json','report.html')]]:
        assert fresh.read_bytes()==old.read_bytes(),fresh
        compared.append(dict(file=str(fresh.relative_to(new)),sha256=sha(fresh)))
    # Independently recheck original fixed source bytes against blobs and trees.
    fixed=read(dest/'raw/sources/fixed-sources.json')['sources']
    for row in fixed:
        group='nccl_gin_20261022' if row['commit']=='12df1a11afad322be5a204a2db890161cbf8131d' else 'deepep_modern_20261022'
        base=source/'data/research'/group;data=(base/'sources'/row['path']).read_bytes()
        assert hashlib.sha256(data).hexdigest()==row['sha256']
        assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==row['git_blob_sha']
        assert any(e['path']==row['path'] and e['sha']==row['git_blob_sha'] for e in read(base/'tree.json')['tree'])
    summary=dict(passed=True,commands=commands,archive_members_verified=len(manifest),new_source_root=True,new_cwd=True,
        fixed_source_files_verified=len(fixed),outputs_byte_identical=compared,new_network_executions=0,new_gpu_executions=0,
        scope='offline CPU model/report replay, not dependency build or real RDMA recovery')
    save(run/'recovery.json',summary);save(ROOT/REVIEW/'recovery.json',summary);print(json.dumps(summary))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['archive','recover']);p.add_argument('--run',type=Path,required=True);a=p.parse_args()
    (archive if a.mode=='archive' else recover)(a.run.resolve())
if __name__=='__main__':main()
