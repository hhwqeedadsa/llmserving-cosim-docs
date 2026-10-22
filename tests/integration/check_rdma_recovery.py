"""Declared Go-Back-N: real native packets, recoverable loss and terminal failure."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def without_recovery(r):
    r=deepcopy(r);r.pop('recovery',None)
    return r

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend-json',required=True,type=Path);p.add_argument('--protoc',required=True)
    p.add_argument('--previous-probe',type=Path);p.add_argument('--previous-service',type=Path)
    p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    out=a.output.resolve();out.mkdir(exist_ok=False);backend=read(a.backend_json);commands=[]
    def cli(name,argv,success=True):
        assert shutil.disk_usage(out).free>=8*1024**3
        assert sum(p.stat().st_size for p in out.rglob('*') if p.is_file())<256*1024**2
        cmd=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,argv)]
        with (out/(name+'.stdout')).open('w') as stdout,(out/(name+'.stderr')).open('w') as stderr:
            r=subprocess.run(cmd,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=90)
        commands.append(dict(name=name,argv=cmd,cwd=str(ROOT),exit_code=r.returncode,expected_success=success))
        save(out/'commands.json',commands);assert (r.returncode==0)==success,(name,r.returncode)
    example=ROOT/'examples/scaleout_rdma'
    cli('topology',['topology','--config',example/'small-clos-100g.json','--output',out/'topology'])
    base=read(example/'rdma.json');base['lifecycle_capture']=dict(max_events=100000)
    base['admission']=dict(policy='fifo',max_active_per_source=1,decision_ns=0)
    base['queue_metrics']=True
    recovery=dict(mode='go_back_n',initial_rto_ns=40000,max_rto_ns=160000,max_retries=3,max_events=100000)
    capture=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=100000)
    message=dict(id=50000,src=0,dst=4,bytes=4000,plane=0,ready_ns=0)
    save(out/'messages.json',[message])
    def fault(kind,seq,count=1):return dict(node=4 if kind=='data' else 0,port=0,message_id=50000,
        kind=kind,sequence=seq,count=count,start_ns=0,end_ns=5000000)
    cases=[('native',None,[],True),('explicit-native',dict(mode='native'),[],True),
        ('native-final-ack',None,[fault('ack',4000)],False),('clean',recovery,[],True),
        ('tail',recovery,[fault('data',3000)],True),('final-ack',recovery,[fault('ack',4000)],True),
        ('lost-nack',recovery,[fault('data',1000),fault('nack',1000)],True),
        ('repeated-data',recovery,[fault('data',1000,2)],True),
        ('two-acks',recovery,[fault('ack',4000,2)],True),
        ('exhausted',dict(recovery,max_retries=2),[fault('ack',4000,10)],False),
        ('short-rto',dict(recovery,initial_rto_ns=2000,max_rto_ns=64000,max_retries=8),[],True),
        ('budget-before-timeout',dict(recovery,initial_rto_ns=5000000,max_rto_ns=5000000),[fault('ack',4000)],False),
        ('capture-off',recovery,[fault('ack',4000)],True),
        ('capture-truncated',dict(recovery,max_events=3),[fault('ack',4000)],True)]
    reports={};diagnoses={}
    for name,rec,faults,success in cases:
        c=deepcopy(base)
        if rec:c['recovery']=rec
        if faults:c['faults']=faults
        if name!='capture-off':c['packet_capture']=dict(capture,max_events=3 if name=='capture-truncated' else 100000)
        save(out/(name+'.json'),c)
        cli(name,['rdma-probe','--topology',out/'topology','--config',out/(name+'.json'),
            '--messages',out/'messages.json','--backend',backend['probe'],'--output',out/name],success)
        cli(name+'-diagnose',['rdma-diagnose','--run',out/name,'--output',out/(name+'-diagnosis.json')])
        reports[name]=read(out/name/'result.json');diagnoses[name]=read(out/(name+'-diagnosis.json'))
        if faults:assert reports[name]['fault_injection']['drop_counts']==[f['count'] for f in faults] or name=='exhausted'
    assert reports['native']==reports['explicit-native']==without_recovery(reports['clean'])
    for name in ('tail','final-ack','lost-nack','repeated-data','two-acks','short-rto'):
        r=reports[name];f=r['messages'][0]
        assert f['ack_callbacks']==1 and f['receiver_contiguous_bytes']==f['source_unique_data_payload_bytes']==4000
        assert f['source_data_payload_bytes']>4000 and f['sender_resource_released_ns'] is not None
        assert r['recovery']['inflight_packets']==0 and diagnoses[name]['recovery']['reconstructed']
    assert reports['final-ack']['recovery']['duplicate_ack_responses']>=1
    assert reports['two-acks']['recovery']['states'][0]['timeout_retransmissions']==2
    r=reports['exhausted'];f=r['messages'][0]
    assert r['failure']['reason']=='recovery_retry_exhausted' and r['finish_ns']<5000000
    assert f['ack_ns'] is None and f['sender_resource_released_ns'] is None and f['sender_qp_present']
    assert r['recovery']['states'][0]['timeout_retransmissions']==2
    assert diagnoses['exhausted']['messages'][0]['state']=='retry_exhausted'
    assert reports['budget-before-timeout']['failure']['reason']=='simulation_time_limit'
    assert not reports['budget-before-timeout']['recovery']['failed']
    for name in ('capture-off','capture-truncated'):
        assert reports[name]['messages']==reports['final-ack']['messages']
        assert reports[name]['ports']==reports['final-ack']['ports']
    assert not diagnoses['capture-truncated']['recovery']['reconstructed']
    if a.previous_probe:
        cli('previous',['rdma-probe','--topology',out/'topology','--config',out/'native.json',
            '--messages',out/'messages.json','--backend',a.previous_probe,'--output',out/'previous'])
        assert read(out/'previous/result.json')==reports['native']
    # Recovery must release a stalled software owner, then let pending work send.
    more=[message,dict(id=50001,src=0,dst=6,bytes=1000,plane=0,ready_ns=0),
          dict(id=50002,src=1,dst=7,bytes=1000,plane=0,ready_ns=0)]
    save(out/'pending-messages.json',more)
    cli('pending',['rdma-probe','--topology',out/'topology','--config',out/'final-ack.json',
        '--messages',out/'pending-messages.json','--backend',backend['probe'],'--output',out/'pending'])
    r=read(out/'pending/result.json');f={f['id']:f for f in r['messages']}
    assert f[50001]['admission_granted_ns']>=f[50000]['sender_resource_released_ns']
    assert f[50002]['ack_ns']<f[50000]['ack_ns']
    # ACK and timeout at exactly one timestamp: either real event order must
    # still deliver one completion and drain all duplicate responses.
    one=[dict(message,bytes=1000)];save(out/'boundary-messages.json',one)
    cli('boundary-reference',['rdma-probe','--topology',out/'topology','--config',out/'native.json',
        '--messages',out/'boundary-messages.json','--backend',backend['probe'],'--output',out/'boundary-reference'])
    ref=read(out/'boundary-reference/result.json')['messages'][0];deadline=ref['ack_ns']-ref['first_tx_ns']
    c=deepcopy(base);c['recovery']=dict(recovery,initial_rto_ns=deadline,max_rto_ns=deadline*4);c['packet_capture']=capture
    save(out/'boundary.json',c)
    cli('boundary',['rdma-probe','--topology',out/'topology','--config',out/'boundary.json',
        '--messages',out/'boundary-messages.json','--backend',backend['probe'],'--output',out/'boundary'])
    r=read(out/'boundary/result.json');assert r['messages'][0]['ack_callbacks']==1 and r['recovery']['inflight_packets']==0
    assert r['recovery']['states'][0]['timeout_retransmissions']==1
    # Two planes share a source and recover independently; source identity/port
    # matching and timers must not become a per-GPU singleton.
    dual=read(example/'small-clos-100g.json');dual['deployment']['pods']=1
    dual['fabric'].update(planes=2,aggs_per_pod_per_group=1,cores_per_group=0,tor_uplinks=1,agg_uplinks=0,core_wiring='none')
    dual['nic']['total_rate_bps']=200000000000;save(out/'dual-topology.json',dual)
    cli('dual-topology',['topology','--config',out/'dual-topology.json','--output',out/'dual-topology'])
    save(out/'dual-messages.json',[dict(id=i,src=0,dst=2,bytes=1000,plane=i,ready_ns=0) for i in (0,1)])
    c=deepcopy(base);c['admission']['max_active_per_source']=2;c['recovery']=recovery
    c['faults']=[dict(node=0,port=0,message_id=0,kind='ack',sequence=1000,count=1,start_ns=0,end_ns=5000000)]
    save(out/'dual.json',c)
    cli('dual',['rdma-probe','--topology',out/'dual-topology','--config',out/'dual.json',
        '--messages',out/'dual-messages.json','--backend',backend['probe'],'--output',out/'dual'])
    r=read(out/'dual/result.json');assert r['messages'][1]['ack_ns']<r['messages'][0]['ack_ns']
    assert [s['timeout_retransmissions'] for s in r['recovery']['states']]==[1,0]
    # Actual frontend/ASTRA execution, including spurious timeout and terminal loss.
    service=read(example/'service.json');service['model']['layers']=1;service['workload']['decode_steps']=2
    save(out/'service.json',service);sr={}
    definitions=[('service-native',None,False,True),('service-clean',recovery,False,True),
        ('service-final-ack',recovery,True,True),('service-short',dict(recovery,initial_rto_ns=2000,max_rto_ns=64000,max_retries=8),False,True),
        ('service-exhausted',dict(recovery,max_retries=2),True,False)]
    for name,rec,drop,success in definitions:
        c=deepcopy(base);c['packet_capture']=capture
        if rec:c['recovery']=rec
        if drop:c['faults']=[dict(node=0,port=0,message_id=0,kind='ack',sequence=2048,count=10 if not success else 1,start_ns=0,end_ns=5000000)]
        save(out/(name+'.json'),c)
        cli(name,['rdma-run','--topology',out/'topology','--protocol',out/(name+'.json'),
            '--config',out/'service.json','--backend',backend['service'],'--protoc',a.protoc,'--output',out/name],success)
        cli(name+'-diagnose',['rdma-diagnose','--run',out/name,'--output',out/(name+'-diagnosis.json')])
        sr[name]=read(out/name/'result.json')
    assert sr['service-native']==without_recovery(sr['service-clean'])
    for name in ('service-clean','service-final-ack','service-short'):
        assert sr[name]['passed'] and sr[name]['drained'] and sr[name]['recovery']['inflight_packets']==0
        assert all(m['send_callback_ns'] is not None and m['recv_callback_ns'] is not None for m in sr[name]['service']['message_callbacks'])
    assert sr['service-exhausted']['failure']['reason']=='recovery_retry_exhausted'
    assert not sr['service-exhausted']['service']['service_finished']
    assert any(m['send_callback_ns'] is None for m in sr['service-exhausted']['service']['message_callbacks'])
    if a.previous_service:
        cli('service-previous',['rdma-run','--topology',out/'topology','--protocol',out/'service-native.json',
            '--config',out/'service.json','--backend',a.previous_service,'--protoc',a.protoc,'--output',out/'service-previous'])
        assert read(out/'service-previous/result.json')==sr['service-native']
    local=read(example/'small-clos-100g.json');local['schema_version']='moe-scaleout-topology/2'
    local['intra_host']=dict(kind='nonblocking_chunked_crossbar',enabled=True,rate_bps=3600000000000,delay_ns=100,chunk_bytes=16384)
    save(out/'local-topology.json',local)
    cli('local-topology',['topology','--config',out/'local-topology.json','--output',out/'local-topology'])
    save(out/'local-messages.json',[dict(message,src=0,dst=1)])
    cli('local',['rdma-probe','--topology',out/'local-topology','--config',out/'clean.json',
        '--messages',out/'local-messages.json','--backend',backend['probe'],'--output',out/'local'])
    r=read(out/'local/result.json');assert not r['recovery']['states'] and r['recovery']['duplicate_ack_responses']==0
    save(out/'summary.json',dict(passed=True,probe_cases=len(cases)+5,service_cases=len(definitions),
        default_native_equivalence=True,lossless_extension_equivalence=True,
        tail_ack_nack_recovery=True,late_duplicate_feedback_once=True,finite_retry_terminal_failure=True,
        pending_credit_resumes=True,receiver_duplicate_ack_is_real_packet=True,
        capture_invariance=True,network_and_timer_drain=True,exact_deadline_feedback=True,
        simulation_limit_distinct_from_retry_failure=True,dual_plane_timer_isolation=True,intra_host_excluded=True))
    print('Native recovery, terminal failure, resource and real-service gates passed')

if __name__=='__main__':main()
