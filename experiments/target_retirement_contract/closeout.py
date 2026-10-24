"""Check audit continuity and preserve a small CPU-only checkpoint locally."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path,value):path.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('stage','persist','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();stage=a.stage.resolve();persist=a.persist.resolve();root=Path(__file__).resolve().parents[2]
    assert not persist.exists()
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).startswith('a30296c')
    subprocess.run(['git','diff','--exit-code','a30296c','--','backend','python','deps.lock.json','third_party'],cwd=root,check=True)
    probes=[]
    for path in sorted(stage.glob('*-monitor/probe.json')):
        row=json.loads(path.read_text());assert not row['remaining_live_group_pids']
        probes.append(dict(name=path.parent.name,status=row['status'],wall_seconds=row['wall_seconds']))
    assert sum(r['wall_seconds'] for r in probes)<240
    receipt=json.loads((stage/'checkpoint-monitor/stdout.log').read_text())
    archive=stage/'checkpoint.tar.gz';assert sha(archive)==receipt['archive_sha256']
    with tarfile.open(archive,'r:gz') as tar:
        manifest=json.load(tar.extractfile('manifest.json'));members=tar.getmembers()
        assert len(members)==receipt['members']==len(manifest['files'])+1
        for name,row in manifest['files'].items():
            raw=tar.extractfile(name).read()
            assert len(raw)==row['bytes'] and hashlib.sha256(raw).hexdigest()==row['sha256']
            assert sha(stage/'restored'/name)==row['sha256']
            if name.startswith('source/experiments/target_retirement_contract/'):
                assert sha(root/name.removeprefix('source/'))==row['sha256'],'archived research source changed'
    assert len(json.loads((stage/'recomputed/replay.json').read_text())['byte_equal'])==5
    assert len(json.loads((stage/'restore-check/result.json').read_text())['rejected_before_output'])==5
    # Verify syntax without producing bytecode or executing any downloaded source.
    syntax=[]
    for path in sorted(Path(__file__).resolve().parent.glob('*.py')):
        compile(path.read_text(),str(path),'exec');syntax.append(path.name)
    assert shutil.disk_usage(root).free>=64*2**20+archive.stat().st_size
    persist.mkdir()
    shutil.copyfile(archive,persist/'checkpoint.tar.gz');write(persist/'archive.json',receipt)
    for name,source in [('replay.json',stage/'recomputed/replay.json'),
            ('restore-check.json',stage/'restore-check/result.json')]:shutil.copyfile(source,persist/name)
    post=persist/'post-package-source';post.mkdir()
    for name in ('check_restore.py','closeout.py'):shutil.copyfile(Path(__file__).parent/name,post/name)
    (persist/'README.md').write_text('''# 目标内存撤销核查本地检查点

archive.json保存checkpoint.tar.gz的SHA256，包内manifest绑定全部普通文件。
离线复核方法见包内source/experiments/target_retirement_contract/README.md。
replay.json记录新源码根/空cwd复算的五份逐字节一致结果；需要宿主Python、GCC、libc。
包中有记录型CPU替身，不是内核、真实provider、GPU或ns-3执行。
post-package-source及最终收据是打包后的材料，由receipts-manifest.json另行绑定。
这是同一根分区的本地持久副本，不是远程备份。
''')
    for path in persist.rglob('*'):
        if path.is_file():
            with path.open('rb') as stream:os.fsync(stream.fileno())
    fd=os.open(persist,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    assert sha(persist/'checkpoint.tar.gz')==receipt['archive_sha256']
    result=dict(schema_version='target-retirement-closeout/1',platform_commit='a30296c',
        platform_code_unchanged=True,syntax_checked=syntax,archive=receipt,
        offline_replay_byte_equal_files=5,malformed_packages_rejected_before_output=5,
        prior_completed_monitor_records=probes,own_monitor_not_yet_final=True,
        initial_raw_acquisition_wall_limit_preserved=True,initial_request_limit_violated=True,
        source_analysis_failure_and_original_parser_preserved=True,
        new_ns3_networks=0,actual_kernel_or_hardware_executed=False,paper_innovation_established=False,
        persistent_checkpoint=str(persist),post_monitor_budget_receipt='budget.json')
    write(a.output,result);print(json.dumps(result))


if __name__=='__main__':main()
