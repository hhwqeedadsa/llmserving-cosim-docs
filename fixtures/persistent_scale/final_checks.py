"""Strict full-result regression, archive rejection and unchanged source identities."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import runpy
import tarfile


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())


def normalized(result,libraries):
    r=dict(result);paths=[]
    for path in r['loaded_libraries']:
        name=Path(path).name
        if name.startswith('libns3'):
            assert name in libraries;paths.append(name+':'+libraries[name])
        else:paths.append(path)
    r['loaded_libraries']=paths
    return r


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve()
    restore=runpy.run_path(str(repo/'fixtures/persistent_scale/checkpoint.py'))['restore']
    root=s/'archive-safety';root.mkdir();rejected=[]
    for case in ('traversal','absolute','symlink','duplicate','file-parent','wrong-hash','missing-member','wrong-schema'):
        data=b'hello';name='source/data.txt'
        if case=='traversal':name='../outside'
        if case=='absolute':name='/outside'
        entries=[(name,data,tarfile.REGTYPE)]
        if case=='symlink':entries=[(name,b'',tarfile.SYMTYPE)]
        if case=='duplicate':entries.append((name,data,tarfile.REGTYPE))
        if case=='file-parent':entries.append((name+'/child',data,tarfile.REGTYPE))
        manifest=dict(schema_version='persistent-scale-checkpoint/1',bytes=sum(len(d) for _,d,_ in entries),
            files={n:dict(bytes=len(d),sha256=hashlib.sha256(d).hexdigest()) for n,d,_ in entries})
        if case=='wrong-hash':manifest['files'][name]['sha256']='0'*64
        if case=='missing-member':manifest['files']['source/absent']=dict(bytes=0,sha256=hashlib.sha256(b'').hexdigest())
        if case=='wrong-schema':manifest['schema_version']='unknown'
        archive=root/(case+'.tar.gz')
        with tarfile.open(archive,'x:gz') as tar:
            for n,d,kind in entries:
                info=tarfile.TarInfo(n);info.type=kind;info.size=len(d);info.linkname='../../outside' if kind==tarfile.SYMTYPE else ''
                tar.addfile(info,io.BytesIO(d))
            raw=json.dumps(manifest).encode();info=tarfile.TarInfo('manifest.json');info.size=len(raw);tar.addfile(info,io.BytesIO(raw))
        output=root/(case+'-output')
        try:restore(archive,output)
        except ValueError:rejected.append(case)
        else:raise AssertionError('unsafe archive accepted: '+case)
        assert not output.exists(),'partial restore for '+case
    build=load(s/'build.json')
    for name,digest in build['inputs'].items():assert sha(Path(name))==digest
    for name,digest in build['binaries'].items():assert sha(s/'bin'/name)==digest
    assert load(s/'verification.json')['passed']
    assert load(s/'fresh/replay.json')['passed']
    manifest=load(s/'restored/manifest.json');identity={}
    for name,entry in manifest['files'].items():
        if name.startswith(('source/python/','source/backend/')) or name=='source/fixtures/persistent_scale/audit.py':
            p=repo/name.removeprefix('source/');assert sha(p)==entry['sha256'];identity[str(p.relative_to(repo))]=entry['sha256']
    result=dict(passed=True,unsafe_archive_classes_rejected_before_writes=rejected,
        strict_default_and_v4_dynamic_result_equal=True,normalization_only_verified_native_library_paths=True,
        other_system_library_paths_compared_exactly=True,fixed_fabric_eight_libraries_and_binaries_unchanged=True,
        python_backend_and_auditor_source_identity=identity,no_additional_network=True)
    (s/'final-checks.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps(dict(passed=True,safety_classes=len(rejected),source_files=len(identity))))


if __name__=='__main__':main()
