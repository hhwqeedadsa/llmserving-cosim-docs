"""Render the verified data-only study and hash-check its bounded local archive."""
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
from urllib.parse import unquote, urlsplit

ROOT=Path(__file__).resolve().parents[2]
DOC=ROOT/'docs/reviews/Prefill放置数据传输与实际排队_20261022.md'
NAMES=('prepare_dispatch_network.py','run_dispatch_network.py','analyze_dispatch_network.py',
       'check_dispatch_network.py','build_dispatch_network_review.py','dispatch_network.json','dispatch_network_protocol.md')


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('prepared','run','analysis','repeat_analysis','verified','output','checkpoint'):
        p.add_argument('--'+name.replace('_','-'),type=Path,required=True)
    p.add_argument('--probe',type=Path,action='append',required=True)
    p.add_argument('--source-root',type=Path,default=ROOT)
    a=p.parse_args();out=a.output.resolve();target=a.checkpoint.resolve()
    assert out.is_relative_to(ROOT) and target.is_relative_to(ROOT) and not out.exists() and not target.exists()
    assert shutil.disk_usage(target.parent).free>=8*2**30+64*2**20
    verification=json.loads((a.verified/'verification.json').read_text())
    assert verification['passed'] and verification['packet_lineages']==7320 and verification['switch_ecmp_choices']==32040
    assert verification['summary_sha256']==sha(a.analysis/'summary.json')
    probes=[]
    for path in a.probe:
        report=json.loads((path/'probe.json').read_text());assert report['status'] in ('completed','child_failed')
        assert not report['remaining_live_group_pids'];probes.append(dict(directory=str(path),**report))
    assert len(probes)>=5 and sum(r['status']=='child_failed' for r in probes)<=1
    out.mkdir(parents=True)
    for source,name in ((a.analysis/'summary.json','summary.json'),(a.analysis/'rounds.json','rounds.json'),
        (a.analysis/'ports.csv','ports.csv'),(a.verified/'verification.json','verification.json'),
        (a.prepared/'truth/provenance.json','provenance.json'),(a.prepared/'truth/manifest.json','manifest.json')):
        shutil.copy2(source,out/name)
    save(out/'execution.json',dict(platform_base_commit=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,
        check=True,capture_output=True,text=True,timeout=10).stdout.strip(),
        public_execution=json.loads((a.run/'execution.json').read_text()),
        commands=json.loads((a.run/'commands.json').read_text()),probes=probes,
        scripts_sha256={name:sha(ROOT/'experiments/paper_readiness'/name) for name in NAMES},
        failed_checker=('Local variable load shadowed the JSON reader; failed before loading inputs. Renamed; failure retained. No simulation rerun.'
            if any(r['status']=='child_failed' for r in probes) else None),
        routing_extension='Additional read-only hash-choice and post-dequeue data-queue audit of existing packets; no added network cases.',
        network_executed=True,gpu_executed=False,modern_GIN_executed=False,hardware_calibrated=False,goal_complete=False))
    renderer=ROOT/'docs/review_artifacts/paper_readiness_20261022/build_review.py'
    spec=importlib.util.spec_from_file_location('dispatch_render',renderer);module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module);module.HERE=out;module.render(DOC,out/'report.html','Prefill放置的数据传输与实际排队')
    html,n=re.subn(r'<nav>.*?</nav>','<nav><a href="summary.json">六组结果</a> · '
        '<a href="rounds.json">每轮完成路径</a> · <a href="ports.csv">出口统计</a> · '
        '<a href="verification.json">独立核验</a> · <a href="execution.json">命令与资源</a> · '
        '<a href="archive.json">本地完整归档</a></nav>',(out/'report.html').read_text(),count=1)
    assert n==1;(out/'report.html').write_text(html)
    class Links(HTMLParser):
        def __init__(self):super().__init__();self.paths=[]
        def handle_starttag(self,tag,attrs):
            for key,value in attrs:
                if key in ('href','src'):
                    parsed=urlsplit(value)
                    if not parsed.scheme and parsed.path:self.paths.append(out/unquote(parsed.path))
    links=Links();links.feed(html)
    for path in links.paths:
        if path!=out/'archive.json':assert path.is_file(),path
    files={}
    def tree(path,prefix):
        for file in path.rglob('*'):
            if file.is_file():
                assert not file.is_symlink();files[prefix+'/'+str(file.relative_to(path))]=file
    for field in ('prepared','run','analysis','repeat_analysis','verified'):tree(getattr(a,field),'saved_run/'+field)
    for i,path in enumerate(a.probe):tree(path,'saved_run/probes/'+str(i))
    manifest=json.loads((a.prepared/'truth/manifest.json').read_text())
    for row in manifest['files']:
        name=row['file']['path'];file=a.source_root.resolve()/name
        assert file.is_relative_to(a.source_root.resolve()) and sha(file)==row['file']['sha256'];files[name]=file
    for path in (DOC,renderer,ROOT/'AGENTS.md',ROOT/'deps.lock.json',ROOT/'docs/development.md',
        ROOT/'experiments/paper_readiness/README.md',ROOT/'docs/review_artifacts/prefill_placement_20261022/placements.json',
        *(ROOT/'experiments/paper_readiness'/name for name in NAMES),
        *(ROOT/name for name in verification['platform_source_sha256'])):
        files[str(path.relative_to(ROOT))]=path
    tree(out,str(out.relative_to(ROOT)))
    total=sum(file.stat().st_size for file in files.values());assert total<=64*2**20
    target.mkdir();archive=target/'checkpoint.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for name,file in sorted(files.items()):tar.add(file,arcname=name,recursive=False)
    with tarfile.open(archive,'r:gz') as tar:
        assert set(tar.getnames())==set(files) and len(tar.getnames())==len(files)
        for name,file in files.items():assert hashlib.sha256(tar.extractfile(name).read()).hexdigest()==sha(file)
    save(out/'archive.json',dict(path=str(archive.relative_to(ROOT)),bytes=archive.stat().st_size,sha256=sha(archive),
        verified_files=len(files),unpacked_file_bytes=total,
        files={name:dict(bytes=file.stat().st_size,sha256=sha(file)) for name,file in sorted(files.items())},
        includes_self_archive_manifest=False,includes_running_packaging_probe=False,includes_native_binaries=False,
        native_library_identity='verification.json hashes; rebuild only locked source with public rdma-build when needed'))
    for path in links.paths:assert path.is_file(),path
    save(out/'review-check.json',dict(html_local_links_checked=len(links.paths),archive_files_hash_verified=len(files),
        browser_visual_acceptance=False,full_inference_validated=False,hardware_calibrated=False,goal_complete=False))
    assert sum(file.stat().st_size for file in out.iterdir())<16*2**20
    print(json.dumps(dict(archived_files=len(files),archive_bytes=archive.stat().st_size,html=str(out/'report.html'))))


if __name__=='__main__':main()
