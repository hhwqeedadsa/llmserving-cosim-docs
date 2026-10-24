"""Verify/extract a research checkpoint, recompute audit, compare exact bytes.

No native network reruns. The nested matched platform archive is kept compressed.
"""
import argparse
import hashlib
import json
from pathlib import Path,PurePosixPath
import subprocess
import sys
import tarfile


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--sha256',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    with a.archive.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==a.sha256
    a.output.mkdir(exist_ok=False)
    with tarfile.open(a.archive,'r:gz') as archive:
        manifest=json.load(archive.extractfile('MANIFEST.json'))
        members=archive.getmembers();assert len(members)==len(manifest['files'])+1
        for member in members:
            parts=PurePosixPath(member.name)
            assert not parts.is_absolute() and '..' not in parts.parts and member.isfile()
            if member.name=='MANIFEST.json':continue
            item=manifest['files'][member.name]
            with archive.extractfile(member) as f:assert hashlib.file_digest(f,'sha256').hexdigest()==item['sha256']
        for member in members:
            target=a.output/member.name;target.parent.mkdir(parents=True,exist_ok=True)
            with archive.extractfile(member) as f,target.open('xb') as output:
                while chunk:=f.read(2**20):output.write(chunk)
    empty=a.output/'empty-cwd';empty.mkdir()
    subprocess.run([sys.executable,'-B',str((a.output/'research/audit.py').resolve()),
        '--stage',str((a.output/'evidence').resolve()),'--output',str((a.output/'recomputed-audit.json').resolve())],
        cwd=empty,check=True)
    subprocess.run([sys.executable,'-B',str((a.output/'research/summarize.py').resolve()),
        '--audit',str((a.output/'recomputed-audit.json').resolve()),'--output',str((a.output/'recomputed-comparison.json').resolve())],
        cwd=empty,check=True)
    for original,recomputed in [('independent-audit.json','recomputed-audit.json'),('comparison.json','recomputed-comparison.json')]:
        assert (a.output/'evidence'/original).read_bytes()==(a.output/recomputed).read_bytes(), 'offline recomputation differs'
    # Verify the preserved raw evidence was not mutated by either analyzer.
    for name,item in manifest['files'].items():
        with (a.output/name).open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==item['sha256']
    receipt=dict(passed=True,verified_members=len(members),native_network_reruns=0,
                 offline_audit_byte_identical=True,comparison_byte_identical=True,all_manifest_hashes_unchanged=True,
                 platform_archive_still_compressed=True)
    (a.output/'reproduction.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))


if __name__=='__main__':main()
