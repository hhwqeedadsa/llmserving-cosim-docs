"""Terminal resources and fixed runtime/source identities; does not run networks."""
import argparse
from pathlib import Path
from moe_ub_cosim.host_probe import disk_usage
from fixtures.nack_grace_edges.stage import load,save,sha


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage','persistent','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();assert not a.output.exists()
    reports=[(str(p.relative_to(s)),load(p)) for p in s.rglob('probe.json') if 'restored' not in p.parts]
    assert all(v['status'] not in ('starting','running') and not v.get('remaining_live_group_pids') for _,v in reports)
    total=sum(v['wall_seconds'] for _,v in reports);assert total+10<480
    peak=max(v['peak_sampled_group_rss_bytes'] for _,v in reports);assert peak<=2**30
    runtime=load(s/'runtime.json')
    assert all(sha(p)==h for p,h in runtime['binaries'].items())
    assert all(sha(p)==h for p,h in runtime['libraries'].items())
    assert all(sha(repo/p)==h for p,h in runtime['sources'].items())
    assert (s/'aggregate-final.json').read_bytes()==(s/'replay-aggregate.json').read_bytes()
    usage=disk_usage(s);persistent=disk_usage(a.persistent);reserve=2*2**20
    amount=max(usage.values())+max(persistent.values())+reserve;assert amount<256*2**20
    records=load(s/'commands.json');assert sum(r['network'] for r in records)==4
    assert all(r['exit_code']==r['expected_exit'] for r in records if r['label']!='verify')
    failed=[dict(path=p,status=v['status'],child_exit_code=v.get('child_exit_code')) for p,v in reports if v['status']!='completed']
    save(a.output,dict(platform_core_acceptance=True,overall_goal='in_progress',network_attempts=4,phase_closed=True,
        successful_applications=3,expected_isolated_partial=1,original_terminal_monitors=len(reports),
        wall_seconds_conservative_including_nested=total,final_management_reserve_seconds=10,peak_sampled_group_rss_bytes=peak,
        stage_usage=usage,persistent_usage=persistent,source_and_receipt_reserve_bytes=reserve,accounted_storage_bytes=amount,
        cumulative_limits=dict(wall_seconds=480,storage_bytes=256*2**20),nonzero_monitors=failed,
        expected_nonzero_scope='isolated stream application and nested native; one preserved offline negative-test bug',
        runtime_and_native_sources_unchanged=True,aggregate_replay_byte_equal=True,
        same_ns_integrated_packet_session_global_stop_not_claimed=True,
        no_dependency_build_install_download_gpu_or_push=True))

if __name__=='__main__':main()
