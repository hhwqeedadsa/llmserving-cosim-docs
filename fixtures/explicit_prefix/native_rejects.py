"""Reject malformed policies and unsupported topologies before native allocation."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess

def load(p):return json.loads(p.read_bytes())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('stage','build'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();stage=a.stage.resolve();out=stage/'native-rejects';out.mkdir(exist_ok=False)
    backend=load(a.build/'backend.json')['stream_probe'];inp=stage/'input'
    topology=load(inp/'topology/topology.json');protocol=load(inp/'prefix.json');cases=[]
    for value in (True,0,100001):
        c=deepcopy(protocol);c['local_failover']['max_events']=value
        cases.append(('budget-'+str(value),topology,c,'invalid repair event budget'))
    c=deepcopy(protocol);del c['local_failover']['max_events'];cases.append(('missing-budget',topology,c,'unsupported local failover policy'))
    c=deepcopy(protocol);c['local_failover']['extra']=0;cases.append(('extra',topology,c,'unsupported local failover policy'))
    c=deepcopy(protocol);c['link_failures']['events'].append(dict(src=129,dst=132,time_ns=5000,route_update_delay_ns=60000))
    cases.append(('two-links',topology,c,'exactly one switch link'))
    t=deepcopy(topology);t['nodes'][133]['plane']=1;cases.append(('multi-plane',t,protocol,'single plane0'))
    t=deepcopy(topology);t['links'].append(dict(src=0,dst=129,src_port=2,dst_port=1024,rate_bps=100000000000,delay_ns=1000))
    cases.append(('multi-NIC',t,protocol,'one NIC per GPU'))
    t=deepcopy(topology);t['nodes'][0]['kind']='tor';cases.append(('wrong-scale',t,protocol,'requires128/256/512'))
    receipts=[]
    for name,t,c,why in cases:
        d=out/name;d.mkdir()
        for filename,value in [('topology.json',t),('protocol.json',c)]:
            (d/filename).write_text(json.dumps(value)+'\n')
        command=[backend,str(d/'topology.json'),str(d/'protocol.json'),str(inp/'work.json'),str(d/'result.json')]
        r=subprocess.run(command,capture_output=True,text=True,timeout=5)
        (d/'stdout').write_text(r.stdout);(d/'stderr').write_text(r.stderr)
        assert r.returncode!=0 and why in r.stderr,(name,r.returncode,r.stderr)
        assert not (d/'result.json').exists()
        receipts.append(dict(case=name,command=command,exit_code=r.returncode,required_error=why))
    (out/'receipt.json').write_text(json.dumps(dict(passed=True,cases=receipts,network_executed=False),indent=2)+'\n')
    print(json.dumps(dict(passed=True,native_input_rejections=len(cases),network_executed=False)))


if __name__=='__main__':main()
