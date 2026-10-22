"""Actual native adaptive-RTO gates: source replay, Karn ambiguity and service."""
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

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend-json',type=Path,required=True);p.add_argument('--protoc',required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    out=a.output.resolve();out.mkdir(exist_ok=False);b=read(a.backend_json);commands=[]
    def cli(name,args,success=True):
        assert shutil.disk_usage(out).free>=8*1024**3
        assert sum(p.stat().st_size for p in out.rglob('*') if p.is_file())<256*1024**2
        argv=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,args)]
        with (out/(name+'.stdout')).open('w') as f,(out/(name+'.stderr')).open('w') as e:
            r=subprocess.run(argv,cwd=ROOT,stdout=f,stderr=e,timeout=90)
        commands.append(dict(name=name,argv=argv,cwd=str(ROOT),exit_code=r.returncode,expected_success=success))
        save(out/'commands.json',commands);assert (r.returncode==0)==success,name
    cli('topology',['topology','--config',ROOT/'examples/scaleout_rdma/small-clos-100g.json','--output',out/'topology'])
    base=read(ROOT/'examples/scaleout_rdma_recovery/recover-clean.json')
    base['recovery']['rtt_estimator']=dict(kind='karn',min_rto_ns=1000,granularity_ns=1)
    message=dict(id=50000,src=0,dst=4,bytes=4000,plane=0,ready_ns=0);save(out/'messages.json',[message])
    def fault(kind,seq,count=1):return dict(node=4 if kind=='data' else 0,port=0,message_id=50000,kind=kind,
        sequence=seq,count=count,start_ns=0,end_ns=5000000)
    definitions=[('clean',[],True),('final-ack',[fault('ack',4000)],True),('tail',[fault('data',3000)],True),
        ('lost-nack',[fault('data',1000),fault('nack',1000)],True),('repeated-data',[fault('data',1000,2)],True),
        ('persistent',[fault('ack',4000,10000)],False),('short',[],True),
        ('capture-zero',[fault('ack',4000)],True),('capture-three',[fault('ack',4000)],True)]
    reports={};diagnoses={}
    for name,faults,success in definitions:
        c=deepcopy(base)
        if faults:c['faults']=faults
        if name=='short':c['recovery'].update(initial_rto_ns=2000,max_rto_ns=64000,max_retries=8)
        if name=='persistent':c['recovery']['max_retries']=2
        if name.startswith('capture-'):c['recovery']['max_events']=0 if name.endswith('zero') else 3
        save(out/(name+'.json'),c)
        cli(name,['rdma-probe','--topology',out/'topology','--config',out/(name+'.json'),
            '--messages',out/'messages.json','--backend',b['probe'],'--output',out/name],success)
        cli(name+'-diagnose',['rdma-diagnose','--run',out/name,'--output',out/(name+'-diagnosis.json')])
        r=reports[name]=read(out/name/'result.json');diagnoses[name]=read(out/(name+'-diagnosis.json'))
        if faults:assert all(n>0 for n in r['fault_injection']['drop_counts'])
        f=r['messages'][0];s=r['recovery']['states'][0]
        if success:assert f['ack_callbacks']==1 and f['source_unique_data_payload_bytes']==f['receiver_contiguous_bytes']==4000
        else:assert f['ack_callbacks']==0 and f['sender_resource_released_ns'] is None and s['state']=='retry_exhausted'
    for name in ('clean','final-ack','tail','lost-nack','repeated-data','short','persistent'):
        assert diagnoses[name]['recovery']['rtt']['reconstructed']
    assert reports['final-ack']['recovery']['states'][0]['rtt']['samples']==1
    assert reports['short']['recovery']['states'][0]['rtt']['samples']==0 # conservative bootstrap limitation
    assert reports['short']['recovery']['states'][0]['rtt']['discarded_probes']>=1
    assert diagnoses['persistent']['recovery']['silence_bounds'][0]['outcome']=='failed_at_bound'
    for name in ('capture-zero','capture-three'):
        assert reports[name]['messages']==reports['final-ack']['messages'] and reports[name]['ports']==reports['final-ack']['ports']
        assert reports[name]['recovery']['states']==reports['final-ack']['recovery']['states']
        assert not diagnoses[name]['recovery']['rtt']['reconstructed']
    # The two planes use distinct source QPs and RTT histories, not a GPU-global estimator.
    dual=read(ROOT/'examples/scaleout_rdma/small-clos-100g.json');dual['deployment']['pods']=1
    dual['fabric'].update(planes=2,aggs_per_pod_per_group=1,cores_per_group=0,tor_uplinks=1,agg_uplinks=0,core_wiring='none')
    dual['nic']['total_rate_bps']=200000000000;save(out/'dual-topology.json',dual)
    cli('dual-topology',['topology','--config',out/'dual-topology.json','--output',out/'dual-topology'])
    save(out/'dual-messages.json',[dict(id=i,src=0,dst=2,bytes=4000,plane=i,ready_ns=0) for i in (0,1)])
    c=deepcopy(base);c['admission']['max_active_per_source']=2
    c['faults']=[dict(node=0,port=0,message_id=0,kind='ack',sequence=4000,count=1,start_ns=0,end_ns=5000000)]
    save(out/'dual.json',c)
    cli('dual',['rdma-probe','--topology',out/'dual-topology','--config',out/'dual.json',
        '--messages',out/'dual-messages.json','--backend',b['probe'],'--output',out/'dual'])
    r=read(out/'dual/result.json');assert [s['timeout_retransmissions'] for s in r['recovery']['states']]==[1,0]
    assert all(s['rtt']['samples']==1 for s in r['recovery']['states'])
    # Actual Scheduler -> Chakra -> ASTRA -> shared native network -> next wave.
    service=read(ROOT/'examples/scaleout_rdma/service.json');service['model']['layers']=1;service['workload']['decode_steps']=2
    save(out/'service.json',service)
    services=[('service-clean',False,True),('service-ack',True,True),('service-persistent',True,False)]
    for name,drop,success in services:
        c=deepcopy(base)
        if drop:c['faults']=[dict(node=0,port=0,message_id=0,kind='ack',sequence=2048,count=1 if success else 10000,start_ns=0,end_ns=5000000)]
        if not success:c['recovery']['max_retries']=2
        save(out/(name+'.json'),c)
        cli(name,['rdma-run','--topology',out/'topology','--config',out/'service.json','--protocol',out/(name+'.json'),
            '--backend',b['service'],'--protoc',a.protoc,'--output',out/name],success)
        cli(name+'-diagnose',['rdma-diagnose','--run',out/name,'--output',out/(name+'-diagnosis.json')])
        r=read(out/name/'result.json');d=read(out/(name+'-diagnosis.json'))
        assert d['recovery']['rtt']['reconstructed']
        if success:
            assert r['drained'] and r['recovery']['inflight_packets']==0
            assert all(m['send_callback_ns'] is not None and m['recv_callback_ns'] is not None for m in r['service']['message_callbacks'])
        else:assert not r['service']['service_finished'] and d['recovery']['silence_bounds']
    save(out/'summary.json',dict(passed=True,probe_cases=len(definitions)+1,service_cases=len(services),
        source_only_rtt_replay=True,conservative_karn_ambiguity=True,finite_silence_bound=True,
        short_initial_rto_bootstrap_limit_observed=True,capture_invariant=True,dual_plane_estimator_isolation=True))
    print('Native adaptive RTT/RTO, silence certificates and frontend gates passed')

if __name__=='__main__':main()
