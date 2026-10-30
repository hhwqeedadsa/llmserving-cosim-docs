"""Check option refusals before topology loading, without creating a network."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend',type=Path,required=True);p.add_argument('--old-backend',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    checks=[('no-shared-compute',a.backend,['--execution-events-v2','32'],'actual compute start events require shared inflight compute'),
        ('mixed-versions',a.backend,['--execution-events-v2','32','--execution-events','32'],'invalid execution event option'),
        ('old-backend',a.old_backend,['--execution-events-v2','32'],'unexpected service option')]
    rows=[]
    for name,backend,options,error in checks:
        result=out/(name+'.result.json')
        command=[str(backend.resolve()),'UNUSED_TOPOLOGY','UNUSED_RDMA','UNUSED_SYSTEM','UNUSED_BOOTSTRAP',str(result),*options]
        r=subprocess.run(command,capture_output=True,text=True)
        (out/(name+'.stdout')).write_text(r.stdout);(out/(name+'.stderr')).write_text(r.stderr)
        assert r.returncode==1 and error in r.stderr and not result.exists()
        rows.append(dict(name=name,argv=command,exit_code=r.returncode,expected_error=error,no_network=True))
    (out/'verification.json').write_text(json.dumps(dict(passed=True,rejections=rows),indent=2)+'\n')
    print(json.dumps(dict(passed=True,rejections=len(rows),network_executed=False)))


if __name__=='__main__':main()
