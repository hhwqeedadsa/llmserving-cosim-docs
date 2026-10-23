"""Bounded offline packaging/recovery; recovery deliberately does not run ns-3."""
import argparse
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'experiments/paper_readiness'))
from package_multiconnection_visibility import render

AUTHOR=['experiments/rail_channel_screening/'+name for name in (
    'study.py','run.py','analyze.py','check.py','package.py','README.md','protocol.md')]+[
    'experiments/paper_readiness/package_multiconnection_visibility.py',
    'docs/reviews/rail_channel_screening_20261023.md','deps.lock.json']


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def archive(repo,root,out):
    dest=root/'checkpoint';dest.mkdir()
    items={}
    for path in root.rglob('*'):
        if path.is_symlink() or not path.is_file():continue
        rel=path.relative_to(root)
        if rel.parts[0] in ('checkpoint','recovery'):continue
        if rel.parts[0].endswith('-monitor') and not (root/rel.parts[0]/'probe.json').exists():continue
        items['raw/'+str(rel)]=path
    for name in AUTHOR:items['source/'+name]=repo/name
    manifest={name:dict(bytes=p.stat().st_size,sha256=sha(p)) for name,p in sorted(items.items())}
    member_bytes=sum(v['bytes'] for v in manifest.values())
    assert len(manifest)<512 and member_bytes<64*2**20
    archive_path=dest/'checkpoint.tar.gz'
    with tarfile.open(archive_path,'w:gz') as tf:
        for name,p in sorted(items.items()):tf.add(p,arcname=name,recursive=False)
    assert archive_path.stat().st_size<24*2**20
    with tarfile.open(archive_path,'r:gz') as tf:
        assert len(tf.getmembers())==len(manifest)
        for member in tf:
            assert member.isfile();data=tf.extractfile(member).read()
            assert len(data)==manifest[member.name]['bytes']
            assert hashlib.sha256(data).hexdigest()==manifest[member.name]['sha256']
    save(dest/'members.json',manifest)
    result=dict(members=len(manifest),member_bytes=member_bytes,compressed_bytes=archive_path.stat().st_size,
        archive_sha256=sha(archive_path),verified_members=True,includes_native_binary=False,
        includes_native_libraries=False,contains_original_routes_local_only=True,
        volatile=True,persistent_backup=False)
    save(dest/'archive.json',result);save(out/'archive.json',result)
    print(json.dumps(result))


def recover(repo,root,out):
    del repo
    dest=root/'recovery';dest.mkdir()
    manifest=load(root/'checkpoint/members.json');path=root/'checkpoint/checkpoint.tar.gz'
    assert sha(path)==load(root/'checkpoint/archive.json')['archive_sha256']
    with tarfile.open(path,'r:gz') as tf:
        assert len(tf.getmembers())==len(manifest)
        for member in tf:
            name=PurePosixPath(member.name)
            assert member.isfile() and not name.is_absolute() and '..' not in name.parts
            data=tf.extractfile(member).read();row=manifest[member.name]
            assert len(data)==row['bytes'] and hashlib.sha256(data).hexdigest()==row['sha256']
            target=dest/member.name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    cwd=dest/'empty-cwd';cwd.mkdir();tool=dest/'source/experiments/rail_channel_screening'
    raw=dest/'raw';new=dest/'new';new.mkdir();commands=[]
    env={**os.environ,'PYTHONPATH':'','PYTHONDONTWRITEBYTECODE':'1'}
    stages=[('plan',[tool/'study.py','plan','--observed',raw/'input/observed','--output',new/'plans']),
        ('analyze',[tool/'analyze.py','--observed',raw/'input/observed','--plans',new/'plans',
                    '--network',raw/'network','--output',new/'analysis']),
        ('check',[tool/'check.py','--inputs',raw/'input','--plans',new/'plans',
                   '--network',raw/'network','--analysis',new/'analysis','--output',new/'verification.json'])]
    for name,args in stages:
        cmd=[sys.executable,*map(str,args)]
        run=subprocess.run(cmd,cwd=cwd,env=env,capture_output=True,timeout=30)
        (new/(name+'.stdout')).write_bytes(run.stdout);(new/(name+'.stderr')).write_bytes(run.stderr)
        commands.append(dict(name=name,command=cmd,cwd=str(cwd),parent_pythonpath='',exit_code=run.returncode))
        save(new/'commands.json',commands);assert run.returncode==0,run.stderr.decode()[-2000:]
    same=[]
    for directory in ('plans','analysis'):
        for p in sorted((raw/directory).glob('*.json')):
            target=new/directory/p.name;assert p.read_bytes()==target.read_bytes()
            same.append(dict(path=directory+'/'+p.name,sha256=sha(p)))
    assert (raw/'verification.json').read_bytes()==(new/'verification.json').read_bytes()
    same.append(dict(path='verification.json',sha256=sha(raw/'verification.json')))
    result=dict(recovered=True,checked_checkpoint_members=len(manifest),new_source_root=True,
        new_cwd=True,empty_parent_pythonpath=True,public_research_commands=3,
        byte_identical_artifacts=same,network_reexecuted=False,native_binary_in_archive=False,
        scope='offline replanning and reanalysis of the original six network records')
    save(root/'recovery.json',result);save(out/'recovery.json',result);print(json.dumps(result))


def report(repo,root,out):
    out.mkdir(parents=True,exist_ok=True)
    for name in ('conditions.json','ports.json','comparisons.json','scope.json'):
        shutil.copyfile(root/'analysis'/name,out/name)
    shutil.copyfile(root/'verification.json',out/'verification.json')
    shutil.copyfile(root/'input/observed/sources.json',out/'sources.json')
    shutil.copyfile(root/'time-basis/sources.json',out/'time-basis.json')
    body=render((repo/'docs/reviews/rail_channel_screening_20261023.md').read_text())
    def relink(m):
        if m[1].startswith(('http:','https:','#')):return m[0]
        target,*fragment=m[1].split('#',1)
        rel=os.path.relpath((repo/'docs/reviews'/target).resolve(),out.resolve())
        return 'href="'+rel+('#'+fragment[0] if fragment else '')+'"'
    body=re.sub(r'href="([^"]+)"',relink,body)
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>双端口网络筛选：收益来自哪里</title>
<style>body{margin:0;background:#f1f4f8;color:#17283d;font:17px/1.85 system-ui,sans-serif}
main{max-width:1060px;margin:30px auto;padding:32px 44px;background:#fff;border-radius:12px}
h1{font-size:30px;line-height:1.4}h2{font-size:23px;margin-top:34px;padding-top:20px;border-top:1px solid #d7e0eb}
.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:15px}td,th{padding:10px;border:1px solid #d7e0eb;text-align:left;vertical-align:top}th{background:#edf3f9}
a{color:#195294;overflow-wrap:anywhere}code{background:#edf3f9;padding:2px 4px;overflow-wrap:anywhere}li{margin:10px 0}
.status{background:#edf3f9;padding:12px;border-radius:6px}@media(max-width:700px){main{margin:0;padding:20px}h1{font-size:25px}}</style>
<main><p class="status">2026-10-23 · 六组实际原生网络 · 全部就绪的数据微基准 · 非完整现代推理/硬件校准</p>'''+body+'</main></html>'
    (out/'report.html').write_text(page);print('report written')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('phase',choices=('archive','recover','report'))
    for n in ('repo','root','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();globals()[a.phase](a.repo.resolve(),a.root.resolve(),a.output.resolve())
