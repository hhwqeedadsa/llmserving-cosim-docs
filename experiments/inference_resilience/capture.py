"""Bounded passive replay of four negative/ambiguous cases; execution must match."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--experiment',type=Path,required=True);p.add_argument('--backend',type=Path,required=True)
    p.add_argument('--protoc',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    source=a.experiment.resolve();output=a.output.resolve();exp=read(source/'experiment.json')
    assert output.is_relative_to(source) and not output.exists(),'Capture output must be a new subdirectory of this bounded study'
    used=sum(p.stat().st_size for p in source.rglob('*') if p.is_file())
    allowance=min(128*1024**2,256*1024**2-used-16*1024**2)
    assert allowance>=16*1024**2,'Insufficient remaining study budget; preserve results, no expansion'
    cases=[]
    names=['b4-short-h0-s0','b4-short-h1-s0','b1-short-h0-s0','b1-short-h1-s0']
    for name in names:
        run=source/exp['cases'][name]['run'];protocol=read(run/'rdma.json')
        protocol['packet_capture']=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=100000)
        cases.append(dict(name=name,comparison_group='batch'+str(read(run/'input.json')['workload']['batch_per_rank']),
            patches=dict(service=read(run/'input.json'),protocol=protocol)))
    spec=dict(schema_version='moe-scaleout-sweep/1',base={k:str(source/(k+'.json')) for k in ['service','protocol','topology']},
         budget=dict(wall_seconds=300,rss_bytes=1073741824,address_bytes=2147483648,artifact_bytes=allowance),cases=cases)
    config=source/'capture-input.json';assert not config.exists();save(config,spec)
    argv=[sys.executable,'-m','moe_ub_cosim','scaleout','sweep','--config',str(config),
          '--backend',str(a.backend),'--protoc',a.protoc,'--output',str(output)]
    with (source/'capture.stdout').open('w') as f,(source/'capture.stderr').open('w') as e:
        completed=subprocess.run(argv,cwd=ROOT,stdout=f,stderr=e,timeout=330)
    save(source/'capture-command.json',dict(argv=argv,cwd=str(ROOT),exit_code=completed.returncode,
        source_closed_bytes_before_capture=used,capture_allowance_bytes=allowance,total_study_limit_bytes=256*1024**2))
    if completed.returncode:raise RuntimeError('Packet replay failed; no automatic retry')
    events={}
    for name in names:
        run=source/exp['cases'][name]['run'];observed=read(output/name/'run/result.json');capture=observed.pop('packet_capture')
        assert capture['complete'] and observed==read(run/'result.json'),'Observation changed execution or was truncated'
        before={p.parent.name:p.read_bytes() for p in (run/'graphs').glob('*/wave.json')}
        after={p.parent.name:p.read_bytes() for p in (output/name/'run/graphs').glob('*/wave.json')};assert before==after
        events[name]=len(capture['events'])
    save(output/'capture.json',dict(passed=True,events=events,full_execution_equal_except_packet_capture=True,
        capture_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),scope='passive selected replays; not independent performance samples'))
    print('Four packet replays exactly match their existing inference executions')


if __name__=='__main__':main()
