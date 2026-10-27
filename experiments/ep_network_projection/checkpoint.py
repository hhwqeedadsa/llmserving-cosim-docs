"""Member-hashed offline EP projection recovery; no network runtime required."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile

SCHEMA='ep-network-projection-checkpoint/1'


def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')


def admit(archive,expected):
    assert archive.stat().st_size<32*2**20 and sha(archive)==expected
    blobs={};total=0
    with tarfile.open(archive) as source:
        for item in source:
            name=PurePosixPath(item.name)
            assert item.isfile() and not name.is_absolute() and '..' not in name.parts
            assert '\\' not in item.name and str(name)==item.name and item.name not in blobs
            assert len(blobs)<1024 and item.size<=8*2**20
            total+=item.size;assert total<56*2**20
            raw=source.extractfile(item).read();assert len(raw)==item.size;blobs[item.name]=raw
    manifest=json.loads(blobs.pop('manifest.json'))
    assert manifest['schema_version']==SCHEMA and set(manifest['files'])==set(blobs)
    for name,raw in blobs.items():
        assert not any(str(p) in blobs for p in PurePosixPath(name).parents)
        assert manifest['files'][name]==dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    return blobs


def create(repo,run,archive,report_directory=None):
    assert not archive.exists() and load(run/'verification.json')['passed']
    files={}
    for directory in ('observed','source','analysis','marginals','references'):
        for p in (run/directory).rglob('*'):
            if p.is_file() and not p.is_symlink():files['run/'+str(p.relative_to(run))]=p
    selected_report=report_directory or run/'report'
    for p in selected_report.rglob('*'):
        if p.is_file() and not p.is_symlink():
            files['run/report/'+str(p.relative_to(selected_report))]=p
    assert all('run/report/'+name in files for name in (
        'report.md','report.html','ep_projection.svg','ep_projection.png','key_metrics.json'))
    for name in ('inputs.json','verification.json'):files['run/'+name]=run/name
    if (run/'presentation-correction.json').is_file():
        files['run/presentation-correction.json']=run/'presentation-correction.json'
    for directory in (run/'monitor').iterdir():
        if (directory/'probe.json').is_file():
            for p in directory.iterdir():
                if p.is_file():files['run/'+str(p.relative_to(run))]=p
    for directory in ('python/moe_ub_cosim','experiments/ep_network_projection'):
        for p in (repo/directory).rglob('*'):
            if p.is_file() and p.suffix in ('.py','.md'):files['source/'+str(p.relative_to(repo))]=p
    entries={name:dict(bytes=p.stat().st_size,sha256=sha(p)) for name,p in files.items()}
    assert sum(r['bytes'] for r in entries.values())<56*2**20 and len(entries)<1023
    with tarfile.open(archive,'x:gz',compresslevel=6) as target:
        for name,p in sorted(files.items()):
            raw=p.read_bytes();assert hashlib.sha256(raw).hexdigest()==entries[name]['sha256']
            info=tarfile.TarInfo(name);info.size=len(raw);target.addfile(info,io.BytesIO(raw))
        raw=(json.dumps(dict(schema_version=SCHEMA,files=entries),indent=2)+'\n').encode()
        info=tarfile.TarInfo('manifest.json');info.size=len(raw);target.addfile(info,io.BytesIO(raw))
    digest=sha(archive);admit(archive,digest)
    save(archive.with_suffix('.receipt.json'),dict(sha256=digest,members=len(entries)+1,
        compressed_bytes=archive.stat().st_size,uncompressed_bytes=sum(r['bytes'] for r in entries.values())))
    print('Sealed EP projection evidence and copied sources.')


def restore(archive,output):
    assert not output.exists();expected=load(archive.with_suffix('.receipt.json'))['sha256']
    blobs=admit(archive,expected);output.mkdir()
    for name,raw in blobs.items():
        p=output/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
    del blobs
    source,run,empty=output/'source',output/'run',output/'empty-cwd';empty.mkdir()
    env=os.environ|{'PYTHONPATH':str(source/'python')+':'+str(source),'PYTHONDONTWRITEBYTECODE':'1',
        'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'}
    commands=[]
    def command(module,*args):
        argv=[sys.executable,'-B','-m','experiments.ep_network_projection.'+module,*map(str,args)]
        r=subprocess.run(argv,cwd=empty,env=env,capture_output=True,text=True,timeout=45)
        commands.append(dict(command=argv,cwd=str(empty),exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr))
        save(output/'commands.json',commands);assert r.returncode==0,commands[-1]
    command('study','analyze','--run',run,'--output',run/'recomputed-analysis')
    assert (run/'analysis/summary.json').read_bytes()==(run/'recomputed-analysis/summary.json').read_bytes()
    command('marginals','--run',run,'--output',run/'recomputed-marginals')
    assert (run/'marginals/summary.json').read_bytes()==(run/'recomputed-marginals/summary.json').read_bytes()
    command('verify','--run',run,'--output',run/'reverification.json')
    assert (run/'verification.json').read_bytes()==(run/'reverification.json').read_bytes()
    command('report','report','--run',run,'--output',run/'recomputed-report')
    for name in ('report.md','report.html','ep_projection.svg','ep_projection.png','key_metrics.json'):
        assert (run/'report'/name).read_bytes()==(run/'recomputed-report'/name).read_bytes(),name
    save(output/'recovery.json',dict(passed=True,archive_sha256=expected,copied_source=True,empty_cwd=True,
        analysis_byte_identical=True,marginal_reference_byte_identical=True,verification_byte_identical=True,
        reports_and_figures_byte_identical=True,new_networks=0,new_downloads=0,new_builds=0,
        historical_native_runtime_required=False,commands=commands))
    print('Recovered both reference models, verification, review and figures byte-for-byte.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('create','restore'))
    for name in ('repository','run','archive','output','report-directory'):p.add_argument('--'+name,type=Path)
    a=p.parse_args()
    if a.action=='create':create(a.repository.resolve(),a.run.resolve(),a.archive.resolve(),
        a.report_directory.resolve() if a.report_directory else None)
    else:restore(a.archive.resolve(),a.output.resolve())
