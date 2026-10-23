"""Bounded report/archive/recovery for the visibility audit, without network access."""
import argparse
import ast
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

AUTHOR_FILES = [
    'experiments/paper_readiness/'+n for n in (
        'multiconnection_visibility.py','audit_multiconnection_visibility.py',
        'check_multiconnection_visibility.py','package_multiconnection_visibility.py',
        'multiconnection_visibility_protocol.md','README.md','modern_backend_contract.md')]
AUTHOR_FILES += ['docs/reviews/多连接屏障与远端可见性_20261023.md']


def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def inline(s):
    s=html.escape(s)
    s=re.sub(r'\[([^]]+)\]\(([^)]+)\)',lambda m:'<a href="'+m[2]+'">'+m[1]+'</a>',s)
    s=re.sub(r'`([^`]+)`',r'<code>\1</code>',s)
    return re.sub(r'\*\*([^*]+)\*\*',r'<strong>\1</strong>',s)


def render(md):
    # A tiny renderer for this controlled Markdown document; no HTML from source
    # files or external scripts is executed. Table/list content is escaped.
    blocks=[];lines=md.splitlines();i=0
    while i<len(lines):
        s=lines[i]
        if not s.strip():i+=1;continue
        if s.startswith('#'):
            level=len(s)-len(s.lstrip('#'));blocks.append(f'<h{level}>'+inline(s[level:].strip())+f'</h{level}>');i+=1
        elif s.startswith('|'):
            rows=[]
            while i<len(lines) and lines[i].startswith('|'):
                row=lines[i].strip().strip('|').split('|');i+=1
                if all(re.fullmatch(r'\s*:?-+:?\s*',v) for v in row):continue
                tag='th' if not rows else 'td'
                rows.append('<tr>'+''.join(f'<{tag}>'+inline(v.strip())+f'</{tag}>' for v in row)+'</tr>')
            blocks.append('<div class="table"><table>'+''.join(rows)+'</table></div>')
        elif re.match(r'^\d+\. ',s):
            rows=[]
            while i<len(lines) and re.match(r'^\d+\. ',lines[i]):
                rows.append('<li>'+inline(re.sub(r'^\d+\. ','',lines[i]))+'</li>');i+=1
            blocks.append('<ol>'+''.join(rows)+'</ol>')
        else:
            para=[s];i+=1
            while i<len(lines) and lines[i].strip() and not lines[i].startswith(('#','|')):
                para.append(lines[i]);i+=1
            blocks.append('<p>'+inline(' '.join(para))+'</p>')
    return ''.join(blocks)


