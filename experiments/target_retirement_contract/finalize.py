"""Finalize receipts after all monitored workers have exited; no workload execution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import time


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')


def main():
    started=time.monotonic()
    signal.alarm(10)
    resource.setrlimit(resource.RLIMIT_AS,(2*2**30,2*2**30))
    resource.setrlimit(resource.RLIMIT_DATA,(2**30,2**30))
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('stage','persist','results'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();stage=a.stage.resolve();persist=a.persist.resolve();results=a.results.resolve()
    root=Path(__file__).resolve().parents[2];results.mkdir(parents=True,exist_ok=True)
    rows=[];group_ids=set()
    for path in sorted(stage.glob('*-monitor/probe.json')):
        row=json.loads(path.read_text());assert not row['remaining_live_group_pids']
        assert row['status'] in ('completed','wall_limit','child_failed')
        group_ids.add(row['process_group'])
        rows.append(dict(name=path.parent.name,status=row['status'],wall_seconds=row['wall_seconds'],
            peak_sampled_group_rss_bytes=row['peak_sampled_group_rss_bytes'],
            max_waited_child_rss_kib=row['max_waited_child_rss_kib']))
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields=path.read_text().rsplit(')',1)[1].split()
            assert int(fields[2]) not in group_ids or fields[0]=='Z','live monitored group remains'
        except FileNotFoundError:pass
    wall=sum(r['wall_seconds'] for r in rows)
    assert wall+10<240,'phase wall allowance exceeded'
    assert max(r['peak_sampled_group_rss_bytes'] for r in rows)<=2**30
    assert max(r['max_waited_child_rss_kib'] for r in rows)*1024<=2**30
    assert shutil.disk_usage(stage).free>=8*2**30
    assert shutil.disk_usage(root).free>=64*2**20
    for target,source in [('summary.json',stage/'summary.json'),('verification.json',stage/'verification.json'),
            ('correction.json',stage/'correction.json'),('sources.json',stage/'inputs/sources.json'),
            ('archive.json',persist/'archive.json'),('replay.json',persist/'replay.json'),
            ('restore-check.json',persist/'restore-check.json'),('closeout.json',stage/'closeout.json')]:
        shutil.copyfile(source,results/target)
        if (persist/target).resolve()!=source.resolve():shutil.copyfile(source,persist/target)
    post=persist/'post-package-source';shutil.copyfile(Path(__file__),post/Path(__file__).name)
    for path in sorted(stage.glob('*-monitor/probe.json')):
        target=persist/'final-monitors'/path.parent.name;target.mkdir(parents=True)
        for filename in ('probe.json','stdout.log','stderr.log'):
            source=path.parent/filename
            if source.exists():shutil.copyfile(source,target/filename)
    # Check repository links after copying the final receipts. Links to anchors are skipped.
    checked_links=[]
    for document in (root/'docs/reviews/目标内存撤销与恢复强基线_20261024.md',Path(__file__).parent/'README.md'):
        for rel in re.findall(r'\]\(([^)]+)\)',document.read_text()):
            if '://' in rel or rel.startswith('#'):continue
            target=(document.parent/rel.split('#')[0]).resolve()
            assert target.exists(),'missing link '+str(target)
            checked_links.append(str(target.relative_to(root)))
    apparent=0;allocated=0;seen=set()
    roots=(stage,persist,Path(__file__).parent,root/'docs/reviews/目标内存撤销与恢复强基线_20261024.md')
    for directory in roots:
        paths=[directory]+(list(directory.rglob('*')) if directory.is_dir() else [])
        for path in paths:
            assert not path.is_symlink()
            stat=path.stat();identity=(stat.st_dev,stat.st_ino)
            if identity in seen:continue
            seen.add(identity);allocated+=stat.st_blocks*512
            if path.is_file():apparent+=stat.st_size
    # Bounded reserve includes this receipt, copied receipt, manifest and final fsync overhead.
    reserve=128*1024;assert max(apparent,allocated)+reserve<64*2**20
    initial=json.loads((stage/'acquisition.json').read_text())
    api=json.loads((stage/'api-acquisition.json').read_text())+[json.loads((stage/'umr-acquisition.json').read_text())]
    known_responses=sum(r.get('bytes',0) for r in initial)+sum(r['response_bytes'] for r in api)
    assert known_responses<=2*2**20 and max(r['response_bytes'] for r in api)<=256*1024
    result=dict(schema_version='target-retirement-budget/1',original_monitor_records=rows,
        monitored_seconds=wall,finalization_seconds_reserved=10,stage_limit_seconds=240,
        all_monitored_groups_exited=True,unique_apparent_bytes_before_receipts=apparent,
        unique_allocated_bytes_before_receipts=allocated,final_receipts_reserved_bytes=reserve,
        stage_disk_limit_bytes=64*2**20,tmpfs_free_bytes=shutil.disk_usage(stage).free,
        root_free_bytes=shutil.disk_usage(root).free,known_recorded_response_bytes=known_responses,
        api_response_count=len(api),initial_raw_batch_unknown_outcomes_preserved=True,
        original_single_request_8_second_goal_violated=True,
        original_wall_limited_command_termination_overhead_preserved=True,
        checked_repository_links=len(checked_links),syntax_checked_files=sorted(p.name for p in Path(__file__).parent.glob('*.py')),
        finalization_elapsed_before_receipts_seconds=time.monotonic()-started)
    for path in Path(__file__).parent.glob('*.py'):compile(path.read_text(),str(path),'exec')
    save(results/'budget.json',result);save(persist/'budget.json',result)
    manifest={str(path.relative_to(persist)):dict(bytes=path.stat().st_size,sha256=sha(path))
        for path in sorted(persist.rglob('*')) if path.is_file() and path.name!='receipts-manifest.json'}
    save(persist/'receipts-manifest.json',manifest)
    for path in persist.rglob('*'):
        if path.is_file():
            with path.open('rb') as stream:os.fsync(stream.fileno())
    for path in results.glob('*.json'):
        with path.open('rb') as stream:os.fsync(stream.fileno())
    print(json.dumps(dict(monitored_seconds=wall,conservative_seconds=wall+10,
        disk_bytes_with_reserve=max(apparent,allocated)+reserve,monitors=len(rows),
        checked_links=len(checked_links),persistent_receipt_files=len(manifest))))


if __name__=='__main__':main()
