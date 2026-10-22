"""Build and verify the real-route operation review; save a bounded local checkpoint."""
import argparse
from collections import defaultdict, deque
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tarfile
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        self.links += [v for k,v in attrs if k in ('href','src')]


def source_evidence():
    previous = json.loads((ROOT/'docs/review_artifacts/deepep_semantics_20261022/audit.json').read_text())
    fixed = next(s for s in previous['sources'] if s['path'] == 'csrc/kernels/ibgda_device.cuh')
    catalog = json.loads((ROOT/'docs/review_artifacts/system_innovation_20261021/sources.json').read_text())
    newer = next(s for s in catalog['sources'] if s['id'] == 'deepep_v2')
    selected = []
    for source, key in [(fixed,'local_snapshot'),(newer,'text_snapshot')]:
        path = ROOT/source[key]
        raw = path.read_bytes()
        assert sha(path) == source['sha256']
        blob = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        assert blob == source['git_blob_sha']
        selected.append(dict(path=str(path.relative_to(ROOT)),sha256=sha(path),git_blob_sha=blob,
            revision=source.get('commit'),
            immutable_blob_api='https://api.github.com/repos/deepseek-ai/DeepEP/git/blobs/'+blob,
            scope='fixed source' if key=='local_snapshot' else 'fixed README blob only; kernel not audited'))
    for name in ('backend/rdma/fabric.cc',
                 'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3/src/point-to-point/model/rdma-hw.cc'):
        selected.append(dict(path=name,sha256=sha(ROOT/name),scope='current locked model path'))
    excerpts = []
    for item in selected:
        path = item['path']
        lines = (ROOT/path).read_text().splitlines()
        if path.endswith('ibgda_device.cuh'):
            anchors = [('ctrl_seg.opmod_idx_opcode = HtoBE32(MLX5_OPCODE_ATOMIC_MASKED_FA',11),
                       ('ibgda_write_amo_add_wqe(qp, value, reinterpret_cast<uint64_t>(qp->ibuf.buf)',2)]
        elif 'deepep_v2.txt' in path:
            anchors = [('Fully remove V1',1),('**V2 release**',1)]
        elif path.endswith('rdma-hw.cc'):
            anchors = [('Ptr<Packet> RdmaHw::GetNxtPacket(',19),('int RdmaHw::Receive(Ptr<Packet>',12)]
        else:
            anchors = [('void Fabric::Send(uint64_t id,',1),('drivers_[f.src]->AddQueuePair(',2)]
        for needle, count in anchors:
            positions = [i for i,line in enumerate(lines) if needle in line]
            assert len(positions) == 1, needle
            begin = positions[0]
            excerpts.append(dict(path=path,first_line=begin+1,last_line=begin+count,
                                 excerpt='\n'.join(lines[begin:begin+count])))
    return dict(sources=selected,excerpts=excerpts,
                caution='No atomic wire cost, NIC completion timing, modern kernel semantics or paper novelty is proved')


def independent_closure(run, plan):
    children = defaultdict(list)
    nodes = {e['id']:e for e in plan['events']}
    assert len(nodes) == len(plan['events'])
    for event in nodes.values():
        for parent in event['deps']:
            assert parent in nodes
            children[parent].append(event['id'])
    checked = {}
    for name in ('normal','zero_count','flag','sender_done'):
        probe = json.loads((run/(name+'.json')).read_text())
        blocked = set(probe['withheld'])
        pending = deque(blocked)
        while pending:
            for child in children[pending.popleft()]:
                if child not in blocked:
                    blocked.add(child)
                    pending.append(child)
        returns = {e['id'] for e in nodes.values() if e['kind']=='kernel_complete'}
        assert set(probe['blocked_kernel_completions']) == returns & blocked
        assert set(probe['reachable_kernel_completions']) == returns-blocked
        checked[name] = len(returns & blocked)
    contexts = set()
    for op in plan['operations']:
        assert op['path'] == ('local' if op['src']==op['dst'] else 'rdma')
        key = [op['src'],op['dst'],op['expert']%64] if op['src']!=op['dst'] else None
        assert op['qp_key'] == key
        if key:
            contexts.add(tuple(key))
    assert len(contexts) == 768
    return dict(reverse_reachability_probes=checked,logical_contexts=768,
                context_and_path_operations_checked=len(plan['operations']))