def report(repo,root,out):
    out.mkdir(parents=True,exist_ok=True)
    for n in ('summary.json','excerpts.json','finite-results.json','cases.json'):
        shutil.copyfile(root/'analysis'/n,out/n)
    for n in ('sources.json','requests.json'):
        shutil.copyfile(root/'sources'/n,out/n)
    shutil.copyfile(root/'verification.json',out/'verification.json')
    md=(repo/AUTHOR_FILES[-1]).read_text()
    # The Markdown's links are relative to docs/reviews; adjust for the HTML.
    body=render(md)
    body=re.sub(r'href="([^"]+)"',lambda m:'href="'+(
        os.path.relpath((repo/'docs/reviews'/m[1].split('#')[0]).resolve(),out.resolve())+
        ('#'+m[1].split('#',1)[1] if '#' in m[1] else '')
        if not m[1].startswith(('https:','http:','#')) else m[1])+'"',body)
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>多context屏障与远端可见性</title><style>
:root{color-scheme:light}body{margin:0;background:#f3f5f8;color:#1e293b;font:17px/1.85 system-ui,sans-serif}
main{max-width:1060px;margin:34px auto;padding:36px 48px;background:white;border-radius:14px}
h1{font-size:30px;line-height:1.4}h2{margin-top:38px;font-size:23px;border-top:1px solid #dce3ed;padding-top:24px}
p{margin:16px 0}a{color:#205ba6;overflow-wrap:anywhere}code{background:#eff3f8;padding:2px 5px;border-radius:4px;overflow-wrap:anywhere}
.table{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:15px}td,th{padding:12px 14px;border:1px solid #dce3ed;text-align:left;vertical-align:top}
th{background:#eaf0f8}li{margin:10px 0}strong{color:#17406f}.status{background:#eaf0f8;padding:14px 18px;border-radius:7px}
@media(max-width:700px){main{margin:0;padding:20px}body{font-size:16px}h1{font-size:25px}}
</style><main><p class="status">研究证据 · 2026-10-23 · 无网络/GPU仿真 · 论文目标进行中</p>'''+body+'</main></html>'
    (out/'report.html').write_text(page)
    save(out/'scope.json',dict(network_simulated=False,GPU_executed=False,
        vendor_source_executed=False,provider_or_library_bug_proven=False,
        physical_visibility_domains_verified=False,paper_goal_complete=False,
        archive_volatile=True,persistent_backup=False,browser_visual_acceptance=False))
    print('report written')


def archive(repo,root,out):
    dest=root/'checkpoint';dest.mkdir()
    items={}
    for p in root.rglob('*'):
        if not p.is_file() or p.is_symlink():continue
        rel=p.relative_to(root)
        if rel.parts[0] in ('checkpoint','recovery','package-monitor'):continue
        # Only finished monitor stages have a stable snapshot.
        if rel.parts[0].endswith('-monitor') and not (root/rel.parts[0]/'probe.json').exists():continue
        items['raw/'+str(rel)]=p
    for name in AUTHOR_FILES:items['source/'+name]=repo/name
    profile=repo/'docs/review_artifacts/slot_projection_20261023/real-profile.json'
    items['input/real-profile.json']=profile
    assert len(items)<512 and sum(p.stat().st_size for p in items.values())<48*2**20
    manifest={name:dict(bytes=p.stat().st_size,sha256=sha(p)) for name,p in sorted(items.items())}
    path=dest/'checkpoint.tar.gz'
    with tarfile.open(path,'w:gz') as tar:
        for name,p in sorted(items.items()):tar.add(p,arcname=name,recursive=False)
    assert path.stat().st_size<16*2**20
    with tarfile.open(path) as tar:
        members=tar.getmembers();assert len(members)==len(items)
        for m in members:
            assert m.isfile() and m.name in manifest
            assert hashlib.sha256(tar.extractfile(m).read()).hexdigest()==manifest[m.name]['sha256']
    save(dest/'members.json',manifest)
    save(out/'archive.json',dict(verified=True,path=str(path),sha256=sha(path),members=len(items),
        source_snapshots=len(AUTHOR_FILES),member_bytes=sum(r['bytes'] for r in manifest.values()),
        compressed_bytes=path.stat().st_size,volatile=True,persistent_backup=False))
    print('archive members verified:',len(items))


def recover(root,out):
    archive=root/'checkpoint/checkpoint.tar.gz';dest=root/'recovery';dest.mkdir()
    manifest=json.loads((root/'checkpoint/members.json').read_text())
    with tarfile.open(archive) as tar:
        for m in tar.getmembers():
            assert m.isfile() and m.name in manifest
            assert not Path(m.name).is_absolute() and '..' not in Path(m.name).parts
            p=dest/m.name;p.parent.mkdir(parents=True,exist_ok=True)
            b=tar.extractfile(m).read();assert hashlib.sha256(b).hexdigest()==manifest[m.name]['sha256'];p.write_bytes(b)
    src=dest/'source';source=dest/'raw/sources';profile=dest/'input/real-profile.json'
    env=dict(os.environ,PYTHONPATH='',PYTHONDONTWRITEBYTECODE='1')
    commands=[]
    for target in ('analysis','repeat'):
        cmd=[sys.executable,'experiments/paper_readiness/audit_multiconnection_visibility.py',
             '--source',str(source),'--profile',str(profile),'--output',str(dest/target)]
        r=subprocess.run(cmd,cwd=src,env=env,capture_output=True,timeout=60)
        commands.append(dict(command=cmd,cwd=str(src),exit_code=r.returncode,
            stdout=r.stdout.decode(),stderr=r.stderr.decode()))
        save(dest/'commands.json',commands);assert r.returncode==0
    cmd=[sys.executable,'experiments/paper_readiness/check_multiconnection_visibility.py',
        '--source',str(source),'--profile',str(profile),'--analysis',str(dest/'analysis'),
        '--repeat',str(dest/'repeat'),'--output',str(dest/'verification.json')]
    r=subprocess.run(cmd,cwd=src,env=env,capture_output=True,timeout=60)
    commands.append(dict(command=cmd,cwd=str(src),exit_code=r.returncode,
        stdout=r.stdout.decode(),stderr=r.stderr.decode()));save(dest/'commands.json',commands);assert r.returncode==0
    files=[]
    for p in sorted((root/'analysis').iterdir()):
        assert p.read_bytes()==(dest/'analysis'/p.name).read_bytes();files.append(p.name)
    assert (root/'verification.json').read_bytes()==(dest/'verification.json').read_bytes()
    save(out/'replay.json',dict(verified=True,new_source_root=str(src),new_cwd=True,
        empty_PYTHONPATH=True,analysis_files_byte_identical=files,independent_verification_byte_identical=True,
        commands=3,source_files_restored=len(AUTHOR_FILES),network_access_required=False,
        vendor_source_executed=False,archive_members_verified=len(manifest)))
    print('offline recovery verified')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=('report','archive','recover'))
    p.add_argument('--repo',type=Path,required=True);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    repo,root,out=a.repo.resolve(),a.root.resolve(),a.output.resolve()
    if a.phase=='report':report(repo,root,out)
    elif a.phase=='archive':archive(repo,root,out)
    else:recover(root,out)


if __name__=='__main__':main()
