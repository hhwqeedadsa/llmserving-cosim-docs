"""Run only the two amended, legal, previously unexecuted conditions."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from moe_ub_cosim.host_probe import probe,disk_usage


def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True);a=p.parse_args()
    s=a.stage.resolve();repo=Path(__file__).resolve().parents[2];m=read(s/'manifest.json')
    review=read(s/'admission-review.json');assert not review['result']['new_committed'] and len(review['tampering'])==3
    for rel,h in m['input_hashes'].items():assert sha(s/rel)==h
    for row in [m['backend'],*m['libraries']]:assert sha(Path(row['path']))==row['sha256']
    amendment=dict(schema_version='late-write-safe-amendment/1',original_matrix_stopped=True,
        script_hashes={str(p.relative_to(repo)):sha(p) for p in sorted((repo/'experiments/late_write_isolation').glob('*.py'))},
        design_sha256=sha(repo/'experiments/late_write_isolation/amendment.md'),conditions=[
            dict(name='safe-late-isolated',policy='inputs/late-isolated.json',topology='inputs/late-topology.json',expected_partial=True,declared_effect_order=False),
            dict(name='safe-late-receive-order',policy='inputs/late-receive-order.json',topology='inputs/late-topology.json',expected_partial=True,declared_effect_order=True)])
    path=s/'safe-amendment.json';assert not path.exists();path.write_text(json.dumps(amendment,sort_keys=True,indent=2)+'\n')
    records=[]
    for case in amendment['conditions']:
        assert sum(read(q)['wall_seconds'] for q in s.rglob('probe.json'))<240
        out=s/'cases'/case['name'];assert not out.exists()
        argv=[sys.executable,'-B',str(repo/'experiments/late_write_isolation/safe_controller.py'),
            '--topology',str(s/case['topology']),'--protocol',str(s/'inputs/protocol.json'),
            '--initial',str(s/'inputs/initial.json'),'--policy',str(s/case['policy']),
            '--backend',m['backend']['path'],'--output',str(out)]
        report=probe(argv,s/'monitors'/case['name'],rss_bytes=2**30,address_bytes=2**31,artifact_bytes=16*2**20,wall_seconds=60,artifact_roots=[out],min_free_bytes=8*2**30)
        records.append(dict(name=case['name'],status=report['status'],exit_code=report['child_exit_code'],wall_seconds=report['wall_seconds']))
        (s/'safe-execution.json').write_text(json.dumps(records,indent=2)+'\n');print(json.dumps(records[-1]),flush=True)
        assert report['status']=='completed' and not report['remaining_live_group_pids'],'preserve failure; stop'
        r=read(out/'truth/result.json');assert not r['passed'] and r['finish_ns']==5000000 and 'execution_error' not in r
        assert max(disk_usage(s).values())<64*2**20


if __name__=='__main__':main()
