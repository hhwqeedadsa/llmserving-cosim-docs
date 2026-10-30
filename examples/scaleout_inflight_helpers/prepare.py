"""Prepare fair 128-endpoint Clos shared-compute conditions, no execution."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_service import make_wave, validate

ROOT=Path(__file__).resolve().parents[2]


def save(path,value):
    with path.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();output=args.output.resolve()
    subprocess.run([sys.executable,'-B',str(ROOT/'examples/scaleout_source_ack_watchdog/prepare.py'),
                    '--output',str(output)],cwd=ROOT,check=True)
    c=json.loads((output/'service-watchdog.json').read_bytes())
    c['inflight_helpers']=dict(schema_version='moe-inflight-helpers/1',policy='observe_only/1',
        max_attempts=1,max_events=12,max_compute_tasks=4096,max_decisions=128)
    c['contribution_tracking']['max_lifecycle_events']+=12
    replay=Replay(c,ROOT)
    waves=[make_wave(c,group,index,step,1,replay) for index,group in enumerate(c['parallel']['groups'])
           for step in range(c['workload']['decode_steps'])]
    streams=c['transport']['streams'];keys={(s['src'],s['dst'],s['plane']) for s in streams}
    needed={(s,d,row['plane']) for w in waves for row in w['helper_candidates']
            for s,d in [(row['source'],row['provider']),(row['provider'],row['source'])]}
    for s,d,p in sorted(needed-keys):
        i=len(streams)
        streams.append(dict(id=i,src=s,dst=d,plane=p,wire_port=21000+i,source_nic=1,destination_nic=1,queue_depth=32))
    c['transport']['limits']['max_streams']=len(streams)
    topology=json.loads((output/'topology/topology.json').read_bytes())
    p=json.loads((output/'rdma.json').read_bytes())
    for policy,name in [('observe_only/1','service-observe.json'),('first_missing_bundle/1','service-helper.json')]:
        selected=deepcopy(c);selected['inflight_helpers']['policy']=policy
        validate(selected,topology,p,Replay(selected,ROOT))
        save(output/name,selected)
    save(output/'helper-preparation.json',dict(passed=True,network_executed=False,gpu_endpoints=128,
        expert_parallel_groups=[64,64],requests=4,synthetic=True,shared_streams=len(streams),
        helper_candidates=sum(len(w['helper_candidates']) for w in waves),max_extra_attempts=1,
        conditions=['normal observe-only','local observe-only','local helper'],
        scope='same streams, resource budgets, compute and original graph gates; no application recovery claim'))
    print(json.dumps(dict(passed=True,network_executed=False,shared_streams=len(streams))))


if __name__=='__main__':main()
