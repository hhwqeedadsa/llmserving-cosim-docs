"""Prepare identical 128-Clos local-fault conditions with declared GPU contention.

Synthetic functional stress, not real model calibration or recovery-benefit evidence.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_service import make_wave, validate

ROOT=Path(__file__).resolve().parents[2]


def load(path):return json.loads(path.read_bytes())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):
    with path.open('x') as f:json.dump(value,f,indent=2);f.write('\n')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();output=a.output.resolve()
    subprocess.run([sys.executable,'-B',str(ROOT/'examples/scaleout_inflight_helpers/prepare.py'),
                    '--output',str(output)],cwd=ROOT,check=True)
    # A distinct derived source, rather than modifying or relabelling its parent.
    raw=load(output/'sources/1.json');raw[1]['0']=[[12,13,14,15,16,17,18,19]]
    save(output/'sources/1-contended.json',raw)
    manifest=load(output/'manifest.json')
    manifest['source']['dataset_revision']='example-compute-contention-v1'
    manifest['files'][1]['file']=dict(path=str(output/'sources/1-contended.json'),sha256=sha(output/'sources/1-contended.json'))
    manifest['files'][1]['original_path']='synthetic/compute-contention/1.json'
    save(output/'manifest-contended.json',manifest)
    c=load(output/'service-observe.json')
    c['route_replay']['manifest']=dict(path=str(output/'manifest-contended.json'),sha256=sha(output/'manifest-contended.json'))
    c['execution_events']['schema_version']='moe-execution-events/2'
    c['compute']['expert_ns_per_contribution']=50000
    replay=Replay(c,ROOT)
    waves=[make_wave(c,g,i,s,1,replay) for i,g in enumerate(c['parallel']['groups']) for s in range(c['workload']['decode_steps'])]
    keys={(m['src'],m['dst'],m['plane']) for w in waves for m in w['messages']}
    keys|={(s,d,h['plane']) for w in waves for h in w['helper_candidates']
           for s,d in [(h['source'],h['provider']),(h['provider'],h['source'])]}
    c['transport']['streams']=[dict(id=i,src=s,dst=d,plane=plane,wire_port=21000+i,source_nic=1,
        destination_nic=1,queue_depth=32) for i,(s,d,plane) in enumerate(sorted(keys))]
    c['transport']['limits']['max_streams']=len(keys)
    t=load(output/'topology/topology.json');rdma=load(output/'rdma-transient.json')
    for policy,name in [('observe_only/1','service-actual-observe.json'),('first_missing_bundle/1','service-actual-helper.json')]:
        selected=deepcopy(c);selected['inflight_helpers']['policy']=policy
        validate(selected,t,rdma,Replay(selected,ROOT));save(output/name,selected)
    save(output/'compute-preparation.json',dict(passed=True,network_executed=False,synthetic=True,
        gpu_endpoints=128,expert_parallel_groups=[64,64],requests=4,shared_streams=len(keys),
        expert_ns_per_contribution=50000,changed_source='request 1, decode entry 1, layer 0, experts 12..19',
        fault='destination GPU6 DATA only [0,20000ns); ACK/control retained',
        conditions=['same local fault observe','same local fault helper'],
        purpose='actual original/helper GPU FIFO start and waiting correctness, no performance calibration'))
    print(json.dumps(dict(passed=True,network_executed=False,shared_streams=len(keys))))


if __name__=='__main__':main()
