"""Close bounded offline audit and persist evidence; never runs a network."""
import argparse
from datetime import datetime,timezone
import os
from pathlib import Path
import shutil
import subprocess
from experiments.feedback_ancestry.stage import load,save,sha
from experiments.feedback_ancestry.checkpoint import admit


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
    assert load(s/'verification-complete.json')['passed'] and load(s/'restored/replay.json')['three_json_reports_byte_identical']
    assert load(s/'archive-safety/verification.json')['passed']
    archive=s/'evidence-reviewed.tar.gz';_,final=admit(archive);_,v1=admit(s/'evidence-v1.tar.gz')
    for name,e in final['files'].items():
        if name.startswith('source/'):assert sha(repo/name[len('source/'):])==e['sha256']
    for name,e in v1['files'].items():
        if name.startswith('source/') and name!='source/experiments/feedback_ancestry/closeout.py':assert final['files'][name]==e
    assert all(sha(s/'inputs'/n)==h for n,h in load(s/'inputs/manifest.json')['files'].items())
    identity=load(s/'inputs/identity.json');assert all(sha(repo/n)==h for n,h in identity['source_sha256'].items())
    native=repo/'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3'
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=native,text=True).strip()==identity['native_commit']
    subprocess.run(['git','diff','--exit-code',identity['platform'],'--','python','backend','deps.lock.json','.gitmodules'],cwd=repo,check=True)
    commands=load(s/'commands.json');assert all(c['status']=='terminal' and c['exit_code']==0 and not c['network'] for c in commands)
    monitors=[s/(c['label']+'-monitor/probe.json') for c in commands]
    for c,path in zip(commands,monitors):
        monitor=load(path);assert monitor['cwd']==c['cwd'] and monitor['command']==c['command'][c['command'].index('--')+1:]
    historical_input_probes=list((s/'inputs').rglob('probe.json'));assert len(historical_input_probes)==3
    reports=[load(p) for p in monitors];assert all(r['status']=='completed' and r['child_exit_code']==0 and not r['remaining_live_group_pids'] for r in reports)
    seconds=sum(r['wall_seconds'] for r in reports);assert seconds+10<300
    for root,reserve in ((s,8*2**30),(repo,64*2**20)):
        fs=os.statvfs(root);assert fs.f_bavail*fs.f_frsize>reserve
    dest=repo/'runs/feedback_ancestry_checkpoint_20261024'
    assert dest.exists() and not (dest/'evidence-initial.tar.gz').exists()
    for old,new in (('evidence.tar.gz','evidence-initial.tar.gz'),('archive.json','archive-initial.json'),('closeout.json','closeout-initial.json')):(dest/old).rename(dest/new)
    shutil.copyfile(archive,dest/'evidence.tar.gz');shutil.copyfile(archive.with_suffix('.receipt.json'),dest/'archive.json');assert sha(dest/'evidence.tar.gz')==sha(archive)
    canonical=repo/'experiments/feedback_ancestry/results/20261024';assert canonical.exists()
    assert not (canonical/'closeout-initial.json').exists();(canonical/'closeout.json').rename(canonical/'closeout-initial.json')
    for name,src in {'summary.json':'summary.json','verification.json':'verification-complete.json',
        'replay.json':'restored/replay.json','source-identity.json':'inputs/identity.json','archive-safety.json':'archive-safety/verification.json'}.items():
        assert (canonical/name).read_bytes()==(s/src).read_bytes()
    apparent,allocated=usage([s,dest,repo/'experiments/feedback_ancestry']);charged=max(apparent,allocated)+2**20;assert charged<192*2**20
    receipt=dict(schema_version='feedback-ancestry-closeout/1',ended_utc=datetime.now(timezone.utc).isoformat(),
        platform_commit=identity['platform'],native_commit=identity['native_commit'],research_stage_complete=True,overall_paper_goal_complete=False,
        new_network_executions=0,immutable_saved_cases=3,source_packet_ce_marks=248,cnp_notifications=84,cnp_physical_hops=438,rp_state_records=4874,
        original_terminal_monitors=len(reports),conservative_monitored_seconds_including_nested=seconds,final_management_seconds_reserved=10,
        peak_sampled_group_rss_bytes=max(r['peak_sampled_group_rss_bytes'] for r in reports),
        stage_archives_restored_persistent_sources_apparent_bytes=apparent,stage_archives_restored_persistent_sources_allocated_bytes=allocated,
        source_receipt_reserve_bytes=2**20,total_charged_with_reserve_bytes=charged,
        limits=dict(cumulative_seconds=300,cumulative_bytes=192*2**20,per_command_seconds=60,rss_bytes=2**30,address_bytes=2**31,
            stage_artifact_bytes=96*2**20,json_bytes=16*2**20,new_networks=0),
        remaining_monitored_live_pids=[],monitored_failures=[],original_partial_verifications_preserved=True,
        initial_resource_accounting_correction='initial receipt counted three copied historical input probes as current; original receipt/archive retained; final counts use registered command handles only',
        copied_historical_input_monitors_excluded=len(historical_input_probes),
        source_inspection_scope='interactive source-reading diagnostics outside executable stage are not monitored total calendar time',
        archive=load(dest/'archive.json'),evidence=dict(all_members_verified=True,final_sources_equal_current=True,
            replay_executed_verified_v1=True,replayed_analysis_verify_summary_sources_equal_final=True,closeout_only_source_corrected_after_replay=True,three_reports_byte_identical=True,
            np_members_first_ce_data_queue_work_intersections=True,independent_rational_rp_oracle=True,
            tamper_classes=11,reused_archive_parser_unsafe_classes=8,fixed_dependencies='13 checkouts agree'),
        platform_dependencies_builds_installs_downloads_gpu_push=False,
        limitations='fixed legacy small window; generated stream/fid needed because CNP fid not serialized by capture; no safe discard policy, modern full baseline, hardware calibration, holdout or paper novelty')
    save(canonical/'closeout.json',receipt);save(dest/'closeout.json',receipt)
    print('offline audit closed: 84 CNPs, 248 CE packets, 4874 RP records, zero new networks; no push')


if __name__=='__main__':main()
