"""Byte admission, passive network metrics and source progress: real bounded gates."""
import argparse
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')


def packet_queue_audit(result):
    """Independent all-port state integral AND packet size * residence time."""
    capture=result['packet_capture']
    assert capture['complete'] and capture['selection']['nodes']==[]
    ports={(p['node'],p['port']):p for p in result['ports'] if 'queue_metrics' in p}
    state=defaultdict(int);last=defaultdict(int);area=defaultdict(int);residence=defaultdict(int)
    peak=defaultdict(int);changes=defaultdict(int);packets={}
    for row in capture['events']:
        e=dict(zip(capture['columns'],row));key=e['node'],e['port']
        if key not in ports or e['event'] not in (3,4):continue
        t=e['time_ns'];size=e['model_bytes'];identity=(*key,e['packet_uid'])
        area[key]+=state[key]*(t-last[key]);last[key]=t;changes[key]+=1
        if e['event']==3:
            assert identity not in packets
            state[key]+=size;packets[identity]=(t,size)
            assert e['egress_queue_bytes']==state[key]
        else:
            assert state[key]==e['egress_queue_bytes']
            enqueued,original=packets.pop(identity);assert original==size
            residence[key]+=size*(t-enqueued);state[key]-=size
        assert state[key]>=0;peak[key]=max(peak[key],state[key])
    for identity,(t,size) in packets.items():residence[identity[:2]]+=size*(result['finish_ns']-t)
    for key,p in ports.items():
        area[key]+=state[key]*(result['finish_ns']-last[key])
        assert p['queue_metrics']==dict(area_byte_ns=area[key],peak_bytes=peak[key],final_bytes=state[key],changes=changes[key])
        assert area[key]==residence[key]
    return sum(area.values())


