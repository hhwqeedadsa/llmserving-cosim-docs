"""Linux subprocess resource probe. Limits are probe policy, never simulated HBM.

RSS is sampled across owned descendants, including detached sessions (shared
pages may be counted twice). RLIMIT_AS and RLIMIT_FSIZE are per-process limits.
Only descendants owned by this dedicated monitor can be terminated.
"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import resource
import signal
import shutil
import subprocess
import sys
import time

from .host_processes import ProcessTree

def disk_bytes(path, seen=None):
    # Do not follow output symlinks into unrelated directories.
    # Hard-linked registries occupy storage once, including across task roots.
    if seen is None:seen=set()
    total=0
    for directory,_,files in os.walk(path,followlinks=False):
        for name in files:
            p=Path(directory)/name
            try:
                if not p.is_symlink():
                    stat=p.stat();key=stat.st_dev,stat.st_ino
                    if key not in seen:total+=stat.st_size;seen.add(key)
            except FileNotFoundError:pass
    return total


def probe(command,output,*,rss_bytes,address_bytes,artifact_bytes,wall_seconds,artifact_roots=(),min_free_bytes=0):
    if not command or min(rss_bytes,address_bytes,artifact_bytes,wall_seconds)<=0:
        raise ValueError('Positive explicit limits and a command are required')
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    if min_free_bytes<0:raise ValueError('Free disk reserve must be nonnegative')
    # Count a shared task root once, even when it contains this probe output.
    roots=sorted({output,*(Path(p).resolve() for p in artifact_roots)},key=lambda p:len(p.parts))
    counted=[]
    for path in roots:
        if not any(path.is_relative_to(parent) for parent in counted):counted.append(path)
    def size_now():
        seen=set()
        return sum(disk_bytes(path,seen) for path in counted)
    def limits():
        for kind,value in ((resource.RLIMIT_AS,address_bytes),(resource.RLIMIT_FSIZE,artifact_bytes),(resource.RLIMIT_CORE,0)):
            _,hard=resource.getrlimit(kind)
            value=value if hard==resource.RLIM_INFINITY else min(value,hard)
            resource.setrlimit(kind,(value,value))
    report=dict(schema_version='moe-ub-host-probe/1',command=command,cwd=str(Path.cwd()),
        started_utc=datetime.now(timezone.utc).isoformat(),
        limits=dict(sampled_group_rss_bytes=rss_bytes,per_process_address_bytes=address_bytes,
                    artifact_bytes=artifact_bytes,wall_seconds=wall_seconds),
        peak_sampled_group_rss_bytes=0,peak_sampled_processes=0,status='starting',workload_semantics_validated=False,
        artifact_roots=[str(p) for p in counted],peak_sampled_artifact_bytes=0,min_free_bytes=min_free_bytes,
        rss_scope='owned_process_tree_including_detached_descendants',
        artifact_accounting='unique_regular_file_inodes_apparent_bytes')
    start=time.monotonic();child=None;reason=None;tree=None
    def stop():
        if child is None:return
        tree.terminate(signal.SIGTERM)
        time.sleep(.1)
        tree.terminate(signal.SIGKILL)
        for _ in range(100):
            if not tree.collect():break
            time.sleep(.01)
    try:
        with (output/'stdout.log').open('w') as stdout,(output/'stderr.log').open('w') as stderr,(output/'samples.jsonl').open('w') as samples:
            initial_size=size_now();free=shutil.disk_usage(output).free
            report['peak_sampled_artifact_bytes']=initial_size
            if initial_size>artifact_bytes or free<min_free_bytes:
                report.update(status='artifact_limit' if initial_size>artifact_bytes else 'disk_free_limit',child_exit_code=None)
                return report
            tree=ProcessTree()
            child=subprocess.Popen(command,stdout=stdout,stderr=stderr,start_new_session=True,preexec_fn=limits)
            tree.attach(child.pid)
            report['process_group']=child.pid
            while True:
                elapsed=time.monotonic()-start;group=tree.collect();rss=sum(p['rss_bytes'] for p in group)
                size=size_now();free=shutil.disk_usage(output).free
                report['peak_sampled_artifact_bytes']=max(report['peak_sampled_artifact_bytes'],size)
                report['peak_sampled_group_rss_bytes']=max(report['peak_sampled_group_rss_bytes'],rss)
                report['peak_sampled_processes']=max(report['peak_sampled_processes'],len(group))
                samples.write(json.dumps(dict(elapsed_seconds=elapsed,group_rss_bytes=rss,artifact_bytes=size,free_disk_bytes=free,processes=group))+'\n');samples.flush()
                if rss>rss_bytes:reason='rss_limit'
                elif size>artifact_bytes:reason='artifact_limit'
                elif free<min_free_bytes:reason='disk_free_limit'
                elif elapsed>wall_seconds:reason='wall_limit'
                if reason:stop();break
                if child.poll() is not None:
                    # The parent can exit between the RSS sample and poll().
                    # Re-read instead of treating that stale parent as a live descendant.
                    if tree.collect():reason='descendants_after_exit';stop()
                    else:reason='completed' if child.returncode==0 else 'child_failed'
                    break
                time.sleep(.1)
            code=child.wait();report.update(status=reason,child_exit_code=code)
    except BaseException as error:
        report.update(status='monitor_error',error=type(error).__name__);stop()
        if child is not None:report['child_exit_code']=child.wait()
        raise
    finally:
        if child is not None:
            if tree.collect():stop()
            report['remaining_live_group_pids']=[p['pid'] for p in tree.collect()]
        if tree is not None:tree.close()
        report.setdefault('remaining_live_group_pids',[])
        report.update(ended_utc=datetime.now(timezone.utc).isoformat(),wall_seconds=time.monotonic()-start,
            max_waited_child_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
            artifact_bytes_before_report=disk_bytes(output),aggregate_artifact_bytes_before_report=size_now())
        (output/'probe.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--rss-bytes',type=int,required=True);p.add_argument('--address-bytes',type=int,required=True)
    p.add_argument('--artifact-bytes',type=int,required=True);p.add_argument('--wall-seconds',type=float,required=True)
    p.add_argument('--artifact-root',type=Path,action='append',default=[],help='Additional task directories sharing artifact budget')
    p.add_argument('--min-free-bytes',type=int,default=0)
    p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args();command=a.command
    if command and command[0]=='--':command=command[1:]
    report=probe(command,a.output_dir,rss_bytes=a.rss_bytes,address_bytes=a.address_bytes,
        artifact_bytes=a.artifact_bytes,wall_seconds=a.wall_seconds,artifact_roots=a.artifact_root,min_free_bytes=a.min_free_bytes)
    print(report['status'],'child exit',report['child_exit_code'],'sampled group RSS',report['peak_sampled_group_rss_bytes'])
    sys.exit(0 if report['status']=='completed' and not report['remaining_live_group_pids'] else 1)


if __name__=='__main__':main()
