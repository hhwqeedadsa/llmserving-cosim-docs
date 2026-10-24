"""Independent packet, prefix timer and memory effect checks; stdlib only."""
import argparse,json,struct
from copy import deepcopy
from pathlib import Path

def audit(r,initial):
 tx=r.get('transactions');t=tx['transport'] if tx else r['persistent_transport'];rec=t['recovery'];assert rec['complete']
 packets=t['source_packets'];specs={s['id']:s for s in t['streams']};works=t['work'];checks=0
 cap=r.get('packet_capture',{});assert cap.get('complete',True)
 rows=cap.get('events',[]);headers=cap.get('wire_headers',{});indices=headers.get('event_header_indices',[])
 assert len(indices)==len(rows)
 for e,i in zip(rows,indices):
  h=headers['headers'][i];assert h[0]==e[4] and h[3]==e[6]
  if e[6] in (17,252,253) and e[5]>=0:assert h[4]==h[5]==specs[e[5]]['wire_port']
 for sid,s in specs.items():
  data=[p for p in packets if p[1]==sid];pub=sorted((w for w in works if w['stream']==sid and w['published_ns']>=0),key=lambda w:w['ticket']);prefix=0
  for w in pub:
   assert w['begin']==prefix and w['end']==prefix+w['bytes'];prefix=w['end']
   if w['acked_ns']>=0:assert w['acked_ns']>=w['accepted_ns']>=w['published_ns']
  covered=0
  for stamp,_,src,dst,nic,seq,n,uid in data:
   assert seq<=covered and (src,dst,nic)==(s['src'],s['dst'],s['source_nic']);covered=max(covered,seq+n)
   owner=[w for w in pub if w['begin']<=seq and seq+n<=w['end']];assert len(owner)==1 and owner[0]['published_ns']<=stamp
   if src in cap['selection']['nodes']:
    witness=[e for e in rows if e[0]==stamp and e[1]==src and e[3]==0 and e[4]==uid and e[5]==sid and e[6]==17 and e[7]==seq and e[11]==n];assert len(witness)==1;checks+=1
  assert s['source_payload_bytes']==sum(p[6] for p in data) and s['source_data_packets']==len(data)
  assert s['snd_una']<=covered<=s['published_bytes']
  if r['passed']:assert covered==prefix==s['snd_una'] and s['ack_completed_work']==s['accepted_work']==len(pub)
  prev=dict(ack=0,high=0,retries=0,rto=rec['config']['initial_rto_ns'],deadline=-1);published=0;serial=0
  for stamp,code,src,stream,ack,high,retries,rto,deadline in rec['events']:
   if stream!=sid:continue
   assert src==s['src'] and ack<=high
   if code!=5:assert high<=published
   strict=max((p[5]+p[6] for p in data if p[0]<stamp),default=0);through=max((p[5]+p[6] for p in data if p[0]<=stamp),default=0)
   assert strict<=high<=through
   if code==5:
    assert serial<len(pub) and pub[serial]['published_ns']==stamp;published=pub[serial]['end'];serial+=1
    assert (ack,retries,rto,deadline)==(prev['ack'],prev['retries'],prev['rto'],prev['deadline'])
   elif code==0:assert prev['deadline']<0 and deadline==stamp+rto and high>ack
   elif code in (1,3):
    assert ack>prev['ack'] and retries==0 and deadline==(stamp+rto if high>ack else -1)
    if code==3:assert ack==published
    if src in cap['selection']['nodes']:assert any(e[0]==stamp and e[1]==src and e[3]==1 and e[5]==sid and e[6] in (252,253) and e[7]==ack for e in rows)
   elif code in (2,4):
    assert prev['deadline']==stamp and high>ack
    if code==2:assert retries==prev['retries']+1 and rto==min(rec['config']['max_rto_ns'],prev['rto']*2) and deadline==stamp+rto
    else:assert retries==rec['config']['max_retries'] and deadline==-1
   elif code==6:assert (ack,retries,rto,deadline)==(prev['ack'],prev['retries'],prev['rto'],prev['deadline'])
   else:raise AssertionError('unknown recovery event')
   prev=dict(ack=ack,high=high,retries=retries,rto=rto,deadline=deadline)
  assert serial==len(pub)
 if tx:
  # Reconstruct each stream from actually emitted bytes, requiring every retry
  # overlap to carry identical bytes; decode MRT1 independently of the backend.
  raw={};frames={}
  for p in t['source_payload_packets']:
   b=bytes.fromhex(p['hex']);target=raw.setdefault(p['stream'],{})
   for offset,value in enumerate(b,p['sequence']):
    assert offset not in target or target[offset]==value;target[offset]=value
  for sid,data in raw.items():
   b=bytes(data[i] for i in range(len(data)));at=0;decoded=[]
   while at<len(b):
    magic,verb,seq,address,key,n=struct.unpack('!IIQQII',b[at:at+32]);assert magic==0x4d525431 and verb in (1,2,3) and at+32+n<=len(b)
    decoded.append((verb,seq,address,key,b[at+32:at+32+n]));at+=32+n
   frames[sid]=decoded
  if initial['schema_version']=='moe-rdma-transactions/1':
   windows={(w['owner'],w['key']):(w['address'],bytearray.fromhex(w['hex'])) for w in initial['windows']}
   connections={c['id']:c for c in initial['connections']};seen=set();old_values={}
   for e in tx['target_events']:
    pair=e['connection'],e['sequence'];assert pair not in seen and not e['duplicate'];seen.add(pair)
    decoded=[f for f in frames[pair[0]*2] if f[1]==pair[1]];assert len(decoded)==1
    verb,seq,address,key,body=decoded[0];assert (verb,address,key,body.hex())==(e['verb'],e['address'],e['key'],e['body_hex'])
    base,memory=windows[connections[pair[0]]['dst'],key];off=address-base
    if verb==1:memory[off:off+len(body)]=body
    else:
     value=int.from_bytes(memory[off:off+8],'little');assert value==e['old_value'];old_values[pair]=value
     memory[off:off+8]=((value+int.from_bytes(body,'big'))%(2**64)).to_bytes(8,'little')
   for w in tx['windows']:assert windows[w['owner'],w['key']][1].hex()==w['hex']
   for cid,s in connections.items():
    replies=frames.get(cid*2+1,[])
    for verb,seq,address,key,body in replies:assert verb==3 and int.from_bytes(body,'big')==old_values[cid,seq]
 return dict(captured_source_tx_checked=checks,source_packets=len(packets),streams=len(specs),timer_events=len(rec['events']),wire_payload_decoded=bool(tx))

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();root=a.input
 outputs={};originals={}
 for path in sorted((root/'accept').glob('*/result.json')):
  r=json.loads(path.read_text());config=root/'accept'/(path.parent.name+'-work.json')
  if 'recovery' in r.get('persistent_transport',r.get('transactions',{}).get('transport',{})):
   w=json.loads(config.read_text());outputs[path.parent.name]=audit(r,w);originals[path.parent.name]=(r,w)
 for name in ['reference-loss','source-copy','sparse-prefix']:
  r=json.loads((root/'edges-congested'/name/'result.json').read_text());w=json.loads((root/'edges-congested'/(name+'-work.json')).read_text());outputs[name]=audit(r,w)
 r=json.loads((root/'accept/exhausted-result.json').read_text());audit(r,json.loads((root/'accept/exhausted-work.json').read_text()))
 rejected=[]
 for kind in ('premature-timeout','fabricated-ACK','changed-atomic-effect'):
  r,w=deepcopy(originals['short-rto' if kind=='premature-timeout' else 'transaction-request-ack'])
  t=r.get('persistent_transport',r.get('transactions',{}).get('transport'))
  if kind=='premature-timeout':next(e for e in t['recovery']['events'] if e[1]==2)[0]-=1
  elif kind=='fabricated-ACK':t['streams'][0]['snd_una']+=1
  else:r['transactions']['target_events'][0]['old_value']+=1
  try:audit(r,w)
  except AssertionError:rejected.append(kind)
 assert len(rejected)==3
 report=dict(passed=True,cases=outputs,mutations_rejected=rejected,captured_source_tx_checked=sum(c['captured_source_tx_checked'] for c in outputs.values()),source_packets=sum(c['source_packets'] for c in outputs.values()),timer_events=sum(c['timer_events'] for c in outputs.values()),scope='bounded endpoints/prefix timers/MRT1 effects; not hardware, production fault rates or new algorithm')
 a.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='cases'}))
if __name__=='__main__':main()
