"""Compile CPU-only wire/path checks against a recorded pinned native build."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('stage','build','topology'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--name',default='native-unit')
    a=p.parse_args();assert '/' not in a.name
    out=a.stage.resolve()/a.name;out.mkdir(exist_ok=False)
    build=a.build.resolve();r=json.loads((build/'backend.json').read_bytes())
    libraries=[Path(f) for f in r['linked_ns3_libraries']]
    for f in libraries:assert hashlib.sha256(f.read_bytes()).hexdigest()==r['library_hashes'][f.name]
    command=['g++','-std=c++20','-O2','-Wall','-Wpedantic','-I'+str(build/'output/include'),
      '-I'+str(ROOT/'backend/rdma'),'-I'+str(ROOT/'third_party/LLMServingSim/astra-sim/extern/helper'),
      str(ROOT/'tests/unit/explicit_repair_native.cc'),'-o',str(out/'unit'),
      '-Wl,-rpath,'+str(build/'output/lib'),*[str(f) for f in libraries]]
    commands=[]
    for name,cmd in [('compile',command),('run',[str(out/'unit'),str(a.topology.resolve()/'topology.json')])]:
        with (out/(name+'.stdout')).open('x') as stdout,(out/(name+'.stderr')).open('x') as stderr:
            r=subprocess.run(cmd,cwd=ROOT,stdout=stdout,stderr=stderr)
        commands.append(dict(command=cmd,cwd=str(ROOT),exit_code=r.returncode))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        if r.returncode:raise SystemExit(r.returncode)
    result=json.loads((out/'run.stdout').read_bytes());assert result['passed'] and not result['network_executed']
    from moe_ub_cosim.scaleout_repair_plan import plan
    expected,_=plan(json.loads((a.topology/'topology.json').read_bytes()),128,132)
    converted=[dict(source=row['source'],destination_ip=0x0b000001+row['destination']*32,
      steps=[[e['src'],e['src_port']] for e in row['repair_prefix_edges']],merge=row['merge'])
      for row in expected['destinations'] if row['state']=='explicit_repair_required']
    assert result['prefixes']==converted
    print(json.dumps(dict(passed=True,network_executed=False,independent_prefixes_compared=len(converted),
      protocol_roundtrips=result['protocol_roundtrips'],illegal_cases_rejected=result['illegal_cases_rejected'])))


if __name__=='__main__':main()
