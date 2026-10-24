"""Small, real-network acceptance; incomplete runs deliberately exit one."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from moe_ub_cosim.host_probe import probe

ROOT=Path(__file__).resolve().parents[2]
TASK=Path('/dev/shm/moe-source-isolation-20261024')
def read(p): return json.loads(p.read_text())
def save(p,v): p.write_text(json.dumps(v,indent=2)+'\n')
def sha(p):
    with p.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend-dir',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--only',choices=['all','late-submit'],default='all')
    a=p.parse_args();out=a.output.resolve();out.mkdir(exist_ok=False)
    records=[];cases={}
    def run(label,cmd,code=0):
        m=probe(list(map(str,cmd)),out/(label+'-monitor'),rss_bytes=2**30,
            address_bytes=2**31,artifact_bytes=64*2**20,wall_seconds=60,
            artifact_roots=[out/label],min_free_bytes=8*2**30)
        records.append(m);save(out/'commands.json',records)
        assert m['child_exit_code']==code and m['status'] in ['completed','child_failed'] and not m['remaining_live_group_pids'],(label,m)
        all_records=[read(q) for q in TASK.rglob('probe.json')]
        assert sum(r['wall_seconds'] for r in all_records)<450
        assert sum(f.stat().st_size for f in TASK.rglob('*') if f.is_file())<192*2**20
    def cli(label,args,code=0):
        run(label,[sys.executable,'-B','-m','moe_ub_cosim','scaleout',*args],code)
    cli('topology',['topology','--config',ROOT/'examples/scaleout_rdma/small-clos-100g.json','--output',out/'topology'])
    protocol=read(ROOT/'examples/scaleout_rdma/rdma.json')
    protocol['packet_capture']=dict(nodes=[0,1,4],start_ns=0,end_ns=5000000,max_events=30000,wire_headers=True)
    recovery=dict(mode='go_back_n/1',initial_rto_ns=10000,max_rto_ns=40000,
                  max_retries=2,max_events=10000,on_exhausted='isolate_stream')
    streams=read(ROOT/'examples/scaleout_rdma_streams/work.json')['streams']
    def stream_work():
        return dict(schema_version='moe-rdma-stream-work/3',limits=dict(max_streams=2,max_work=8),
            local_model=dict(mode='serial_per_gpu/1',duration_source='declared_ns'),
            transport_recovery=deepcopy(recovery),streams=deepcopy(streams),work=[
                dict(id=0,kind='write',stream=0,bytes=32,after=[]),
                dict(id=10,kind='local',rank=1,duration_ns=100000,after=[]),
                dict(id=1,kind='write',stream=1,bytes=32,after=[dict(work=10,event='completed')])])
    def fault(stream,kind,sequence,count=10):
        return dict(node=(4 if kind=='data' else (0 if stream==0 else 1)),port=0,
            message_id=stream,kind=kind,sequence=sequence,count=count,start_ns=0,end_ns=5000000)
    def execute(label,w,pc,kind='stream',code=1,binary=None):
        save(out/(label+'-work.json'),w);save(out/(label+'-protocol.json'),pc)
        target='stream-probe' if kind=='stream' else 'transaction-probe'
        binary=binary or a.backend_dir/('ns3.42-moe-rdma-'+target)
        cli(label,['rdma-'+target,'--topology',out/'topology','--protocol',out/(label+'-protocol.json'),
            '--config',out/(label+'-work.json'),'--backend',binary,'--output',out/label],code)
        result=out/label/'result.json'
        assert result.is_file(),label
        value=read(result);assert value['passed']==(code==0)
        cases[label]=dict(result=str(result),sha256=sha(result),kind=kind)
        save(out/'cases.json',cases)
        return value
    pc=deepcopy(protocol);pc['faults']=[fault(0,'data',0)]
    if a.only=='late-submit':
        w=stream_work()
        w['work'].append(dict(id=2,kind='write',stream=0,bytes=16,after=[dict(work=10,event='completed')]))
        r=execute('late-submit',w,pc)
        t=r['persistent_transport'];assert next(x for x in t['work'] if x['id']==2)['published_ns']==-1
        assert next(x for x in t['work'] if x['id']==1)['acked_ns']>=100000
        save(out/'verification.json',dict(passed=True,cases=cases))
        return
    # Complete default reports are a strict byte comparison against old entries.
    for kind,example,old in [
        ('stream','scaleout_rdma_streams/work.json','/dev/shm/moe-persistent-recovery-20261024/bin/ns3.42-moe-rdma-stream-probe'),
        ('transaction','scaleout_rdma_transactions/work.json','/dev/shm/moe-effect-order-20261024/build/bin/ns3.42-moe-rdma-transaction-probe')]:
        w=read(ROOT/'examples'/example)
        for suffix,b in [('old',Path(old)),('new',None)]:execute('default-'+kind+'-'+suffix,w,protocol,kind,0,b)
        assert (out/('default-'+kind+'-old')/'result.json').read_bytes()==(out/('default-'+kind+'-new')/'result.json').read_bytes()
    w=stream_work();r=execute('delayed-healthy',w,pc)
    t=r['persistent_transport'];assert t['recovery']['failure']['time_ns']==70000
    assert next(x for x in t['work'] if x['id']==1)['acked_ns']>100000
    pc2=deepcopy(pc);pc2['faults'].append(fault(1,'ack',32,1))
    r=execute('healthy-own-recovery',w,pc2)
    t=r['persistent_transport'];assert next(x for x in t['recovery']['states'] if x['stream']==1)['timeout_retransmissions']>=1
    assert next(x for x in t['work'] if x['id']==1)['acked_ns']>=100000
    pc2['faults'][1]=fault(1,'data',0)
    r=execute('two-failures',w,pc2)
    assert len(r['persistent_transport']['recovery']['failures'])==2
    assert r['persistent_transport']['recovery']['failure']['stream']==0
    # A lost request ACK keeps the registered source lease while healthy work retires.
    tx=read(ROOT/'examples/scaleout_rdma_transactions/local-io.json')
    tx['connections']=[tx['connections'][0],dict(tx['connections'][0],id=1,src=1,request_port=24002,response_port=24003)]
    tx['windows'].append(dict(owner=1,key=10,address=16384,allow_atomic=False,hex='ab'*64))
    first=next(o for o in tx['operations'] if o['id']==0)
    tx['operations']=[first,dict(id=10,kind='local',rank=1,duration_ns=100000,after=[]),
        dict(first,id=1,connection=1,address=8576,source=dict(key=10,address=16384,bytes=64),after=[dict(operation=10,event='completed')])]
    tx['transport_recovery']=deepcopy(recovery)
    pct=deepcopy(protocol);pct['faults']=[fault(0,'ack',289)]
    r=execute('transaction-leases',tx,pct,'transaction')
    v=r['transactions'];assert len(v['active_source_leases'])==1 and len(v['target_events'])==2
    assert v['operations'][0]['completed_ns']==-1 and v['operations'][1]['completed_ns']>=100000
    # Failure of a reply sender is local to the responder; the initiator must
    # still wait for actual reply bytes, not learn failure through global state.
    reply=deepcopy(tx)
    op=reply['operations'][0];op.pop('source');op['verb']='fetch_add';op['value']=1
    pr=deepcopy(protocol);pr['faults']=[dict(node=0,port=0,message_id=1,kind='data',
        sequence=0,count=10,start_ns=0,end_ns=5000000)]
    r=execute('response-source-failure',reply,pr,'transaction')
    v=r['transactions'];assert v['transport']['recovery']['failure']['stream']==1
    assert v['operations'][0]['request_acked_ns']>=0 and v['operations'][0]['completed_ns']==-1
    assert v['operations'][1]['completed_ns']>=100000
    # With a short timer, real in-flight bytes still arrive after source failure.
    tx2=deepcopy(tx);tx2['transport_recovery'].update(initial_rto_ns=1000,max_rto_ns=1000,max_retries=1)
    r=execute('late-ack-target-effect',tx2,protocol,'transaction')
    v=r['transactions'];t=v['transport'];failure=t['recovery']['failure']['time_ns']
    assert v['operations'][0]['applied_ns']>failure and v['operations'][0]['request_acked_ns']==-1
    assert t['source_isolation']['terminal_feedback_packets']>0
    # Independent congestion notifications remain actual network packets even
    # after a source is terminal. Use competing streams, not a zero threshold.
    ref=stream_work();ref['dcqcn_reference']=read(ROOT/'examples/scaleout_rdma_streams/work-reference.json')['dcqcn_reference']
    ref['work']=[dict(id=0,kind='write',stream=0,bytes=2144,after=[]),
        dict(id=1,kind='write',stream=1,bytes=16000,after=[])]
    pc3=deepcopy(protocol);pc3['ecn_by_rate']['100000000000'].update(kmin_bytes=1,kmax_bytes=2)
    pc3['faults']=[fault(0,'ack',2144)]
    r=execute('independent-cnp',ref,pc3)
    t=r['persistent_transport'];assert any(e['kind']=='cnp_received' for e in t['dcqcn_reference']['events'])
    assert t['source_isolation']['terminal_feedback_complete']
    # Both CLI validation and direct native execution must reject malformed policies.
    for label,value in [('unknown','retry_forever'),('bool',True),('list',[]),('null',None),('extra','isolate_stream')]:
        bad=stream_work();bad['transport_recovery']['on_exhausted']=value
        if label=='extra':bad['transport_recovery']['extra']=1
        save(out/(label+'-work.json'),bad);save(out/(label+'-protocol.json'),protocol)
        cli(label+'-cli',['rdma-stream-probe','--topology',out/'topology','--protocol',out/(label+'-protocol.json'),
            '--config',out/(label+'-work.json'),'--backend',a.backend_dir/'ns3.42-moe-rdma-stream-probe','--output',out/(label+'-cli')],1)
        run(label+'-native',[a.backend_dir/'ns3.42-moe-rdma-stream-probe',out/'topology/topology.json',out/(label+'-protocol.json'),out/(label+'-work.json'),out/(label+'-native-result.json')],1)
    save(out/'verification.json',dict(passed=True,cases=cases,commands=len(records)))
    print(json.dumps(dict(passed=True,cases=len(cases),commands=len(records))))

if __name__=='__main__': main()
