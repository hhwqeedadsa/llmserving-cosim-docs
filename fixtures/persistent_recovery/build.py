"""Isolated affected targets, pinned local compiler/link command receipts."""
import argparse,hashlib,json
from pathlib import Path
from moe_ub_cosim.host_probe import probe

def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 repo=Path(__file__).resolve().parents[2];out=a.output.resolve();out.mkdir(exist_ok=False);(out/'objects').mkdir();(out/'bin').mkdir()
 receipts=Path('/dev/shm/moe-wire-headers-20261023');prefix=Path('/dev/shm/moe-submission-prefix-20261023')
 prior={str(x):sha(x) for folder in (receipts/'bin',prefix/'bin') for x in folder.iterdir() if x.is_file()}
 libraries={str(x):sha(x) for x in Path('/dev/shm/moe-fault-20261021/native/output/lib').glob('libns3*.so')};assert len(libraries)==8;prior.update(libraries)
 records=[]
 def run(name,cmd):
  m=probe(list(map(str,cmd)),out/name,rss_bytes=2**30,address_bytes=2**31,artifact_bytes=64*2**20,wall_seconds=60,artifact_roots=[out/'objects',out/'bin'],min_free_bytes=8*2**30)
  records.append(m);(out/'build-records.json').write_text(json.dumps(records,indent=2)+'\n');assert m['status']=='completed',name
  assert sum(r['wall_seconds'] for r in records)<600
 for name in ('fabric','astra_adapter','recovery','persistent_streams','stream_probe','remote_transactions','transaction_probe','transaction_session'):
  receipt=(prefix/'build-transaction_session/probe.json') if name=='transaction_session' else receipts/('build-'+name)/'probe.json'
  cmd=read(receipt)['command'];cmd[-1]=str(repo/'backend/rdma'/(name+'.cc'))
  for flag,suffix in (('-o','.cc.o'),('-MT','.cc.o'),('-MF','.cc.o.d')):cmd[cmd.index(flag)+1]=str(out/'objects'/(name+suffix))
  run('build-'+name,cmd)
 run('archive',['/usr/bin/ar','rcs',out/'objects/libmoe-rdma-fabric.a',*[out/'objects'/(n+'.cc.o') for n in ('fabric','astra_adapter','recovery')]])
 for target in ('stream-probe','transaction-probe','transaction-session'):
  receipt=(prefix/'build-link-session/probe.json') if target.endswith('session') else receipts/('build-link-moe-rdma-'+target)/'probe.json'
  cmd=read(receipt)['command'];cmd[cmd.index('-o')+1]=str(out/'bin'/('ns3.42-moe-rdma-'+target))
  for i,v in enumerate(cmd):
   if v.endswith('.cc.o') or v.endswith('/libmoe-rdma-fabric.a'):cmd[i]=str(out/'objects'/Path(v).name)
  run('link-'+target,cmd)
 assert all(sha(Path(k))==v for k,v in prior.items())
 report=dict(passed=True,steps=len(records),old_runtime=prior,libraries=libraries,binaries={str(x):sha(x) for x in (out/'bin').iterdir()},source_hashes={str(x.relative_to(repo)):sha(x) for x in (repo/'backend').rglob('*') if x.is_file() and x.suffix in ('.cc','.hh')},monitor_wall_seconds=sum(r['wall_seconds'] for r in records))
 (out/'build.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ('source_hashes','old_runtime','libraries')}))
if __name__=='__main__':main()
