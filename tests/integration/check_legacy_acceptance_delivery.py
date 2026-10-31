"""Check the published functional evidence and links without a network replay."""
import argparse
import ast
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();root=a.root.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    repo=Path(__file__).resolve().parents[2];fixture=repo/'fixtures/legacy_native'
    load=lambda path:json.loads(path.read_bytes())
    sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    evidence=load(fixture/'verification.json')
    assert evidence['passed'] and evidence['complete_legacy_functional_execution_accepted']
    assert evidence['new_network_executions']==3 and evidence['cross_call_reuse_accepted']
    assert not evidence['algorithm_innovation_validated'] and not evidence['real_routes']
    for name,expected in evidence['source_hashes'].items():assert sha(repo/name)==expected,name
    for name in ('report.html','verification.json'):
        assert (fixture/name).read_bytes()==(root/'authorized-capacity-100k/summary'/name).read_bytes()
    links=[]
    class Links(HTMLParser):
        def __init__(self,path):super().__init__();self.path=path
        def handle_starttag(self,tag,attrs):
            for key,value in attrs:
                if key in ('href','src') and value:links.append((self.path,value))
    for path in fixture.rglob('*'):
        if path.suffix=='.html':Links(path).feed(path.read_text())
        if path.suffix=='.md':links.extend((path,t) for t in re.findall(r'\]\(([^)]+)\)',path.read_text()))
    paths=[repo/name for name in ('examples/legacy_native/README.md','docs/tasks/legacy_native_spec.md',
        'docs/user_guide.md','docs/record_storage.md','docs/architecture.md','docs/model_validation.md','backend/README.md')]
    for path in paths:links.extend((path,t) for t in re.findall(r'\]\(([^)]+)\)',path.read_text()))
    for path,target in links:
        if target.startswith(('https:','http:','#','mailto:')):continue
        assert (path.parent/target.split('#',1)[0]).exists(),(path,target)
    for name in evidence['source_hashes']:
        if name.endswith('.py'):ast.parse((repo/name).read_text(),filename=name)
    ast.parse(Path(__file__).read_text())
    deps=root/'monitors/authorized-dependency-check'
    assert load(deps/'probe.json')['status']=='completed'
    assert load(deps/'probe.json')['child_exit_code']==0
    assert load(root/'monitors/authorized-summary/probe.json')['status']=='completed'
    stages=[load(path) for path in (root/'monitors').glob('*/probe.json')]
    assert all(not stage['remaining_live_group_pids'] for stage in stages)
    assert sum(s['wall_seconds'] for s in stages)<1800
    subprocess.run(['git','diff','--check'],cwd=repo,check=True)
    result=dict(passed=True,source_hashes_checked=len(evidence['source_hashes']),
        static_links_checked=len(links),dependency_log_sha256=sha(deps/'stdout.log'),
        source_sha256=sha(Path(__file__)),new_network_executions_this_check=0,
        complete_functional_acceptance=True,goal_complete=False,browser_rendering_checked=False,
        monitored_wall_seconds_before_delivery_check=sum(s['wall_seconds'] for s in stages),
        monitored_stage_count_before_delivery_check=len(stages),
        peak_sampled_group_rss_bytes=max(s['peak_sampled_group_rss_bytes'] for s in stages))
    (out/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
