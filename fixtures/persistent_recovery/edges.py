"""Public recovery composition, online frontend and native rejection boundaries."""
import argparse,json,sys,time
from pathlib import Path
from copy import deepcopy
from moe_ub_cosim.host_probe import probe
ROOT=Path(__file__).resolve().parents[2]
def read(p):return json.loads(p.read_text())
def save(p,v):
 if p.exists() and (p.name.endswith(('-work.json','-protocol.json')) or p.name=='session-program.json'):
  assert read(p)==v,'cannot reuse a different input';return
 p.write_text(json.dumps(v,indent=2)+'\n')
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--reuse-completed',action='store_true');a=p.parse_args();root=a.runtime.resolve();out=a.output.resolve();
 if not a.reuse_completed:out.mkdir()
 records=read(out/'commands.json') if a.reuse_completed else []
 base=root/'accept';topology=base/'topology';binaries=root/'updated-entries/bin';recovery=read(base/'clean-work.json')['transport_recovery'];protocol=read(base/'clean-protocol.json');config=read(base/'clean-work.json')
 def run(n,c,expected=0):
  dest=out/(n+'-monitor')
  if dest.exists():
   assert a.reuse_completed
   m=read(dest/'probe.json');assert m['command']==list(map(str,c)) and m['child_exit_code']==expected and m['status'] in ('completed','child_failed') and not m['remaining_live_group_pids'];return
  m=probe(list(map(str,c)),dest,rss_bytes=2**30,address_bytes=2**31,artifact_bytes=64*2**20,wall_seconds=60,artifact_roots=[out/n],min_free_bytes=8*2**30);records.append(m);save(out/'commands.json',records);assert m['child_exit_code']==expected and m['status'] in ('completed','child_failed') and not m['remaining_live_group_pids'],(n,m['status'])
 def cli(n,c,kind='stream',expected=0):
  save(out/(n+'-work.json'),c)
  binary=root/'bin/ns3.42-moe-rdma-stream-probe' if kind=='stream' else binaries/'ns3.42-moe-rdma-transaction-probe'
  run(n,[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-stream-probe' if kind=='stream' else 'rdma-transaction-probe','--topology',topology,'--protocol',out/(n+'-protocol.json'),'--config',out/(n+'-work.json'),'--backend',binary,'--output',out/n],expected)
  return read(out/n/'result.json') if (out/n/'result.json').exists() else None
 reference=deepcopy(config);reference['dcqcn_reference']=read(ROOT/'examples/scaleout_rdma_streams/work-reference.json')['dcqcn_reference']
 reference['streams'].append(read(ROOT/'examples/scaleout_rdma_streams/work.json')['streams'][1]);reference['work'].append(dict(id=40,stream=1,bytes=16000,after=[]))
 pc=deepcopy(protocol);pc['ecn_by_rate']['100000000000'].update(kmin_bytes=1,kmax_bytes=2);pc['faults']=[dict(node=0,port=0,message_id=0,kind='ack',sequence=2144,count=1,start_ns=0,end_ns=5000000)]
 save(out/'reference-loss-protocol.json',pc);r=cli('reference-loss',reference)
 assert r['fault_injection']['drop_counts']==[1]
 assert r['persistent_transport']['recovery']['duplicate_ack_responses']>=1
 events=r['persistent_transport']['dcqcn_reference']['events'];assert any(e['kind']=='cnp_received' for e in events)
 for e in events:
  if e['kind']=='qp_close':assert not e['np_pending']
 # Online controller constructs its put only from the actual returned atomic value.
 program=read(ROOT/'examples/scaleout_rdma_transactions/session-program.json');program['transport_recovery']=recovery
 save(out/'session-program.json',program);pc=deepcopy(protocol);pc['faults']=[dict(node=0,port=0,message_id=1,kind='data',sequence=0,count=1,start_ns=0,end_ns=5000000)]
 save(out/'session-protocol.json',pc)
 run('online-loss',[sys.executable,'-B',ROOT/'examples/scaleout_rdma_transactions/session_driver.py','--topology',topology,'--protocol',out/'session-protocol.json','--config',out/'session-program.json','--backend',binaries/'ns3.42-moe-rdma-transaction-session','--output',out/'online-loss'])
 r=read(out/'online-loss/result.json');assert r['passed'] and r['fault_injection']['drop_counts']==[1]
 assert len(r['transactions']['target_events'])==2 and all(not x['duplicate'] for x in r['transactions']['target_events'])
 returned=r['transactions']['operations'][0]['returned_value'];effective=read(out/'online-loss/effective-program.json');assert int.from_bytes(bytes.fromhex(effective['operations'][1]['hex']),'little')==returned+100
 # v5 and source leases/local copy paths remain valid with explicit recovery.
 for name,example in [('source-copy','local-io.json'),('sparse-prefix','submission-prefix.json')]:
  c=read(ROOT/'examples/scaleout_rdma_transactions'/example);c['transport_recovery']=recovery
  save(out/(name+'-protocol.json'),protocol);r=cli(name,c,kind='transaction');assert r['transactions']['transport']['recovery']['complete']
 # True simulation deadline saves partial work, not a fabricated completion.
 cutoff=deepcopy(config);cutoff['transport_recovery']['initial_rto_ns']=400;cutoff['transport_recovery']['max_rto_ns']=640000
 pc=deepcopy(protocol);pc['schema_version']='moe-scaleout-rdma/2';pc['packet_capture']['end_ns']=500;pc['limits']=dict(max_gpus=32,max_switches=16,max_links=256,max_messages=8192,max_sim_ns=500,max_queue_events=1000,queue_capture='events',wall_seconds=60,rss_bytes=1073741824,address_bytes=2147483648,artifact_bytes=67108864,result_json_bytes=16777216)
 save(out/'cutoff-work.json',cutoff);save(out/'cutoff-protocol.json',pc)
 run('cutoff',[root/'bin/ns3.42-moe-rdma-stream-probe',topology/'topology.json',out/'cutoff-protocol.json',out/'cutoff-work.json',out/'cutoff-result.json'],1)
 r=read(out/'cutoff-result.json');assert not r['passed'] and not r['persistent_transport']['closed']
 invalid=[('bool-rto',dict(recovery,initial_rto_ns=True)),('zero-rto',dict(recovery,initial_rto_ns=0)),('unknown-field',dict(recovery,foo=1)),('unknown-mode',dict(recovery,mode='go_back_n')),('bad-karn',dict(recovery,rtt_estimator=dict(kind='karn',min_rto_ns=80001,granularity_ns=1))),('null',None)]
 for name,value in invalid:
  c=deepcopy(config);c['transport_recovery']=value
  save(out/(name+'-work.json'),c);save(out/(name+'-protocol.json'),protocol)
  # Both entry surfaces receive identical inputs; native is not hidden by Python validation.
  run(name+'-cli',[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-stream-probe','--topology',topology,'--protocol',out/(name+'-protocol.json'),'--config',out/(name+'-work.json'),'--backend',root/'bin/ns3.42-moe-rdma-stream-probe','--output',out/(name+'-cli')],1)
  run(name+'-native',[root/'bin/ns3.42-moe-rdma-stream-probe',topology/'topology.json',out/(name+'-protocol.json'),out/(name+'-work.json'),out/(name+'-native-result.json')],1)
 save(out/'verification.json',dict(passed=True,commands=len(records),native_and_cli_rejections=len(invalid),online_actual_return=returned,independent_cnp_received=True))
 print('edge and online recovery checks passed')
if __name__=='__main__':main()
