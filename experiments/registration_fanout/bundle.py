"""Bounded offline package, relocated CPU replay, and local phase receipts."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import resource
import shutil
import signal
import subprocess
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[2]
LOCAL=Path(__file__).resolve().parent
MAX_BYTES=16*2**20
SCHEMA='registration-fanout-checkpoint/1'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')


def pack(stage,out):
    files={}
    for folder in ('inputs','build','prepare-monitor','build-monitor','source-run-monitor','analyze-monitor','evidence-monitor','verify-monitor'):
        for p in sorted((stage/folder).rglob('*')):
            assert not p.is_symlink()
            if p.is_file():files['evidence/'+str(p.relative_to(stage))]=p.read_bytes()
    for name in ('summary.json','evidence.json','verification.json'):files['evidence/'+name]=(stage/name).read_bytes()
    for p in LOCAL.rglob('*'):
        assert not p.is_symlink()
        if p.is_file():files['source/'+str(p.relative_to(ROOT))]=p.read_bytes()
    for name in ('experiments/target_retirement_contract/extract.py','docs/reviews/共享窗口多连接撤销与部分失败_20261024.md'):
        files['source/'+name]=(ROOT/name).read_bytes()
    manifest=dict(schema_version=SCHEMA,files={k:dict(bytes=len(v),sha256=hashlib.sha256(v).hexdigest()) for k,v in sorted(files.items())})
    files['manifest.json']=(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode()
    assert len(files)<=512 and sum(map(len,files.values()))<=MAX_BYTES
    with out.open('xb') as raw,tarfile.open(fileobj=raw,mode='w:gz') as tar:
        for name,data in sorted(files.items()):
            item=tarfile.TarInfo(name);item.size=len(data);item.mode=0o644;tar.addfile(item,io.BytesIO(data))
    assert out.stat().st_size<=MAX_BYTES
    print(json.dumps(dict(schema_version=SCHEMA,members=len(files),plain_bytes=sum(map(len,files.values())),
        archive_bytes=out.stat().st_size,archive_sha256=sha(out))))


def restore(archive,out):
    if out.exists():raise ValueError('restore destination exists')
    if archive.stat().st_size>MAX_BYTES:raise ValueError('compressed size limit')
    files={};total=0
    with tarfile.open(archive,'r:gz') as tar:
        for item in tar:
            name=PurePosixPath(item.name)
            if item.name!='manifest.json' and (name.is_absolute() or '..' in name.parts or str(name)!=item.name or
                    len(name.parts)<2 or name.parts[0] not in ('source','evidence')):raise ValueError('unsafe member')
            if not item.isfile() or item.name in files:raise ValueError('nonregular or duplicate member')
            total+=item.size
            if item.size<0 or total>MAX_BYTES or len(files)>=512:raise ValueError('package size limit')
            files[item.name]=tar.extractfile(item).read()
    manifest=json.loads(files.pop('manifest.json'))
    if manifest['schema_version']!=SCHEMA or set(files)!=set(manifest['files']):raise ValueError('manifest mismatch')
    for name,data in files.items():
        if manifest['files'][name]!=dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest()):raise ValueError('member hash mismatch')
    out.mkdir()
    for name,data in files.items():
        p=out/name;p.parent.mkdir(parents=True,exist_ok=True)
        with p.open('xb') as stream:stream.write(data)
    print(json.dumps(dict(restored=len(files),all_members_verified=True,executes_on_restore=False)))


def replay(stage,out):
    out.mkdir();cwd=out/'empty-cwd';cwd.mkdir();env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1');env.pop('PYTHONPATH',None)
    commands=[]
    def run(name,cmd):
        with (out/(name+'.stdout')).open('xb') as stdout,(out/(name+'.stderr')).open('xb') as stderr:
            r=subprocess.run(cmd,cwd=cwd,env=env,stdout=stdout,stderr=stderr,timeout=15)
        commands.append(dict(command=cmd,cwd=str(cwd),exit_code=r.returncode));save(out/'commands.json',commands)
        assert r.returncode==0,name+' failed; logs preserved'
    def audit(action,*args):return [sys.executable,'-B',str(LOCAL/'audit.py'),action,*map(str,args)]
    run('build',audit('build','--inputs',stage/'inputs','--output',out/'build'))
    run('records',[str(out/'build/source-harness')])
    run('analyze',audit('analyze','--records',out/'records.stdout','--output',out/'summary.json'))
    run('evidence',audit('evidence','--inputs',stage/'inputs','--output',out/'evidence.json'))
    run('verify',[sys.executable,'-B',str(LOCAL/'verify.py'),'--inputs',str(stage/'inputs'),
        '--build',str(out/'build'),'--records',str(out/'records.stdout'),'--summary',str(out/'summary.json'),
        '--evidence',str(out/'evidence.json'),'--output',str(out/'verification.json')])
    equal={}
    for old,new in [('build/selected.cc','build/selected.cc'),('source-run-monitor/stdout.log','records.stdout'),
            ('evidence.json','evidence.json'),('summary.json','summary.json'),('verification.json','verification.json')]:
        assert (stage/old).read_bytes()==(out/new).read_bytes(),'byte mismatch '+new;equal[new]=sha(out/new)
    receipt=dict(schema_version='registration-fanout-replay/1',byte_equal=equal,
        actual_provider_executed=False,new_ns3_networks=0,requires='host Python standard library, G++, libc',commands=len(commands))
    save(out/'replay.json',receipt);print(json.dumps(receipt))


def finish(stage,persist):
    # Final file management only; no child workload. Reserve 10 s and 128 KiB
    # instead of recursively putting the final monitor inside its own receipt.
    signal.alarm(10);resource.setrlimit(resource.RLIMIT_AS,(2*2**30,2*2**30));resource.setrlimit(resource.RLIMIT_DATA,(2**30,2**30))
    assert not persist.exists()
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).startswith('c4256a6')
    subprocess.run(['git','diff','--exit-code','c4256a6','--','backend','python','third_party','deps.lock.json'],cwd=ROOT,check=True)
    monitors=[];groups=set()
    for path in sorted(stage.glob('*-monitor/probe.json')):
        row=json.loads(path.read_text());assert row['status']=='completed' and not row['remaining_live_group_pids']
        groups.add(row['process_group']);monitors.append(dict(name=path.parent.name,wall_seconds=row['wall_seconds'],
            sampled_rss_bytes=row['peak_sampled_group_rss_bytes'],waited_rss_kib=row['max_waited_child_rss_kib']))
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields=path.read_text().rsplit(')',1)[1].split();assert int(fields[2]) not in groups or fields[0]=='Z'
        except FileNotFoundError:pass
    assert sum(r['wall_seconds'] for r in monitors)+10<180
    assert max(max(r['sampled_rss_bytes'],r['waited_rss_kib']*1024) for r in monitors)<2**30
    assert shutil.disk_usage(stage).free>=8*2**30 and shutil.disk_usage(ROOT).free>=64*2**20
    receipt=json.loads((stage/'pack-monitor/stdout.log').read_text());archive=stage/'checkpoint.tar.gz'
    assert sha(archive)==receipt['archive_sha256']
    with tarfile.open(archive,'r:gz') as tar:
        manifest=json.load(tar.extractfile('manifest.json'));assert len(manifest['files'])+1==receipt['members']
        for name,row in manifest['files'].items():
            raw=tar.extractfile(name).read();assert len(raw)==row['bytes'] and hashlib.sha256(raw).hexdigest()==row['sha256']
            assert sha(stage/'restored'/name)==row['sha256']
            if name.startswith('source/experiments/'):assert sha(ROOT/name.removeprefix('source/'))==row['sha256']
    replay_result=json.loads((stage/'recomputed/replay.json').read_text());assert len(replay_result['byte_equal'])==5
    syntax=[]
    for path in LOCAL.glob('*.py'):compile(path.read_text(),str(path),'exec');syntax.append(path.name)
    persist.mkdir();shutil.copyfile(archive,persist/'checkpoint.tar.gz');save(persist/'archive.json',receipt)
    results=LOCAL/'results/20261024'
    for name,source in [('summary.json',stage/'summary.json'),('evidence.json',stage/'evidence.json'),
            ('verification.json',stage/'verification.json'),('sources.json',stage/'inputs/sources.json'),
            ('replay.json',stage/'recomputed/replay.json')]:
        shutil.copyfile(source,persist/name);shutil.copyfile(source,results/name)
    shutil.copyfile(persist/'archive.json',results/'archive.json')
    for path in stage.glob('*-monitor/probe.json'):
        target=persist/'final-monitors'/path.parent.name;target.mkdir(parents=True)
        for name in ('probe.json','stdout.log','stderr.log'):
            if (path.parent/name).exists():shutil.copyfile(path.parent/name,target/name)
    apparent=0;allocated=0;seen=set()
    for base in (stage,persist,LOCAL,ROOT/'docs/reviews/共享窗口多连接撤销与部分失败_20261024.md'):
        paths=[base]+(list(base.rglob('*')) if base.is_dir() else [])
        for path in paths:
            assert not path.is_symlink();st=path.stat();identity=(st.st_dev,st.st_ino)
            if identity in seen:continue
            seen.add(identity);allocated+=st.st_blocks*512
            if path.is_file():apparent+=st.st_size
    assert max(apparent,allocated)+128*1024<32*2**20
    closeout=dict(schema_version='registration-fanout-closeout/1',platform_commit='a30296c',parent_commit='c4256a6',
        platform_and_dependencies_unchanged=True,original_monitors=monitors,monitored_seconds=sum(r['wall_seconds'] for r in monitors),
        finalization_seconds_reserved=10,phase_seconds_limit=180,all_monitored_groups_exited=True,
        unique_apparent_bytes_before_receipts=apparent,unique_allocated_bytes_before_receipts=allocated,
        final_receipts_reserved_bytes=128*1024,phase_disk_limit_bytes=32*2**20,syntax_checked=sorted(syntax),
        offline_byte_equal_files=5,source_functions=3,cpu_cases=1120,new_downloads=0,new_ns3_networks=0,
        actual_provider_executed=False,paper_innovation_established=False)
    save(results/'closeout.json',closeout);save(persist/'closeout.json',closeout)
    (persist/'README.md').write_text('''# 共享窗口注册扇出核查检查点

archive.json绑定checkpoint.tar.gz；包内manifest校验全部成员。
恢复与离线复算方法见source/experiments/registration_fanout/README.md。
需要宿主Python标准库、G++、libc；CPU记录替身不执行provider/GPU/ns-3。
replay.json与closeout.json为打包后收据，receipts-manifest.json另行绑定其hash。
这是本地持久副本，不是远程备份。
''')
    saved={str(p.relative_to(persist)):dict(bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(persist.rglob('*')) if p.is_file()}
    save(persist/'receipts-manifest.json',saved)
    for p in list(persist.rglob('*'))+list(results.glob('*.json')):
        if p.is_file():
            with p.open('rb') as stream:os.fsync(stream.fileno())
    fd=os.open(persist,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    print(json.dumps(dict(persistent_files=len(saved),conservative_seconds=closeout['monitored_seconds']+10,
        disk_bytes_with_reserve=max(apparent,allocated)+128*1024)))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('pack','restore','replay','finish'))
    p.add_argument('--stage',type=Path);p.add_argument('--archive',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve()
    if a.action=='restore':
        if a.archive is None:p.error('restore needs --archive')
        restore(a.archive.resolve(),out)
    else:
        if a.stage is None:p.error(a.action+' needs --stage')
        stage=a.stage.resolve()
        if a.action=='pack':pack(stage,out)
        elif a.action=='replay':replay(stage,out)
        else:finish(stage,out)


if __name__=='__main__':main()
