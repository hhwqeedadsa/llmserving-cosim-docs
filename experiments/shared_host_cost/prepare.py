"""Restore only registered observations, reference executions and accepted runtime."""
import argparse
from pathlib import Path
import subprocess
from experiments.shared_uplink_choice.prepare import admit, load, save, sha

ARCHIVE_SHA='147c7ae9352b7761964b47cb18653a4a1bf314ec22410b900da3668d8a9c38f7'


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--repository',type=Path,required=True)
    p.add_argument('--stage',type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve()
    blobs,manifest=admit(repo/'runs/shared_uplink_choice_20261027_r2/checkpoint-v2.tar.gz',ARCHIVE_SHA,
                         'shared-uplink-choice-checkpoint/1')
    import json
    identity=json.loads(blobs['run/stage/runtime-identity.json'])
    for name,digest in identity['sources'].items():assert sha(repo/name)==digest
    prefixes=[('run/stage/observed/','observed/'),('run/stage/runtime/','runtime/'),
              ('run/stage/predictions/','reference-predictions/')]
    for batch in (4,8):
        base=f'run/stage/truth/batch{batch}/'
        prefixes += [(base+'frozen/',f'frozen/batch{batch}/')]
        for mode in ('modern','generic'):prefixes.append((base+f'baseline/{mode}/',f'reference/batch{batch}/{mode}/'))
    copied={}
    for name,raw in blobs.items():
        match=[(old,new) for old,new in prefixes if name.startswith(old)]
        if not match:continue
        assert len(match)==1
        old,new=match[0]
        dest=stage/(new+name.removeprefix(old))
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(raw);dest.chmod(manifest['files'][name]['mode'])
        copied[str(dest.relative_to(stage))]=sha(dest)
    assert all(sha(stage/'runtime'/n)==d for n,d in identity['files'].items())
    save(stage/'runtime-identity.json',identity)
    save(stage/'source.json',dict(archive_sha256=ARCHIVE_SHA,copied=copied,
        repository_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
        reference_networks_executed=0,runtime_rebuilt=False,hardware_calibrated=False))


if __name__=='__main__':main()
