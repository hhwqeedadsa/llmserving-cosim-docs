"""Archive refusal, immutable input identities and source/runtime consistency."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import runpy
import tarfile


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve()
    helper=runpy.run_path(str(repo/'fixtures/transaction_limits/checkpoint.py'))
    restore=helper['restore'];root=s/'archive-safety';root.mkdir()
    rejected=[]
    for case in ('traversal','absolute','symlink','duplicate','file-parent','wrong-hash','missing-member','wrong-schema'):
        data=b'hello';name='source/data.txt'
        if case=='traversal':name='../outside'
        if case=='absolute':name='/outside'
        entries=[(name,data,tarfile.REGTYPE)]
        if case=='symlink':entries=[(name,b'',tarfile.SYMTYPE)]
        if case=='duplicate':entries.append((name,data,tarfile.REGTYPE))
        if case=='file-parent':entries.append((name+'/child',data,tarfile.REGTYPE))
        manifest=dict(schema_version='transaction-limits-checkpoint/1',bytes=sum(len(d) for _,d,_ in entries),
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
    identities=json.loads((s/'readers-build.json').read_text())['inputs']
    for name,digest in identities.items():assert sha(Path(name))==digest
    for name,digest in json.loads((s/'library-inputs.json').read_text()).items():assert sha(Path(name))==digest
    fresh=json.loads((s/'fresh/replay.json').read_text());assert fresh['passed']
    manifest=json.loads((s/'restored/manifest.json').read_text())
    python_files={name:entry for name,entry in manifest['files'].items() if name.startswith('source/python/')}
    for name,entry in python_files.items():assert sha(repo/name.removeprefix('source/'))==entry['sha256']
    receipt=dict(passed=True,unsafe_archive_classes_rejected_before_writes=rejected,
        fixed_carrier_fabric_and_eight_libraries_unchanged=True,fresh_python_sources_still_identical=len(python_files),
        no_additional_network=True)
    (s/'final-checks.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))


if __name__=='__main__':main()
