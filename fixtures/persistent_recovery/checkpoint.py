"""Bounded offline evidence plus actual public CLI replay in a new source root."""
import argparse,hashlib,json,os,shutil,sys,tarfile
from pathlib import Path
from moe_ub_cosim.host_probe import probe
ROOT=Path(__file__).resolve().parents[2]
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def normalized(r):r=dict(r);r.pop('loaded_libraries',None);return r

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();root=a.input.resolve();out=a.output.resolve();out.mkdir();mapping={}
 def add(p,name):assert name not in mapping;mapping[name]=p
 for directory in ('backend','python','examples'):
  for p in (ROOT/directory).rglob('*'):
   if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts:add(p,'source/'+str(p.relative_to(ROOT)))
 for p in (ROOT/'fixtures/persistent_recovery').glob('*.py'):add(p,'source/'+str(p.relative_to(ROOT)))
 for p in (ROOT/'tests/unit').glob('persistent_recovery*'):add(p,'source/'+str(p.relative_to(ROOT)))
 for group in ('accept','edges','edges-valid','edges-congested','fresh-user'):
  for p in (root/group).rglob('*'):
   if p.is_file() and p.suffix in ('.json','.jsonl','.log') and p.name!='samples.jsonl':add(p,'input/'+str(p.relative_to(root)))
 add(root/'independent-audit.json','expected/audit.json')
 for p in (root/'bin').glob('*'):
  if p.name.endswith('stream-probe'):add(p,'runtime/bin/'+p.name)
 for p in (root/'updated-entries/bin').iterdir():add(p,'runtime/bin/'+p.name)
 build=read(root/'build.json')
 for path in build['libraries']:add(Path(path),'runtime/lib/'+Path(path).name)
 for name in ('build.json','unit-records.json','regressions.json'):add(root/name,'input/'+name)
 members={name:dict(sha256=sha(p),bytes=p.stat().st_size) for name,p in mapping.items()}
 size=sum(x['bytes'] for x in members.values());assert size<128*2**20
 archive=out/'checkpoint.tar.gz'
 with tarfile.open(archive,'w:gz') as tar:
  for name,p in sorted(mapping.items()):
   info=tarfile.TarInfo(name);info.size=p.stat().st_size;info.mode=0o755 if name.startswith('runtime/bin/') else 0o644
   with p.open('rb') as f:tar.addfile(info,f)
 assert archive.stat().st_size<40*2**20
 save(out/'members.json',members)
 restored=out/'restored';restored.mkdir()
 with tarfile.open(archive) as tar:
  assert all(m.isfile() and not Path(m.name).is_absolute() and '..' not in Path(m.name).parts for m in tar.getmembers())
  tar.extractall(restored,filter='data')
 assert all(sha(restored/n)==v['sha256'] for n,v in members.items())
 empty=out/'empty-cwd';empty.mkdir();os.chdir(empty)
 os.environ['PYTHONPATH']=str(restored/'source/python');os.environ['PYTHONDONTWRITEBYTECODE']='1';os.environ['LD_LIBRARY_PATH']=str(restored/'runtime/lib')
 records=[]
 def run(name,command):
  m=probe(list(map(str,command)),out/(name+'-monitor'),rss_bytes=2**30,address_bytes=2**31,artifact_bytes=64*2**20,wall_seconds=60,artifact_roots=[out/name],min_free_bytes=8*2**30);records.append(m);save(out/'commands.json',records);assert m['status']=='completed',name
 cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout'];source=restored/'source';input=restored/'input';bin=restored/'runtime/bin'
 run('offline',[sys.executable,'-B',source/'fixtures/persistent_recovery/audit.py','--input',input,'--output',out/'audit.json'])
 assert sha(out/'audit.json')==sha(restored/'expected/audit.json')
 run('topology',cli+['topology','--config',source/'examples/scaleout_rdma/small-clos-100g.json','--output',out/'topology'])
 run('stream',cli+['rdma-stream-probe','--topology',out/'topology','--protocol',source/'examples/scaleout_rdma_streams/rdma-loss.json','--config',source/'examples/scaleout_rdma_streams/work-recovery.json','--backend',bin/'ns3.42-moe-rdma-stream-probe','--output',out/'stream'])
 run('transaction',cli+['rdma-transaction-probe','--topology',out/'topology','--protocol',input/'edges-congested/source-copy-protocol.json','--config',input/'edges-congested/source-copy-work.json','--backend',bin/'ns3.42-moe-rdma-transaction-probe','--output',out/'transaction'])
 run('session',[sys.executable,'-B',source/'examples/scaleout_rdma_transactions/session_driver.py','--topology',out/'topology','--protocol',input/'edges-congested/session-protocol.json','--config',input/'edges-congested/session-program.json','--backend',bin/'ns3.42-moe-rdma-transaction-session','--output',out/'session'])
 equal={}
 for name,reference in [('stream',input/'fresh-user/recovery/result.json'),('transaction',input/'edges-congested/source-copy/result.json'),('session',input/'edges-congested/online-loss/result.json')]:
  result=read(out/name/'result.json');equal[name]=normalized(result)==normalized(read(reference));assert equal[name],name
  native=[Path(p) for p in result['loaded_libraries'] if Path(p).name.startswith('libns3')];assert len(native)==8 and all(p.parent==restored/'runtime/lib' for p in native)
 total=sum(p.stat().st_size for p in root.rglob('*') if p.is_file());assert total<256*2**20
 report=dict(passed=True,archive_members=len(members),uncompressed_bytes=size,archive_bytes=archive.stat().st_size,archive_sha256=sha(archive),offline_audit_byte_equal=True,restored_network_results_equal_except_library_paths=equal,public_commands=4,actual_restored_networks=3,all_eight_libraries_loaded_from_archive=True,new_source_root=str(source),cwd=str(empty),task_tree_bytes=total,monitor_wall_seconds=sum(m['wall_seconds'] for m in records),scope='functional native replay; requires host Python/loader/system C++ libraries; not a complete offline dependency build')
 save(out/'recovery.json',report);print(json.dumps(report))
if __name__=='__main__':main()
