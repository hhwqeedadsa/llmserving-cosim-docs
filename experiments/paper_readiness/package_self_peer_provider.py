"""Offline checkpoint, relocation/rebuild and HTML for the self-peer host audit."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile

from package_multiconnection_visibility import render

RESEARCH = [
    'self_peer_provider.py', 'self_peer_driver.cpp', 'self_peer_recording_api.hpp',
    'check_self_peer_provider.py', 'run_self_peer_provider.py',
    'package_self_peer_provider.py', 'package_multiconnection_visibility.py',
    'self_peer_provider_protocol.md', 'modern_backend_contract.md', 'README.md']
AUTHOR = ['experiments/paper_readiness/'+n for n in RESEARCH]+[
    'python/moe_ub_cosim/__init__.py','python/moe_ub_cosim/host_probe.py',
    'python/moe_ub_cosim/host_processes.py', 'deps.lock.json',
    'docs/reviews/self_peer_provider_20261023.md',
    'third_party/LLMServingSim/astra-sim/extern/helper/json/json.hpp']


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, value):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def archive(repo, root, output):
    dest=root/'checkpoint'
    dest.mkdir()
    items={}
    for p in root.rglob('*'):
        if p.is_symlink() or not p.is_file():
            continue
        rel=p.relative_to(root)
        if rel.parts[0] in ('checkpoint','recovery'):
            continue
        if rel.parts[0].endswith('-monitor') and not (root/rel.parts[0]/'probe.json').exists():
            continue
        items['raw/'+str(rel)]=p
    for name in AUTHOR:
        items['source/'+name]=repo/name
    manifest={name:dict(bytes=p.stat().st_size,sha256=sha(p)) for name,p in sorted(items.items())}
    total=sum(row['bytes'] for row in manifest.values())
    assert len(manifest)<256 and total<32*2**20
    path=dest/'checkpoint.tar.gz'
    with tarfile.open(path,'w:gz') as tar:
        for name,p in sorted(items.items()):
            tar.add(p,arcname=name,recursive=False)
    assert path.stat().st_size<16*2**20
    with tarfile.open(path,'r:gz') as tar:
        assert len(tar.getmembers())==len(manifest)
        for member in tar:
            raw=tar.extractfile(member).read()
            assert member.isfile() and len(raw)==manifest[member.name]['bytes']
            assert hashlib.sha256(raw).hexdigest()==manifest[member.name]['sha256']
    save(dest/'members.json',manifest)
    result=dict(members=len(manifest),member_bytes=total,compressed_bytes=path.stat().st_size,
                archive_sha256=sha(path),verified_members=True,volatile=True,persistent_backup=False)
    save(dest/'archive.json',result)
    save(output/'archive.json',result)
    print(json.dumps(result))


def recover(repo, root, output):
    del repo
    dest=root/'recovery';dest.mkdir()
    manifest=json.loads((root/'checkpoint/members.json').read_text())
    archive_info=json.loads((root/'checkpoint/archive.json').read_text())
    archive_path=root/'checkpoint/checkpoint.tar.gz'
    assert sha(archive_path)==archive_info['archive_sha256']
    with tarfile.open(archive_path,'r:gz') as tar:
        assert len(tar.getmembers())==len(manifest)
        for member in tar:
            name=PurePosixPath(member.name)
            assert member.isfile() and not name.is_absolute() and '..' not in name.parts
            assert member.name in manifest
            raw=tar.extractfile(member).read()
            assert len(raw)==manifest[member.name]['bytes']
            assert hashlib.sha256(raw).hexdigest()==manifest[member.name]['sha256']
            path=dest/member.name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(raw)
    source=dest/'source'
    cwd=dest/'empty-cwd';cwd.mkdir()
    cmd=[sys.executable,str(source/'experiments/paper_readiness/run_self_peer_provider.py'),
         '--source',str(dest/'raw/sources'),'--output',str(dest/'new-run'),
         '--json-include',str(source/'third_party/LLMServingSim/astra-sim/extern/helper')]
    env={**os.environ,'PYTHONPATH':'','PYTHONDONTWRITEBYTECODE':'1'}
    run=subprocess.run(cmd,cwd=cwd,env=env,capture_output=True,timeout=55)
    (dest/'stdout.log').write_bytes(run.stdout)
    (dest/'stderr.log').write_bytes(run.stderr)
    save(dest/'command.json',dict(command=cmd,cwd=str(cwd),parent_pythonpath='',exit_code=run.returncode))
    assert run.returncode==0,run.stderr.decode()[-2000:]
    pairs=[('execute-monitor/stdout.log','execute-monitor/stdout.log'),
           ('verification.json','verification.json')]
    identical=[]
    for old,new in pairs:
        assert (dest/'raw'/old).read_bytes()==(dest/'new-run'/new).read_bytes()
        identical.append(dict(original=old,recovered=new,sha256=sha(dest/'raw'/old)))
    # All generated original fragments must also survive extractor relocation.
    names=['fill.inc','valid_mtu.inc','path_mtu.inc','connect.inc','self_loops.inc',
           'resource_declarations.inc','original_functions.inc','generated_api.inc',
           'excerpts.json','static-evidence.json','synthetic_api.json']
    for name in names:
        assert (dest/'raw/extracted'/name).read_bytes()==(dest/'new-run/extracted'/name).read_bytes()
    result=dict(recovered=True,checkpoint_members_verified=len(manifest),new_source_root=True,
        new_cwd=True,empty_parent_pythonpath=True,rebuilt_from_snapshots=True,
        repeated_execution_byte_identical=True,identical_results=identical,
        identical_extracted_files=len(names),requires_host_python_compiler_and_system_libraries=True,
        network_executed=False,GPU_executed=False)
    save(root/'recovery.json',result)
    save(output/'recovery.json',result)
    print(json.dumps(result))


def report(repo, root, output):
    output.mkdir(parents=True,exist_ok=True)
    for source,name in ((root/'verification.json','verification.json'),
                        (root/'sources/sources.json','sources.json'),
                        (root/'extracted/excerpts.json','excerpts.json'),
                        (root/'extracted/static-evidence.json','static-evidence.json'),
                        (root/'extracted/synthetic_api.json','synthetic_api.json')):
        shutil.copyfile(source,output/name)
    body=render((repo/'docs/reviews/self_peer_provider_20261023.md').read_text())
    def relocate(match):
        target=match[1]
        if target.startswith(('http:','https:','#')):
            return match[0]
        file,*fragment=target.split('#',1)
        rel=os.path.relpath((repo/'docs/reviews'/file).resolve(),output.resolve())
        return 'href="'+rel+('#'+fragment[0] if fragment else '')+'"'
    body=re.sub(r'href="([^"]+)"',relocate,body)
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>现代GIN自发请求与千卡连接规模</title><style>
body{margin:0;background:#f3f5f8;color:#172b40;font:17px/1.85 system-ui,sans-serif}
main{max-width:1050px;margin:30px auto;padding:32px 44px;background:white;border-radius:12px}
h1{font-size:30px;line-height:1.4}h2{font-size:23px;margin-top:36px;border-top:1px solid #d8e2ec;padding-top:20px}
a{color:#17549b;overflow-wrap:anywhere}code{background:#eef3f8;padding:2px 4px;overflow-wrap:anywhere}
.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:15px}td,th{border:1px solid #d8e2ec;padding:10px;text-align:left;vertical-align:top}th{background:#eef3f8}
.scope{padding:12px;background:#eef3f8;border-radius:6px}li{margin:10px 0}
@media(max-width:700px){main{margin:0;padding:20px}h1{font-size:25px}}
</style><main><p class="scope">2026-10-23 · CPU源码片段执行 · 非厂商运行时/网络仿真 · 论文目标进行中</p>'''+body+'</main></html>'
    (output/'report.html').write_text(page)
    save(output/'scope.json',dict(original_host_slices_executed=True,
        vendor_runtime_executed=False,ABI_verified=False,GPU_executed=False,
        network_executed=False,cost_calibrated=False,thousand_gpu_simulated=False,
        paper_contribution_established=False,browser_visual_acceptance=False))
    print('report written')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=('archive','recover','report'))
    for name in ('repo','root','output'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    globals()[a.phase](a.repo.resolve(),a.root.resolve(),a.output.resolve())
