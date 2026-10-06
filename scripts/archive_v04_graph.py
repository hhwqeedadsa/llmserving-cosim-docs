"""Archive a registered v0.4 graph or completed execution, then verify and reclaim.

Cleanup is limited to runs/v04-platform, exact recorded files and explicitly
listed hardlink aliases. Unknown files, symlinks and changed inputs are rejected.
The inventory is accepted by the existing public slice archive importer.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile

from moe_ub_cosim.plan_io import file_sha256
from moe_ub_cosim.user_io import inventory,save,verify_inventory


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory','--graph',dest='directory',type=Path,required=True)
    p.add_argument('--kind',choices=('graph','execution'),default='graph')
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--alias',type=Path,action='append',default=[])
    p.add_argument('--remove-verified',action='store_true');a=p.parse_args()
    source=a.directory.resolve();archive=a.archive.resolve();root=Path(__file__).resolve().parents[1]/'runs/v04-platform'
    directories=[source,*[d.resolve() for d in a.alias]]
    assert all(d.is_relative_to(root) and d!=root and d.is_dir() for d in directories)
    assert archive.is_relative_to(root) and not archive.exists() and not any(archive.is_relative_to(d) for d in directories)
    assert len(set(directories))==len(directories)
    files=inventory(source)
    if a.kind=='graph':assert 'plan.json' in files and 'generation.json' in files
    else:
        result=json.loads((source/'result.json').read_text())
        assert result['normal_exit'] and result['network_drain']['idle'] and result['schema_version']=='moe-ub-service-result/2'
    for directory in directories:
        assert all(not path.is_symlink() for path in directory.rglob('*'))
        assert inventory(directory)==files
        for name in files:assert (directory/name).samefile(source/name)
    evidence=archive.with_suffix('.inventory.json');assert not evidence.exists()
    with tarfile.open(archive,'w:gz',compresslevel=1,dereference=True) as stream:
        for name in sorted(files):stream.add(source/name,arcname=name,recursive=False)
    seen=set()
    with tarfile.open(archive,'r|gz') as stream:
        for member in stream:
            assert member.isfile() and member.name in files and member.name not in seen
            expected=files[member.name];assert member.size==expected['bytes'];digest=hashlib.sha256()
            with stream.extractfile(member) as content:
                for block in iter(lambda:content.read(2**20),b''):digest.update(block)
            assert digest.hexdigest()==expected['sha256'];seen.add(member.name)
    assert seen==set(files)
    record=dict(schema_version='moe-ub-v04-artifact-archive/1',kind=a.kind,files=files,
        archive=dict(path=str(archive),bytes=archive.stat().st_size,sha256=file_sha256(archive)),
        generated_directories=list(map(str,directories)),archive_content_verified=True,expanded_removed=False)
    save(evidence,record)
    if a.remove_verified:
        for directory in directories:
            assert inventory(directory)==files
            verify_inventory(directory,files)
        for directory in directories:
            for name in files:(directory/name).unlink()
            for sub in sorted((d for d in directory.rglob('*') if d.is_dir()),key=lambda d:len(d.parts),reverse=True):sub.rmdir()
            directory.rmdir()
        record['expanded_removed']=True;save(evidence,record)
    print(json.dumps(dict(passed=True,files=len(files),expanded_unique_bytes=sum(v['bytes'] for v in files.values()),
        archive_bytes=archive.stat().st_size,expanded_removed=record['expanded_removed'],inventory=str(evidence))))


if __name__=='__main__':main()
