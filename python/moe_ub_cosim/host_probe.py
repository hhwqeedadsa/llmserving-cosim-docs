"""Linux subprocess resource probe. Limits are probe policy, never simulated HBM.

RSS is sampled and summed across this probe's process group (shared pages may
be counted twice). RLIMIT_AS and RLIMIT_FSIZE are separate per-process limits.
Only the process group created here can be terminated by the monitor.
"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time


def members(pgid):
    rows=[]
    for path in Path('/proc').iterdir():
        if not path.name.isdecimal():continue
        try:
            fields=(path/'stat').read_text().rsplit(')',1)[1].split()
            if int(fields[2])!=pgid or fields[0]=='Z':continue
            rss=int((path/'statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')
            rows.append(dict(pid=int(path.name),rss_bytes=rss))
        except (FileNotFoundError,ProcessLookupError,PermissionError):continue
    return rows


def disk_bytes(path):
    # Do not follow output symlinks into unrelated directories.
    total=0
    for directory,_,files in os.walk(path,followlinks=False):
        for name in files:
            p=Path(directory)/name
            try:
                if not p.is_symlink():total+=p.stat().st_size
            except FileNotFoundError:pass
    return total


def probe(command,output,*,rss_bytes,address_bytes,artifact_bytes,wall_seconds):
    if not command or min(rss_bytes,address_bytes,artifact_bytes,wall_seconds)<=0:
        raise ValueError('Positive explicit limits and a command are required')
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    def limits():
        for kind,value in ((resource.RLIMIT_AS,address_bytes),(resource.RLIMIT_FSIZE,artifact_bytes),(resource.RLIMIT_CORE,0)):
            _,hard=resource.getrlimit(kind)
            value=value if hard==resource.RLIM_INFINITY else min(value,hard)
            resource.setrlimit(kind,(value,value))
    report=dict(schema_version='moe-ub-host-probe/1',command=command,cwd=str(Path.cwd()),
        started_utc=datetime.now(timezone.utc).isoformat(),
        limits=dict(sampled_group_rss_bytes=rss_bytes,per_process_address_bytes=address_bytes,
                    artifact_bytes=artifact_bytes,wall_seconds=wall_seconds),
        peak_sampled_group_rss_bytes=0,peak_sampled_processes=0,status='starting',workload_semantics_validated=False)
    start=time.monotonic();child=None;reason=None
    def stop():
        if child is None:return
        try:os.killpg(child.pid,signal.SIGTERM)
        except ProcessLookupError:return
        time.sleep(.1)
        try:os.killpg(child.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    try:
        with (output/'stdout.log').open('w') as stdout,(output/'stderr.log').open('w') as stderr,(output/'samples.jsonl').open('w') as samples:
            child=subprocess.Popen(command,stdout=stdout,stderr=stderr,start_new_session=True,preexec_fn=limits)
            report['process_group']=child.pid
            while True:
                elapsed=time.monotonic()-start;group=members(child.pid);rss=sum(p['rss_bytes'] for p in group)
                size=disk_bytes(output)
                report['peak_sampled_group_rss_bytes']=max(report['peak_sampled_group_rss_bytes'],rss)
                report['peak_sampled_processes']=max(report['peak_sampled_processes'],len(group))
                samples.write(json.dumps(dict(elapsed_seconds=elapsed,group_rss_bytes=rss,artifact_bytes=size,processes=group))+'\n');samples.flush()
                if rss>rss_bytes:reason='rss_limit'
                elif size>artifact_bytes:reason='artifact_limit'
                elif elapsed>wall_seconds:reason='wall_limit'
                if reason:stop();break
                if child.poll() is not None:
                    # The parent can exit between the RSS sample and poll().
                    # Re-read instead of treating that stale parent as a live descendant.
                    if members(child.pid):reason='descendants_after_exit';stop()
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
            if members(child.pid):stop()
            report['remaining_live_group_pids']=[p['pid'] for p in members(child.pid)]
        report.update(ended_utc=datetime.now(timezone.utc).isoformat(),wall_seconds=time.monotonic()-start,
            max_waited_child_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
            artifact_bytes_before_report=disk_bytes(output))
        (output/'probe.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--rss-bytes',type=int,required=True);p.add_argument('--address-bytes',type=int,required=True)
    p.add_argument('--artifact-bytes',type=int,required=True);p.add_argument('--wall-seconds',type=float,required=True)
    p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args();command=a.command
    if command and command[0]=='--':command=command[1:]
    report=probe(command,a.output_dir,rss_bytes=a.rss_bytes,address_bytes=a.address_bytes,
        artifact_bytes=a.artifact_bytes,wall_seconds=a.wall_seconds)
    print(report['status'],'child exit',report['child_exit_code'],'sampled group RSS',report['peak_sampled_group_rss_bytes'])
    sys.exit(0 if report['status']=='completed' and not report['remaining_live_group_pids'] else 1)


if __name__=='__main__':main()
