"""Same-input synthetic host comparison; no network, GPU or MoE timing."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

from moe_ub_cosim.native_proof_coverage import NativeProofCoverage

ROOT=Path(__file__).resolve().parents[2]


class DictionaryCoverage:
    """Frozen e55ae45eb1 coverage logic, independent of the new index."""
    def __init__(self):self.rows={}

    def compact(self,pairs):
        merged={}
        for identity,event in pairs:
            assert event in ('applied','completed')
            merged[identity]=max(merged.get(identity,0),int(event=='completed'))
        return [(i,'completed' if level else 'applied') for i,level in sorted(merged.items())
                if not any(j!=i and self.rows[j].get(i,-1)>=level for j in merged)]

    def append(self,proof):
        closure={};identity=len(self.rows)
        for i,event in proof:
            for p,level in self.rows[i].items():closure[p]=max(closure.get(p,-1),level)
            closure[i]=max(closure.get(i,-1),int(event=='completed'))
        self.rows[identity]=closure
        return identity

    def covers(self,node,ancestor,event):return self.rows[node].get(ancestor,-1)>=int(event=='completed')

    def storage_bytes(self):
        seen=set()
        def size(obj):
            if id(obj) in seen:return 0
            seen.add(id(obj));n=sys.getsizeof(obj)
            if isinstance(obj,dict):n+=sum(size(k)+size(v) for k,v in obj.items())
            return n
        return size(self.rows)


def worker(mode,nodes):
    index=DictionaryCoverage() if mode=='dictionary' else NativeProofCoverage()
    sha=hashlib.sha256();start=time.perf_counter();cpu=time.process_time();checks=0
    for node in range(nodes):
        proof=[]
        if node:proof.append((node-1,'applied' if node%3==0 else 'completed'))
        if node>2:proof.append((node//2,'completed' if node%5 else 'applied'))
        frontier=index.compact(proof)
        assert index.append(frontier)==node
        values=[index.covers(node,a,event) for a in sorted({0,node//2,node}) for event in ('applied','completed')]
        checks+=len(values)
        sha.update(json.dumps([node,frontier,values],separators=(',',':')).encode()+b'\n')
    build_wall=time.perf_counter()-start;build_cpu=time.process_time()-cpu
    stored=index.storage_bytes()
    return dict(mode=mode,nodes=nodes,query_checks=checks,semantic_sha256=sha.hexdigest(),
                build_wall_seconds=build_wall,build_cpu_seconds=build_cpu,
                retained_index_object_bytes_estimate=stored,peak_process_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                scope='One synthetic labelled causal chain; no controller, modern task, simulation or inference latency')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path)
    p.add_argument('--worker',choices=('dictionary','compact'));p.add_argument('--nodes',type=int,default=4096)
    a=p.parse_args()
    if not __debug__:raise RuntimeError('Do not use -O')
    if not 1<=a.nodes<=65536:raise ValueError('nodes out of native identity range')
    if a.worker:print(json.dumps(worker(a.worker,a.nodes)));return
    if a.output is None:raise ValueError('output required')
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);rows=[];commands=[]
    env=dict(os.environ,PYTHONPATH=str(ROOT/'python'),PYTHONDONTWRITEBYTECODE='1')
    for mode in ('dictionary','compact'):
        command=[sys.executable,'-B',str(Path(__file__).resolve()),'--worker',mode,'--nodes',str(a.nodes)]
        r=subprocess.run(command,cwd=out,env=env,capture_output=True,text=True,timeout=90)
        (out/(mode+'-stdout.log')).write_text(r.stdout);(out/(mode+'-stderr.log')).write_text(r.stderr)
        commands.append(dict(command=command,cwd=str(out),exit_code=r.returncode))
        assert r.returncode==0,(mode,r.stderr)
        row=json.loads(r.stdout);rows.append(row)
    assert rows[0]['semantic_sha256']==rows[1]['semantic_sha256']
    assert rows[0]['query_checks']==rows[1]['query_checks']
    result=dict(passed=True,commands=commands,observations=rows,semantic_sha256=rows[0]['semantic_sha256'],
                network_runs=0,repetitions_per_mode=1,performance_threshold_asserted=False,
                baseline='e55ae45eb1 coverage/compact arithmetic; snapshot compatibility checked independently',
                source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
