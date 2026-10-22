"""Archive a verified, fixed routing-constraint study and render its Chinese review."""
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
DOC=ROOT/'docs/reviews/分组路由约束与网络结论修正_20261022.md'
NAMES=('routing_constraints.py','check_routing_constraints.py','build_routing_review.py',
       'collect_placement_sources.py','collect_mapping_holdout.py','routing_constraint_protocol.md','README.md')


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


class Text(HTMLParser):
    def __init__(self):super().__init__();self.skip=0;self.parts=[]
    def handle_starttag(self,t,a):
        if t in ('script','style'):self.skip+=1
        if t in ('p','h1','h2','h3','li'):self.parts.append('\n')
    def handle_endtag(self,t):
        if t in ('script','style'):self.skip-=1
    def handle_data(self,d):
        if not self.skip:self.parts.append(d)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for flag in ('work','old-observed','sources','source-probe','holdout-failure','output','checkpoint'):
        p.add_argument('--'+flag,type=Path,required=True)
    p.add_argument('--reference-config',type=Path,action='append',required=True)
    a=p.parse_args();out=a.output.resolve();target=a.checkpoint.resolve();work=a.work.resolve()
    assert out.is_relative_to(ROOT) and target.is_relative_to(ROOT) and not out.exists() and not target.exists()
    assert shutil.disk_usage(target.parent).free>=8*2**30+64*2**20
    verified=read(work/'verified/verification.json');summary=read(work/'analyzed/summary.json')
    assert verified['completed'] and verified['repeat_files']==12
    assert verified['summary_sha256']==sha(work/'analyzed/summary.json')
    assert verified['windows_sha256']==sha(work/'analyzed/windows.csv')
    assert verified['checker_sha256']==sha(Path(__file__).with_name('check_routing_constraints.py'))
    conditions={r['condition']:r for r in summary['conditions']}
    assert [conditions['cap4_'+str(s)]['direct_copies'] for s in (2026102201,2026102202,2026102203)]==[68733,68651,68672]
    assert [conditions['swap_'+str(s)]['cap_violations'] for s in (2026102201,2026102202,2026102203)]==[23594,23717,23450]
    assert conditions['original']['direct_copies']==69409
    files={}
    def tree(folder,prefix):
        for path in folder.rglob('*'):
            if path.is_file():assert not path.is_symlink();files[prefix+'/'+str(path.relative_to(folder))]=path
    probes=[]
    for name in ('prepare','analyze','repeat-prepare','check'):
        folder=work/(name+'-probe');report=read(folder/'probe.json')
        assert report['status']=='completed' and not report['remaining_live_group_pids'];probes.append(report)
        tree(folder,'saved_run/probes/'+name)
    collection=read(a.sources/'collection.json')
    html=a.sources/'moetuner-v1.html';assert sha(html)=='e7291b909720505d938a8d30d1bdf8fa2a3f8486491277bbaf20d28de346412b'
    text=Text();text.feed(html.read_text());moetuner=''.join(text.parts)
    if (a.sources/'moetuner-v1.txt').exists():assert moetuner==(a.sources/'moetuner-v1.txt').read_text()
    cached=ROOT/'data/research/system_innovation_20261021';patterns=cached/'patterns.txt'
    assert sha(patterns)=='1b356cd1b4725c540110ef745e9f2702cfb8ca716131d29f4276f877da912168'
    assert sha(cached/'patterns.html')=='ea29c8cc1a0c1e8cfa815af439bf8440f73d989e07e7685899712afbc54a5387'
    # Short exact locators; the rest of the comparison is paraphrased in the review.
    locators=[]
    for ident,body,needles in (
        ('moetuner_v1',moetuner,('token routing dependency across layers','4EP-2TP and 4EP-4TP','tolerance of 0.025')),
        ('patterns_v5',patterns.read_text(),('separation also introduces cross-unit communication overhead','depends on system topology and batch size'))):
        for needle in needles:
            position=body.index(needle)
            locators.append(dict(source=ident,exact_locator=needle,line=body[:position].count('\n')+1))
    refs=[]
    assert len(a.reference_config)==2
    for path in a.reference_config:
        config=read(path);fields={k:config[k] for k in ('n_group','topk_group','num_experts_per_tok','topk_method')}
        assert fields==dict(n_group=8,topk_group=4,num_experts_per_tok=8,topk_method='noaux_tc')
        expert_key='n_routed_experts' if 'n_routed_experts' in config else 'num_routed_experts'
        assert config[expert_key]==256;fields[expert_key]=config[expert_key]
        refs.append(dict(name=path.name,sha256=sha(path),fields=fields,trace_collector_identity_proven=False))
        files['reference_configs/'+path.name]=path
    out.mkdir(parents=True)
    for src,name in ((work/'analyzed/summary.json','summary.json'),(work/'verified/verification.json','verification.json'),
        (work/'verified/placement_groups.json','placement_groups.json'),(a.sources/'collection.json','collection.json')):
        shutil.copy2(src,out/name)
    save(out/'sources.json',dict(primary_sources=[dict(id='moetuner_v1',url='https://arxiv.org/html/2502.06643v1',
        sha256=sha(html),bytes=html.stat().st_size,text_sha256=hashlib.sha256(moetuner.encode()).hexdigest(),
        archive_member='sources/moetuner-v1.html',publication_status='arXiv v1; no venue status inferred'),
        dict(id='patterns_v5',url='https://arxiv.org/html/2510.05497v5',sha256=sha(cached/'patterns.html'),
        text_sha256=sha(patterns),archive_member='sources/patterns.html')],locators=locators,
        supplemental_configs=refs,collector_model_revision=None,logical32_groups='declared reference hypothesis',
        unavailable='HF model metadata timed out with 0 bytes; no retry and no historical collector revision recovered',
        literature_search_exhaustive=False,moetuner_optimizer_reproduced=False))
    source_probe=read(a.source_probe/'probe.json');assert source_probe['status']=='completed'
    save(out/'execution.json',dict(platform_base_commit=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,
        capture_output=True,text=True,check=True,timeout=10).stdout.strip(),probes=probes,source_probe=source_probe,
        source_collection=collection,scripts_sha256={name:sha(Path(__file__).with_name(name)) for name in NAMES},
        snapshot_audit='Old artifact hashes checked before generation; old counts retained, not rewritten',
        holdout_collector_correction='mmlu has subject directories: use recursive=true, numeric basename/path ordering, reject duplicate basenames; not remotely retried',
        network_executed=False,gpu_executed=False,hardware_calibrated=False,goal_complete=False))
    renderer=ROOT/'docs/review_artifacts/paper_readiness_20261022/build_review.py'
    spec=importlib.util.spec_from_file_location('routing_render',renderer);module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module);module.HERE=out;module.render(DOC,out/'report.html','分组路由约束与网络结论修正')
    page,n=re.subn(r'<nav>.*?</nav>','<nav><a href="summary.json">14条件数据</a> · '
        '<a href="verification.json">独立核验</a> · <a href="sources.json">一手来源</a> · '
        '<a href="execution.json">命令与资源</a> · <a href="archive.json">本地归档</a></nav>',
        (out/'report.html').read_text(),count=1);assert n==1;(out/'report.html').write_text(page)
    class Links(HTMLParser):
        def __init__(self):super().__init__();self.paths=[]
        def handle_starttag(self,tag,attrs):
            for key,value in attrs:
                if key in ('href','src'):
                    u=urlsplit(value)
                    if not u.scheme and u.path:self.paths.append(out/unquote(u.path))
    links=Links();links.feed(page)
    for path in links.paths:
        if path!=out/'archive.json':assert path.is_file(),path
    for name in ('prepared','analyzed','repeat-prepared','verified'):tree(work/name,'saved_run/'+name)
    tree(a.old_observed,'old_observed');tree(a.sources,'sources');tree(a.source_probe,'saved_run/source_probe')
    tree(a.holdout_failure,'prior_failed_holdout')
    for name in ('patterns.txt','patterns.html'):files['sources/'+name]=cached/name
    manifest=read(ROOT/'data/moe-trace-small/manifest.json')
    for item in manifest['files']:
        path=ROOT/item['file']['path'];assert sha(path)==item['file']['sha256'];files[item['file']['path']]=path
    for path in (DOC,renderer,ROOT/'AGENTS.md',ROOT/'deps.lock.json',ROOT/'docs/development.md',
        ROOT/'data/moe-trace-small/manifest.json',ROOT/'docs/review_artifacts/route_controls_20261022/summary.json',
        ROOT/'docs/review_artifacts/prefill_placement_20261022/placements.json',
        *(Path(__file__).with_name(name) for name in NAMES)):
        files[str(path.relative_to(ROOT))]=path
    tree(out,str(out.relative_to(ROOT)));total=sum(path.stat().st_size for path in files.values());assert total<64*2**20
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
    print(json.dumps(dict(archived_files=len(files),unpacked_bytes=total,archive_bytes=archive.stat().st_size,html=str(out/'report.html'))))


if __name__=='__main__':main()
