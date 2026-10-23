"""Rebuild affected objects using existing pinned compiler/link receipts only."""
import argparse
import hashlib
import json
from pathlib import Path
from moe_ub_cosim.host_probe import probe

def read(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v): p.write_text(json.dumps(v,indent=2)+'\n')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--receipts',type=Path,required=True)
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[2];root=args.root.resolve();old=args.receipts.resolve()
    root.mkdir(exist_ok=False);(root/'bin').mkdir();(root/'objects').mkdir()
    prior=read(Path('/dev/shm/moe-sparse-windows-20261023/old-runtime.json'))
    for p in Path('/dev/shm/moe-sparse-windows-20261023/bin').iterdir():
        if p.is_file():prior[str(p)]=sha(p)
    assert all(sha(Path(p))==digest for p,digest in prior.items())
    save(root/'old-runtime.json',prior)
    records=[]
    def run(name,cmd):
        r=probe(list(map(str,cmd)),root/('build-'+name),rss_bytes=2**30,address_bytes=2**31,
                artifact_bytes=64*2**20,wall_seconds=60,artifact_roots=[root/'objects',root/'bin'],min_free_bytes=8*2**30)
        records.append(r);save(root/'build-records.json',records)
        assert r['status']=='completed',name
    for name in ('remote_transactions','transaction_probe','transaction_session'):
        cmd=read(old/'build-remote_transactions/probe.json')['command']
        cmd[-1]=str(repo/'backend/rdma'/(name+'.cc'))
        for flag,ending in (('-o','.cc.o'),('-MT','.cc.o'),('-MF','.cc.o.d')):
            cmd[cmd.index(flag)+1]=str(root/'objects'/(name+ending))
        run(name,cmd)
    for target in ('probe','session'):
        cmd=read(old/'build-link-moe-rdma-transaction-probe/probe.json')['command']
        cmd[cmd.index('-o')+1]=str(root/'bin'/('ns3.42-moe-rdma-transaction-'+target))
        for i,v in enumerate(cmd):
            for name in ('remote_transactions','transaction_probe'):
                if v.endswith('/'+name+'.cc.o'):
                    cmd[i]=str(root/'objects'/((name if name=='remote_transactions' else 'transaction_'+target)+'.cc.o'))
        run('link-'+target,cmd)
    assert all(sha(Path(p))==digest for p,digest in prior.items())
    summary=dict(passed=True,steps=len(records),old_runtime_hashes_unchanged=len(prior),
                 binaries={p.name:dict(sha256=sha(p),bytes=p.stat().st_size) for p in (root/'bin').iterdir()})
    save(root/'build.json',summary);print(json.dumps(summary))

if __name__=='__main__':main()
