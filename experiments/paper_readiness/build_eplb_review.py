"""Render the fixed five-condition baseline, retaining primary sources and failed acquisition."""
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
DOC=ROOT/'docs/reviews/拓扑感知近邻与分层EPLB强基线_20261022.md'
NAMES=('prepare_placement_baselines.py','evaluate_placement_baselines.py','plan_eplb_groups.py',
       'check_eplb_groups.py','collect_topology_baselines.py','build_eplb_review.py','eplb_group_protocol.md','README.md')


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for flag in ('work','output','checkpoint'):p.add_argument('--'+flag,type=Path,required=True)
    p.add_argument('--source-group',action='append',required=True,help='NAME=PATH; discovery/primary/paper/commits/trees/code')
    p.add_argument('--source-probe',type=Path,action='append',required=True)
    a=p.parse_args();work=a.work.resolve();out=a.output.resolve();target=a.checkpoint.resolve()
    groups={k:Path(v).resolve() for k,v in (s.split('=',1) for s in a.source_group)}
    assert set(groups)=={'discovery','primary','paper','commits','trees','code'}
    assert out.is_relative_to(ROOT) and target.is_relative_to(ROOT) and not out.exists() and not target.exists()
    assert shutil.disk_usage(target.parent).free>=8*2**30+64*2**20
    verified=read(work/'verified/verification.json');assert verified['completed'] and verified['repeat_files']==2
    assert verified['summary_sha256']==sha(work/'evaluated/summary.json')
    assert verified['csv_sha256']==sha(work/'evaluated/windows.csv')
    assert verified['checker_sha256']==sha(Path(__file__).with_name('check_eplb_groups.py'))
    summary=read(work/'evaluated/summary.json')
    assert [r['direct_copies'] for r in summary['conditions']]==[69409,80596,79515,70123,69469]
    assert [r['compute_max']['p95'] for r in summary['conditions']]==[15,14,14,15,15]
    collections={};total=0;source_records=[]
    for name,folder in groups.items():
        collection=read(folder/'collection.json');collections[name]=collection
        for row in collection['requests']:
            if row['bytes']:
                file=folder/row['name'];assert file.stat().st_size==row['bytes'] and sha(file)==row['sha256']
            total+=row['bytes']
    assert total<=16*2**20
    failed=next(r for r in collections['primary']['requests'] if r['name']=='exflow-v2.pdf')
    assert failed['exit_code']==63 and failed['bytes']==0
    for name,pdf,expected in (
        ('ta-moe','ta-moe.pdf','3350b1d868ac0a36fb54e5a52662b7fda801acb0eeb0afccfbf3223db1f227e1'),
        ('ta-moe-supplement','ta-moe-supplement.pdf','a7d2b3a96ea637f7401932483fde1f171c82cd3aed08425f281b49d022ff6ab9')):
        file=groups['paper']/pdf;assert sha(file)==expected
        text=file.with_suffix('.txt');assert text.is_file()
        source_records.append(dict(id=name,pdf_sha256=sha(file),text_sha256=sha(text),
            archive_member='sources/paper/'+pdf,extraction_command=['pdftotext','-layout',str(file),str(text)]))
    ta_text=(groups['paper']/'ta-moe.txt').read_text()
    for needle in ('4.1 Communication Model','4.3   Routing Strategy','topology-aware auxiliary loss'):
        assert needle in ta_text
    files={}
    def tree(folder,prefix):
        for file in folder.rglob('*'):
            if file.is_file():assert not file.is_symlink();files[prefix+'/'+str(file.relative_to(folder))]=file
    probes=[]
    for name in ('prepare','plan','evaluate','repeat-plan','check'):
        folder=work/(name+'-probe');report=read(folder/'probe.json')
        assert report['status']=='completed' and not report['remaining_live_group_pids'];probes.append(report)
        tree(folder,'saved_run/probes/'+name)
    source_probes=[];assert len(a.source_probe)==6
    for i,folder in enumerate(a.source_probe):
        report=read(folder/'probe.json');assert report['status']=='completed' and not report['remaining_live_group_pids']
        source_probes.append(report);tree(folder,'saved_run/source_probes/'+str(i))
    out.mkdir(parents=True)
    for file,name in ((work/'evaluated/summary.json','summary.json'),(work/'verified/paired.json','paired.json'),
        (work/'verified/verification.json','verification.json'),(work/'verified/source_identities.json','source_identities.json')):
        shutil.copy2(file,out/name)
    save(out/'sources.json',dict(collections=collections,total_retained_response_bytes=total,primary_documents=source_records,
        downloaded_code_executed=False,paper_source='Official NeurIPS 2022 full text and supplement',
        exflow_scope='arXiv v2 author metadata/abstract only; PDF refused by size cap, not retried',
        eplb_scope='Fixed source read and blob verified; independent no-replica/one-active-GPU-per-node count adapter',
        ta_scope='Fixed gate read and blob verified; training auxiliary loss not run on frozen routes',
        literature_search_exhaustive=False))
    save(out/'execution.json',dict(platform_base_commit=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,
        capture_output=True,text=True,check=True,timeout=10).stdout.strip(),probes=probes,source_probes=source_probes,
        scripts_sha256={name:sha(Path(__file__).with_name(name)) for name in NAMES},
        group_weight_tie_layers=read(work/'planned/packing.json')['group_weight_tie_layers'],
        upstream_eplb_runtime_executed=False,new_network_executed=False,gpu_executed=False,goal_complete=False))
    renderer=ROOT/'docs/review_artifacts/paper_readiness_20261022/build_review.py'
    spec=importlib.util.spec_from_file_location('eplb_render',renderer);mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod);mod.HERE=out;mod.render(DOC,out/'report.html','拓扑感知近邻与分层EPLB强基线')
    page,n=re.subn(r'<nav>.*?</nav>','<nav><a href="summary.json">五条件结果</a> · '
        '<a href="paired.json">窗口配对</a> · <a href="verification.json">独立核验</a> · '
        '<a href="sources.json">来源与失败</a> · <a href="execution.json">命令与资源</a> · '
        '<a href="archive.json">本地归档</a></nav>',(out/'report.html').read_text(),count=1)
    assert n==1;(out/'report.html').write_text(page)
    class Links(HTMLParser):
        def __init__(self):super().__init__();self.paths=[]
        def handle_starttag(self,t,attrs):
            for k,v in attrs:
                if k in ('href','src'):
                    u=urlsplit(v)
                    if not u.scheme and u.path:self.paths.append(out/unquote(u.path))
    links=Links();links.feed(page)
    for path in links.paths:
        if path!=out/'archive.json':assert path.is_file(),path
    for name in ('prepared','planned','evaluated','repeat-planned','relocated-observed','verified'):
        tree(work/name,'saved_run/'+name)
    if (work/'relocation.json').exists():files['saved_run/relocation.json']=work/'relocation.json'
    for name,folder in groups.items():tree(folder,'sources/'+name)
    for item in read(ROOT/'data/moe-trace-small/manifest.json')['files']:
        file=ROOT/item['file']['path'];assert sha(file)==item['file']['sha256'];files[item['file']['path']]=file
    for file in (DOC,renderer,ROOT/'AGENTS.md',ROOT/'deps.lock.json',ROOT/'docs/development.md',
        ROOT/'data/moe-trace-small/manifest.json',ROOT/'docs/review_artifacts/prefill_placement_20261022/placements.json',
        *(Path(__file__).with_name(name) for name in NAMES)):
        files[str(file.relative_to(ROOT))]=file
    tree(out,str(out.relative_to(ROOT)));unpacked=sum(f.stat().st_size for f in files.values());assert unpacked<64*2**20
    target.mkdir();archive=target/'checkpoint.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for name,file in sorted(files.items()):tar.add(file,arcname=name,recursive=False)
    with tarfile.open(archive,'r:gz') as tar:
        assert set(tar.getnames())==set(files) and len(tar.getnames())==len(files)
        for name,file in files.items():assert hashlib.sha256(tar.extractfile(name).read()).hexdigest()==sha(file)
    save(out/'archive.json',dict(path=str(archive.relative_to(ROOT)),bytes=archive.stat().st_size,sha256=sha(archive),
        verified_files=len(files),unpacked_file_bytes=unpacked,
        files={name:dict(bytes=file.stat().st_size,sha256=sha(file)) for name,file in sorted(files.items())},
        includes_self_archive_manifest=False,includes_running_packaging_probe=False,includes_binaries=False))
    for path in links.paths:assert path.is_file(),path
    save(out/'review-check.json',dict(html_links_checked=len(links.paths),archive_files_verified=len(files),
        browser_visual_acceptance=False,goal_complete=False))
    print(json.dumps(dict(archive_files=len(files),unpacked_bytes=unpacked,archive_bytes=archive.stat().st_size,html=str(out/'report.html'))))


if __name__=='__main__':main()
