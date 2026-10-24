"""Final archive/identity/resource audit; no build or network work."""
import argparse
import os
from pathlib import Path
from fixtures.self_peer_service.checkpoint import admit
from fixtures.self_peer_service.stage import load,save,sha,monitors,roots,usage


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage','archive','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();commands=load(s/'commands.json')
    assert all(r['status']=='terminal' or (r['label']=='closeout' and r['status']=='started') for r in commands)
    failed=[r['label'] for r in commands if r.get('exit_code',0)!=0]
    assert failed==['compile-transaction_probe','link-transaction-probe','final-checks','final-checks-help-wrap']
    assert sum(r['network'] for r in commands)==4
    receipts=[]
    for path in monitors(s):
        r=load(path)
        assert r['status'] in ('completed','child_failed') and not r['remaining_live_group_pids'] and 'ended_utc' in r
        try:os.killpg(r['process_group'],0)
        except ProcessLookupError:pass
        else:raise AssertionError('recorded process group still alive: '+str(path))
        receipts.append((str(path.relative_to(s)),r))
    wall=sum(r['wall_seconds'] for _,r in receipts);assert wall+10<600
    reserve=2*2**20;stored=usage(roots(repo,s))+reserve;assert stored<384*2**20
    assert usage([s])<128*2**20
    assert all(r['peak_sampled_artifact_bytes']<=128*2**20 for _,r in receipts)
    for root,minimum in ((s,8*2**30),(repo,64*2**20)):
        fs=os.statvfs(root);assert fs.f_bavail*fs.f_frsize>minimum
    identity=load(s/'identity.json')
    assert all(sha(repo/p)==h for p,h in identity['backend_sources'].items())
    assert all(sha(s/'runtime'/p)==h for p,h in identity['built_runtime'].items())
    assert all(sha(Path(p))==h==sha(s/'runtime/lib'/Path(p).name) for p,h in identity['fixed_libraries'].items())
    assert all(sha(Path(p))==h for p,h in identity['fixed_objects'].items())
    content,manifest=admit(a.archive)
    assert all(sha(repo/name.removeprefix('source/'))==e['sha256'] for name,e in manifest['files'].items() if name.startswith('source/'))
    initial,initial_manifest=admit(a.archive.parent/'portable-initial.tar.gz')
    assert initial['evidence/runtime/transaction-session']==content['evidence/runtime/transaction-session']
    for name in ('remote_transactions.cc','remote_transactions.hh','self_peer_service.hh'):
        assert initial['source/backend/rdma/'+name]==content['source/backend/rdma/'+name]
    # The migrated final executor only adds a pure pre-Fabric admission branch.
    old=initial['source/backend/rdma/transaction_probe.cc'].decode()
    current=content['source/backend/rdma/transaction_probe.cc'].decode()
    extra='''    // Older schemas do not enter the v5 session validator. Reject an explicit
    // self-service declaration there too, before constructing any network.
    if(input.contains("self_peer_model") || std::any_of(input.at("connections").begin(),input.at("connections").end(),
        [](const auto& c){return c.contains("self_service");}))
      ValidateSelfServices(input,topology,config.contains("limits")?config.at("limits").at("max_sim_ns").get<int64_t>():5000000);
'''
    assert current.count(extra)==1 and current.replace(extra,'')==old
    for name in ('verification.json','checks.json','recovery.json','final-checks.json','admission-guard.json'):
        assert load(s/name)['passed']
    assert load(s/'archive-safety/verification.json')['passed']
    save(a.output,dict(passed=True,actual_network_executions=4,network_failures=0,successful_network_reexecutions=0,
        preserved_unexpected_failure_labels=failed,monitored_seconds_including_nested=wall,
        final_management_seconds_reserved=10,current_closeout_monitor='terminal receipt audited separately within management reserve',
        sampled_peak_group_rss_bytes=max(r['peak_sampled_group_rss_bytes'] for _,r in receipts),
        total_storage_charged_bytes_including_receipt_reserve=stored,receipt_reserve_bytes=reserve,
        storage_limit_bytes=384*2**20,stage_storage_bytes=usage([s]),stage_storage_limit_bytes=128*2**20,
        monitor_receipts=[name for name,_ in receipts],actual_native_commit=identity['actual_native_commit'],
        final_archive_sha256=sha(a.archive),final_archive_members=len(content),all_packaged_sources_current=True,
        final_guard_only_native_rebuild=True,final_probe_network_reexecuted=False,
        final_probe_delta_verified_pure_admission=True,core_session_and_eight_libraries_unchanged=True,
        no_remaining_recorded_process_groups=True,dependencies_installed_or_upgraded=False,gpu_executed=False,
        scope='platform functional increment accepted; modern integration/calibration/novelty/scale remain open'))


if __name__=='__main__':main()
