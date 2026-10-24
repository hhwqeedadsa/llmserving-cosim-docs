"""Public-CLI/native acceptance of persistent loss recovery, independent assertions."""
import argparse,hashlib,json,os,sys,time
from pathlib import Path
from copy import deepcopy
from moe_ub_cosim.host_probe import probe
ROOT=Path(__file__).resolve().parents[2]
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def comparable(v):
 v=deepcopy(v);v.pop('loaded_libraries',None);return v

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--reuse-completed',action='store_true');a=p.parse_args()
 runtime=a.runtime.resolve();out=a.output.resolve();
 if a.reuse_completed:assert out.is_dir()
 else:out.mkdir(exist_ok=False)
 receipts=read(out/'commands.json') if a.reuse_completed else [];start=time.monotonic()
 def run(name,cmd,code=0):
  dest=out/(name+'-monitor')
  if dest.exists():
   assert a.reuse_completed
   m=read(dest/'probe.json');assert m['command']==list(map(str,cmd)) and m['child_exit_code']==code and not m['remaining_live_group_pids']
   assert m['status'] in ('completed','child_failed')
   return
  m=probe(list(map(str,cmd)),dest,rss_bytes=2**30,address_bytes=2**31,artifact_bytes=64*2**20,wall_seconds=60,artifact_roots=[out/name],min_free_bytes=8*2**30)
  receipts.append(m);save(out/'commands.json',receipts)
  assert m['child_exit_code']==code and not m['remaining_live_group_pids'],(name,m['status'],m['child_exit_code'])
  assert m['status'] in ('completed','child_failed'),m['status']
  assert sum(x['wall_seconds'] for x in receipts)+read(runtime/'build.json')['monitor_wall_seconds']<600
  assert sum(x.stat().st_size for x in runtime.rglob('*') if x.is_file())<256*2**20
 def cli(name,args,code=0):run(name,[sys.executable,'-B','-m','moe_ub_cosim','scaleout',*args],code)
 cli('topology',['topology','--config',ROOT/'examples/scaleout_rdma/small-clos-100g.json','--output',out/'topology'])
 topology=out/'topology';protocol=read(ROOT/'examples/scaleout_rdma/rdma.json')
 protocol['packet_capture']=dict(start_ns=0,end_ns=5000000,max_events=30000,nodes=[0,4],wire_headers=True)
 recovery=dict(mode='go_back_n/1',initial_rto_ns=80000,max_rto_ns=640000,max_retries=3,max_events=10000)
 base=read(ROOT/'examples/scaleout_rdma_streams/work.json')
 base['streams']=base['streams'][:1];base['work']=[dict(id=0,stream=0,bytes=2144,after=[]),dict(id=1,stream=0,bytes=17,after=[dict(work=0,event='acked')]),dict(id=2,stream=0,bytes=1001,after=[dict(work=1,event='accepted')])]
 cases=read(out/'cases.json') if a.reuse_completed else {}
 def execute(name,work,config,kind='stream',old=False,code=0,native=False):
  for suffix,data in (('-work.json',work),('-protocol.json',config)):
   path=out/(name+suffix)
   if path.exists():assert a.reuse_completed and read(path)==data
   else:save(path,data)
  if name in cases:assert sha(Path(cases[name]['path']))==cases[name]['sha256']
  binary=Path('/dev/shm/moe-wire-headers-20261023/bin/ns3.42-moe-rdma-stream-probe') if old else runtime/'bin'/('ns3.42-moe-rdma-'+('stream-probe' if kind=='stream' else 'transaction-probe'))
  if native:
   run(name,[binary,topology/'topology.json',out/(name+'-protocol.json'),out/(name+'-work.json'),out/(name+'-result.json')],code)
   path=out/(name+'-result.json')
  else:
   cli(name,['rdma-stream-probe' if kind=='stream' else 'rdma-transaction-probe','--topology',topology,'--protocol',out/(name+'-protocol.json'),'--config',out/(name+'-work.json'),'--backend',binary,'--output',out/name],code)
   path=out/name/'result.json'
  if path.exists():
   r=read(path);cases[name]=dict(path=str(path),sha256=sha(path),passed=r['passed']);save(out/'cases.json',cases);return r
 def fault(stream,kind,seq,count=1):return dict(node=4 if (stream==0)==(kind=='data') else 0,port=0,message_id=stream,kind=kind,sequence=seq,count=count,start_ns=0,end_ns=5000000)
 old=execute('old-default',base,protocol,old=True);new=execute('new-default',base,protocol)
 assert comparable(old)==comparable(new)
 work=deepcopy(base);work['transport_recovery']=recovery
 clean=execute('clean',work,protocol)
 for key in ('messages','ports','queue_events','packet_capture'):
  if key in old: # Opt-in stream identity is a declared passive association.
   if key!='packet_capture':assert clean[key]==old[key],key
 for name,kind,seq in [('middle','data',1000),('tail','data',2000),('final-ack','ack',2144)]:
  config=deepcopy(protocol);config['faults']=[fault(0,kind,seq)]
  r=execute(name,work,config);assert r['fault_injection']['drop_counts']==[1]
  t=r['persistent_transport'];assert t['physical_drained'] and t['closed'] and t['inflight_packets']==0
  assert len(t['work'])==3 and all(w['acked_ns']>=w['accepted_ns']>=w['published_ns'] for w in t['work'])
  assert t['streams'][0]['qp_creations']==1 and t['streams'][0]['snd_una']==3162
  if kind=='ack':assert t['recovery']['duplicate_ack_responses']>0
 # Multiple already-published work boundaries and full duplicate feedback.
 multi=deepcopy(work);multi['streams'][0]['queue_depth']=4
 for w in multi['work']:w['after']=[]
 config=deepcopy(protocol);config['faults']=[fault(0,'data',1000)]
 execute('cross-work',multi,config)
 short=deepcopy(work);short['transport_recovery']=dict(recovery,initial_rto_ns=1000)
 r=execute('short-rto',short,protocol);assert sum(s['timeout_retransmissions'] for s in r['persistent_transport']['recovery']['states'])>0
 adaptive=deepcopy(work);adaptive['transport_recovery']['rtt_estimator']=dict(kind='karn',min_rto_ns=20000,granularity_ns=1)
 r=execute('adaptive-idle',adaptive,protocol);assert r['persistent_transport']['recovery']['states'][0]['rtt']['samples']>=2
 # Declared local time guarantees a real idle gap before the same QP appends.
 idle=dict(schema_version='moe-rdma-stream-work/3',limits=dict(max_streams=1,max_work=3),local_model=dict(mode='serial_per_gpu/1',duration_source='declared_ns'),streams=base['streams'],transport_recovery=recovery,
  work=[dict(base['work'][0],kind='write'),dict(id=8,kind='local',rank=0,duration_ns=200000,after=[dict(work=0,event='acked')]),dict(id=9,kind='write',stream=0,bytes=17,after=[dict(work=8,event='completed')])])
 r=execute('idle-append',idle,protocol);t=r['persistent_transport'];assert t['work'][1]['published_ns']-t['work'][0]['acked_ns']==200000
 # Exhaustion is a terminal real failure with a saved incomplete result.
 exhausted=deepcopy(work);exhausted['transport_recovery']=dict(recovery,initial_rto_ns=10000,max_rto_ns=40000,max_retries=2)
 config=deepcopy(protocol);config['faults']=[fault(0,'data',0,count=10)]
 r=execute('exhausted',exhausted,config,code=1,native=True)
 assert not r['passed'] and not r['persistent_transport']['closed'] and r['persistent_transport']['recovery']['failed']
 # Transactions: actual packet bytes, target atomic and reply retransmissions.
 tx=read(ROOT/'examples/scaleout_rdma_transactions/work.json');tx['transport_recovery']=recovery
 normal=execute('transaction-clean',tx,protocol,kind='transaction')
 # Choose exact emitted request/reply packet boundaries from a successful run.
 transport=normal['transactions']['transport'];packets=transport['source_packets']
 for name,stream,kind in [('transaction-request-ack',0,'ack'),('transaction-response-data',1,'data'),('transaction-response-ack',1,'ack')]:
  rows=[x for x in packets if x[1]==stream];assert rows
  seq=rows[0][5] if kind=='data' else rows[0][5]+rows[0][6]
  config=deepcopy(protocol);config['faults']=[fault(stream,kind,seq)]
  r=execute(name,tx,config,kind='transaction');assert r['fault_injection']['drop_counts']==[1]
  assert r['transactions']['windows']==normal['transactions']['windows']
  events=r['transactions']['target_events'];assert len(events)==len(tx['operations'])
  assert len({(e['connection'],e['sequence']) for e in events})==len(events)
  assert not any(e['duplicate'] for e in events)
 save(out/'verification.json',dict(passed=True,cases=cases,commands=len(receipts),default_equal=True,wall_seconds=time.monotonic()-start))
 print(json.dumps(dict(passed=True,cases=len(cases),commands=len(receipts))))
if __name__=='__main__':main()
