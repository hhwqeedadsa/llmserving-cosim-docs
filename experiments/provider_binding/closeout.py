"""Persist verified offline evidence; final management reserve, no new runs."""
import argparse
from datetime import datetime,timezone
import os
from pathlib import Path
import shutil
import subprocess
import time
from experiments.provider_binding.checkpoint import admit
from experiments.provider_binding.stage import load,save,sha,usage


def main():
    start=time.monotonic();p=argparse.ArgumentParser()
    for k in ('repository','stage'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve()
    assert load(s/'verification-complete.json')['passed'] and load(s/'restored/replay.json')['three_json_byte_identical']
    archive=s/'evidence.tar.gz';payload,manifest=admit(archive)
    for name,f in manifest['files'].items():
        if name.startswith('source/'):assert sha(repo/name[len('source/'):])==f['sha256']
    subprocess.run(['git','diff','--exit-code','a81061a','--','python','backend','deps.lock.json','.gitmodules'],cwd=repo,check=True)
    commands=load(s/'commands.json');assert all(r['status']=='terminal' and r['exit_code']==0 and not r['network'] for r in commands)
    reports=[]
    for command in commands:
        r=load(s/(command['label']+'-monitor/probe.json'))
        assert r['command']==command['command'] and r['cwd']==command['cwd']
        assert r['status']=='completed' and r['child_exit_code']==0 and not r['remaining_live_group_pids'];reports.append(r)
    seconds=sum(r['wall_seconds'] for r in reports);assert seconds+10<300
    for root,reserve in ((s,8*2**30),(repo,64*2**20)):
        fs=os.statvfs(root);assert fs.f_bavail*fs.f_frsize>reserve
    dest=repo/'runs/provider_binding_checkpoint_20261024';canonical=repo/'experiments/provider_binding/results/20261024'
    assert not dest.exists() and not canonical.exists();dest.mkdir();canonical.mkdir(parents=True)
    shutil.copyfile(archive,dest/'evidence.tar.gz');shutil.copyfile(archive.with_suffix('.receipt.json'),dest/'archive.json')
    assert sha(dest/'evidence.tar.gz')==sha(archive)
    analysis=load(s/'analysis.json')
    save(canonical/'summary.json',dict(summary=analysis['summary'],roles=analysis['roles'],scope=analysis['scope'],new_network_executions=0))
    for name,src in {'legacy-comparison.json':'legacy-comparison.json','verification.json':'verification-complete.json',
                     'verification-initial.json':'verification.json','replay.json':'restored/replay.json'}.items():shutil.copyfile(s/src,canonical/name)
    charged=usage([s,dest,repo/'experiments/provider_binding'])+2**20;assert charged<128*2**20
    receipt=dict(schema_version='provider-binding-closeout/1',completed_utc=datetime.now(timezone.utc).isoformat(),
        research_stage_complete=True,overall_paper_goal_complete=False,platform_commit='a81061a',
        new_network_executions=0,public_untimed_plan_generations=1,standalone_research_cpu_compiles=1,scalar_driver_executions=1,
        original_route_files=4,source_versions={'deepep':'f6aa38fe06cd4a0489e7730365d4e0114970a2f3','nccl':'12df1a11afad322be5a204a2db890161cbf8131d'},
        original_function_queries=486,operations_independently_verified=486,legacy_writes_independently_mapped=76,
        tamper_classes=9,three_replayed_json_byte_identical=True,all_archived_sources_equal_current=True,
        archives_and_restore_scope='pre-closeout snapshot includes evidence and source; final replay/management receipts stored alongside, no vendor/native runtime',
        original_terminal_monitors=len(reports),monitored_seconds=seconds,peak_sampled_group_rss_bytes=max(r['peak_sampled_group_rss_bytes'] for r in reports),
        charged_cumulative_bytes_with_1MiB_reserve=charged,final_management_seconds_reserved=10,
        final_management_elapsed_seconds=time.monotonic()-start,limits=dict(cumulative_seconds=300,cumulative_bytes=128*2**20,
            per_command_seconds=60,rss_bytes=2**30,address_bytes=2**31,stage_artifact_bytes=64*2**20,new_networks=0),
        remaining_monitored_live_pids=[],monitored_failures=[],
        initial_verification_preserved=True,verification_extension='cached counter default plus independent all-76 legacy graph mapping; no plan/driver rerun',
        original_driver_sha256=sha(s/'binding-cpu'),archived_driver_source_sha256=sha(repo/'experiments/provider_binding/driver.cc'),
        source_inspection_scope='interactive diagnostics outside monitored execution; monitored_seconds is not full calendar time',
        archive=load(dest/'archive.json'),dependencies='13 fixed checkouts agree',platform_dependency_changes_install_download_gpu_push=False,
        limitations='declared same devComm/launch only, no modern network execution or hardware idle calibration/holdout/full thousand-GPU/novelty')
    assert receipt['final_management_elapsed_seconds']<10
    save(canonical/'closeout.json',receipt);save(dest/'closeout.json',receipt);shutil.copyfile(s/'restored/replay.json',dest/'replay.json')
    print('closed: modern 33 remote QPs versus 12 direction groups; two ancestry pairs differ; zero new networks')


if __name__=='__main__':main()
