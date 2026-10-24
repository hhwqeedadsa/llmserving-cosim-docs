"""Fixed-budget native callback acceptance; six network executions maximum."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def load(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2)+'\n')


def sha(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def roots(repo, stage):
    return [stage, repo/'fixtures/native_wakeup', repo/'runs/native_wakeup_checkpoint_20261024',
            repo/'python/moe_ub_cosim', repo/'tests/unit/native_wakeup_test.py',
            repo/'tests/unit/session_wakeups_test.cc', repo/'backend/rdma',
            *[repo/'docs'/name for name in ('tasks/current.md', 'tasks/native_wakeup_spec.md',
                'architecture.md', 'record_storage.md', 'user_guide.md', 'reviews/原生回调唤醒与完整调用验收_20261024.md')],
            repo/'examples/modern_deepep_native/README.md', repo/'backend/README.md']


def usage(paths):
    files = set()
    for path in paths:
        if path.is_file():
            files.add(path)
        elif path.is_dir():
            files.update(p for p in path.rglob('*') if p.is_file())
    return sum(path.stat().st_blocks*512 for path in files)


def run(repo, stage, label, command, *, network=False, expected_exit=0):
    stage.mkdir(exist_ok=True)
    records = load(stage/'commands.json') if (stage/'commands.json').exists() else []
    assert label not in {row['label'] for row in records} and all(row['status']=='terminal' for row in records)
    assert not network or sum(row['network'] and row.get('network_started',True) for row in records) < 6
    monitors = [p for p in stage.rglob('probe.json') if p.relative_to(stage).parts[0]!='restored']
    assert sum(load(p)['wall_seconds'] for p in monitors)+70 < 600
    assert usage(roots(repo, stage))+2*2**20 < 512*2**20
    for path, reserve in ((stage, 8*2**30), (repo, 64*2**20)):
        filesystem = os.statvfs(path)
        assert filesystem.f_bavail*filesystem.f_frsize > reserve
    command = list(map(str,command))
    records.append(dict(label=label,command=command,cwd=str(repo),network=network,
                        expected_exit=expected_exit,status='started'))
    save(stage/'commands.json',records)
    env = dict(os.environ,PYTHONPATH=str(repo/'python')+':'+str(repo),PYTHONDONTWRITEBYTECODE='1')
    monitor = [sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(stage/(label+'-monitor')),
               '--wall-seconds','60','--rss-bytes',str(2**30),'--address-bytes',str(2**31),
               '--artifact-bytes',str(128*2**20),'--artifact-root',str(stage),'--min-free-bytes',str(8*2**30),
               '--artifact-root',str(repo/'runs/native_wakeup_checkpoint_20261024'),
               '--',*command]
    execution = subprocess.run(monitor,cwd=repo,env=env,timeout=65)
    # A classification child may attach evidence that a prior failed input
    # never launched a network. Preserve that evidence instead of replacing
    # the journal with the stale pre-command copy.
    latest=load(stage/'commands.json')
    assert [row['label'] for row in latest]==[row['label'] for row in records]
    records=latest
    records[-1].update(status='terminal',exit_code=execution.returncode)
    save(stage/'commands.json',records)
    assert execution.returncode==expected_exit, label+': preserve failure; do not raise limits or rerun blindly'


def main():
    parser=argparse.ArgumentParser()
    for name in ('repository','stage'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--label',required=True)
    parser.add_argument('--network',action='store_true')
    parser.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    command=args.command[1:] if args.command[:1]==['--'] else args.command
    run(args.repository.resolve(),args.stage.resolve(),args.label,command,network=args.network)


if __name__=='__main__':
    main()
