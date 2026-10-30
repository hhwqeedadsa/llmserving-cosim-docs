"""Seal this bounded functional acceptance from actual logs and saved results."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess

from moe_ub_cosim.host_probe import disk_usage

ROOT=Path(__file__).resolve().parents[2]
DEST=Path(__file__).resolve().parent


def load(path):return json.loads(path.read_bytes())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):
    with path.open('x') as f:json.dump(value,f,indent=2);f.write('\n')


class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':self.links.extend(v for k,v in attrs if k=='href')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run-root',type=Path,required=True)
    a=p.parse_args();root=a.run_root.resolve();proof=load(root/'final-audit/verification.json')
    assert proof['passed'] and proof['application_change_ns']==119996
    for name in ('verification.json','report.html'):shutil.copyfile(root/'final-audit'/name,DEST/name)
    build=load(root/'build/backend.json');old=load(root/'build/original-hashes.json')
    assert all(sha(Path(k))==v for k,v in old.items())
    assert sha(root/'build/ns3.42-moe-rdma-service')==build['service_sha256']
    for k,v in build['source_sha256'].items():assert sha(ROOT/k)==v
    freeze=subprocess.check_output(['git','rev-parse','4ffcfae94b'],cwd=ROOT,text=True).strip()
    changed=subprocess.check_output(['git','diff-tree','--no-commit-id','--name-only','-r',freeze],cwd=ROOT,text=True).splitlines()
    execution_sources={k:sha(ROOT/k) for k in changed if k.startswith(('backend/','python/'))}
    for k,v in execution_sources.items():
        fixed=subprocess.check_output(['git','show',freeze+':'+k],cwd=ROOT)
        assert hashlib.sha256(fixed).hexdigest()==v
    monitors={str(p.relative_to(root)):load(p) for p in sorted(root.glob('**/probe.json'))}
    for r in monitors.values():assert not r['remaining_live_group_pids']
    failures={k:r for k,r in monitors.items() if r['status']!='completed'}
    assert set(failures)=={'monitors/build/probe.json','monitors/related/probe.json',
                           'monitors/new-guards/probe.json','monitors/service-guard-fixed/probe.json'}
    tests=set()
    for pth in (root/'monitors').glob('*/stderr.log'):
        for line in pth.read_text().splitlines():
            if line.startswith('test_') and line.endswith(' ... ok'):tests.add(line.removesuffix(' ... ok'))
    assert len(tests)==90
    for name in ('legacy-normal','legacy-local','legacy-helper','check-observe','check-helper','harness-audit','final-audit','native-reject'):
        assert monitors[f'monitors/{name}/probe.json']['status']=='completed'
    resources=dict(passed=True,monitored_wall_seconds_upper_bound=sum(r['wall_seconds'] for r in monitors.values()),
        wall_accounting='sum including nested monitors; upper bound, not unique wall time',
        peak_sampled_group_rss_bytes=max(r['peak_sampled_group_rss_bytes'] for r in monitors.values()),
        artifact_usage=disk_usage(root),monitors=monitors,
        scope='sampled host resources, not simulated GPU memory; current sealing command still running')
    assert resources['monitored_wall_seconds_upper_bound']<900
    assert max(resources['artifact_usage'].values())<536870912
    save(DEST/'resources.json',resources)
    dep=subprocess.run(['bash','scripts/check_deps.sh'],cwd=ROOT,text=True,capture_output=True)
    assert dep.returncode==0
    (root/'dependencies.log').write_text(dep.stdout+dep.stderr)
    links=Links();links.feed((DEST/'report.html').read_text())
    assert links.links==['verification.json'] and all((DEST/link).is_file() for link in links.links)
    run_hashes={str(p.relative_to(root)):sha(p) for p in sorted(root.glob('**/*')) if p.is_file() and
        ('monitors' not in p.relative_to(root).parts and p.suffix not in ('.o',) and '/build/' not in str(p))}
    save(DEST/'receipt.json',dict(passed=True,execution_commit=freeze,execution_sources=execution_sources,
        tests_passed_unique=sorted(tests),failed_commands_retained=sorted(failures),
        failed_commands_scope='build failure before compiler, missing test module, two incomplete artificial guard inputs; corrected checks recorded separately',
        dependency_check_exit=dep.returncode,dependency_log_sha256=sha(root/'dependencies.log'),
        backend=build,build_commands=load(root/'build/commands.json'),
        old_outputs_unchanged=True,old_output_sha256=old,
        native_rejections=load(root/'native-reject/verification.json'),
        run_sha256=run_hashes,checker_sha256=sha(ROOT/'tests/integration/check_compute_start.py'),
        artifacts_sha256={name:sha(DEST/name) for name in ('verification.json','report.html','resources.json')},
        network_executions=2,network_retries=0,dependency_builds=0,gpu_executions=0,downloads=0,
        scope='shared actual-start functionality accepted; application recovery and overall research remain incomplete'))
    print(json.dumps(dict(passed=True,unique_unit_checks=len(tests),network_executions=2,
        monitored_wall_seconds_upper_bound=resources['monitored_wall_seconds_upper_bound'],artifact_usage=resources['artifact_usage'])))


if __name__=='__main__':main()
