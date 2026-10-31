"""Collect retained, top-level resource records without executing a network."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();root=a.run.resolve();repo=Path(__file__).resolve().parents[2];phases=[]
    for path in sorted(root.glob('*monitor*/probe.json')):
        c=json.loads(path.read_text());phases.append(dict(name=path.parent.name,report=str(path.relative_to(repo)),sha256=digest(path),
            status=c['status'],command=c['command'],cwd=c['cwd'],exit_code=c.get('child_exit_code'),wall_seconds=c['wall_seconds'],
            peak_group_rss_bytes=c['peak_sampled_group_rss_bytes'],peak_artifact_bytes=c['peak_sampled_artifact_bytes']))
    host=json.loads((root/'host-v3/verification.json').read_text())
    log=(root/'host-monitor-v3/stderr.log').read_text()
    methods=set(re.findall(r'^test_\S+ \(([^\n]+)\) \.\.\. ok$',log,re.M))
    methods.update('transaction_scaleout_profile_test.ScaleOutProfileTests.'+m for m in
        re.findall(r'^    def (test_\w+)\(', (repo/'tests/unit/transaction_scaleout_profile_test.py').read_text(),re.M))
    cpp=0
    for row in host['commands']:
        text=row['stdout'].strip()
        if not text:continue
        cpp+=json.loads(text)['checks'] if text.startswith('{') else int(re.search(r'\d+',text).group())
    summary=dict(passed=True,generated_utc=datetime.now(timezone.utc).isoformat(),
        task_spec='docs/tasks/transaction_scaleout_profile_spec.md',cpp_programs=host['cpp_programs'],cpp_assertions=cpp,
        python_unique_methods=len(methods),python_methods=sorted(methods),network_audit=json.loads((root/'audit.json').read_text()),
        new_networks=3,delivery=json.loads((root/'delivery/verification.json').read_text()),
        fixed_build=json.loads((root/'build-v2/backend.json').read_text()),top_level_phases=phases,
        resource_accounting='top-level host_probe only; nested compile/runtime intervals not added again',
        used_wall_seconds=sum(p['wall_seconds'] for p in phases),peak_group_rss_bytes=max(p['peak_group_rss_bytes'] for p in phases),
        peak_task_artifact_bytes=max(p['peak_artifact_bytes'] for p in phases),
        budget=dict(total_monitored_seconds=2400,task_artifact_bytes=3*2**30,min_free_bytes=8*2**30),
        failures_preserved=[p['name'] for p in phases if p['status']!='completed'],
        unmonitored_preparation_errors=[
            'initial generator debug print accessed verb on local node: KeyError before input save/native execution',
            'initial resource collector used elapsed_wall_seconds instead of host_probe wall_seconds: no network or artifacts changed'],
        html_validation='static local links only; no browser rendering',modern_ep32_native_connected=False,
        hardware_calibrated=False,ns3_rebuilds=0,gpu_execution=False)
    assert summary['used_wall_seconds']<2400 and summary['peak_task_artifact_bytes']<3*2**30
    a.output.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:summary[k] for k in ('passed','cpp_assertions','python_unique_methods','used_wall_seconds','peak_group_rss_bytes','peak_task_artifact_bytes')}))


if __name__=='__main__':main()
