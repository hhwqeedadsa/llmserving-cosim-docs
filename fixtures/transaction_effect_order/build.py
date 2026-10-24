"""Rebuild only effect-order consumers using fixed existing build receipts."""
import hashlib
import json
from pathlib import Path
from moe_ub_cosim.host_probe import probe

ROOT=Path(__file__).resolve().parents[2]
OLD=Path('/dev/shm/moe-persistent-recovery-20261024')
OUT=Path('/dev/shm/moe-effect-order-20261024/build')
def read(p):return json.loads(p.read_text())
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def main():
    OUT.mkdir();(OUT/'objects').mkdir();(OUT/'bin').mkdir();records=[]
    protected={str(p):sha(p) for folder in [OLD/'bin',OLD/'updated-entries/bin',
        Path('/dev/shm/moe-fault-20261021/native/output/lib')] for p in folder.iterdir() if p.is_file()}
    def run(name,cmd):
        r=probe(list(map(str,cmd)),OUT/name,rss_bytes=2**30,address_bytes=2**31,
            artifact_bytes=64*2**20,wall_seconds=60,artifact_roots=[OUT/'objects',OUT/'bin'],min_free_bytes=8*2**30)
        records.append(r);(OUT/'records.json').write_text(json.dumps(records,indent=2)+'\n')
        assert r['status']=='completed',name
    for name in ['remote_transactions','transaction_session']:
        receipt=OLD/('build-'+name)/'probe.json'
        cmd=read(receipt)['command'];cmd[-1]=str(ROOT/'backend/rdma'/(name+'.cc'))
        for flag,suffix in [('-o','.cc.o'),('-MT','.cc.o'),('-MF','.cc.o.d')]:
            cmd[cmd.index(flag)+1]=str(OUT/'objects'/(name+suffix))
        run('build-'+name,cmd)
    for name in ['transaction-probe','transaction-session']:
        cmd=read(OLD/'updated-entries'/('link-'+name)/'probe.json')['command']
        cmd[cmd.index('-o')+1]=str(OUT/'bin'/('ns3.42-moe-rdma-'+name))
        for i,v in enumerate(cmd):
            if Path(v).name in ['remote_transactions.cc.o','transaction_session.cc.o']:
                cmd[i]=str(OUT/'objects'/Path(v).name)
        run('link-'+name,cmd)
    assert all(sha(Path(p))==h for p,h in protected.items())
    (OUT/'identity.json').write_text(json.dumps(dict(protected=protected,
        binaries={str(p):sha(p) for p in (OUT/'bin').iterdir()},
        sources={str(p.relative_to(ROOT)):sha(p) for p in (ROOT/'backend/rdma').glob('*') if p.suffix in ['.hh','.cc']},
        monitor_seconds=sum(r['wall_seconds'] for r in records)),indent=2)+'\n')
    print('built effect-order transaction and session entries; prior runtimes unchanged')
if __name__=='__main__':main()
