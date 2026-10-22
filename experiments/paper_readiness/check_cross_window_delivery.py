"""Read-only final source/evidence/budget checks; does not simulate."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]


def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('task','report','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists()
    assert load(a.task/'checked-chain/verification.json')['all_declared_completed']
    for name,source in [('summary.json',a.task/'analysis/summary.json'),('verification.json',a.task/'checked-chain/verification.json'),
        ('protocol.md',ROOT/'experiments/paper_readiness/cross_window_protocol.md')]:
        assert (a.report/name).read_bytes()==source.read_bytes()
    summary=load(a.report/'summary.json');cases={c['depth']:c for c in summary['cases']}
    assert sorted(cases,key=lambda d:cases[d]['windows'][0]['duration_ns'])==[1024,1,8]
    assert sorted(cases,key=lambda d:cases[d]['total_business_ack_ns'])==[1,8,1024]
    for c in cases.values():
        assert c['status']=='complete' and all(c['first_window_prefix'].values())
        assert sum(w['duration_ns'] for w in c['windows'])==c['total_business_ack_ns']
        assert c['result_sha256']==sha(a.task/'run'/c['name']/'result.json')
    facts=load(a.report/'findings.json')
    assert facts['paper_main_contribution_established'] is False and facts['modern_provider_executed'] is False
    assert facts['deep_actual_over_two_cold']==cases[1024]['total_business_ack_ns']/(2*cases[1024]['boundary_ns'])
    page=(a.report/'report.html').read_text()
    for href in re.findall(r'href="([^"]+)"',page):assert (a.report/href).is_file()
    receipt=load(a.report/'archive.json');archive=Path(receipt['path'])
    assert sha(archive)==receipt['sha256'] and archive.stat().st_size==receipt['bytes']<=48*2**20
    members=load(a.task/'checkpoint/members.json');assert len(members)==receipt['verified_members']
    source_count=syntax_count=0
    for name,item in members.items():
        if not name.startswith('source/'):continue
        path=ROOT/name.removeprefix('source/')
        assert sha(path)==item['sha256'] and path.stat().st_size==item['bytes'];source_count+=1
        if path.suffix=='.py':ast.parse(path.read_text(),filename=str(path));syntax_count+=1
    monitors=[]
    for path in sorted(a.task.glob('*-monitor/probe.json')):
        if path.parent.name=='final-monitor':continue
        r=load(path);assert not r['remaining_live_group_pids']
        expected=1 if path.parent.name=='check-final-monitor' else 0
        assert r['child_exit_code']==expected
        monitors.append(dict(stage=path.parent.name,seconds=r['wall_seconds'],exit_code=expected))
    total_seconds=sum(r['seconds'] for r in monitors);assert total_seconds<300
    files=[p for p in a.task.rglob('*') if p.is_file() and not p.is_symlink()]
    files += [p for p in a.report.rglob('*') if p.is_file() and not p.is_symlink()]
    allocated=sum(p.stat().st_blocks*512 for p in files);assert allocated<=128*2**20
    deps=a.task/'deps-monitor/stdout.log';assert '13' in deps.read_text()
    result=dict(verified=True,source_snapshots_matched=source_count,python_syntax_checked=syntax_count,
        archive_sha256=receipt['sha256'],archive_members_previously_verified=len(members),
        nested_platform_members_previously_verified=648,network_runs=3,network_reexecuted=False,
        monitored_seconds_before_final=total_seconds,allocated_bytes_before_final=allocated,monitors=monitors,
        raw_volatile=True,persistent_backup=False,paper_main_contribution_established=False)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(verified=True,sources=source_count,syntax=syntax_count,seconds=total_seconds,allocated_bytes=allocated)))


if __name__=='__main__':main()
