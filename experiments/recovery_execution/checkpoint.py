"""Package fixed evidence and code, verify every member, preserve old failures."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('stage','repo','platform-checkpoint','output'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();stage,repo=a.stage.resolve(),a.repo.resolve()
    assert not a.output.exists()
    # Exclude ongoing checkpoint/restore monitors and derivative archive/output.
    excluded={'checkpoint','checkpoint-monitor','restored','restore-monitor','closeout.json'}
    files={}
    for path in sorted(stage.rglob('*')):
        relative=path.relative_to(stage)
        if relative.parts[0] in excluded or not path.is_file():continue
        assert not path.is_symlink()
        files['evidence/'+str(relative)]=path
    for path in sorted((repo/'experiments/recovery_execution').iterdir()):
        if path.is_file():files['research/'+path.name]=path
    files['review/report.md']=repo/'docs/reviews/接收进展恢复执行与强基线判别_20261024.md'
    files['platform/source_stream_isolation.tar.gz']=a.platform_checkpoint.resolve()
    assert sha(a.platform_checkpoint)=='bbce33410746b66343080f7f6ae54f7a0f32531dc3b18c3e161feb25645a14ee'
    manifest=dict(schema_version='receiver-recovery-checkpoint/1',
        files={name:dict(bytes=path.stat().st_size,sha256=sha(path)) for name,path in sorted(files.items())},
        platform_archive_embedded=True,platform_archive_expanded_here=False,
        offline_audit_requires='Python standard library; source-machine absolute raw paths not required',
        native_rerun_requires='extract matched platform runtime plus host loader/glibc/libstdc++; no offline third-party rebuild claim')
    raw=(json.dumps(manifest,indent=2,sort_keys=True)+'\n').encode()
    a.output.parent.mkdir(exist_ok=False)
    with tarfile.open(a.output,'w:gz',compresslevel=6) as archive:
        for name,path in sorted(files.items()):archive.add(path,arcname=name,recursive=False)
        info=tarfile.TarInfo('MANIFEST.json');info.size=len(raw);archive.addfile(info,io.BytesIO(raw))
    with tarfile.open(a.output,'r:gz') as archive:
        members=archive.getmembers()
        assert len(members)==len(files)+1
        for member in members:
            assert member.isfile()
            with archive.extractfile(member) as stream:
                if member.name=='MANIFEST.json':assert stream.read()==raw
                else:
                    item=manifest['files'][member.name]
                    assert member.size==item['bytes']
                    assert hashlib.file_digest(stream,'sha256').hexdigest()==item['sha256']
    for name,path in files.items():assert sha(path)==manifest['files'][name]['sha256'], 'source changed during packaging'
    receipt=dict(archive=str(a.output.resolve()),sha256=sha(a.output),archive_bytes=a.output.stat().st_size,
                 members=len(files)+1,uncompressed_file_bytes=sum(i['bytes'] for i in manifest['files'].values()),
                 verified_all_members=True)
    (a.output.parent/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))


if __name__=='__main__':main()
