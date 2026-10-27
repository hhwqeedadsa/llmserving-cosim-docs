"""Member-hashed offline recovery from copied source; never starts a network."""
import argparse
import io
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from experiments.shared_uplink_choice.prepare import admit, load, save, sha

SCHEMA='shared-uplink-choice-checkpoint/1'


def create(repo, run, preparation, archive):
    assert load(run/'stage/verification.json')['passed'] and load(run/'stage/execution.json')['state']=='completed'
    assert not archive.exists()
    files={}
    for prefix,root in (('run',run),('preparation',preparation)):
        for path in root.rglob('*'):
            if not path.is_file() or path.is_symlink():continue
            relative=path.relative_to(root)
            if relative.parts[0] not in ('stage','monitor'):continue
            # A running monitor appends samples.jsonl; only archive terminal monitors.
            if relative.parts[0]=='monitor' and not (root/'monitor'/relative.parts[1]/'probe.json').exists():continue
            if any(part.startswith('observed-only-check') or part=='empty-cwd' for part in relative.parts):continue
            files[prefix+'/'+str(relative)]=path
    for directory in ('python/moe_ub_cosim','experiments/shared_uplink_choice',
                      'experiments/compact_network_choice','experiments/static_dag_transfer'):
        for path in (repo/directory).glob('*.py'):files['source/'+str(path.relative_to(repo))]=path
    for name in ('deps.lock.json','experiments/shared_uplink_choice/protocol.md',
                 'experiments/shared_uplink_choice/README.md'):
        files['source/'+name]=repo/name
    for name in load(run/'stage/runtime-identity.json')['sources']:
        files['source/'+name]=repo/name
    entries={name:dict(bytes=path.stat().st_size,sha256=sha(path),mode=0o755 if os.access(path,os.X_OK) else 0o644)
             for name,path in files.items()}
    assert sum(r['bytes'] for r in entries.values())<256*2**20
    with tarfile.open(archive,'x:gz',compresslevel=6) as target:
        for name,path in sorted(files.items()):
            raw=path.read_bytes()
            assert hashlib.sha256(raw).hexdigest()==entries[name]['sha256']
            info=tarfile.TarInfo(name)
            info.size,info.mode=len(raw),entries[name]['mode']
            target.addfile(info,io.BytesIO(raw))
        import json
        raw=(json.dumps(dict(schema_version=SCHEMA,files=entries),indent=2)+'\n').encode()
        info=tarfile.TarInfo('manifest.json')
        info.size=len(raw)
        target.addfile(info,io.BytesIO(raw))
    digest=sha(archive)
    admit(archive,digest,SCHEMA)
    save(archive.with_suffix('.receipt.json'),dict(sha256=digest,members=len(entries)+1,
        compressed_bytes=archive.stat().st_size,uncompressed_bytes=sum(r['bytes'] for r in entries.values())))


def restore(archive,output):
    assert not output.exists()
    digest=load(archive.with_suffix('.receipt.json'))['sha256']
    blobs,manifest=admit(archive,digest,SCHEMA)
    output.mkdir()
    for name,raw in blobs.items():
        dest=output/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(raw)
        dest.chmod(manifest['files'][name]['mode'])
    del blobs
    source=output/'source'
    empty=output/'empty-cwd'
    empty.mkdir()
    stage=output/'run/stage'
    env=os.environ|{'PYTHONPATH':str(source/'python')+':'+str(source),'PYTHONDONTWRITEBYTECODE':'1'}
    commands=[]
    def command(module,*args):
        argv=[sys.executable,'-B','-m','experiments.shared_uplink_choice.'+module,*map(str,args)]
        result=subprocess.run(argv,cwd=empty,env=env,capture_output=True,text=True,timeout=50)
        commands.append(dict(command=argv,cwd=str(empty),exit_code=result.returncode,
                             stdout=result.stdout,stderr=result.stderr))
        assert result.returncode==0,commands[-1]
    command('verify','--stage',stage,'--output',stage/'recovery-verification.json')
    assert (stage/'verification.json').read_bytes()==(stage/'recovery-verification.json').read_bytes()
    command('evaluate','report','--stage',stage,'--output',stage/'recovered-report')
    for name in ('report.md','summary.json'):
        assert (stage/'report'/name).read_bytes()==(stage/'recovered-report'/name).read_bytes()
    command('explain','--stage',stage,'--output',stage/'recovered-explanation')
    for name in ('conclusions.md','mechanisms.json','benefits.json','report.html'):
        assert (stage/'explanation'/name).read_bytes()==(stage/'recovered-explanation'/name).read_bytes()
    save(output/'recovery.json',dict(passed=True,sha256=digest,members=len(manifest['files'])+1,
        copied_source=True,empty_cwd=True,identical_verification=True,identical_reports=True,
        identical_predictions_without_truth=True,new_networks=0,new_builds=0,commands=commands))


def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=('create','restore'))
    p.add_argument('--repository',type=Path)
    p.add_argument('--run',type=Path)
    p.add_argument('--preparation',type=Path)
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--output',type=Path)
    a=p.parse_args()
    if a.action=='create':create(a.repository.resolve(),a.run.resolve(),a.preparation.resolve(),a.archive.resolve())
    else:restore(a.archive.resolve(),a.output.resolve())


if __name__=='__main__':main()
