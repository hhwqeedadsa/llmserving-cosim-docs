"""Freeze delayed inputs and four saved controls before new networks exist."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess


def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def identity(c,base):
 p=base/c['route_replay']['manifest']['path'];assert sha(p)==c['route_replay']['manifest']['sha256']
 m=load(p)
 for f in m['files']:
  assert sha(base/f['file']['path'])==f['file']['sha256'];f['file'].pop('path')
 return m

def main():
 p=argparse.ArgumentParser(description=__doc__)
 for n in ('prepared','single-normal','single-local','eager-normal','eager-local','backend','output'):
  p.add_argument('--'+n,type=Path,required=True)
 a=p.parse_args();prepared=a.prepared.resolve();c=load(prepared/'service-delayed.json')
 assert c['whole_call_replication']['hedge_after_ns']==70000
 assert c['whole_call_replication']['policy']=='delayed_complete_segment/1'
 original=identity(c,prepared);normalized=deepcopy(c);normalized.pop('whole_call_replication');normalized['route_replay'].pop('manifest')
 references={}
 for name in ('single_normal','single_local','eager_normal','eager_local'):
  path=getattr(a,name).resolve();old=load(path/'input.json');assert identity(old,path)==original
  old.pop('whole_call_replication',None);old['route_replay'].pop('manifest');old['route_replay']['request_layout']='sparse_explicit/1'
  assert old==normalized and load(path/'topology.json')==load(prepared/'topology/topology.json')
  assert load(path/'rdma.json')==load(prepared/('rdma.json' if name.endswith('normal') else 'rdma-whole-local.json'))
  assert load(path/'verification.json')['passed']
  references[name]=dict(directory=str(path),sha256={n:sha(path/n) for n in
   ('input.json','topology.json','rdma.json','frontend.json','result.json','verification.json')})
 assert not (a.output.parent/'normal').exists() and not (a.output.parent/'local').exists()
 backend=a.backend.resolve()
 frozen=dict(passed=True,network_executed=False,platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
  hedge_after_ns=70000,raw_source_identity=original,references=references,backend=dict(path=str(backend),sha256=sha(backend)),
  prepared=str(prepared),prepared_sha256={n:sha(prepared/n) for n in
  ('service-delayed.json','rdma.json','rdma-whole-local.json','topology/topology.json')},
  method_sha256=sha(Path(__file__).with_name('protocol.md')),preparer_sha256=sha(Path(__file__)))
 with a.output.open('x') as stream:json.dump(frozen,stream,indent=2);stream.write('\n')
 print(json.dumps(dict(passed=True,network_executed=False,hedge_after_ns=70000)))

if __name__=='__main__':main()
