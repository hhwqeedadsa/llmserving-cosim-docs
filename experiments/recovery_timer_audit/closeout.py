"""Reconcile source/result identities and audit budgets before writing final receipt."""
import argparse
import json
from pathlib import Path
import shutil

from audit import ROOT,checked,load,sha


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',type=Path,required=True);p.add_argument('--delivery',type=Path,required=True)
    a=p.parse_args();stage,out=a.stage.resolve(),a.delivery.resolve();f=checked(stage/'input')
    proof=load(out/'verification.json');assert proof['passed'] and len(proof['tamper_checks'])==3
    assert proof['analysis_sha256']==sha(out/'analysis.json')
    assert proof['verifier_sha256']==sha(Path(__file__).with_name('verify.py'))
    probes={p.parent.name:load(p) for p in (stage/'monitors').glob('*/probe.json')}
    assert probes and all(p['status']=='completed' and p['child_exit_code']==0 for p in probes.values())
    seconds=sum(p['wall_seconds'] for p in probes.values())
    peak=max(p['peak_sampled_artifact_bytes'] for p in probes.values())
    assert seconds<300 and peak<64*2**20 and shutil.disk_usage(stage).free>8*2**30
    assert 'PASS: checked 13 dependency checkouts' in (stage/'dependency-check.txt').read_text()
    for file in ('README.md','report.html'):
        assert (out/file).stat().st_size>1000
    for file in ('analysis.json','verification.json','sources.json','README.md'):
        assert '"'+file+'"' in (out/'report.html').read_text()
    value=dict(passed=True,method_commit=f['platform_commit'],frozen_sha256=sha(stage/'input/frozen.json'),
      source_sha256=f['source_sha256'],public_source_sha256=f['public_source_sha256'],cases=f['cases'],
      monitored_stages=len(probes),monitored_seconds=seconds,peak_artifact_bytes=peak,
      peak_group_rss_bytes=max(p['peak_sampled_group_rss_bytes'] for p in probes.values()),budget=f['budget'],
      source_requests=5,source_preflight_deadline_upper_bound_seconds=53,source_bodies_imported_without_new_requests=True,
      retained_request_failure=load(stage/'input/sources/sources.json')['retained_request_failure'],
      dependency_checks=13,new_networks=0,new_builds=0,installs=0,GPU_execution=False,
      browser_rendering_verified=False,hardware_calibrated=False,
      delivery_sha256={p.name:sha(p) for p in out.iterdir() if p.is_file()})
    with (out/'receipt.json').open('x') as s:json.dump(value,s,indent=2);s.write('\n')
    for file,h in value['delivery_sha256'].items():assert sha(out/file)==h
    print(json.dumps(dict(passed=True,monitored_seconds=seconds,peak_artifact_bytes=peak)))


if __name__=='__main__':main()
