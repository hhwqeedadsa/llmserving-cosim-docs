"""Archive the exact-bound checkpoint on tmpfs and render a small versioned review."""
import argparse
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
from urllib.parse import unquote,urlsplit

ROOT=Path(__file__).resolve().parents[2]
DOC=ROOT/'docs/reviews/整组容量约束的精确改进空间_20261022.md'
NAMES=('prepare_placement_baselines.py','group_capacity_oracle.py','check_group_capacity.py',
       'analyze_group_witness.py','build_group_capacity_review.py','group_capacity_protocol.md','README.md')


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('work','output','checkpoint'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();work=a.work.resolve();out=a.output.resolve();target=a.checkpoint.resolve()
    assert out.is_relative_to(ROOT) and target.is_relative_to(work) and target.is_relative_to(Path('/dev/shm'))
    assert not out.exists() and not target.exists() and shutil.disk_usage(target.parent).free>=8*2**30+64*2**20
    verification=read(work/'verified/verification.json');oracle=read(work/'optimized/oracle.json')
    assert verification['completed'] and verification['repeat_files']==1
    assert verification['oracle_sha256']==sha(work/'optimized/oracle.json')
    assert verification['candidates_sha256']==sha(work/'enumerated/candidates.json')
    assert verification['checker_sha256']==sha(Path(__file__).with_name('check_group_capacity.py'))
    assert oracle['best_p95_with_communication_budget']==oracle['best_p95_without_communication_budget']==14
    assert oracle['witness']['minimum_copies']==66676 and oracle['witness']['exceedances']==371
    diagnostic=read(work/'diagnostic/diagnostic.json');assert [r['worse_layers'] for r in diagnostic['tradeoffs']]==[54,56]
    files={}
    def tree(folder,prefix):
        for file in folder.rglob('*'):
            if file.is_file():assert not file.is_symlink();files[prefix+'/'+str(file.relative_to(folder))]=file
    probes=[]
    for name in ('prepare','enumerate','optimize','repeat-optimize','check','diagnostic'):
        folder=work/(name+'-probe');report=read(folder/'probe.json')
        assert report['status']=='completed' and not report['remaining_live_group_pids']
        probes.append(report);tree(folder,'saved_run/probes/'+name)
    out.mkdir(parents=True)
    for file,name in ((work/'optimized/oracle.json','oracle.json'),(work/'verified/verification.json','verification.json'),
        (work/'verified/certificates.json','certificates.json'),(work/'diagnostic/diagnostic.json','diagnostic.json')):
        shutil.copy2(file,out/name)
    bounds=read(work/'enumerated/prefill_bounds.json')
    save(out/'prefill_summary.json',dict(optimal_layers=bounds['optimal_layers'],layers=58,
        full_result_sha256=sha(work/'enumerated/prefill_bounds.json'),
        full_archive_member='saved_run/enumerated/prefill_bounds.json',scope=bounds['scope']))
    save(out/'execution.json',dict(platform_base_commit=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,
        check=True,capture_output=True,text=True,timeout=10).stdout.strip(),probes=probes,
        scripts_sha256={name:sha(Path(__file__).with_name(name)) for name in NAMES},
        archive_storage='tmpfs_volatile',persistent_backup=False,
        storage_reason='Root free space was about6.1GiB, below8GiB artifact reserve; no large archive on root',
        reads_future_decode=True,deployable=False,new_network_executed=False,gpu_executed=False,goal_complete=False))
    renderer=ROOT/'docs/review_artifacts/paper_readiness_20261022/build_review.py'
    spec=importlib.util.spec_from_file_location('group_render',renderer);module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module);module.HERE=out;module.render(DOC,out/'report.html','整组容量约束的精确改进空间')
    page,n=re.subn(r'<nav>.*?</nav>','<nav><a href="oracle.json">精确界与全知见证</a> · '
        '<a href="certificates.json">独立证书</a> · <a href="verification.json">核验</a> · '
        '<a href="diagnostic.json">目标变化</a> · <a href="execution.json">命令与资源</a> · '
        '<a href="archive.json">易失临时检查点</a></nav>',(out/'report.html').read_text(),count=1)
    assert n==1;(out/'report.html').write_text(page)
    class Links(HTMLParser):
        def __init__(self):super().__init__();self.paths=[]
        def handle_starttag(self,tag,attrs):
            for k,v in attrs:
                if k in ('href','src'):
                    u=urlsplit(v)
                    if not u.scheme and u.path:self.paths.append(out/unquote(u.path))
    links=Links();links.feed(page)
    for path in links.paths:
        if path!=out/'archive.json':assert path.is_file(),path
    for name in ('prepared','enumerated','optimized','relocated-candidates','repeat-optimized','verified','diagnostic'):
        tree(work/name,'saved_run/'+name)
    if (work/'relocation.json').exists():files['saved_run/relocation.json']=work/'relocation.json'
    for item in read(work/'prepared/truth/manifest.json')['files']:
        file=ROOT/item['file']['path'];assert sha(file)==item['file']['sha256'];files[item['file']['path']]=file
    for file in (DOC,renderer,ROOT/'AGENTS.md',ROOT/'deps.lock.json',ROOT/'docs/development.md',
        ROOT/'data/moe-trace-small/manifest.json',*(Path(__file__).with_name(name) for name in NAMES)):
        files[str(file.relative_to(ROOT))]=file
    tree(out,str(out.relative_to(ROOT)));unpacked=sum(f.stat().st_size for f in files.values());assert unpacked<64*2**20
    target.mkdir();archive=target/'checkpoint.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for name,file in sorted(files.items()):tar.add(file,arcname=name,recursive=False)
    with tarfile.open(archive,'r:gz') as tar:
        assert set(tar.getnames())==set(files) and len(tar.getnames())==len(files)
        for name,file in files.items():assert hashlib.sha256(tar.extractfile(name).read()).hexdigest()==sha(file)
    save(out/'archive.json',dict(path=str(archive),bytes=archive.stat().st_size,sha256=sha(archive),
        storage='tmpfs_volatile',persistent_backup=False,verified_files=len(files),unpacked_file_bytes=unpacked,
        files={name:dict(bytes=file.stat().st_size,sha256=sha(file)) for name,file in sorted(files.items())},
        includes_self_archive_manifest=False,includes_running_packaging_probe=False,includes_binaries=False))
    for path in links.paths:assert path.is_file(),path
    save(out/'review-check.json',dict(html_links_checked=len(links.paths),archive_files_verified=len(files),
        browser_visual_acceptance=False,goal_complete=False))
    assert sum(f.stat().st_size for f in out.iterdir())<2*2**20
    print(json.dumps(dict(files=len(files),archive_bytes=archive.stat().st_size,unpacked_bytes=unpacked,
        archive_storage='tmpfs_volatile',html=str(out/'report.html'))))


if __name__=='__main__':main()
