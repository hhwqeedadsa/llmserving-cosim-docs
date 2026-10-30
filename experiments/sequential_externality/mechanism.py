"""Trace actual tight EP joins to their last DATA packet and source ACK."""
import argparse,csv,hashlib,json
from pathlib import Path


def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--frozen',type=Path,required=True);p.add_argument('--analysis',type=Path,required=True)
 p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
 frozen=load(a.frozen);details={}
 for name in ('control-local','treatment-local'):
  path=Path(frozen['cases'][name]['output']);r=load(path/'result.json');c=load(path/'input.json')
  observed=[json.loads(s) for s in (path/'execution_events.jsonl').read_text().splitlines()]
  times={(e['rank'],e['node']):e['time_ns'] for e in observed if e.get('graph_id')=='g2.w1' and e['kind']=='node_complete'}
  with (a.analysis/(name+'-switch-residence.csv')).open() as stream:rows=list(csv.DictReader(stream))
  details[name]=dict(result=r,config=c,times=times,rows=rows,wave=load(path/'graphs/g2.w1/wave.json'),
   gates={g['id']:g for g in r['service']['phase_completion']['gates'] if g['graph_id']=='g2.w1'},
   messages={m['id']:m for m in r['service_transport']['messages']})
 c,t=(details[n] for n in ('control-local','treatment-local'));proof=[]
 for gid in ('L0.dispatch','L0.combine','L1.dispatch','L1.combine'):
  gate=t['gates'][gid];latest=max(v['arrived_ns'] for v in gate['members']);tight=[]
  for member in gate['members']:
   if member['arrived_ns']!=latest:continue
   nodes={n['id']:n for n in t['wave']['rank_nodes'][str(member['rank'])]};join=nodes[member['node']]
   for dep in join['deps']:
    node=nodes[dep]
    if t['times'][member['rank'],dep]!=latest:continue
    assert node['kind']=='SEND','tight predecessor is not a source ACK in this saved case'
    mid=node['message']['id'];packets={};residences={};messages={}
    for label,case in (('control',c),('treatment',t)):
     m=case['messages'][mid];messages[label]=m
     last=[v for v in case['result']['service_transport']['native']['source_packets'] if v[1]==m['stream'] and v[5]<=m['end']-1<v[5]+v[6]]
     assert len(last)==1,'healthy last DATA packet was retransmitted or missing';packet=last[0];packets[label]=packet
     rows=[v for v in case['rows'] if int(v['stream'])==packet[1] and int(v['sequence'])==packet[5] and int(v['attempt'])==0]
     assert rows;residences[label]=rows
    paths=[[(int(v['node']),int(v['port'])) for v in residences[label]] for label in ('control','treatment')]
    assert paths[0]==paths[1]
    cm,tm=(messages[k] for k in ('control','treatment'));cp,tp=(packets[k] for k in ('control','treatment'))
    assert (cp[1],cp[5],cp[6])==(tp[1],tp[5],tp[6])
    ready_delta=tm['ready_ns']-cm['ready_ns'];tx_delta=tp[0]-cp[0]
    residence_delta=sum(int(v['residence_ns']) for v in residences['treatment'])-sum(int(v['residence_ns']) for v in residences['control'])
    receive_delta=tm['receive_ready_ns']-cm['receive_ready_ns'];ack_delta=tm['ack_ns']-cm['ack_ns']
    assert ready_delta==tx_delta and tx_delta+residence_delta==receive_delta==ack_delta
    baseline_slack=c['gates'][gid]['released_ns']-cm['ack_ns'];gate_delta=gate['released_ns']-c['gates'][gid]['released_ns']
    assert tm['ack_ns']==gate['released_ns'] and ack_delta-baseline_slack==gate_delta
    hops=[dict(node=int(old['node']),port=int(old['port']),baseline_residence_ns=int(old['residence_ns']),
      treatment_residence_ns=int(new['residence_ns']),change_ns=int(new['residence_ns'])-int(old['residence_ns']))
      for old,new in zip(residences['control'],residences['treatment'])]
    tight.append(dict(rank=member['rank'],node=dep,message=mid,stream=cm['stream'],sequence=cp[5],last_payload_bytes=cp[6],
      ready_change_ns=ready_delta,last_source_tx_change_ns=tx_delta,last_packet_residence_change_ns=residence_delta,
      receive_change_ns=receive_delta,ack_change_ns=ack_delta,baseline_gate_slack_ns=baseline_slack,gate_change_ns=gate_delta,hops=hops))
  assert tight;proof.append(dict(gate=gid,tight_predecessors=tight))
 # Demonstrate actual backup transmissions ahead of the first tight healthy
 # packet on its first switch egress, without attributing every shifted byte to it.
 first=proof[0]['tight_predecessors'][0];node,port=first['hops'][0]['node'],first['hops'][0]['port']
 hop=next(v for v in t['rows'] if int(v['stream'])==first['stream'] and int(v['sequence'])==first['sequence'] and int(v['node'])==node)
 begin,end=int(hop['rx_ns']),int(hop['tx_ns']);streams={v['id']:v for v in t['result']['service_transport']['native']['streams']}
 owners={rank:i for i,g in enumerate(t['config']['parallel']['groups']) for rank in g}
 ahead=[dict(time_ns=v[0],node=node,port=port,stream=v[5],sequence=v[7],model_bytes=v[8]) for v in t['result']['packet_capture']['events']
  if v[1]==node and v[2]==port and v[3]==0 and v[6]==17 and begin<=v[0]<end and owners[streams[v[5]]['src']]==1]
 assert ahead
 result=dict(passed=True,source='actual local matched networks and tight native gate predecessors',proof=proof,
  backup_packets_transmitted_while_first_tight_healthy_packet_waited=ahead,
  new_network_executions=0,analyzer_sha256=sha(Path(__file__)),
  scope='All tight join predecessors enumerated for this healthy second-step path. Per-packet residence is not summed across unrelated packets as business savings; no new strategy or production calibration.')
 (a.output/'mechanism.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(dict(passed=True,gates=len(proof),direct_backup_packets=len(ahead),new_network_executions=0)))


if __name__=='__main__':main()
