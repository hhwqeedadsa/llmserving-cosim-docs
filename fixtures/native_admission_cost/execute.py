"""Execute exactly the frozen compiler command or one offline measurement."""
import argparse
from pathlib import Path
import subprocess
from fixtures.native_wakeup.stage import load, sha


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--repository',type=Path,required=True)
    p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--action',choices=('compile','measure'),required=True)
    a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve()
    identity=load(stage/'identity.json')
    assert all(sha(Path(name))==digest for name,digest in identity['fixed_link_inputs'].items())
    assert all(sha(repo/name)==digest for name,digest in identity['native_headers'].items())
    assert sha(repo/'fixtures/native_admission_cost/measure.cc')==identity['driver_sha256']
    for name,row in identity['inputs'].items():assert sha(stage/'inputs'/name)==row['sha256']
    if a.action=='measure':
        assert not (stage/'timings.jsonl').exists() and not (stage/'final-program.json').exists()
    command=identity['compile_command' if a.action=='compile' else 'measurement_command']
    subprocess.run(command,cwd=repo,check=True,timeout=55)


if __name__=='__main__':main()
