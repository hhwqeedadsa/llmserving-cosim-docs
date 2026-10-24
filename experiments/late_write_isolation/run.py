"""Execute the two-case gate or its three unexecuted baselines, once only."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from moe_ub_cosim.host_probe import probe, disk_usage


def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--baselines',action='store_true')
    a=p.parse_args();s=a.stage.resolve();repo=Path(__file__).resolve().parents[2]
    m=read(s/'manifest.json')
    for name,digest in m['input_hashes'].items():assert sha(s/name)==digest,name
    for name,digest in m['script_hashes'].items():assert sha(repo/name)==digest,name
    for row in [m['backend'],*m['libraries']]:assert sha(Path(row['path']))==row['sha256']
    conditions=m['conditions'][:2]
    if a.baselines:
        gate=read(s/'gate-audit.json')
        assert len(gate['cases'])==2 and gate['cases'][1]['stale_overwrite']
        assert gate['cases'][0]['output_correct'] and len(gate['tampering'])==3
        conditions=m['conditions'][2:]
    else:
        (s/'cases').mkdir(exist_ok=False);(s/'monitors').mkdir(exist_ok=False)
    records=[]
    for case in conditions:
        assert sum(read(q)['wall_seconds'] for q in s.rglob('probe.json'))<240
        name=case['name'];out=s/'cases'/name
        assert not out.exists() and not (s/'monitors'/name).exists(), 'never rerun a condition'
        argv=[sys.executable,'-B',str(repo/'experiments/late_write_isolation/controller.py'),
            '--topology',str(s/case['topology']),'--protocol',str(s/'inputs/protocol.json'),
            '--initial',str(s/'inputs/initial.json'),'--policy',str(s/case['policy']),
            '--backend',m['backend']['path'],'--output',str(out)]
        report=probe(argv,s/'monitors'/name,rss_bytes=2**30,address_bytes=2**31,
            artifact_bytes=16*2**20,wall_seconds=60,artifact_roots=[out],min_free_bytes=8*2**30)
        row=dict(name=name,status=report['status'],exit_code=report['child_exit_code'],wall_seconds=report['wall_seconds'])
        records.append(row)
        (s/('baselines-execution.json' if a.baselines else 'gate-execution.json')).write_text(json.dumps(records,indent=2)+'\n')
        print(json.dumps(row),flush=True)
        assert report['status']=='completed' and not report['remaining_live_group_pids'], 'preserve unexpected result; stop'
        result=read(out/'truth/result.json')
        assert 'execution_error' not in result
        assert result['passed'] != case['expected_partial']
        assert max(disk_usage(s).values())<64*2**20


if __name__=='__main__':main()
