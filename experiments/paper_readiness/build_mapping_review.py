"""Render and archive the verified group-to-rank baseline and its negative results."""
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
DOC=ROOT/'docs/reviews/专家分组与节点映射强基线_20261022.md'
NAMES=('collect_mapping_holdout.py','mapping_baseline.py','check_mapping_baseline.py',
       'mapping_baseline_bound.py','check_mapping_bound.py','build_mapping_review.py','mapping_baseline_protocol.md')


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',action='append',required=True,help='NAME=PATH for prepared/planned/evaluation/repeat_planned/verification/bound/bound_verification/holdout')
    p.add_argument('--probe',type=Path,action='append',required=True)
    for name in ('reference_result','output','checkpoint'):p.add_argument('--'+name.replace('_','-'),type=Path,required=True)
    a=p.parse_args();stage={k:Path(v).resolve() for k,v in (r.split('=',1) for r in a.stage)}
    assert set(stage)=={'prepared','planned','evaluation','repeat_planned','verification','bound','bound_verification','holdout'}
    out=a.output.resolve();target=a.checkpoint.resolve();assert out.is_relative_to(ROOT) and target.is_relative_to(ROOT)
    assert not out.exists() and not target.exists() and shutil.disk_usage(target.parent).free>=8*2**30+64*2**20
    verified=load(stage['verification']/'verification.json');bound_check=load(stage['bound_verification']/'verification.json')
    assert verified['passed'] and bound_check['passed']
    assert verified['summary_sha256']==sha(stage['evaluation']/'summary.json')==bound_check['summary_sha256']
    assert bound_check['bound_sha256']==sha(stage['bound']/'bound.json')
    assert verified['reference_result_sha256']==sha(a.reference_result)
    probes=[]
    for directory in a.probe:
        report=load(directory/'probe.json');assert report['status'] in ('completed','child_failed')
        assert not report['remaining_live_group_pids'];probes.append(dict(directory=str(directory),**report))
    out.mkdir(parents=True)
    for source,name in ((stage['evaluation']/'summary.json','summary.json'),(stage['verification']/'verification.json','verification.json'),
        (stage['bound_verification']/'verification.json','bound-verification.json'),(stage['holdout']/'collection.json','collection.json')):
        shutil.copy2(source,out/name)
    bound=load(stage['bound']/'bound.json')
    save(out/'bound-summary.json',dict(results=[{k:v for k,v in r.items() if k!='by_layer'} for r in bound['results']],
        full_bound_sha256=sha(stage['bound']/'bound.json'),full_bound_archive_member='saved_run/bound/bound.json',
        oracle_reads_future=True,oracle_is_deployable=False,post_result_mechanism_check=True))
    save(out/'execution.json',dict(platform_base_commit=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,check=True,
        capture_output=True,text=True,timeout=10).stdout.strip(),probes=probes,
        scripts_sha256={name:sha(ROOT/'experiments/paper_readiness'/name) for name in NAMES},
        collection_attempt_source_sha256=sha(stage['holdout']/'collector-attempt.py') if (stage['holdout']/'collector-attempt.py').exists() else None,
        collection_attempt='Original socket timeout was not an end-to-end deadline: request took 40.070s against intended20s. Failure and exact attempted source retained. Corrected child deadline not remotely retried.',
        newly_executed_topology_cli=1,new_network_executed=False,gpu_executed=False,hardware_calibrated=False,
        independent_holdout_requests=verified['independent_holdout_requests'],goal_complete=False))
    renderer=ROOT/'docs/review_artifacts/paper_readiness_20261022/build_review.py'
    spec=importlib.util.spec_from_file_location('mapping_render',renderer);module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module);module.HERE=out;module.render(DOC,out/'report.html','专家分组与节点映射强基线')
    html,n=re.subn(r'<nav>.*?</nav>','<nav><a href="summary.json">九组结果</a> · '
        '<a href="bound-summary.json">覆盖下界与未来oracle</a> · <a href="verification.json">独立核验</a> · '
        '<a href="bound-verification.json">动态规划交叉核查</a> · <a href="collection.json">新数据采集失败</a> · '
        '<a href="execution.json">命令与资源</a> · <a href="archive.json">本地完整归档</a></nav>',
        (out/'report.html').read_text(),count=1);assert n==1;(out/'report.html').write_text(html)
    class Links(HTMLParser):
        def __init__(self):super().__init__();self.paths=[]
        def handle_starttag(self,tag,attrs):
            for key,value in attrs:
                if key in ('href','src'):
                    u=urlsplit(value)
                    if not u.scheme and u.path:self.paths.append(out/unquote(u.path))
    links=Links();links.feed(html)
    for path in links.paths:
        if path!=out/'archive.json':assert path.is_file(),path
    files={}
    def tree(directory,prefix):
        for file in directory.rglob('*'):
            if file.is_file():assert not file.is_symlink();files[prefix+'/'+str(file.relative_to(directory))]=file
    for name,path in stage.items():tree(path,'saved_run/'+name)
    for i,path in enumerate(a.probe):tree(path,'saved_run/probes/'+str(i))
    for group in load(stage['prepared']/'truth/sources.json')['groups']:
        for source in group:
            path=Path(source['path']);assert sha(path)==source['sha256']
            name=str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else 'downloaded_holdout/'+path.name
            files[name]=path
    files['reference_packet_result.json']=a.reference_result
    for path in (DOC,renderer,ROOT/'AGENTS.md',ROOT/'deps.lock.json',ROOT/'docs/development.md',
        ROOT/'experiments/paper_readiness/README.md',ROOT/'data/moe-trace-small/manifest.json',
        ROOT/'docs/review_artifacts/prefill_placement_20261022/placements.json',
        *(ROOT/'experiments/paper_readiness'/name for name in NAMES)):
        files[str(path.relative_to(ROOT))]=path
    tree(out,str(out.relative_to(ROOT)));total=sum(p.stat().st_size for p in files.values());assert total<64*2**20
    target.mkdir();archive=target/'checkpoint.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for name,path in sorted(files.items()):tar.add(path,arcname=name,recursive=False)
    with tarfile.open(archive,'r:gz') as tar:
        assert len(tar.getnames())==len(files) and set(tar.getnames())==set(files)
        for name,path in files.items():assert hashlib.sha256(tar.extractfile(name).read()).hexdigest()==sha(path)
    save(out/'archive.json',dict(path=str(archive.relative_to(ROOT)),bytes=archive.stat().st_size,sha256=sha(archive),
        verified_files=len(files),unpacked_file_bytes=total,files={name:dict(bytes=path.stat().st_size,sha256=sha(path)) for name,path in sorted(files.items())},
        includes_self_archive_manifest=False,includes_running_packaging_probe=False,includes_binaries=False))
    for path in links.paths:assert path.is_file(),path
    save(out/'review-check.json',dict(html_local_links_checked=len(links.paths),archive_files_hash_verified=len(files),
        browser_visual_acceptance=False,hardware_calibrated=False,goal_complete=False))
    print(json.dumps(dict(archived_files=len(files),archive_bytes=archive.stat().st_size,html=str(out/'report.html'))))


if __name__=='__main__':main()
