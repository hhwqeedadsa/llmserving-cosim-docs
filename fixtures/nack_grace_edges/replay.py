"""Recompute aggregate acceptance in an extracted Python root and empty cwd."""
import argparse
from pathlib import Path
from fixtures.nack_grace_edges.stage import run,save,sha


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();root=s/'restored/source';empty=s/'empty-cwd';empty.mkdir()
    run(repo,s,'restored-aggregate',['python3','-B','-m','fixtures.nack_grace.verify','--repository',root,
        '--stage',s/'restored/baseline','--additional-stage',s/'restored/evidence','--output',s/'replay-aggregate.json'],
        cwd=empty,env=dict(PYTHONPATH=str(root/'python')+':'+str(root)))
    assert (s/'aggregate-final.json').read_bytes()==(s/'replay-aggregate.json').read_bytes()
    save(s/'replay.json',dict(passed=True,aggregate_byte_equal=True,network_executions=0,
        source_root=str(root),empty_cwd=str(empty),archive_sha256=sha(s/'evidence.tar.gz'),
        scope='offline source/evidence replay; native library/OS clean-build portability not claimed'))

if __name__=='__main__':main()
