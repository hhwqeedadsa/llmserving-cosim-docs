"""Copy only owned saved records; pin unmodified platform and native identities."""
import argparse
from pathlib import Path
import shutil
from fixtures.batched_callback_replay.stage import load,save,sha


CASES=('default-healthy','timed-healthy','wake-fault','four-routes')
FILES=('topology.json','protocol.json','input.json','control.jsonl','modern-execution.json')


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve()
    previous=Path('/dev/shm/moe-incremental-progress-20261025')
    runtime=load(previous/'runtime-identity.json')
    assert all(sha(repo/name)==digest for name,digest in runtime['sources'].items())
    assert all(sha(previous/'runtime'/name)==digest for name,digest in runtime['files'].items())
    original=stage/'original';original.mkdir()
    for case in CASES:
        target=original/case;target.mkdir()
        for name in FILES:shutil.copyfile(previous/case/name,target/name)
    save(stage/'identity.json',dict(platform_sources={str(path.relative_to(repo)):sha(path)
        for path in (repo/'python/moe_ub_cosim').rglob('*.py')},native_runtime=runtime,
        source_stage=str(previous),platform_or_native_modified=False))
    save(stage/'inputs.json',dict(files={str(path.relative_to(stage)):sha(path)
        for path in original.rglob('*') if path.is_file()}))


if __name__=='__main__':main()
