"""Persist six-case evidence, terminal resources and failures; no networks."""
import argparse
from datetime import datetime,timezone
import os
from pathlib import Path
import shutil
from experiments.nic_wakeup_effect.stage import load,save,sha
from experiments.nic_wakeup_effect.checkpoint import admit
from experiments.nic_wakeup_effect.run import identity


def usage(roots):
    seen=set();apparent=allocated=0
    for root in roots:
        for p in [root,*root.rglob('*')]:
            assert not p.is_symlink();st=p.stat();key=st.st_dev,st.st_ino
            if key in seen:continue
            seen.add(key);allocated+=st.st_blocks*512
            if p.is_file():apparent+=st.st_size
    return apparent,allocated


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve()
    assert load(s/'run.json')['phase_closed'] and load(s/'restored/replay.json')['six_reports_byte_identical']
    assert load(s/'verification.json')['passed'] and load(s/'eligibility-verification.json')['passed']
    assert load(s/'archive-safety/verification.json')['passed']
    archive=s/'evidence-final.tar.gz';_,final=admit(archive);_,prior=admit(s/'evidence-v1.tar.gz')
    for name,entry in final['files'].items():
        if name.startswith('source/'):assert sha(repo/name[len('source/'):])==entry['sha256']
    for name,entry in prior['files'].items():
        if name.startswith('source/'):assert final['files'][name]==entry
    commands=load(s/'commands.json');assert all(c['status']=='terminal' for c in commands)
    networks=[c for c in commands if c['network']];assert len(networks)==3 and all(c['exit_code']==0 for c in networks)
    monitors=[]
    for path in sorted(s.rglob('probe.json')):
        if path.relative_to(s).parts[:2]==('restored','evidence'):continue
        monitors.append(path)
    reports=[load(p) for p in monitors]
    assert all(r['status'] in ('completed','child_failed') and not r['remaining_live_group_pids'] for r in reports)
    seconds=sum(r['wall_seconds'] for r in reports);assert seconds+10<600
    runtime=identity(repo,load(s/'runtime.json'))
    assert all(sha(s/'inputs'/n)==h for n,h in load(s/'inputs/provenance.json')['files'].items())
    for root,reserve in ((s,8*2**30),(repo,64*2**20)):
        fs=os.statvfs(root);assert fs.f_bavail*fs.f_frsize>reserve
    dest=repo/'runs/nic_wakeup_effect_checkpoint_20261024';assert not dest.exists();dest.mkdir()
    shutil.copyfile(archive,dest/'evidence.tar.gz');shutil.copyfile(archive.with_suffix('.receipt.json'),dest/'archive.json')
    assert sha(dest/'evidence.tar.gz')==sha(archive)
    canonical=repo/'experiments/nic_wakeup_effect/results/20261024';assert not canonical.exists();canonical.mkdir(parents=True)
    for name,src in {'summary.json':'summary-final.json','verification.json':'verification.json',
        'eligibility-verification.json':'eligibility-verification.json','replay.json':'restored/replay.json',
        'identity.json':'identity.json','archive-safety.json':'archive-safety/verification.json','diagnostics.json':'diagnostics.json'}.items():
        shutil.copyfile(s/src,canonical/name)
    apparent,allocated=usage([s,dest,repo/'experiments/nic_wakeup_effect']);charged=max(apparent,allocated)+2*2**20
    assert charged<384*2**20
    receipt=dict(schema_version='nic-wakeup-effect-closeout/1',ended_utc=datetime.now(timezone.utc).isoformat(),
        platform_commit=load(s/'runtime.json')['platform_commit'],native_patch_commit='68a07f7',research_stage_complete=True,overall_paper_goal_complete=False,
        new_actual_networks=3,immutable_saved_baselines=3,network_phase_closed=True,successful_network_reruns=0,
        original_terminal_monitors=len(monitors),conservative_cumulative_monitored_seconds_including_nested=seconds,
        final_management_seconds_reserved=10,peak_sampled_group_rss_bytes=max(r['peak_sampled_group_rss_bytes'] for r in reports),
        stage_restored_archives_persistent_sources_apparent_bytes=apparent,stage_restored_archives_persistent_sources_allocated_bytes=allocated,
        source_and_receipt_reserve_bytes=2*2**20,total_charged_with_reserve_bytes=charged,
        limits=dict(cumulative_seconds=600,cumulative_bytes=384*2**20,per_command_seconds=60,rss_bytes=2**30,address_bytes=2**31,
            artifact_bytes=128*2**20,max_new_networks=3,max_sim_ns=5000000,result_bytes=16*2**20),
        remaining_monitored_live_pids=[],preserved_failures=load(s/'failures.json'),
        failed_registered_command_labels=[c['label'] for c in commands if c['exit_code']!=0],
        archive=load(dest/'archive.json'),evidence=dict(all_members_hash_verified=True,final_sources_equal_current=True,
            replay_executed_verified_v1=True,final_replay_sources_equal_v1=True,six_json_reports_byte_identical=True,
            original_work_mapping_topology_byte_identical=True,semantic_tamper_classes=10,piecewise_eligibility_tamper_classes=3,
            reused_archive_parser_unsafe_classes=8,runtime=runtime,fixed_dependencies='13 checkouts agree'),
        platform_dependencies_builds_installs_downloads_gpu_push=False,
        scope='four MMLU requests, legacy two-layer/two-step graph, declared uncalibrated compute/DCQCN; no holdout, full thousand-GPU inference or novelty')
    save(canonical/'closeout.json',receipt);save(dest/'closeout.json',receipt)
    print('research closed: three new networks, immutable old results, six offline reports equal; no push')


if __name__=='__main__':main()
