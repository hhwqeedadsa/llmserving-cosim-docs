"""Rebuild stream-layout consumers from pinned, existing local receipts."""
import argparse
import hashlib
import json
from pathlib import Path
from moe_ub_cosim.host_probe import probe

ROOT = Path(__file__).resolve().parents[2]
OLD = Path('/dev/shm/moe-persistent-recovery-20261024')
ORDER = Path('/dev/shm/moe-effect-order-20261024/build')
OUT = Path('/dev/shm/moe-source-isolation-20261024/build')

def read(p): return json.loads(p.read_text())
def sha(p):
    with p.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()

def main():
    global OUT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUT)
    OUT=parser.parse_args().output.resolve()
    OUT.mkdir(parents=True, exist_ok=False)
    (OUT/'objects').mkdir(); (OUT/'bin').mkdir()
    records = []
    protected = {str(p):sha(p) for folder in [OLD/'bin', ORDER/'bin',
        Path('/dev/shm/moe-fault-20261021/native/output/lib')]
        for p in folder.iterdir() if p.is_file()}
    def run(label, cmd):
        r = probe(list(map(str, cmd)), OUT/label, rss_bytes=2**30,
            address_bytes=2**31, artifact_bytes=64*2**20, wall_seconds=60,
            artifact_roots=[OUT/'objects', OUT/'bin'], min_free_bytes=8*2**30)
        records.append(r)
        (OUT/'records.json').write_text(json.dumps(records, indent=2)+'\n')
        assert r['status']=='completed', label
        assert sum(x['wall_seconds'] for x in records)<450
    sources = ['persistent_streams','stream_probe','remote_transactions',
               'transaction_probe','transaction_session']
    for name in sources:
        base = ORDER if name in ['remote_transactions','transaction_session'] else OLD
        if name=='transaction_probe': base=OLD/'updated-entries'
        cmd = read(base/('build-'+name)/'probe.json')['command']
        cmd[-1]=str(ROOT/'backend/rdma'/(name+'.cc'))
        for flag, suffix in [('-o','.cc.o'),('-MT','.cc.o'),('-MF','.cc.o.d')]:
            cmd[cmd.index(flag)+1]=str(OUT/'objects'/(name+suffix))
        run('build-'+name, cmd)
    for target in ['stream-probe','transaction-probe','transaction-session']:
        base = OLD if target=='stream-probe' else ORDER
        cmd=read(base/('link-'+target)/'probe.json')['command']
        cmd[cmd.index('-o')+1]=str(OUT/'bin'/('ns3.42-moe-rdma-'+target))
        for i, value in enumerate(cmd):
            if Path(value).name in [n+'.cc.o' for n in sources]:
                cmd[i]=str(OUT/'objects'/Path(value).name)
        run('link-'+target, cmd)
    assert all(sha(Path(p))==h for p,h in protected.items())
    (OUT/'identity.json').write_text(json.dumps(dict(protected=protected,
        binaries={str(p):sha(p) for p in (OUT/'bin').iterdir()},
        sources={str(p.relative_to(ROOT)):sha(p) for p in (ROOT/'backend/rdma').glob('*')
                 if p.suffix in ['.hh','.cc']},
        monitor_seconds=sum(r['wall_seconds'] for r in records)),indent=2)+'\n')
    print('Five stream-layout consumers and three entries rebuilt; old runtimes unchanged')

if __name__=='__main__': main()
