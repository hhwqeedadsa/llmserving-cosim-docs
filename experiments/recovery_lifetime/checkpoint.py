"""Package, restore and independently recompute this offline lifetime audit."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def save(path,value):
    with path.open('x') as stream:stream.write(json.dumps(value,indent=2,sort_keys=True)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();stage=a.stage.resolve();out=a.output.resolve()
    root=Path(__file__).resolve().parents[2];out.mkdir()
    files={}
    for folder in ('inputs','prepare-monitor','analyze-monitor','verify-monitor'):
        for path in (stage/folder).rglob('*'):
            assert not path.is_symlink()
            if path.is_file():files['evidence/'+str(path.relative_to(stage))]=path
    for rel in ('summary.json','verification.json'):
        files['evidence/'+rel]=stage/rel
    for path in (root/'experiments/recovery_lifetime').iterdir():
        if path.is_file():files['source/experiments/recovery_lifetime/'+path.name]=path
    report='docs/reviews/有限缓冲恢复与快照模型边界_20261024.md'
    files['source/'+report]=root/report
    continuity=[]
    inputs=json.loads((stage/'inputs/inputs.json').read_text())
    for source in inputs['sources']:
        path=source['path']
        old=subprocess.check_output(['git','show','18025e3:'+path],cwd=root)
        assert hashlib.sha256(old).hexdigest()==source['sha256']
        continuity.append(dict(path=path,sha256=source['sha256'],unchanged_since_old_network_platform=True))
    save(out/'source-continuity.json',continuity)
    files['evidence/source-continuity.json']=out/'source-continuity.json'
    manifest={n:dict(bytes=p.stat().st_size,sha256=sha(p)) for n,p in sorted(files.items())}
    raw=(json.dumps(manifest,indent=2,sort_keys=True)+'\n').encode()
    assert sum(r['bytes'] for r in manifest.values())+len(raw)<32*2**20
    archive=out/'checkpoint.tar.gz'
    with tarfile.open(archive,'w:gz',compresslevel=6) as tf:
        info=tarfile.TarInfo('manifest.json');info.size=len(raw);info.mode=0o644
        tf.addfile(info,io.BytesIO(raw))
        for name,path in sorted(files.items()):tf.add(path,arcname=name,recursive=False)
    assert archive.stat().st_size<16*2**20
    restored=out/'restored';restored.mkdir()
    with tarfile.open(archive,'r:gz') as tf:
        members=tf.getmembers()
        assert len(members)==len(manifest)+1 and len({m.name for m in members})==len(members)
        assert {m.name for m in members}==set(manifest)|{'manifest.json'}
        assert json.load(tf.extractfile('manifest.json'))==manifest
        for m in members:
            name=PurePosixPath(m.name)
            assert m.isfile() and not name.is_absolute() and '..' not in name.parts and str(name)==m.name
            assert m.name=='manifest.json' or name.parts[0] in ('source','evidence')
            target=restored/m.name;target.parent.mkdir(parents=True,exist_ok=True)
            if m.name!='manifest.json':assert m.size==manifest[m.name]['bytes']
            with tf.extractfile(m) as stream,target.open('xb') as output:shutil.copyfileobj(stream,output)
            if m.name!='manifest.json':assert sha(target)==manifest[m.name]['sha256']
    assert all(sha(path)==manifest[n]['sha256'] for n,path in files.items())
    cwd=out/'empty-cwd';cwd.mkdir();env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH',None)
    tool=restored/'source/experiments/recovery_lifetime'
    evidence=restored/'evidence';commands=[]
    for name,cmd in [('analyze',[str(tool/'analyze.py'),'analyze','--inputs',str(evidence/'inputs'),
                                 '--output',str(out/'recomputed-summary.json')]),
                     ('verify',[str(tool/'verify.py'),'--inputs',str(evidence/'inputs'),
                                '--summary',str(out/'recomputed-summary.json'),
                                '--output',str(out/'recomputed-verification.json')])]:
        command=[sys.executable,'-B',*cmd]
        with (out/(name+'.stdout')).open('xb') as stdout,(out/(name+'.stderr')).open('xb') as stderr:
            result=subprocess.run(command,cwd=cwd,env=env,stdout=stdout,stderr=stderr,timeout=40)
        commands.append(dict(command=command,cwd=str(cwd),exit_code=result.returncode))
        save(out/(name+'-command.json'),commands[-1]);assert result.returncode==0
    assert (out/'recomputed-summary.json').read_bytes()==(stage/'summary.json').read_bytes()
    assert (out/'recomputed-verification.json').read_bytes()==(stage/'verification.json').read_bytes()
    assert all(sha(restored/n)==r['sha256'] for n,r in manifest.items())
    receipt=dict(passed=True,sha256=sha(archive),archive_bytes=archive.stat().st_size,
        members=len(manifest)+1,plain_bytes=sum(r['bytes'] for r in manifest.values())+len(raw),
        all_members_hash_verified=True,offline_recomputed_files_byte_equal=2,new_network_executions=0,
        restored_source=str(tool),empty_cwd=str(cwd),requires='host Python standard library')
    save(out/'archive.json',receipt)
    persist=root/'runs/recovery_lifetime_checkpoint_20261024'
    assert not persist.exists() and shutil.disk_usage(root).free>archive.stat().st_size+64*2**20
    persist.mkdir()
    for name in ('checkpoint.tar.gz','archive.json','source-continuity.json'):
        shutil.copyfile(out/name,persist/name)
        with (persist/name).open('rb') as stream:os.fsync(stream.fileno())
    assert sha(persist/'checkpoint.tar.gz')==receipt['sha256']
    assert shutil.disk_usage(root).free>=64*2**20
    (persist/'README.md').write_text('''# 恢复缓冲生命周期审计检查点

archive.json保存归档SHA256和逐成员验证结论。包内manifest.json绑定全部普通文件；输入包含16份完整旧result的gzip原件。
没有程序/共享库，也不执行网络；只需宿主Python标准库。源码、协议和中文报告随包保存。
先校验archive.json的SHA256，再在新目录解压（不要覆盖现有文件），运行包内analyze.py analyze和verify.py；参数见包内README。
本轮已在新源码根/空cwd离线复算，summary和verification两份JSON逐字节一致。
这是同一根分区的持久副本，不是远程备份；原始监控和阶段原件仍在tmpfs。
''')
    print(json.dumps(receipt))


if __name__=='__main__':main()
