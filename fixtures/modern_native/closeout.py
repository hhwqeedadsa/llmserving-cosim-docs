"""Resource/identity closeout; preserves an incomplete acceptance requirement."""
import argparse
import os
from pathlib import Path
import subprocess
from fixtures.context_transactions.checkpoint import admit
from fixtures.modern_native.stage import load,save,sha,usage,roots,monitors


def main():
    p=argparse.ArgumentParser()
    for key in ('repository','stage','archive','output'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();records=load(s/'commands.json')
    assert all(r['status']=='terminal' or (r['label']=='closeout' and r['status']=='started') for r in records)
    failures=[r['label'] for r in records if r.get('exit_code',0)!=0]
    assert failures==['unit','fault-audit','four-routes']
    assert sum(r['network'] for r in records)==4
    receipts=[]
    for path in monitors(s):
        r=load(path);assert r['status'] in ('completed','child_failed') and not r['remaining_live_group_pids'] and 'ended_utc' in r
        try:os.killpg(r['process_group'],0)
        except ProcessLookupError:pass
        else:raise AssertionError('recorded group still live: '+str(path))
        receipts.append((str(path.relative_to(s)),r))
    seconds=sum(r['wall_seconds'] for _,r in receipts);assert seconds+10<600
    charged=usage(roots(repo,s))+2*2**20;assert charged<384*2**20 and usage([s])<128*2**20
    assert all(r['peak_sampled_artifact_bytes']<=128*2**20 for _,r in receipts)
    for path,reserve in ((s,8*2**30),(repo,64*2**20)):
        fs=os.statvfs(path);assert fs.f_bavail*fs.f_frsize>reserve
    identity=load(s/'runtime-identity.json')
    assert all(sha(s/'runtime'/name)==h for name,h in identity['runtime'].items())
    assert all(sha(repo/name)==h for name,h in identity['original_build']['sources'].items())
    native=repo/'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3'
    head=subprocess.check_output(['git','-C',str(native),'rev-parse','HEAD'],text=True,timeout=3).strip()
    assert head==identity['actual_native_commit']
    assert not subprocess.check_output(['git','-C',str(native),'status','--porcelain'],text=True,timeout=3)
    assert 'decision_<256' in (repo/'backend/rdma/transaction_session.cc').read_text()
    content,manifest=admit(a.archive)
    assert all(sha(repo/name.removeprefix('source/'))==entry['sha256'] for name,entry in manifest['files'].items() if name.startswith('source/'))
    assert load(s/'verification.json')['verified'] and not load(s/'verification.json')['acceptance_complete']
    assert load(s/'checks.json')['passed'] and load(s/'recovery.json')['passed'] and load(s/'archive-safety/verification.json')['passed']
    assert load(s/'final-checks.json')['passed']
    assert not load(s/'limitation.json')['four_request_full_call_passed']
    save(a.output,dict(verified=True,acceptance_complete=False,stage_status='in_progress',goal_status='in_progress',
        actual_network_executions=4,successful_network_executions=3,failed_network_executions=1,successful_network_reexecutions=0,
        preserved_unexpected_failure_labels=failures,launcher_preflight_rejections_before_child=1,
        monitored_seconds_including_nested=seconds,final_management_seconds_reserved=10,
        current_closeout_monitor='terminal receipt verified separately within management reserve',
        sampled_peak_group_rss_bytes=max(r['peak_sampled_group_rss_bytes'] for _,r in receipts),
        total_storage_charged_bytes_including_2MiB_reserve=charged,storage_limit_bytes=384*2**20,
        stage_storage_bytes=usage([s]),stage_storage_limit_bytes=128*2**20,
        monitor_receipts=[name for name,_ in receipts],native_control_decision_limit_unchanged=256,
        actual_native_commit=head,fixed_runtime_and_backend_sources_unchanged=True,
        final_archive_sha256=sha(a.archive),archive_members=len(content),all_packaged_sources_current=True,
        no_remaining_recorded_process_groups=True,dependencies_installed_or_upgraded=False,gpu_executed=False,
        scope='verified partial functional integration and actual unmet four-rank requirement; not full stage acceptance'))


if __name__=='__main__':main()
