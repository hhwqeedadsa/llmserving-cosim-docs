"""Reject malformed evidence bundles before creating a destination."""
import argparse
import io
import json
from pathlib import Path
import tarfile
from bundle import restore


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir();rejected=[]
    for name in ('traversal','symlink','duplicate','wrong_hash','root_member','file_parent','member_cap','byte_cap'):
        archive=a.output/(name+'.tar.gz');data=b'x'
        with tarfile.open(archive,'w:gz') as t:
            info=tarfile.TarInfo('../escape' if name=='traversal' else '.' if name=='root_member' else 'x');info.size=1
            if name=='symlink':info.type=tarfile.SYMTYPE;info.linkname='/tmp/escape';info.size=0
            t.addfile(info,None if name=='symlink' else io.BytesIO(data))
            if name=='duplicate':t.addfile(info,io.BytesIO(data))
            if name=='file_parent':
                info=tarfile.TarInfo('x/child');info.size=1;t.addfile(info,io.BytesIO(data))
            if name=='member_cap':
                for index in range(512):
                    info=tarfile.TarInfo('member'+str(index));info.size=0;t.addfile(info)
            if name=='byte_cap':
                # Reject the oversized header before attempting to read its payload.
                info=tarfile.TarInfo('oversized');info.size=16*2**20;t.addfile(info)
            raw=json.dumps({'x':dict(bytes=1,sha256='0'*64)}).encode()
            info=tarfile.TarInfo('manifest.json');info.size=len(raw);t.addfile(info,io.BytesIO(raw))
        target=a.output/(name+'-restored')
        try:restore(archive,target)
        except ValueError:pass
        else:raise AssertionError('unsafe archive accepted: '+name)
        if target.exists():raise AssertionError('destination created before rejection')
        rejected.append(name)
    (a.output/'verification.json').write_text(json.dumps(dict(schema_version='communicator-recovery-bundle-checks/1',
        rejected_before_destination_creation=rejected),sort_keys=True,indent=2)+'\n')
    print(len(rejected),'unsafe archives rejected before destination creation')


if __name__=='__main__':main()
