"""Run the six registered functional networks once each, via public CLI."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('stage','build','old-build'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();stage=a.stage.resolve();out=stage/'network';out.mkdir(exist_ok=False)
    old,new=load(a.old_build/'backend.json'),load(a.build/'backend.json')
    provenance=load(a.build/'provenance.json')
    for f,h in provenance['sources'].items():assert sha(ROOT/f)==h,f
    inp=stage/'input';cases=['normal-old','normal-new','live','prefix','restore','instant']
    frozen=dict(cases=cases,max_networks=6,gpu_endpoints=128,platform_source_sha256=provenance['sources'],
      input_sha256={str(f.relative_to(inp)):sha(f) for f in inp.rglob('*') if f.is_file()},
      native_sha256={x['stream_probe']:sha(Path(x['stream_probe'])) for x in (old,new)})
    with (stage/'frozen.json').open('x') as s:json.dump(frozen,s,indent=2);s.write('\n')
    receipts=[]
    for name in cases:
        backend=old if name=='normal-old' else new
        protocol='normal' if name.startswith('normal') else name
        command=[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-stream-probe',
          '--topology',str(inp/'topology'),'--protocol',str(inp/(protocol+'.json')),
          '--config',str(inp/'work.json'),'--backend',backend['stream_probe'],'--output',str(out/name)]
        monitored=[sys.executable,'-B',str(ROOT/'fixtures/explicit_prefix/monitor.py'),
          '--root',str(stage),'--stage','network-'+name,'--',*command]
        print('Starting '+name,flush=True);status=subprocess.run(monitored,cwd=ROOT).returncode
        receipts.append(dict(case=name,command=command,cwd=str(ROOT),exit_code=status))
        (stage/'network-receipts.json').write_text(json.dumps(receipts,indent=2)+'\n')
        if status:raise SystemExit(status)
    print('Six registered128Clos networks completed once each',flush=True)


if __name__=='__main__':main()
