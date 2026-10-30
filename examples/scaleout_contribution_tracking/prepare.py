"""Prepare a fresh 128-endpoint Clos contribution-tracking functional input.

Uses the public synthetic route generator; no tests, old runs or real accounts.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.contribution_binding import ContributionBinding, preflight
from moe_ub_cosim.execution_events import ExecutionObserver
from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_service import make_wave, validate

ROOT = Path(__file__).resolve().parents[2]


def save(path,value):
    with path.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')


def prepare(output,namespace):
    output=output.resolve()
    command=[sys.executable,'-B',str(ROOT/'examples/scaleout_token_transport/prepare.py'),
             '--output',str(output),'--combine','rank_reduced/1','--local-reduce-ns-per-extra','100']
    subprocess.run(command,cwd=ROOT,check=True)
    c=json.loads((output/'service.json').read_bytes())
    c['execution_events']=dict(schema_version='moe-execution-events/1',max_events=32768)
    c['contribution_tracking']=dict(schema_version='moe-contribution-binding/1',namespace=namespace,
        max_invocations=64,max_bundles=512,max_lifecycle_events=6144,max_report_bytes=4*2**20)
    replay=Replay(c,ROOT)
    binding=ContributionBinding(c,replay,ExecutionObserver(c['execution_events']))
    for index,group in enumerate(c['parallel']['groups']):
        for step in range(c['workload']['decode_steps']):
            binding.register(make_wave(c,group,index,step,1,replay))
    p=json.loads((output/'rdma.json').read_bytes())
    fault=deepcopy(p)
    fault['data_loss']=dict(kind='periodic_destination_data/1',numerator=1,denominator=1,offset=0,
        start_ns=0,end_ns=20000,nodes=[6])
    topology=json.loads((output/'topology/topology.json').read_bytes())
    validate(c,topology,p,replay);validate(c,topology,fault,replay)
    save(output/'service-tracked.json',c)
    save(output/'rdma-transient.json',fault)
    summary=dict(passed=True,network_executed=False,gpu_endpoints=128,ep_sizes=[64,64],
        independent_synthetic_sources=4,preflight=preflight(c,replay),
        reference_report_bytes=4*2**20,fault_scope='destination GPU6 DATA blackhole [0,20us); controls retained',
        preparation_command=command)
    save(output/'preparation-tracking.json',summary)
    print(json.dumps(summary))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--namespace',default='contribution-example-v1')
    args=parser.parse_args()
    prepare(args.output,args.namespace)
