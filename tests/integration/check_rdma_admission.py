"""Bounded native admission, lifecycle, observation equivalence and ACK fault gates."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]

def read(path):return json.loads(path.read_text())
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')

def without_observers(result):
    r=deepcopy(result)
    for key in ('admission','lifecycle_capture'):r.pop(key,None)
    for f in r['messages']:
        for key in ('admission_granted_ns','qp_registered_ns','sender_resource_released_ns',
                    'sender_qp_present','completion_state','receiver_contiguous_bytes',
                    'source_unique_data_payload_bytes','first_packet_uid'):
            f.pop(key,None)
    return r

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend-json',type=Path,required=True)
    p.add_argument('--previous-probe',type=Path)
    p.add_argument('--previous-service',type=Path)
    p.add_argument('--protoc',required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();out=args.output.resolve();out.mkdir(exist_ok=False)
    backend=read(args.backend_json);commands=[]
    def cli(name,argv,success=True):
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,argv)]
        with (out/(name+'.stdout')).open('w') as stdout,(out/(name+'.stderr')).open('w') as stderr:
            r=subprocess.run(command,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=90)
        commands.append(dict(name=name,argv=command,cwd=str(ROOT),exit_code=r.returncode,expected_success=success))
        save(out/'commands.json',commands)
        assert (r.returncode==0)==success,(name,r.returncode)
    example=ROOT/'examples/scaleout_rdma'
    cli('topology',['topology','--config',example/'small-clos-100g.json','--output',out/'topology'])
    protocol=read(example/'rdma.json')
    messages=[dict(id=i,src=src,dst=dst,bytes=size,plane=0,ready_ns=ready)
              for i,src,dst,size,ready in [(8,0,4,4000,0),(2,0,4,1000,0),(9,0,6,1000,0),
                                          (1,0,4,1000,1),(22,1,7,1000,0)]]
    save(out/'messages.json',messages)
    def options(policy,limit,cost=0,events=100000):
        return dict(admission=dict(policy=policy,max_active_per_source=limit,decision_ns=cost),
                    lifecycle_capture=dict(max_events=events))
    cases=[('default',{},True),('pass',options('passthrough',0),True),
           ('capture-only',dict(lifecycle_capture=dict(max_events=100000)),True),
           ('truncated',options('passthrough',0,events=3),True),
           ('fifo1',options('fifo',1),True),('fifo2',options('fifo',2),True),
           ('rr1',options('destination_round_robin',1),True),
           ('decision-cost',options('fifo',1,cost=10),True),
           ('ack-stall',dict(options('fifo',1),faults=[dict(node=0,port=0,message_id=8,kind='ack',
               sequence=4000,start_ns=0,end_ns=5000000,count=1)]),False)]
    reports={};diagnoses={}
    for name,extra,success in cases:
        save(out/(name+'.json'),dict(deepcopy(protocol),**extra))
        cli(name,['rdma-probe','--topology',out/'topology','--config',out/(name+'.json'),
            '--messages',out/'messages.json','--backend',backend['probe'],'--output',out/name],success)
        cli(name+'-diagnose',['rdma-diagnose','--run',out/name,'--output',out/(name+'-diagnosis.json')])
        reports[name]=read(out/name/'result.json');diagnoses[name]=read(out/(name+'-diagnosis.json'))
    for name in ('pass','capture-only','truncated'):
        assert without_observers(reports[name])==without_observers(reports['default']),name
    assert diagnoses['truncated']['admission_resources']['reconstructed'] is False
    assert diagnoses['truncated']['admission_resources']['reason']=='capture_truncated'
    def grants(name):return [e[2] for e in reports[name]['lifecycle_capture']['events'] if e[1]==1 and e[3]==0]
    assert grants('fifo1')==[8,2,9,1]
    assert grants('rr1')==[8,9,2,1]
    events=reports['fifo2']['lifecycle_capture']['events']
    assert any(e[1]==1 and e[2]==2 and e[4]==2 for e in events)
    assert diagnoses['fifo2']['admission_resources']['peak_active']['0']==2
    cost=reports['decision-cost'];by_id={m['id']:m for m in cost['messages']}
    starts={}
    for e in cost['lifecycle_capture']['events']:
        if e[1]==6:starts[e[3]]=e[0]
        if e[1]==1:assert e[0]-starts.pop(e[3])==10
    assert not starts
    fault=reports['ack-stall'];fs={m['id']:m for m in fault['messages']}
    assert fault['fault_injection']['drop_counts']==[1]
    assert fs[8]['receive_ready_ns'] is not None and fs[8]['ack_ns'] is None
    assert fs[8]['sender_resource_released_ns'] is None and fs[8]['sender_qp_present']
    source=next(s for s in fault['admission']['sources'] if s['src']==0)
    assert source['active_ids']==[8] and set(source['pending_ids'])=={2,9,1}
    assert all(fs[i]['first_tx_ns'] is None and fs[i]['admission_granted_ns'] is None for i in (2,9,1))
    assert fs[22]['sender_resource_released_ns'] is not None
    assert diagnoses['ack-stall']['admission_resources']['reconstructed']
    if args.previous_probe:
        cli('previous',['rdma-probe','--topology',out/'topology','--config',out/'default.json',
            '--messages',out/'messages.json','--backend',args.previous_probe,'--output',out/'previous'])
        assert read(out/'previous/result.json')==reports['default']
    # Real LLMServingSim -> graph -> ASTRA -> native packets -> frontend feedback.
    service=read(example/'service.json');service['model']['layers']=1;service['workload']['decode_steps']=2
    save(out/'service.json',service)
    service_reports={}
    service_cases=[('service-default',{},backend['service']),
        ('service-pass',options('passthrough',0),backend['service']),
        ('service-fifo',options('fifo',1),backend['service']),
        ('service-rr',options('destination_round_robin',1),backend['service'])]
    if args.previous_service:service_cases.append(('service-previous',{},str(args.previous_service)))
    for name,extra,binary in service_cases:
        save(out/(name+'.json'),dict(deepcopy(protocol),**extra))
        cli(name,['rdma-run','--topology',out/'topology','--protocol',out/(name+'.json'),
            '--config',out/'service.json','--backend',binary,'--protoc',args.protoc,'--output',out/name])
        service_reports[name]=read(out/name/'result.json')
    assert without_observers(service_reports['service-default'])==without_observers(service_reports['service-pass'])
    if args.previous_service:assert service_reports['service-previous']==service_reports['service-default']
    # Posted SENDs behind the lost ACK must be diagnosed as admission waits,
    # not as packets already sent and awaiting ACKs.
    stalled_protocol=dict(deepcopy(protocol),**options('fifo',1))
    stalled_protocol['faults']=[dict(node=0,port=0,message_id=0,kind='ack',sequence=2048,
        start_ns=0,end_ns=5000000,count=1)]
    save(out/'service-stalled.json',stalled_protocol)
    cli('service-stalled',['rdma-run','--topology',out/'topology','--protocol',out/'service-stalled.json',
        '--config',out/'service.json','--backend',backend['service'],'--protoc',args.protoc,
        '--output',out/'service-stalled'],False)
    cli('service-stalled-diagnose',['rdma-diagnose','--run',out/'service-stalled',
        '--output',out/'service-stalled-diagnosis.json'])
    stalled=read(out/'service-stalled/result.json');diagnosis=read(out/'service-stalled-diagnosis.json')
    assert stalled['fault_injection']['drop_counts']==[1]
    assert diagnosis['admission_resources']['reconstructed']
    assert any(e['state']=='waiting_admission' and e['kind']=='SEND' and e['issue_ns'] is not None
               for e in diagnosis['outstanding_graph_edges'])
    callback=next(c for c in stalled['service']['message_callbacks'] if c['id']==0)
    assert callback['recv_callback_ns'] is not None and callback['send_callback_ns'] is None
    # Same-host crossbar messages consume no source RDMA admission credits.
    local_topology=read(example/'small-clos-100g.json')
    local_topology['schema_version']='moe-scaleout-topology/2'
    local_topology['intra_host']=dict(kind='nonblocking_chunked_crossbar',enabled=True,
        rate_bps=3600000000000,delay_ns=100,chunk_bytes=16384)
    save(out/'local-topology.json',local_topology)
    cli('local-topology',['topology','--config',out/'local-topology.json','--output',out/'local-topology'])
    local=[]
    for name,extra in [('local-default',{}),('local-pass',options('passthrough',0)),('local-fifo',options('fifo',1))]:
        save(out/(name+'.json'),dict(deepcopy(protocol),**extra))
        cli(name,['rdma-run','--topology',out/'local-topology','--protocol',out/(name+'.json'),
            '--config',out/'service.json','--backend',backend['service'],'--protoc',args.protoc,'--output',out/name])
        local.append(read(out/name/'result.json'))
    assert without_observers(local[0])==without_observers(local[1])
    assert any(m.get('transport')=='intra_host_chunked_crossbar' for m in local[2]['messages'])
    assert all('admission_granted_ns' not in m for m in local[2]['messages'] if 'transport' in m)
    # Separate planes share the same source credit pool. Symmetric uncapped
    # transfers deliver two ACKs at one timestamp before software releases.
    dual=read(example/'small-clos-100g.json');dual['deployment']['pods']=1
    dual['fabric'].update(planes=2,aggs_per_pod_per_group=1,cores_per_group=0,
        tor_uplinks=1,agg_uplinks=0,core_wiring='none')
    dual['nic']['total_rate_bps']=200000000000
    save(out/'dual-topology.json',dual)
    save(out/'dual-messages.json',[dict(id=i,src=0,dst=2,bytes=1000,plane=i,ready_ns=0) for i in range(2)])
    cli('dual-topology',['topology','--config',out/'dual-topology.json','--output',out/'dual-topology'])
    dual_reports=[]
    for name,extra in [('dual-pass',options('passthrough',0)),('dual-fifo',options('fifo',1))]:
        save(out/(name+'.json'),dict(deepcopy(protocol),**extra))
        cli(name,['rdma-probe','--topology',out/'dual-topology','--config',out/(name+'.json'),
            '--messages',out/'dual-messages.json','--backend',backend['probe'],'--output',out/name])
        dual_reports.append(read(out/name/'result.json'))
    assert dual_reports[0]['messages'][0]['ack_ns']==dual_reports[0]['messages'][1]['ack_ns']
    assert dual_reports[1]['messages'][1]['admission_granted_ns']>=dual_reports[1]['messages'][0]['sender_resource_released_ns']
    save(out/'summary.json',dict(passed=True,probe_cases=len(cases)+2,service_cases=len(service_cases)+len(local)+1,
        pass_observer_invariance=True,legacy_default_equivalence=bool(args.previous_probe and args.previous_service),
        queue_order_and_capacity=True,ack_loss_retains_owner=True,independent_source_progress=True,
        explicit_decision_cost=True,intra_host_excluded=True,planes_share_source_quota=True,
        simultaneous_ack_native_map_vs_software_release=True))
    print('Native admission, ownership, service and fault gates passed')

if __name__=='__main__':main()
