"""Check that malformed evidence packages cannot create a restore destination."""
import argparse
import io
import json
from pathlib import Path
import tarfile
from checkpoint import restore, SCHEMA


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir();rejected=[]
    for kind in ('traversal','symlink','duplicate','bad_hash','oversize'):
        archive=a.output/(kind+'.tar.gz');destination=a.output/(kind+'-restored')
        manifest=dict(schema_version=SCHEMA,files={'evidence/value':dict(bytes=1,sha256='0'*64)})
        with tarfile.open(archive,'w:gz') as tar:
            def add(name,raw):
                item=tarfile.TarInfo(name);item.size=len(raw);tar.addfile(item,io.BytesIO(raw))
            if kind=='traversal':add('evidence/../../escape',b'x')
            elif kind=='symlink':
                item=tarfile.TarInfo('evidence/link');item.type=tarfile.SYMTYPE;item.linkname='/tmp/escape';tar.addfile(item)
            elif kind=='duplicate':
                add('manifest.json',b'{}');add('manifest.json',b'{}')
            elif kind=='bad_hash':
                add('manifest.json',json.dumps(manifest).encode());add('evidence/value',b'x')
            else:
                # An incomplete, inflated member must be rejected before its body is read.
                item=tarfile.TarInfo('evidence/huge');item.size=16*2**20+1;tar.addfile(item)
        try:restore(archive,destination)
        except ValueError as exc:
            assert not destination.exists(),kind+' created output before validation'
            rejected.append(dict(kind=kind,reason=str(exc)))
        else:raise AssertionError('malformed package accepted: '+kind)
    result=dict(schema_version='target-retirement-restore-check/1',
        rejected_before_output=rejected,network_or_hardware_executed=False)
    (a.output/'result.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