def checkpoint(run, review, evidence, target):
    target = target.resolve()
    target.relative_to(ROOT)
    assert not target.exists() and shutil.disk_usage(target.parent).free >= 8*2**30+32*2**20
    files = {}

    def add(path,name=None):
        path = path.resolve()
        name = name or str(path.relative_to(ROOT))
        assert path.is_file() and name not in files and not Path(name).is_absolute() and '..' not in Path(name).parts
        files[name]=path

    for path in run.rglob('*'):
        if path.is_file() and path.relative_to(run).parts[0] != 'repeat':
            add(path,'saved_run/'+str(path.relative_to(run)))
    provenance = json.loads((run/'provenance.json').read_text())
    manifest = ROOT/provenance['manifest']['path']
    add(manifest)
    for source in json.loads(manifest.read_text())['files']:
        add(ROOT/source['file']['path'])
    for source in evidence['sources']:
        if source['path'].startswith('data/'):
            add(ROOT/source['path'])
    for path in review.iterdir():
        if path.is_file() and path.name != 'archive.json':
            add(path)
    for name in ('experiments/paper_readiness/deepep_operations.py','experiments/paper_readiness/build_operation_review.py',
                 'docs/reviews/真实路由操作计划与网络后端边界_20261022.md'):
        add(ROOT/name)
    assert sum(p.stat().st_size for p in files.values()) <= 32*2**20
    identities={n:dict(bytes=p.stat().st_size,sha256=sha(p)) for n,p in sorted(files.items())}
    target.mkdir()
    archive=target/'checkpoint.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for name,path in sorted(files.items()):
            tar.add(path,arcname=name,recursive=False)
    with tarfile.open(archive,'r:gz') as tar:
        assert set(tar.getnames())==set(identities)
        for member in tar.getmembers():
            assert member.isfile()
            raw=tar.extractfile(member).read()
            assert len(raw)==identities[member.name]['bytes']
            assert hashlib.sha256(raw).hexdigest()==identities[member.name]['sha256']
    index=dict(path=str(archive.relative_to(ROOT)),bytes=archive.stat().st_size,sha256=sha(archive),
               verified_files=len(identities),files=identities,
               scope='offline real-route operation checkpoint; source data and fixed platform checkout required for rerun',
               repeated_plan_not_duplicated=True,overall_paper_ready=False)
    save(target/'archive.json',index)
    save(review/'archive.json',index)
    return {k:v for k,v in index.items() if k!='files'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--checkpoint',required=True,type=Path)
    a=p.parse_args()
    run,review=a.run.resolve(),a.output.resolve()
    review.mkdir(parents=True,exist_ok=False)
    for name in ('input.json','provenance.json','commands.json','summary.json'):
        shutil.copy2(run/name,review/name)
    evidence=source_evidence()
    save(review/'source_evidence.json',evidence)
    plan=json.loads((run/'plan/plan.json').read_text())
    independent=independent_closure(run,plan)
    records=json.loads((run/'commands.json').read_text())
    assert len(records)==6 and all(r['exit_code']==0 and r['reason'] is None for r in records)
    for name in ('input.json','manifest.json','plan.json'):
        assert sha(run/'plan'/name)==sha(run/'repeat'/name)
    renderer=ROOT/'docs/review_artifacts/paper_readiness_20261022/build_review.py'
    spec=importlib.util.spec_from_file_location('document_renderer',renderer)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.HERE=review
    module.render(ROOT/'docs/reviews/真实路由操作计划与网络后端边界_20261022.md',review/'report.html',
                  '真实路由操作计划与网络后端边界')
    text,count=re.subn(r'<nav>.*?</nav>',
        '<nav><a href="../deepep_semantics_20261022/report.html">前阶段源码审计</a> · '
        '<a href="summary.json">真实路由操作核查</a> · <a href="source_evidence.json">后端与版本证据</a></nav>',
        (review/'report.html').read_text(),count=1)
    assert count==1
    (review/'report.html').write_text(text)
    parser=Links();parser.feed(text);links=0
    for link in parser.links:
        url=urlsplit(link)
        if not url.scheme and url.path:
            assert (review/unquote(url.path)).exists(),link
            links+=1
    verification=dict(scope='offline operation counts, reverse reachability and review integrity',
        raw_files_verified=4,selected_choice_rows=16,public_commands=6,
        independent=independent,repeated_artifact_files_byte_identical=3,
        source_files_verified=len(evidence['sources']),exact_source_excerpts=len(evidence['excerpts']),
        maximum_child_rss_kib=max(r['resource']['peak_rss_kib'] for r in records),
        longest_command_seconds=max(r['wall_seconds'] for r in records),
        local_html_links=links,browser_visual_acceptance=False,
        input_sha256=sha(review/'input.json'),plan_sha256=sha(run/'plan/plan.json'),
        report_sha256=sha(review/'report.html'),
        source_buffer_reuse_validated=False,packet_execution=False,hardware_calibrated=False,overall_paper_ready=False)
    save(review/'verification.json',verification)
    index=checkpoint(run,review,evidence,a.checkpoint)
    print(json.dumps(dict(verification=verification,archive=index),ensure_ascii=False))


if __name__=='__main__':
    main()
