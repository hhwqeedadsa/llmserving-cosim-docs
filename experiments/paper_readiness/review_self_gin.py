"""Verify the self-peer correction against originals and frozen historical plans."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tarfile
from urllib.parse import unquote, urlsplit

ROOT=Path(__file__).resolve().parents[2]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,value): p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('run','reference','output','checkpoint'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();run,reference,out,target=(getattr(args,n).resolve() for n in ('run','reference','output','checkpoint'))
    assert not out.exists() and not target.exists() and shutil.disk_usage(target.parent).free>=8*2**30+16*2**20
    summary=json.loads((run/'summary.json').read_text());provenance=json.loads((run/'provenance.json').read_text())
    manifest=json.loads((run/'plan/manifest.json').read_text());assert sha(run/'plan/plan.json')==manifest['plan_sha256']
    plan=json.loads((run/'plan/plan.json').read_text());assert plan['schema_version'].endswith('/2')
    originals={}
    for source in provenance['files']:
        p=ROOT/source['path'];assert sha(p)==source['sha256'];originals[source['sha256']]=json.loads(p.read_text())
    for row in provenance['selections']:
        assert originals[row['source_sha256']][row['source_entry']][str(row['source_layer'])][0]==row['choices']
    # Historical results are immutable input to this comparison, not fixtures.
    archive_meta=json.loads((reference/'archive.json').read_text());archive=ROOT/archive_meta['path']
    assert sha(archive)==archive_meta['sha256'] and archive.stat().st_size<16*2**20
    with tarfile.open(archive) as history:
        member='saved_run/plan/plan.json';data=history.extractfile(member).read(16*2**20+1)
        assert len(data)<=16*2**20 and hashlib.sha256(data).hexdigest()==archive_meta['files'][member]['sha256']
        old=json.loads(data)
    for key in ('events','slots','layout'):assert old[key]==plan[key],key
    assert len(old['operations'])==len(plan['operations'])==486
    changed_path=changed_verb=added_source=0
    for before,after in zip(old['operations'],plan['operations']):
        a,b=deepcopy(before),deepcopy(after)
        same=after['src']==after['dst'];kind=after['kind']
        expected='gin_self' if same and kind in ('expert_count_block','barrier_signal') else 'local' if same else 'rdma'
        assert after['path']==expected
        changed_path+=a['path']!=b['path'];changed_verb+=a['verb']!=b['verb']
        for key in ('path','verb'):a.pop(key);b.pop(key)
        if kind=='expert_count_block':
            assert 'source' not in a and b['source']['element_offset']==64*after['dst']
            b.pop('source');added_source+=1
        assert a==b,after['id']
    assert (changed_path,changed_verb,added_source)==(80,80,64)
    old_summary=json.loads((reference/'summary.json').read_text())
    for key in ('events','operations','symbolic_slots','remote_operation_counts','remote_operand_bytes',
                'dispatch_data_by_declared_connection','combine_data_by_connection_given_unique_slots'):
        assert summary[key]==old_summary[key],key
    # Independent algebra and per-operation classification, not production APIs.
    paths=Counter(o['path'] for o in plan['operations']);assert dict(paths)==summary['operation_paths']
    assert paths['gin_self']==4*5*4 and paths['rdma']+paths['gin_self']==summary['gin_provider_requests']==444
    gin_control=sum(o['kind']!='token_data' and o['path']!='local' for o in plan['operations'])
    assert gin_control==4*(6*4*4-4)==368
    nodes={e['id']:e for e in plan['events']};successors=defaultdict(list);checks={}
    for event in nodes.values():
        for parent in event['deps']:successors[parent].append(event['id'])
    for name in summary['probes']:
        record=json.loads((run/(name+'.json')).read_text());blocked=set(record['withheld']);todo=list(blocked)
        while todo:
            for child in successors[todo.pop()]:
                if child not in blocked:blocked.add(child);todo.append(child)
        expected={e['id'] for e in nodes.values() if e['kind']=='application_stage_complete' and e['id'] in blocked}
        assert expected==set(record['blocked_completions']);checks[name]=len(expected)
    for name in ('input.json','plan.json','manifest.json'):
        assert (run/'plan'/name).read_bytes()==(run/'repeat'/name).read_bytes()
    out.mkdir(parents=True)
    for name in ('summary.json','provenance.json','commands.json','execution.json'):shutil.copy2(run/name,out/name)
    doc=ROOT/'docs/reviews/现代GIN自发请求与网络基线更正_20261022.md'
    spec=importlib.util.spec_from_file_location('report_renderer',ROOT/'docs/review_artifacts/paper_readiness_20261022/build_review.py')
    renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(renderer);renderer.HERE=out
    renderer.render(doc,out/'report.html','现代GIN自发请求与网络基线更正')
    html,count=re.subn(r'<nav>.*?</nav>','<nav><a href="summary.json">摘要</a> · <a href="verification.json">独立核查</a> · <a href="archive.json">检查点</a></nav>',(out/'report.html').read_text(),count=1)
    assert count==1;(out/'report.html').write_text(html)
    class Links(HTMLParser):
        def __init__(self):super().__init__();self.paths=[]
        def handle_starttag(self,tag,attrs):
            for key,value in attrs:
                if key in ('href','src'):
                    url=urlsplit(value)
                    if not url.scheme and url.path:self.paths.append((out/unquote(url.path)).resolve())
    links=Links();links.feed(html)
    for path in links.paths:
        if path.name not in ('verification.json','archive.json'):assert path.exists(),path
    verification=dict(passed=True,source_files=4,original_choices_rechecked=16,historical_archive_sha256=sha(archive),
        changed_paths=changed_path,changed_verbs=changed_verb,added_expert_source_regions=added_source,
        identical_event_DAG=True,identical_slots_and_layout=True,unchanged_historical_cross_rank_results=True,
        gin_controls_independently_counted=gin_control,independent_dependency_probes=checks,
        repeated_outputs_byte_identical=True,html_links=len(links.paths),browser_visual_acceptance=False,
        network_execution=False,hardware_calibrated=False,goal_complete=False)
    save(out/'verification.json',verification)
    files={}
    for p in run.rglob('*'):
        if p.is_file() and 'repeat' not in p.relative_to(run).parts:files['saved_run/'+str(p.relative_to(run))]=p
    for p in out.rglob('*'):
        if p.is_file():files[str(p.relative_to(ROOT))]=p
    for source in provenance['files']:files[source['path']]=ROOT/source['path']
    for p in (doc,Path(__file__).resolve(),ROOT/'experiments/paper_readiness/modern_operations.py'):
        files[str(p.relative_to(ROOT))]=p
    assert sum(p.stat().st_size for p in files.values())<16*2**20
    target.mkdir();checkpoint=target/'checkpoint.tar.gz'
    with tarfile.open(checkpoint,'w:gz') as result:
        for name,p in files.items():result.add(p,arcname=name,recursive=False)
    with tarfile.open(checkpoint) as check:
        for name,p in files.items():assert hashlib.sha256(check.extractfile(name).read()).hexdigest()==sha(p)
    save(out/'archive.json',dict(path=str(checkpoint.relative_to(ROOT)),sha256=sha(checkpoint),bytes=checkpoint.stat().st_size,
        verified_files=len(files),files={n:dict(bytes=p.stat().st_size,sha256=sha(p)) for n,p in files.items()}))
    for path in links.paths:assert path.exists(),path
    print(json.dumps(dict(passed=True,paths=dict(paths),gin_controls=gin_control,historical_fields_verified=True,
                         probes=checks,checkpoint_bytes=checkpoint.stat().st_size)))


if __name__=='__main__':main()
