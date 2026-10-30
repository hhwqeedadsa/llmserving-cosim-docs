"""Offline public checks and wrong-admission rejection; no network execution."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def load(path):return json.loads(path.read_bytes())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):
    with path.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();root=args.runs.resolve();output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    original_hashes={str(p):sha(p) for mode in ('normal','local','helper') for p in (root/mode).rglob('*') if p.is_file()}
    commands=[]
    def check(run,ok,reason=None):
        argv=[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-check','--run',str(run)]
        r=subprocess.run(argv,capture_output=True,text=True,timeout=30)
        commands.append(dict(argv=argv,exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr))
        assert (r.returncode==0)==ok,r.stderr
        if reason:assert reason in r.stderr,r.stderr
        return r
    for mode in ('normal','local','helper'):check(root/mode,True)
    relocated=output/'relocated';shutil.copytree(root/'helper',relocated);check(relocated,True)
    originals={name:(relocated/name).read_bytes() for name in ('helper_events.jsonl','native.stdout.log')}
    rejected={}
    for name,kind,delta,reason in [('backdated','accepted',-1,'admission differs from synchronous commit'),
        ('delayed','accepted',1,'admission differs from synchronous commit'),
        ('delayed-post','input_post',1,'input post differs from immediate admission')]:
        for path,raw in originals.items():(relocated/path).write_bytes(raw)
        events=[json.loads(line) for line in originals['helper_events.jsonl'].decode().splitlines()]
        target=next(e for e in events if e['kind']==kind);target['time_ns']+=delta
        raw=originals['native.stdout.log'].decode().splitlines(keepends=True)
        index=next(i for i,line in enumerate(raw) if line.startswith('MOE_RDMA_EVENT ') and
            (f:=json.loads(line[len('MOE_RDMA_EVENT '):]))['type']=='HELPER_OBSERVE' and f['kind']==kind)
        raw[index]='MOE_RDMA_EVENT '+json.dumps(target)+'\n'
        (relocated/'native.stdout.log').write_text(''.join(raw))
        (relocated/'helper_events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
        rejected[name]=check(relocated,False,reason).stderr
    for path,raw in originals.items():(relocated/path).write_bytes(raw)
    assert all(sha(Path(p))==h for p,h in original_hashes.items())
    save(output/'verification.json',dict(passed=True,public_original_checks=3,relocated_check=True,
        rejected=rejected,original_hashes=original_hashes,commands=commands,
        new_network_executions=0,scope='saved source admission must match exact synchronous decision time; no native behavior changed'))
    print(json.dumps(dict(passed=True,originals_unchanged=True,negative_checks=3,new_network_executions=0)))


if __name__=='__main__':main()
