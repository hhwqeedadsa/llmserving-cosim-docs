"""Account saved terminal monitors and source/runtime identities without network execution."""
import argparse
from pathlib import Path
from moe_ub_cosim.host_probe import disk_usage
from fixtures.nack_grace.stage import load,save,sha


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage','persistent','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();assert not a.output.exists()
    reports=[(str(p.relative_to(s)),load(p)) for p in s.rglob('probe.json') if 'restored' not in p.parts]
    assert reports and all(v['status'] not in ('starting','running') and not v.get('remaining_live_group_pids') for _,v in reports)
    total=sum(v['wall_seconds'] for _,v in reports);assert total+10<720
    peak=max(v['peak_sampled_group_rss_bytes'] for _,v in reports);assert peak<=2**30
    build=load(s/'build.json');assert all(sha(p)==h for p,h in build['inputs'].items())
    assert all(sha(s/'bin'/p)==h for p,h in build['binaries'].items())
    assert all(sha(repo/p)==h for p,h in build['sources'].items())
    manifest=load(s/'restored/manifest.json')
    assert all(sha(s/'restored'/p)==entry['sha256'] for p,entry in manifest['files'].items())
    assert (s/'inspection.json').read_bytes()==(s/'replay-inspection.json').read_bytes()
    usage=disk_usage(s);stored=disk_usage(a.persistent);reserved=2*2**20
    amount=max(usage.values())+max(stored.values())+reserved;assert amount<384*2**20
    assert sum(x['network'] for x in load(s/'commands.json'))==6
    failures=[dict(path=p,status=v['status'],child_exit_code=v.get('child_exit_code')) for p,v in reports if v['status']!='completed']
    save(a.output,dict(stage_closed_for_networks=True,platform_acceptance_complete=False,overall_goal='in_progress',
        original_terminal_monitors=len(reports),wall_seconds_conservative_including_nested=total,final_management_reserve_seconds=10,
        peak_sampled_group_rss_bytes=peak,stage_usage=usage,persistent_usage=stored,source_and_receipt_reserve_bytes=reserved,
        accounted_storage_bytes=amount,cumulative_limits=dict(wall_seconds=720,storage_bytes=384*2**20),
        preserved_failures=failures,source_hashes_match_build=True,fixed_runtime_unchanged=True,
        offline_replay_byte_equal=True,network_attempts=6,successful_networks=5,dependencies_checked=13,
        missing_gate='real packet-level cumulative progress cancels a pending grace timer',
        no_dependency_build_install_download_gpu_or_push=True))

if __name__=='__main__':main()
