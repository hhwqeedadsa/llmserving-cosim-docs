"""Close four bounded networks, preserve runtime/source identity and failures."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
from fixtures.nic_wakeup.stage import load,save,sha
from fixtures.nic_wakeup.checkpoint import admit


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
    commands=load(s/'commands.json');assert all(c['status']=='terminal' for c in commands)
    networks=[c for c in commands if c['network']];assert len(networks)==4 and all(c['exit_code']==0 for c in networks)
    assert load(s/'verification-final.json')['passed'] and load(s/'final-replay.json')['final_two_offline_reports_byte_identical']
    assert load(s/'recovery-evidence/healthy-audit.json')['passed'] and load(s/'archive-safety/verification.json')['passed']
    build=load(s/'build.json')
    for group in ('inputs','libraries'):
        assert all(sha(Path(name))==value for name,value in build[group].items())
    assert all(sha(s/name)==value for name,value in build['binaries'].items())
    assert all(sha(repo/name)==value for name,value in build['platform_sources'].items())
    native=repo/'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3'
    assert all(sha(native/name)==value for name,value in build['native_sources'].items())
    for name,item in load(s/'dependency-patches.json')['changes'].items():
        assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo/item['path'],text=True).strip()==item['new']
        assert not subprocess.check_output(['git','status','--porcelain','--ignore-submodules=all'],cwd=repo/item['path'],text=True).strip()
    path=s/'evidence-final.tar.gz';content,manifest=admit(path)
    for name,entry in manifest['files'].items():
        if name.startswith('source/'):assert sha(repo/name[len('source/'):])==entry['sha256']
    _,v1=admit(s/'evidence-v1.tar.gz')
    for name,entry in v1['files'].items():
        if name.startswith(('runtime/','source/python/')):assert manifest['files'][name]==entry
    monitors=[]
    for p in s.rglob('probe.json'):
        parts=p.relative_to(s).parts
        if parts[:2]==('restored','evidence'):continue
        monitors.append(p)
    reports=[load(p) for p in monitors]
    assert all(r['status'] in ('completed','child_failed') and not r['remaining_live_group_pids'] for r in reports)
    seconds=sum(r['wall_seconds'] for r in reports);assert seconds+10<900
    assert os.statvfs(s).f_bavail*os.statvfs(s).f_frsize>8*2**30
    assert os.statvfs(repo).f_bavail*os.statvfs(repo).f_frsize>64*2**20
    dest=repo/'runs/nic_wakeup_checkpoint_20261024';assert not dest.exists();dest.mkdir()
    shutil.copyfile(path,dest/'evidence.tar.gz');shutil.copyfile(path.with_suffix('.receipt.json'),dest/'archive.json')
    assert sha(path)==sha(dest/'evidence.tar.gz')
    canonical=repo/'fixtures/nic_wakeup'
    outputs={'verification.json':s/'verification-final.json','recovery.json':s/'final-replay.json',
        'healthy-audit.json':s/'recovery-evidence/healthy-audit.json','archive-safety.json':s/'archive-safety/verification.json'}
    for name,path in outputs.items():assert not (canonical/name).exists();shutil.copyfile(path,canonical/name)
    apparent,allocated=usage([s,dest,canonical]);reserve=2*2**20;charged=max(apparent,allocated)+reserve;assert charged<512*2**20
    failed=[c['label'] for c in commands if c['exit_code']!=0]
    result=dict(schema_version='nic-wakeup-closeout/1',ended_utc=datetime.now(timezone.utc).isoformat(),
        platform_code_commit='ca915ed',native_patch_commit='68a07f7',platform_stage_complete=True,overall_paper_goal_complete=False,
        network_attempts=4,successful_networks=4,network_phase_closed=True,successful_network_reruns=0,native_cpu_conditions=15,
        original_terminal_monitors=len(monitors),conservative_cumulative_monitored_seconds_including_nested=seconds,
        final_management_seconds_reserved=10,peak_sampled_group_rss_bytes=max(r['peak_sampled_group_rss_bytes'] for r in reports),
        stage_archives_restored_persistent_apparent_bytes=apparent,stage_archives_restored_persistent_allocated_bytes=allocated,
        source_and_final_receipt_reserve_bytes=reserve,total_charged_with_reserve_bytes=charged,
        limits=dict(cumulative_seconds=900,cumulative_bytes=512*2**20,per_command_seconds=60,rss_bytes=2**30,address_bytes=2**31,
            artifact_bytes=192*2**20,max_networks=4,max_sim_ns=5000000,result_json_bytes=16*2**20),
        remaining_monitored_live_pids=[],preserved_failed_command_labels=failed,
        startup_failure_without_monitor='restored-healthy-wire-port-audit: missing host module in checker-only PYTHONPATH; command/error retained',
        archive=load(dest/'archive.json'),evidence=dict(all_archive_members_verified=True,final_sources_equal_current=True,
            execution_runtime_and_platform_python_equal_verified_v1=True,final_two_offline_reports_byte_identical=True,
            one_fresh_public_default_network=True,all_eight_libraries_loaded_from_restored_root=True,
            physical_data_ack_bytes_and_timer_causality=True,transaction_remote_ops=5,transaction_local_copies=3,transaction_memory_events=25,
            semantic_tamper_classes=4,unsafe_archive_classes=6,fixed_dependencies='13 checkouts agree after ca915ed',
            compiled_native_objects=3,changed_native_objects=2,copied_unchanged_libraries=7,
            old_inputs_sources_objects_libraries_preserved=True,local_dependency_bundles_and_upstream_source_patch=True),
        builds='incremental fixed native objects and links; no clean build',
        source_changes='local native patch plus nested gitlinks/lock; no upstream update, UB or serving change',
        installs_downloads_gpu_push=False,
        limitations='same-ns tests are synthetic CPU states; complete application effect, calibration, modern inference, holdout, thousand-GPU and novelty unproven')
    save(canonical/'closeout.json',result);save(dest/'closeout.json',result)
    print('platform stage closed: four networks, 15 native CPU conditions; no push')


if __name__=='__main__':main()
