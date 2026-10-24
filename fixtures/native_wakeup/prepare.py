"""Freeze original evidence against its closed archive; no network execution."""
import argparse
from pathlib import Path
import shutil
import tarfile
from fixtures.native_wakeup.stage import load, save, sha


def main():
    parser=argparse.ArgumentParser()
    for name in ('repository','stage'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();repo=args.repository.resolve();stage=args.stage.resolve()
    old=Path('/dev/shm/moe-modern-native-20261024')
    archive=repo/'runs/modern_native_checkpoint_20261024/portable-final.tar.gz'
    receipt=load(archive.with_suffix('.receipt.json'));assert sha(archive)==receipt['sha256']
    import json
    with tarfile.open(archive,'r:gz') as bundled:
        manifest=json.load(bundled.extractfile('manifest.json'))['files']
    original=stage/'original';original.mkdir();inputs=stage/'inputs';inputs.mkdir()
    hashes={}
    for case in ('synthetic-healthy','synthetic-fault','four-routes'):
        (original/case).mkdir()
        for name in ('result.json','modern-execution.json','modern-plan.json','initial.json','control.jsonl','topology.json','protocol.json','input.json'):
            source=old/case/name;expected=manifest['evidence/'+case+'/'+name]['sha256']
            assert sha(source)==expected
            shutil.copyfile(source,original/case/name);hashes[case+'/'+name]=expected
        shutil.copyfile(old/case/'input.json',inputs/(case+'.json'))
        shutil.copyfile(old/case/'protocol.json',inputs/(case+'-protocol.json'))
    source=repo/'examples/modern_deepep_native/topology.json'
    shutil.copyfile(source,inputs/'topology.json')
    provenance=load(old/'route-provenance.json')
    (stage/'original-routes').mkdir()
    for row in provenance['original_files']:
        source=repo/row['path'];assert sha(source)==row['sha256']
        shutil.copyfile(source,stage/'original-routes'/source.name)
    save(stage/'route-provenance.json',provenance)
    save(stage/'original-identity.json',dict(original_archive_sha256=sha(archive),files=hashes,
        routes_hash_checked=True,fixed_original_failed_four_rank=True,new_data_downloaded=False))


if __name__=='__main__':main()
