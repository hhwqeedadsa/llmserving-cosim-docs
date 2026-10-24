"""Offline replay from the restored source root, never invoking a network."""
import argparse
from pathlib import Path
from fixtures.nack_grace.stage import load,save,run,sha


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();root=s/'restored/source';empty=s/'replay-empty';empty.mkdir()
    run(repo,s,'restored-inspection',['python3','-B','-m','fixtures.nack_grace.inspect','--stage',s/'restored/evidence',
        '--output',s/'replay-inspection.json'],cwd=empty,env=dict(PYTHONPATH=str(root/'python')+':'+str(root)))
    assert (s/'inspection.json').read_bytes()==(s/'replay-inspection.json').read_bytes()
    save(s/'replay.json',dict(passed=True,inspection_byte_equal=True,platform_acceptance_complete=False,
        empty_cwd=str(empty),source_root=str(root),archive_sha256=sha(s/'evidence.tar.gz'),network_executions=0))

if __name__=='__main__':main()
