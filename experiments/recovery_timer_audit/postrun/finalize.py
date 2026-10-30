"""Finalize including the retained, specifically diagnosed report-stage failure."""
import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from audit import ROOT,checked,load,sha


class Links(HTMLParser):
    def __init__(self):super().__init__();self.values=[]
    def handle_starttag(self,tag,attrs):
        self.values.extend(v for k,v in attrs if k in ('href','src'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',type=Path,required=True);p.add_argument('--delivery',type=Path,required=True)
    a=p.parse_args();stage,out=a.stage.resolve(),a.delivery.resolve();f=checked(stage/'input')
    proof=load(out/'verification.json');assert proof['passed'] and len(proof['tamper_checks'])==3
    assert proof['analysis_sha256']==sha(out/'analysis.json')
    assert proof['verifier_sha256']==sha(Path(__file__).parents[1]/'verify.py')
    competition=load(out/'competition.json');assert competition['passed']
    assert competition['source_sha256']==sha(Path(__file__).with_name('explain_competition.py'))
    ack=load(out/'ack_policy.json');assert ack['passed'] and ack['post_result_diagnosis']
    revision=ack.get('editorial_revision')
    if revision:
        assert ack['source_sha256']==sha(ROOT/revision['original_source_path'])
        assert revision['source_sha256']==sha(ROOT/revision['source_path'])
        assert revision['original_artifact_sha256']==sha(stage/'before-example-correction/ack_policy.json')
        assert ack['reproduction_source_sha256']==sha(Path(__file__).with_name('ack_policy.py'))
        assert ack['conditional_example']['logical_message_bytes']==6000
        assert ack['conditional_example']['assumed_path_MTU_bytes']==4096
    else:assert ack['source_sha256']==sha(Path(__file__).with_name('ack_policy.py'))
    for excerpt in ack['excerpts'].values():
        path=ROOT/excerpt['path'];assert sha(path)==excerpt['sha256']
        assert '\n'.join(path.read_text().splitlines()[excerpt['first_line']-1:excerpt['last_line']])==excerpt['text']
    sources=load(out/'sources.json')
    assert sources['total_source_requests']==6 and sources['total_preflight_deadline_upper_bound_seconds']==58
    for row in sources['sources']:
        path=ROOT/row['path'] if row.get('post_result_source') else stage/'input/sources'/row['path']
        assert sha(path)==row['sha256']
    before=load(stage/'before-ack-policy/receipt.json')
    assert sha(stage/'finalize-before-ack.py')==before['postrun_source_sha256']['experiments/recovery_timer_audit/postrun/finalize.py']
    for name,h in before['delivery_sha256'].items():
        archive=stage/'before-ack-policy'/name
        assert sha(archive if archive.is_file() else out/name)==h
    probes={p.parent.name:load(p) for p in (stage/'monitors').glob('*/probe.json')}
    failures={name:p for name,p in probes.items() if p['status']!='completed' or p['child_exit_code']!=0}
    known=None
    if failures:
        assert set(failures)=={'competition'} and failures['competition']['child_exit_code']==1
        known=load(stage/'competition-failure.json')
        assert known['source_sha256']==sha(ROOT/known['source_copy'])
        error=(stage/'monitors/competition/stderr.log').read_text()
        assert known['error'] in error and "row['result_sha256']" in error
        assert probes['competition-v2']['status']=='completed' and probes['competition-v2']['child_exit_code']==0
    for name in ('prepare','analyze','verify','publish'):
        assert probes[name]['status']=='completed' and probes[name]['child_exit_code']==0
    assert probes['ack-policy']['status']=='completed' and probes['ack-policy']['child_exit_code']==0
    if revision:assert probes['ack-example-correction']['status']=='completed' and probes['ack-example-correction']['child_exit_code']==0
    seconds=sum(p['wall_seconds'] for p in probes.values());peak=max(p['peak_sampled_artifact_bytes'] for p in probes.values())
    assert seconds<300 and peak<64*2**20 and shutil.disk_usage(stage).free>8*2**30
    assert 'PASS: checked 13 dependency checkouts' in (stage/'dependency-check-final.txt').read_text()
    links=Links();links.feed((out/'report.html').read_text())
    allowed={row['reference'] for row in load(out/'sources.json')['sources']}
    for link in links.values:
        if link.startswith('https://'):assert link in allowed
        else:assert (out/link).is_file(),link
    # Snapshot is computed before receipt creation; no self-reference.
    assert not (out/'receipt.json').exists()
    identity={p.name:sha(p) for p in out.iterdir() if p.is_file()}
    value=dict(passed=True,method_commit=f['platform_commit'],frozen_sha256=sha(stage/'input/frozen.json'),
      execution_source_sha256=f['source_sha256'],postrun_source_sha256={str(p.relative_to(ROOT)):sha(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
      public_source_sha256=f['public_source_sha256'],postrun_public_source=ack['public_source'],
      ACK_example_editorial_revision=revision,cases=f['cases'],
      monitored_stages=len(probes),monitored_seconds=seconds,peak_artifact_bytes=peak,
      peak_group_rss_bytes=max(p['peak_sampled_group_rss_bytes'] for p in probes.values()),budget=f['budget'],
      source_requests=6,source_preflight_deadline_upper_bound_seconds=58,
      source_bodies_imported_without_new_requests=True,
      retained_HTTP_failure=load(stage/'input/sources/sources.json')['retained_request_failure'],
      retained_report_stage_failure=known,prior_receipt_sha256=sha(stage/'before-ack-policy/receipt.json'),
      independent_tamper_checks=proof['tamper_checks'],
      dependency_checks=13,new_networks=0,new_builds=0,installs=0,GPU_execution=False,
      browser_rendering_verified=False,hardware_calibrated=False,static_HTML_links=len(links.values),delivery_sha256=identity)
    with (out/'receipt.json').open('x') as s:json.dump(value,s,indent=2);s.write('\n')
    for name,h in load(out/'receipt.json')['delivery_sha256'].items():assert sha(out/name)==h
    print(json.dumps(dict(passed=True,monitored_seconds=seconds,peak_artifact_bytes=peak,retained_failed_stages=len(failures))))


if __name__=='__main__':main()
