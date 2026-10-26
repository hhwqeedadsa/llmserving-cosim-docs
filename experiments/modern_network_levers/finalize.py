"""Persist research evidence, verify report data and account the closed stage."""
import argparse
from html.parser import HTMLParser
from pathlib import Path
import shutil
from experiments.modern_network_levers.analyze import load,save,sha
from experiments.modern_network_levers.checkpoint import admit
from experiments.modern_network_levers.render import render


def usage(root):
    logical=allocated=0
    for path in [root,*root.rglob('*')]:
        if path.is_symlink():continue
        s=path.stat();allocated+=s.st_blocks*512
        if path.is_file():logical+=s.st_size
    return max(logical,allocated)


class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':self.links.extend(v for k,v in attrs if k=='href')


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage','outer-monitor'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve()
    assert load(stage/'execution.json')['closed'] and load(stage/'restored/recovery.json')['passed']
    archive=stage/'checkpoint.tar.gz';_,manifest=admit(archive)
    for name,entry in manifest['files'].items():
        if name.startswith('source/'):assert sha(repo/name.removeprefix('source/'))==entry['sha256'],name
    identity=load(stage/'runtime-identity.json');runtime=Path(load(stage/'source.json')['runtime'])
    assert all(sha(runtime/name)==digest for name,digest in identity['files'].items())
    assert all(sha(repo/name)==digest for name,digest in identity['sources'].items())
    page=(stage/'report.html').read_bytes();assert render(stage).encode()==page==render(stage).encode()
    persistent=repo/'runs/modern_network_levers_checkpoint_20261026';persistent.mkdir(exist_ok=False)
    for name in ('checkpoint.tar.gz','checkpoint.tar.receipt.json','summary.json','verification.json','sources.json'):
        shutil.copyfile(stage/name,persistent/name);assert sha(stage/name)==sha(persistent/name)
    shutil.copyfile(stage/'restored/recovery.json',persistent/'recovery.json')
    results=repo/'experiments/modern_network_levers/results/20261026';results.mkdir(parents=True,exist_ok=False)
    for name in ('summary.json','verification.json','sources.json','report.html'):shutil.copyfile(stage/name,results/name)
    shutil.copyfile(stage/'restored/recovery.json',results/'recovery.json')
    parser=Links();parser.feed(page.decode())
    assert parser.links and all((results/href).is_file() for href in parser.links)
    probes=[path for path in stage.rglob('probe.json') if path.relative_to(stage).parts[0]!='restored']
    probes.append(a.outer_monitor/'probe.json');reports=[load(path) for path in probes]
    assert all(r['status']=='completed' and not r['remaining_live_group_pids'] for r in reports)
    seconds=sum(r['wall_seconds'] for r in reports);assert seconds+10<450
    roots=[stage,persistent,repo/'experiments/modern_network_levers',a.outer_monitor]
    charged=sum(usage(root) for root in roots)+2*2**20;assert charged<256*2**20
    closeout=dict(passed=True,network_phase_closed=True,actual_network_executions=3,successful_network_executions=3,
        unchanged_successful_network_reexecutions=0,failed_network_executions=0,analysis_failures=0,
        seven_tamper_rejections=load(stage/'verification.json')['tamper_rejections'],
        cumulative_monitored_seconds_including_nested=seconds,final_management_seconds_reserved=10,
        cumulative_storage_charged_bytes_including_2MiB_reserve=charged,cumulative_storage_limit_bytes=256*2**20,
        peak_sampled_group_rss_bytes=max(r['peak_sampled_group_rss_bytes'] for r in reports),
        terminal_probe_receipts=[str(p) for p in probes],archive_sha256=sha(archive),archive_members=len(manifest['files'])+1,
        archive_scope='pre-closeout research source and evidence; final terminal receipts alongside archive',
        archive_research_sources_current=True,offline_recovery_two_json_byte_identical=True,
        native_runtime_and_sources_unchanged=True,dependency_checkouts_verified=13,
        cached_primary_documents_verified=8,html_deterministic=True,html_local_links_valid=True,
        browser_layout_verified=False,platform_modified=False,native_recompiled=False,gpu_executed=False,
        new_method_benefit_validated=False,thousand_gpu_modern_call_validated=False,pushed=False,
        overall_research_goal_status='in_progress')
    for target in (stage/'closeout.json',persistent/'closeout.json',results/'closeout.json'):save(target,closeout)
    for path in probes:
        target=persistent/'terminal-receipts'/(path.relative_to(stage) if path.is_relative_to(stage) else Path('driver-monitor/probe.json'))
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
    save(persistent/'source-manifest.json',manifest)
    print({k:closeout[k] for k in ('passed','archive_sha256','cumulative_monitored_seconds_including_nested',
        'cumulative_storage_charged_bytes_including_2MiB_reserve')})


if __name__=='__main__':main()
