"""A lost request ACK must retain the real registered source lease on failure."""
import argparse,json,sys
from pathlib import Path
from moe_ub_cosim.host_probe import probe
ROOT=Path(__file__).resolve().parents[2]
def read(p):return json.loads(p.read_text())
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',type=Path,required=True);a=p.parse_args();r=a.runtime.resolve();out=r/'lease-failure';out.mkdir()
 w=read(ROOT/'examples/scaleout_rdma_transactions/local-io.json');w['operations']=[next(o for o in w['operations'] if o['id']==0)];w['connections']=w['connections'][:1]
 w['transport_recovery']=dict(mode='go_back_n/1',initial_rto_ns=10000,max_rto_ns=40000,max_retries=2,max_events=1000)
 c=read(r/'accept/clean-protocol.json');c['faults']=[dict(node=0,port=0,message_id=0,kind='ack',sequence=289,count=10,start_ns=0,end_ns=5000000)]
 for name,data in [('work.json',w),('protocol.json',c)]:(out/name).write_text(json.dumps(data,indent=2)+'\n')
 cmd=[str(r/'updated-entries/bin/ns3.42-moe-rdma-transaction-probe'),str(r/'accept/topology/topology.json'),str(out/'protocol.json'),str(out/'work.json'),str(out/'result.json')]
 m=probe(cmd,out/'monitor',rss_bytes=2**30,address_bytes=2**31,artifact_bytes=64*2**20,wall_seconds=60,artifact_roots=[out],min_free_bytes=8*2**30);assert m['status']=='child_failed' and m['child_exit_code']==1
 result=read(out/'result.json');tx=result['transactions'];op=tx['operations'][0]
 assert not result['passed'] and tx['transport']['recovery']['failed']
 assert op['applied_ns']>=0 and op['request_acked_ns']==op['completed_ns']==-1
 assert len(tx['active_source_leases'])==1 and len(tx['target_events'])==1 and not tx['target_events'][0]['duplicate']
 assert tx['connections'][0]['completed']==0 and not tx['connections'][0]['closed']
 report=dict(passed=True,applied_ns=op['applied_ns'],request_acked_ns=op['request_acked_ns'],completed_ns=op['completed_ns'],active_source_leases=tx['active_source_leases'],target_effect_count=1,source_timeout_retransmissions=tx['transport']['recovery']['states'][0]['timeout_retransmissions'],failure=tx['transport']['recovery']['failure'],actual_fault_drops=result['fault_injection']['drop_counts'])
 (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main()
