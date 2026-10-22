"""Independently verify saved modern dependencies, render, and checkpoint inputs."""
import argparse
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
from urllib.parse import unquote,urlsplit

ROOT=Path(__file__).resolve().parents[2]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--checkpoint',required=True,type=Path)
    a=p.parse_args();run,out,target=(p.resolve() for p in (a.run,a.output,a.checkpoint))
    assert not out.exists() and not target.exists()
    assert shutil.disk_usage(target.parent).free>=8*2**30+16*2**20
    summary=json.loads((run/'summary.json').read_text())
    provenance=json.loads((run/'provenance.json').read_text())
    manifest=json.loads((run/'plan/manifest.json').read_text())
    assert sha(run/'plan/plan.json')==manifest['plan_sha256']
    assert sha(run/'input.json')==manifest['input_sha256']
    for source in provenance['files']:
        path=ROOT/source['path'];assert sha(path)==source['sha256']
    for row in provenance['selections']:
        source=next(s for s in provenance['files'] if s['sha256']==row['source_sha256'])
        raw=json.loads((ROOT/source['path']).read_text())
        assert raw[row['source_entry']][str(row['source_layer'])][0]==row['choices']
    plan=json.loads((run/'plan/plan.json').read_text())
    nodes={e['id']:e for e in plan['events']};assert len(nodes)==len(plan['events'])
    successors={name:[] for name in nodes}
    for event in nodes.values():
        for parent in event['deps']:successors[parent].append(event['id'])
    independent={}
    for name in summary['probes']:
        check=json.loads((run/(name+'.json')).read_text())
        blocked=set(check['withheld']);todo=list(blocked)
        while todo:
            for child in successors[todo.pop()]:
                if child not in blocked:blocked.add(child);todo.append(child)
        expected={e['id'] for e in nodes.values() if e['kind']=='application_stage_complete' and e['id'] in blocked}
        assert expected==set(check['blocked_completions'])
        independent[name]=dict(blocked_completions=len(expected),matches=True)
    repeats={}
    for name in ('input.json','plan.json','manifest.json'):
        assert (run/'plan'/name).read_bytes()==(run/'repeat'/name).read_bytes()
        repeats[name]=sha(run/'plan'/name)
    out.mkdir(parents=True)
    for name in ('summary.json','provenance.json','commands.json','execution.json'):
        shutil.copy2(run/name,out/name)
    doc=ROOT/'docs/reviews/现代DeepEP真实路由与连接分流_20261022.md'
    spec=importlib.util.spec_from_file_location('render',ROOT/'docs/review_artifacts/paper_readiness_20261022/build_review.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module.HERE=out
    module.render(doc,out/'report.html','现代DeepEP真实路由与连接分流')
    raw,count=re.subn(r'<nav>.*?</nav>','<nav><a href="summary.json">结果摘要</a> · '
        '<a href="verification.json">独立核查</a> · <a href="archive.json">本地检查点</a></nav>',
        (out/'report.html').read_text(),count=1)
    assert count==1;(out/'report.html').write_text(raw)
    class Links(HTMLParser):
        def __init__(self):super().__init__();self.paths=[]
        def handle_starttag(self,tag,attrs):
            for key,value in attrs:
                if key in ('href','src'):
                    url=urlsplit(value)
                    if not url.scheme and url.path:self.paths.append(out/unquote(url.path))
    links=Links();links.feed(raw)
    for path in links.paths:
        if path.name not in ('archive.json','verification.json'):assert path.exists(),path
    save(out/'verification.json',dict(source_files=4,original_choices_rechecked=16,
        independent_descendant_checks=independent,repeated_outputs=repeats,
        html_local_links=len(links.paths),browser_visual_acceptance=False,packet_execution=False,
        hardware_calibrated=False,goal_complete=False))
    files={}
    for path in run.rglob('*'):
        if path.is_file() and 'repeat' not in path.relative_to(run).parts:
            files['saved_run/'+str(path.relative_to(run))]=path
    for path in out.rglob('*'):
        if path.is_file():files[str(path.relative_to(ROOT))]=path
    for source in provenance['files']:files[source['path']]=ROOT/source['path']
    for path in (doc,Path(__file__).resolve(),ROOT/'experiments/paper_readiness/modern_operations.py',
        ROOT/'experiments/moe_trace_hotspots/results/four_requests_20261018/manifest.json'):
        files[str(path.relative_to(ROOT))]=path
    assert sum(p.stat().st_size for p in files.values())<=16*2**20
    target.mkdir();archive=target/'checkpoint.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for name,path in sorted(files.items()):tar.add(path,arcname=name,recursive=False)
    with tarfile.open(archive,'r:gz') as tar:
        assert set(tar.getnames())==set(files)
        for name,path in files.items():assert hashlib.sha256(tar.extractfile(name).read()).hexdigest()==sha(path)
    save(out/'archive.json',dict(path=str(archive.relative_to(ROOT)),sha256=sha(archive),bytes=archive.stat().st_size,
        verified_files=len(files),files={n:dict(bytes=p.stat().st_size,sha256=sha(p)) for n,p in sorted(files.items())}))
    for path in links.paths:assert path.exists(),path
    print(json.dumps(dict(operations=summary['operations'],events=summary['events'],independent_probes=len(independent),
                         checkpoint_bytes=archive.stat().st_size,verified_files=len(files),packet_execution=False)))


if __name__=='__main__':main()
