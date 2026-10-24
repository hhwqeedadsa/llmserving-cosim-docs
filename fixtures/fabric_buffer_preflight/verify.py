"""Public offline workflow and independent comparison to saved native MMU facts."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]


def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,sort_keys=True,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True);a=p.parse_args();out=a.stage.resolve();out.mkdir(exist_ok=False)
    reference=read(Path(__file__).with_name('native-reference.json'))
    for rel,digest in reference['source_hashes'].items():assert sha(ROOT/rel)==digest,rel
    for name,v in [('protocol.json',reference['protocol']),('reference-topology.json',reference['topology'])]:save(out/name,v)
    commands=[]
    def cli(name,args,expected):
        command=[sys.executable,'-B','-m','moe_ub_cosim',*map(str,args)]
        result=subprocess.run(command,cwd=ROOT,text=True,capture_output=True,timeout=15)
        row=dict(name=name,command=command,cwd=str(ROOT),exitcode=result.returncode,stdout=result.stdout,stderr=result.stderr)
        commands.append(row);save(out/'commands.json',commands);assert result.returncode==expected,name
        return result
    cli('fresh-topology',['scaleout','topology','--config',ROOT/'examples/scaleout_rdma/small-clos-100g.json','--output',out/'topology'],0)
    assert read(out/'topology/topology.json')==reference['topology']
    common=['--topology',out/'topology','--protocol',out/'protocol.json']
    cli('good-preflight',['scaleout','rdma-preflight',*common,'--output',out/'good.json'],0)
    good=read(out/'good.json');assert good['passed'] and not good['network_executed']
    ports={(r['node'],p['port']):(p,r) for r in good['switches'] for p in r['ports']}
    assert len(ports)==len(reference['native_ports'])
    for actual in reference['native_ports']:
        p,r=ports[actual['node'],actual['port']]
        assert all(p[k]==actual[k] for k in ('headroom_bytes','rate_bps','delay_ns'))
        assert r['initial_pfc_threshold_bytes']==actual['initial_pfc_threshold_bytes']
        assert r['reserve_bytes']==len(r['ports'])*4096
    # A purpose-built v1 input checks the existing ordering requirement.
    work=dict(schema_version='moe-rdma-transactions/1',connections=[dict(id=i,src=i,dst=4,plane=0,request_port=24000+2*i,response_port=24001+2*i,source_nic=1,destination_nic=1,queue_depth=2) for i in (0,1)],windows=[dict(owner=4,key=9,address=8192,allow_atomic=False,hex='00'*32)],operations=[dict(id=i,connection=i,verb='put',key=9,address=8192,hex=('%02x'%(17+i*17))*8,after=[]) for i in (0,1)])
    save(out/'unordered.json',work)
    cli('unordered-preflight',['scaleout','rdma-preflight',*common,'--transactions',out/'unordered.json','--output',out/'unordered-check.json'],1)
    assert 'remote effect order' in read(out/'unordered-check.json')['transaction_check']['error']
    work['operations'][1]['after']=[dict(operation=0,event='applied')];save(out/'ordered.json',work)
    cli('ordered-preflight',['scaleout','rdma-preflight',*common,'--transactions',out/'ordered.json','--output',out/'ordered-check.json'],0)
    late=deepcopy(reference['topology']);next(l for l in late['links'] if l['src']==0)['delay_ns']=250000
    (out/'late').mkdir();save(out/'late/topology.json',late)
    cli('bad-capacity-preflight',['scaleout','rdma-preflight','--topology',out/'late','--protocol',out/'protocol.json','--output',out/'bad.json'],1)
    bad=[r for r in read(out/'bad.json')['switches'] if not r['passed']]
    assert [(r['node'],r['required_bytes'],r['buffer_bytes']) for r in bad]==[(8,9503884,8388608)]
    # This executable records any actual launch. Every public runtime entry
    # must reject before invoking it or creating its run directory.
    backend=out/'must-not-run';marker=out/'backend-was-launched'
    backend.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\nexit 9\n');backend.chmod(0o755)
    runtime_common=['--topology',out/'late','--backend',backend]
    calls=[('rdma-probe',['--config',out/'protocol.json','--messages',ROOT/'examples/scaleout_rdma/messages.json']),
        ('rdma-stream-probe',['--protocol',out/'protocol.json','--config',ROOT/'examples/scaleout_rdma_streams/work.json']),
        ('rdma-transaction-probe',['--protocol',out/'protocol.json','--config',ROOT/'examples/scaleout_rdma_transactions/bounded-buffers.json']),
        ('rdma-transaction-session',['--protocol',out/'protocol.json','--config',ROOT/'examples/scaleout_rdma_transactions/bounded-buffers.json']),
        ('rdma-run',['--protocol',out/'protocol.json','--config',ROOT/'examples/scaleout_rdma/service.json'])]
    for name,extra in calls:
        destination=out/('rejected-'+name)
        result=cli(name,['scaleout',name,*runtime_common,*extra,'--output',destination],1)
        assert 'switch 8 headroom/reserve requires 9503884B' in result.stderr,(name,result.stderr)
        assert not destination.exists() and not marker.exists()
    save(out/'verification.json',dict(passed=True,native_ports_matched=len(ports),public_commands=len(commands),runtime_entries_rejected_before_backend=len(calls),native_networks_executed=0,source_hashes=reference['source_hashes'],native_reference_sha256=sha(Path(__file__).with_name('native-reference.json')),
        required_bad_bytes=9503884,capacity_bytes=8388608,transaction_order_rejected=True,explicit_order_accepted=True))
    print((out/'verification.json').read_text())


if __name__=='__main__':main()
