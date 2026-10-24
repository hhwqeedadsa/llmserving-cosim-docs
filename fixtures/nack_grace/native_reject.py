"""Reject malformed new options before Fabric; no valid network is started."""
import argparse
from copy import deepcopy
from pathlib import Path
import subprocess
from stage import load,save


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();out=s/'native-rejections';out.mkdir()
    work=load(s/'inputs/head-loss-work.json');topo=s/'topology/topology.json'
    # If validation reaches Fabric, this invalid control mode must fail there;
    # every expected error below must instead identify the reordering option.
    proto=load(s/'inputs/head-loss-protocol.json');proto['cc_mode']=999
    save(out/'protocol.json',proto)
    options=[None,{},dict(mode='unknown',wait_ns=1,max_events=1)]
    normal=work['transport_recovery']['reordering']
    for key,values in [('wait_ns',[0,True,1.5,80001]),('max_events',[0,True,100001])]:
        for value in values:options.append(dict(normal,**{key:value}))
    options.append(dict(normal,oracle=True))
    results=[]
    for exe in ('stream-probe','transaction-probe','transaction-session'):
        original=work if exe=='stream-probe' else load(repo/'examples/scaleout_rdma_transactions/selective-chain.json')
        if exe=='transaction-session':original['operations']=[]
        for i,option in enumerate(options):
            x=deepcopy(original);x['transport_recovery']=deepcopy(work['transport_recovery'])
            x['transport_recovery']['reordering']=option
            inp=out/(exe+'-'+str(i)+'.json');save(inp,x);target=out/(exe+'-'+str(i)+'-result.json')
            cmd=[str(s/'bin'/exe),str(topo),str(out/'protocol.json'),str(inp),str(target)]
            done=subprocess.run(cmd,capture_output=True,text=True,timeout=5)
            save(out/(exe+'-'+str(i)+'-receipt.json'),dict(command=cmd,exit_code=done.returncode,stdout=done.stdout,stderr=done.stderr))
            assert done.returncode!=0 and not target.exists()
            assert 'reordering' in done.stderr,done.stderr
            results.append(dict(executable=exe,case=i,rejected_before_fabric=True))
    # go_back_n must reject the option even when no selective capacities exist.
    x=deepcopy(work);x['transport_recovery']['mode']='go_back_n/1'
    for key in ('receive_buffer_bytes','inflight_bytes'):del x['transport_recovery'][key]
    save(out/'gbn.json',x);target=out/'gbn-result.json'
    cmd=[str(s/'bin/stream-probe'),str(topo),str(out/'protocol.json'),str(out/'gbn.json'),str(target)]
    done=subprocess.run(cmd,capture_output=True,text=True,timeout=5)
    save(out/'gbn-receipt.json',dict(command=cmd,exit_code=done.returncode,stdout=done.stdout,stderr=done.stderr))
    assert done.returncode!=0 and 'reordering' in done.stderr and not target.exists()
    save(s/'native-rejections.json',dict(passed=True,cases=results,go_back_n_rejected=True,actual_networks=0))

if __name__=='__main__':main()