def without_metrics(r):
    r=deepcopy(r);r.pop('queue_metrics_scope',None);r.pop('packet_capture',None)
    for p in r['ports']:p.pop('queue_metrics',None)
    return r


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend-json',required=True,type=Path);p.add_argument('--protoc',required=True)
    p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    out=a.output.resolve();out.mkdir(exist_ok=False);backend=read(a.backend_json);commands=[]
    def cli(name,argv,success=True):
        assert shutil.disk_usage(out).free>=8*1024**3
        assert sum(f.stat().st_size for f in out.rglob('*') if f.is_file())<256*1024**2
        cmd=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,argv)]
        with (out/(name+'.stdout')).open('w') as stdout,(out/(name+'.stderr')).open('w') as stderr:
            r=subprocess.run(cmd,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=90)
        commands.append(dict(name=name,argv=cmd,cwd=str(ROOT),exit_code=r.returncode,expected_success=success))
        save(out/'commands.json',commands);assert (r.returncode==0)==success,(name,r.returncode)
    example=ROOT/'examples/scaleout_rdma'
    cli('topology',['topology','--config',example/'small-clos-100g.json','--output',out/'topology'])
    messages=[dict(id=i,src=s,dst=d,bytes=b,plane=0,ready_ns=t)
        for i,s,d,b,t in [(1,0,4,4000,0),(2,0,4,2000,1),(3,0,6,1000,1),(4,0,4,1000,1),(5,1,7,1000,0)]]
    save(out/'messages.json',messages);base=read(example/'rdma.json')
    base['lifecycle_capture']=dict(max_events=100000)
    base['admission']=dict(policy='fifo',max_active_per_source=3,decision_ns=0)
    configs={}
    configs['count']=deepcopy(base)
    configs['zero']=deepcopy(base);configs['zero']['admission']['max_active_bytes_per_source']=0
    configs['fifo']=deepcopy(base);configs['fifo']['admission']['max_active_bytes_per_source']=5000
    configs['rr']=deepcopy(configs['fifo']);configs['rr']['admission']['policy']='destination_round_robin'
    configs['cost']=deepcopy(configs['fifo']);configs['cost']['admission']['decision_ns']=10
    configs['capture']=deepcopy(configs['rr']);configs['capture']['queue_metrics']=True
    configs['capture']['packet_capture']=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=100000)
    configs['truncated']=deepcopy(configs['capture']);configs['truncated']['packet_capture']['max_events']=3
    from moe_ub_cosim.scaleout_limits import DEFAULTS
    configs['summary']=deepcopy(configs['rr']);configs['summary'].update(schema_version='moe-scaleout-rdma/2',
        queue_metrics=True,limits=dict(DEFAULTS,queue_capture='summary',max_queue_events=1))
    configs['ack']=deepcopy(configs['capture']);configs['ack']['faults']=[dict(node=0,port=0,message_id=1,
        kind='ack',sequence=4000,start_ns=0,end_ns=5000000,count=1)]
    reports={};diagnoses={}
    for name,c in configs.items():
        save(out/(name+'.json'),c)
        cli(name,['rdma-probe','--topology',out/'topology','--config',out/(name+'.json'),
            '--messages',out/'messages.json','--backend',backend['probe'],'--output',out/name],name!='ack')
        cli(name+'-diagnose',['rdma-diagnose','--run',out/name,'--output',out/(name+'-diagnosis.json')])
        reports[name]=read(out/name/'result.json');diagnoses[name]=read(out/(name+'-diagnosis.json'))
    def grant(name,mid):return next(f['admission_granted_ns'] for f in reports[name]['messages'] if f['id']==mid)
    assert grant('fifo',3)>1 and grant('rr',3)==1
    assert grant('rr',4)>=grant('rr',2) # no bypass inside peer4
    assert diagnoses['count']['admission_resources']['peak_active_logical_bytes']['0']>5000
    assert diagnoses['fifo']['admission_resources']['peak_active_logical_bytes']['0']<=5000
    assert diagnoses['rr']['admission_resources']['peak_active_logical_bytes']['0']==5000
    assert reports['count']['messages']==reports['zero']['messages']
    assert without_metrics(reports['rr'])==without_metrics(reports['capture'])==without_metrics(reports['truncated'])
    summary=deepcopy(reports['summary'])
    for k in ('execution_limits','queue_capture','queue_observations'):summary.pop(k)
    summary['queue_events']=reports['rr']['queue_events']
    assert without_metrics(summary)==without_metrics(reports['rr'])
    assert not reports['summary']['queue_events'] and reports['summary']['queue_observations']>1
    for case in ('capture','ack'):packet_queue_audit(reports[case])
    expected=[p['queue_metrics'] for p in reports['capture']['ports'] if 'queue_metrics' in p]
    for case in ('truncated','summary'):
        assert [p['queue_metrics'] for p in reports[case]['ports'] if 'queue_metrics' in p]==expected
    f=next(f for f in reports['ack']['messages'] if f['id']==1)
    assert f['sender_progress']==dict(scope='source_QP_snapshot; no timeout/recovery is inferred',snd_una_bytes=3000,snd_nxt_bytes=4000)
    d=next(f for f in diagnoses['ack']['messages'] if f['id']==1)
    assert d['stage_times']['open_stage']['name']=='confirmation_tail'
    assert f['sender_resource_released_ns'] is None and reports['ack']['fault_injection']['drop_counts']==[1]
    assert diagnoses['ack']['network_queues']['final_bytes']==0
    # Oversize is rejected before network execution and never silently split.
    save(out/'oversize.json',[dict(messages[0],bytes=5001)])
    cli('oversize',['rdma-probe','--topology',out/'topology','--config',out/'fifo.json',
        '--messages',out/'oversize.json','--backend',backend['probe'],'--output',out/'oversize'],False)
    assert not (out/'oversize').exists()
    # Independent real frontend service, byte cap, summary-only and ACK fault.
    service=read(example/'service.json');service['model']['layers']=1;service['workload']['decode_steps']=2
    save(out/'service.json',service)
    sr={}
    for name,source in [('service-off','count'),('service-metrics','count'),('service-bytes','count')]:
        c=deepcopy(configs[source]);c['queue_metrics']=name!='service-off'
        if name=='service-bytes':c['admission']['max_active_bytes_per_source']=8192
        save(out/(name+'.json'),c)
        cli(name,['rdma-run','--config',out/'service.json','--topology',out/'topology',
            '--protocol',out/(name+'.json'),'--backend',backend['service'],'--protoc',a.protoc,'--output',out/name])
        sr[name]=read(out/name/'result.json')
    assert without_metrics(sr['service-off'])==without_metrics(sr['service-metrics'])
    for r in sr.values():assert r['passed'] and all(f['sender_resource_released_ns'] is not None for f in r['messages'])
    dual=read(example/'small-clos-100g.json');dual['deployment']['pods']=1
    dual['fabric'].update(planes=2,aggs_per_pod_per_group=1,cores_per_group=0,tor_uplinks=1,agg_uplinks=0,core_wiring='none')
    dual['nic']['total_rate_bps']=200000000000
    save(out/'dual-topology.json',dual)
    cli('dual-topology',['topology','--config',out/'dual-topology.json','--output',out/'dual-topology'])
    save(out/'dual-messages.json',[dict(id=i,src=0,dst=2,bytes=1000,plane=i,ready_ns=0) for i in (0,1)])
    c=deepcopy(configs['fifo']);c['admission']['max_active_bytes_per_source']=1000
    save(out/'dual.json',c)
    cli('dual',['rdma-probe','--topology',out/'dual-topology','--config',out/'dual.json',
        '--messages',out/'dual-messages.json','--backend',backend['probe'],'--output',out/'dual'])
    d=read(out/'dual/result.json');assert d['messages'][1]['admission_granted_ns']>=d['messages'][0]['sender_resource_released_ns']
    local=read(example/'small-clos-100g.json');local['schema_version']='moe-scaleout-topology/2'
    local['intra_host']=dict(kind='nonblocking_chunked_crossbar',enabled=True,rate_bps=3600000000000,delay_ns=100,chunk_bytes=16384)
    save(out/'local-topology.json',local)
    cli('local-topology',['topology','--config',out/'local-topology.json','--output',out/'local-topology'])
    c=deepcopy(configs['fifo']);c['admission']['max_active_bytes_per_source']=1
    save(out/'local.json',c);save(out/'local-messages.json',[dict(id=0,src=0,dst=1,bytes=4000,plane=0,ready_ns=0)])
    cli('local',['rdma-probe','--topology',out/'local-topology','--config',out/'local.json',
        '--messages',out/'local-messages.json','--backend',backend['probe'],'--output',out/'local'])
    assert read(out/'local/result.json')['messages'][0]['transport']=='intra_host_chunked_crossbar'
    save(out/'summary.json',dict(passed=True,probe_cases=11,oversize_preflight=True,service_cases=3,
        byte_budget_shared_across_planes=True,intra_host_excluded_from_byte_budget=True,
        queue_integrals_independently_reconstructed=True,packet_residence_crosscheck=True,
        metrics_observation_invariant=True,truncated_and_summary_exact=True,byte_ownership=True,
        strict_fifo_and_eligible_peer_rotation=True,source_progress_and_ack_stall=True))
    print('Byte admission, queue integrals, stage timing and source progress gates passed')


if __name__=='__main__':main()
