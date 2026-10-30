"""Verify final source/input/results identities, local links and stage budgets."""
import argparse
import ast
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
from urllib.parse import unquote,urlsplit

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

class Links(HTMLParser):
    def __init__(self):super().__init__();self.paths=[]
    def handle_starttag(self,tag,attrs):self.paths.extend(v for k,v in attrs if k in ('src','href') and v)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True)
    a=p.parse_args();stage=a.stage.resolve();proof=load(OUT/'verification.json')
    assert proof['passed'] and proof['actual_Clos128_networks']==6
    assert sha(OUT/'verification.json')==sha(stage/'verification.json')
    assert sha(OUT/'audit.py')==proof['verifier_sha256']
    for name,h in proof['results_sha256'].items():assert sha(stage/'network'/name/'result.json')==h
    for name,h in proof['inputs_sha256'].items():assert sha(stage/'input'/name)==h
    provenance=load(stage/'build/provenance.json');backend=load(stage/'build/backend.json')
    for name,h in provenance['sources'].items():assert sha(ROOT/name)==h,name
    for path in backend['linked_ns3_libraries']:assert sha(Path(path))==backend['library_hashes'][Path(path).name]
    frozen=load(stage/'frozen.json')
    for name,h in frozen['native_sha256'].items():assert sha(Path(name))==h
    commands=load(stage/'network-receipts.json');assert len(commands)==6 and all(x['exit_code']==0 for x in commands)
    probes={f.parent.name:load(f) for f in (stage/'monitors').glob('*/probe.json')}
    failed={n:x for n,x in probes.items() if x['child_exit_code']!=0}
    assert set(failed)=={'native-unit','audit'}
    assert 'native-unit-before-L2-fix.cc' in {x.name for x in stage.iterdir()}
    assert 'wire overhead must include modeled14-byte L2' in (stage/'native-unit/run.stderr').read_text()
    assert (stage/'audit-before-CNP-check.py').is_file()
    assert "counts['merge_enqueues']==counts['repair_3']" in (stage/'monitors/audit/stderr.log').read_text()
    assert all(x['status']==('child_failed' if n in failed else 'completed') for n,x in probes.items())
    assert (stage/'monitors/python-unit/stderr.log').read_text().count('Ran 3 tests')==1
    assert (stage/'monitors/regression/stderr.log').read_text().count('Ran 23 tests')==1
    native_unit=load(stage/'native-unit-corrected/run.stdout')
    assert native_unit['passed'] and len(native_unit['prefixes'])==16 and native_unit['protocol_roundtrips']==4 and native_unit['illegal_cases_rejected']==8
    rejected=load(stage/'native-rejects/receipt.json');assert rejected['passed'] and len(rejected['cases'])==9
    assert (stage/'dependency-check.txt').read_text().count('PASS: checked 13 dependency checkouts')==1
    seconds=sum(x['wall_seconds'] for x in probes.values());peak=max(x['peak_sampled_artifact_bytes'] for x in probes.values())
    assert seconds<1800 and peak<2*2**30 and shutil.disk_usage(stage).free>8*2**30
    links=0
    documents=[OUT/'README.md',OUT/'report.html',ROOT/'examples/scaleout_explicit_prefix/README.md',
      *[ROOT/f for f in ('docs/tasks/current.md','docs/tasks/explicit_prefix_execution_spec.md',
        'docs/user_guide.md','docs/architecture.md','docs/model_validation.md','docs/record_storage.md',
        'backend/README.md','examples/scaleout_repair_plan/README.md')]]
    for doc in documents:
        if doc.suffix=='.html':parser=Links();parser.feed(doc.read_text());paths=parser.paths
        else:paths=re.findall(r'\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)',doc.read_text())
        for path in paths:
            u=urlsplit(path)
            if u.scheme or u.netloc or not u.path:continue
            target=(doc.parent/unquote(u.path)).resolve()
            if target==OUT/'receipt.json':continue
            assert target.exists(),(str(doc),path);links+=1
    sources=[*sorted(OUT.glob('*.py')),ROOT/'tests/unit/explicit_repair_test.py',ROOT/'tests/unit/explicit_repair_native.cc',
      ROOT/'python/moe_ub_cosim/scaleout_link_failures.py',ROOT/'examples/scaleout_explicit_prefix/prepare.py',
      ROOT/'fixtures/selective_repeat/audit.py',ROOT/'fixtures/live_ecmp/audit.py']
    for f in sources:
        if f.suffix=='.py':ast.parse(f.read_text(),filename=str(f))
    value=dict(passed=True,actual_Clos128_networks=6,native_builds=1,dependency_changes=0,GPU_execution=False,
      python_test_methods=26,native_protocol_roundtrips=4,native_illegal_cases=8,native_input_rejections=9,
      independent_prefixes_compared=16,tamper_rejections=len(proof['tamper_checks']),monitored_stages=len(probes),
      monitored_seconds=seconds,peak_group_rss_bytes=max(x['peak_sampled_group_rss_bytes'] for x in probes.values()),
      peak_artifact_bytes=peak,failed_stages={n:dict(exit_code=x['child_exit_code']) for n,x in failed.items()},
      local_links_checked=links,browser_rendering_verified=False,diagram_kind='inline SVG, static checks only',
      sources_sha256={str(f.relative_to(ROOT)):sha(f) for f in sources},native_sources_sha256=provenance['sources'],
      native_binaries_sha256=frozen['native_sha256'],native_library_sha256=backend['library_hashes'],
      results_sha256=proof['results_sha256'],artifacts_sha256={f.name:sha(f) for f in OUT.iterdir() if f.suffix in ('.json','.html')},
      boundaries=['artificial functional streams, no MoE performance claim','project-local IPv4 protocol250, not standard RoCE/MPLS/TI-LFA',
        'real wire/queue/MMU size; no hardware processing/detection/distribution or MTU fragmentation calibration'])
    with (OUT/'receipt.json').open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    print(json.dumps(dict(passed=True,monitored_seconds=seconds,networks=6,python_methods=26,local_links=links)))


if __name__=='__main__':main()
