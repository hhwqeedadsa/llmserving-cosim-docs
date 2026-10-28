"""Pack packet captures, study code and committed reports for offline recovery."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

SUFFIXES = {'.json', '.py', '.md', '.html', '.txt', '.cc', '.hh'}
MAX_FILES, MAX_BYTES, MAX_MEMBER = 4096, 64*2**20, 32*2**20


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2)+'\n').encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pack(repo, raw, output):
    assert not output.exists()
    files = {}
    for source_root, prefix in ((raw/'runs', 'raw/runs'),
                                (raw/'runs-recovery', 'raw/runs-recovery'),
                                (raw/'analysis-recovery', 'raw/analysis-recovery')):
        for path in sorted(source_root.rglob('*')):
            if path.is_symlink():
                raise ValueError('symlink is not admissible: '+str(path))
            if path.is_file():
                name = prefix+'/'+path.relative_to(source_root).as_posix()
                content = path.read_bytes()
                if len(content) > MAX_MEMBER:
                    raise ValueError('member exceeds evidence cap: '+name)
                files[name] = content
    study = repo/'experiments/heavy_loss_inference'
    for path in sorted(study.rglob('*')):
        if path.is_symlink():
            raise ValueError('symlink is not admissible: '+str(path))
        if path.is_file() and path.suffix in SUFFIXES:
            files['source/experiments/heavy_loss_inference/'+path.relative_to(study).as_posix()] = path.read_bytes()
    for rel in ('docs/reviews/20261028_丢包实验与周期工作复盘.md',
                'docs/reviews/强传输基线后的故障外溢_20261024.md',
                'backend/rdma/fabric.cc', 'backend/rdma/recovery.cc',
                'backend/rdma/persistent_streams.cc',
                'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3/src/point-to-point/model/rdma-hw.cc'):
        path = repo/rel
        files['source/'+rel] = path.read_bytes()
    if len(files) > MAX_FILES or sum(map(len, files.values())) > MAX_BYTES:
        raise ValueError('aggregate evidence cap exceeded')
    manifest = dict(schema_version='moe-heavy-loss-evidence/1', platform_ns3_commit='e9292641d56de4781128642bd48da22691d4e430',
        original_runs='synthetic one-layer/two-step Decode service; data-loss cases deterministic per message/sequence',
        limits=dict(max_files=MAX_FILES, max_member_bytes=MAX_MEMBER, max_uncompressed_bytes=MAX_BYTES),
        files={name:dict(bytes=len(data), sha256=sha(data)) for name, data in sorted(files.items())})
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, 'w:gz') as archive:
        body=canonical(manifest)
        info=tarfile.TarInfo('manifest.json');info.size=len(body);info.mode=0o444
        archive.addfile(info,io.BytesIO(body))
        for name, data in sorted(files.items()):
            info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o444
            archive.addfile(info,io.BytesIO(data))
    return dict(archive=str(output), archive_sha256=sha(output.read_bytes()), member_count=len(files),
                uncompressed_bytes=sum(map(len,files.values())), raw_roots=3,
                network_replays=0, package_replays=0)


def restore(archive_path, output):
    assert not output.exists()
    with tarfile.open(archive_path,'r:gz') as archive:
        members=archive.getmembers()
        if len(members)>MAX_FILES+1 or len({m.name for m in members})!=len(members):
            raise ValueError('member count or duplicate path invalid')
        by_name={m.name:m for m in members}
        meta=by_name.get('manifest.json')
        if not meta or not meta.isfile() or meta.size>2**20:
            raise ValueError('manifest missing or invalid')
        manifest=json.loads(archive.extractfile(meta).read())
        rows=manifest['files']
        if len(rows)>MAX_FILES or len(rows)!=len(members)-1:
            raise ValueError('manifest member count mismatch')
        bodies={}
        total=0
        for name,row in rows.items():
            path=PurePosixPath(name);member=by_name.get(name)
            if path.is_absolute() or '..' in path.parts or not member or not member.isfile() or member.issym() or member.islnk():
                raise ValueError('unsafe or missing archive member')
            if member.size!=row['bytes'] or member.size>MAX_MEMBER:
                raise ValueError('member size differs from manifest')
            data=archive.extractfile(member).read(MAX_MEMBER+1)
            if len(data)!=row['bytes'] or sha(data)!=row['sha256']:
                raise ValueError('member hash differs from manifest')
            total+=len(data);bodies[name]=data
        if total>MAX_BYTES or total!=sum(row['bytes'] for row in rows.values()):
            raise ValueError('aggregate size differs from manifest')
        output.mkdir(parents=True)
        for name,data in bodies.items():
            dest=output/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
        return dict(passed=True, restored_files=len(bodies), uncompressed_bytes=total,
                    archive_sha256=sha(Path(archive_path).read_bytes()), network_replays=0, builds=0)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='action',required=True)
    p=subs.add_parser('pack');p.add_argument('--repository',type=Path,required=True);p.add_argument('--raw',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    r=subs.add_parser('restore');r.add_argument('--archive',type=Path,required=True);r.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=pack(args.repository.resolve(),args.raw.resolve(),args.output.resolve()) if args.action=='pack' else restore(args.archive.resolve(),args.output.resolve())
    print(json.dumps(result,ensure_ascii=False,indent=2))
