"""Pure pre-network native admission rejections; no Simulator::Run occurs."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess


def main():
    p=argparse.ArgumentParser()
    for name in ('topology','protocol','work','backend','output'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();a.output.mkdir(exist_ok=False);base=json.loads(a.work.read_text())
    cases=[]
    for key,low,high in [('max_connections',1,64),('max_remote_operations',1,1024),('max_local_operations',0,256)]:
        for value in (True,low-1,high+1,1.0):
            bad=deepcopy(base);bad['execution_limits'][key]=value
            cases.append((key+'-'+str(value),bad))
        bad=deepcopy(base);del bad['execution_limits'][key];cases.append((key+'-missing',bad))
    for label,value in [('null',None),('array',[]),('extra',dict(base['execution_limits'],extra=1))]:
        bad=deepcopy(base);bad['execution_limits']=value;cases.append((label,bad))
    for key,value in [('max_connections',11),('max_remote_operations',95),('max_local_operations',19)]:
        bad=deepcopy(base);bad['execution_limits'][key]=value;cases.append((key+'-count',bad))
    bad=deepcopy(base);bad['schema_version']='moe-rdma-transactions/4';cases.append(('wrong-schema',bad))
    bad=deepcopy(base);bad['connections'][-1]['id']=12;cases.append(('id-range',bad))
    records=[]
    for i,(label,work) in enumerate(cases):
        directory=a.output/(str(i)+'-'+label);directory.mkdir();config=directory/'input.json';result=directory/'result.json'
        config.write_text(json.dumps(work)+'\n');cmd=[str(a.backend),str(a.topology/'topology.json'),str(a.protocol),str(config),str(result)]
        done=subprocess.run(cmd,capture_output=True,timeout=5,cwd=directory)
        (directory/'stdout.log').write_bytes(done.stdout);(directory/'stderr.log').write_bytes(done.stderr)
        assert done.returncode!=0 and not result.exists() and b'RDMA:' in done.stderr,label
        records.append(dict(label=label,command=cmd,cwd=str(directory),exit_code=done.returncode,result_absent=True))
    (a.output/'verification.json').write_text(json.dumps(dict(native_pre_network_rejections=len(records),records=records),indent=2)+'\n')
    print(len(records),'native inputs rejected before execution')


if __name__=='__main__':main()
