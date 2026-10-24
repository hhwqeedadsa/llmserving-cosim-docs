"""Malformed reference inputs rejected by both native entrypoints before Fabric."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess


def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);a=p.parse_args();s=a.stage
    out=s/'native-rejections';out.mkdir();base=json.loads((s/'inputs/chain.json').read_text());records=[]
    for mode in ('missing','v4','pool','gain_bool','gain_zero','extra','interval','max_events_bool','cnp_bytes'):
        w=deepcopy(base);ref=w['dcqcn_reference'];expected='dcqcn_reference'
        if mode=='missing':del w['dcqcn_reference']
        elif mode=='v4':w['schema_version']='moe-rdma-transactions/4';expected='requires v5'
        elif mode=='pool':w['buffer_model']={};expected='excludes bounded'
        elif mode=='gain_bool':ref['gain']=True;expected='invalid reference gain'
        elif mode=='gain_zero':ref['gain']=0;expected='invalid reference gain'
        elif mode=='extra':ref['extra']=0
        elif mode=='interval':ref['np_interval_ns']=5000001;expected='reference interval'
        elif mode=='max_events_bool':ref['max_events']=True;expected='positive integers'
        else:ref['cnp_model_bytes']=29;expected='reference parameters'
        for entry in ('transaction-probe','transaction-session'):
            candidate=deepcopy(w)
            if entry.endswith('session'):candidate['operations']=[]
            path=out/(mode+'-'+entry+'.json');save(path,candidate);result=out/(mode+'-'+entry+'-result.json')
            cmd=[str(s/'bin'/entry),str(s/'topology/topology.json'),str(s/'inputs/protocol.json'),str(path),str(result)]
            done=subprocess.run(cmd,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=5)
            path.with_suffix('.stdout.log').write_text(done.stdout);path.with_suffix('.stderr.log').write_text(done.stderr)
            record=dict(mode=mode,entry=entry,command=cmd,cwd=str(Path.cwd()),exit_code=done.returncode,expected_error=expected)
            records.append(record);save(out/'commands.json',records)
            assert done.returncode!=0 and expected in done.stderr and not result.exists() and not done.stdout,record
    save(s/'native-rejections.json',dict(passed=True,rejections=len(records),network_executions=0,
        scope='all chosen errors detected by pure pre-Fabric reference validation',commands=records))


if __name__=='__main__':main()
