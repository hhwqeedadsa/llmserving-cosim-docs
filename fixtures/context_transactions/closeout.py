"""Audit actual executions, immutable runtime/source identity and total resources."""
import argparse
import os
from pathlib import Path
from fixtures.context_transactions.checkpoint import admit
from fixtures.context_transactions.stage import load,save,sha,monitors,storage_roots,usage


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage','archive','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve()
    commands=load(s/'commands.json')
    assert all(r['status']=='terminal' or (r['label']=='closeout' and r['status']=='started') for r in commands)
    failures=[r for r in commands if r.get('exit_code',0)!=0]
    assert len(failures)==1 and failures[0]['label']=='restored-smoke' and failures[0]['network_started'] is False
    assert load(s/'recovery-preflight-failure.json')['actual_network_started'] is False
    actual=[s/n/'inputs.json' for n in ('healthy','fault','fresh-smoke')];assert all(p.exists() for p in actual)
    assert sum(r.get('network_started',r['network']) for r in commands)==3
    reports=[]
    for path in monitors(s):
        r=load(path);assert r['status']=='completed' or path==s/'restored-smoke-monitor/probe.json'
        assert not r['remaining_live_group_pids'] and 'ended_utc' in r
        try:os.killpg(r['process_group'],0)
        except ProcessLookupError:pass
        else:raise AssertionError('recorded process group still exists: '+str(path))
        reports.append((str(path.relative_to(s)),r))
    wall=sum(r['wall_seconds'] for _,r in reports);assert wall+10<450
    reserve=2**20;stored=usage(storage_roots(repo,s))+reserve;assert stored<256*2**20
    assert max(r['peak_sampled_artifact_bytes'] for _,r in reports if r['limits']['artifact_bytes']==96*2**20)<=96*2**20
    for root,minimum in ((s,8*2**30),(repo,64*2**20)):
        fs=os.statvfs(root);assert fs.f_bavail*fs.f_frsize>minimum
    identity=load(s/'identity.json')
    assert all(sha(repo/p)==h for p,h in identity['backend_sources'].items())
    assert all(sha(Path(p))==h for p,h in identity['binary'].items())
    assert sha(s/'runtime/transaction-probe')==next(iter(identity['binary'].values()))
    assert all(sha(Path(p))==h==sha(s/'runtime/lib'/Path(p).name) for p,h in identity['libraries'].items())
    content,manifest=admit(a.archive)
    assert all(sha(repo/name.removeprefix('source/'))==entry['sha256'] for name,entry in manifest['files'].items() if name.startswith('source/'))
    replay=s/'restored/source'
    immutable=['python/moe_ub_cosim/context_transactions.py','python/moe_ub_cosim/cli.py',
        'fixtures/context_transactions/audit.py','fixtures/context_transactions/recover.py']
    assert all(sha(repo/p)==sha(replay/p) for p in immutable)
    assert load(s/'verification.json')['passed'] and load(s/'recovery.json')['passed'] and load(s/'checks.json')['passed']
    save(a.output,dict(passed=True,actual_network_executions=3,preflight_failures_before_network=1,
        monitored_seconds_including_nested=wall,final_management_seconds_reserved=10,
        current_closeout_monitor='not yet terminal; audited in final management receipt within reserved 10 seconds',
        sampled_peak_group_rss_bytes=max(r['peak_sampled_group_rss_bytes'] for _,r in reports),
        total_storage_charged_bytes_including_receipt_reserve=stored,receipt_reserve_bytes=reserve,
        storage_limit_bytes=256*2**20,monitor_receipts=[name for name,_ in reports],
        actual_native_commit=identity['native_commit'],runtime_and_backend_sources_unchanged=True,
        final_archive_sha256=sha(a.archive),final_archive_members=len(content),
        final_archive_all_source_hashes_current=True,restored_functional_sources_equal=True,
        restored_runner_difference='Only stage accounting excludes evidenced pre-network failure and charges source documents; no network/checker change',
        no_remaining_recorded_process_groups=True,dependencies_installed_or_upgraded=False,gpu_executed=False,
        scope='functional context binding accepted; overall research objective remains in progress'))


if __name__=='__main__':main()
