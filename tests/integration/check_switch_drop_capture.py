"""Public workflow acceptance: passive capture and real packet conservation.

Each new network condition once; no private data or performance-gain claims.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.scaleout_rdma import ROOT,save
from moe_ub_cosim.scaleout_switch_drops import analyze
from moe_ub_cosim.scaleout_topology import require


def check(probe,service,protoc,output,reference,resume=False):
    output.mkdir(parents=True,exist_ok=resume)
    commands=json.loads((output/'commands.json').read_text()) if resume else []
    def run(name,args,expected=0):
        args=list(map(str,args))
        old=[c for c in commands if c['name']==name]
        if old:
            require(resume and len(old)==1 and old[0]['command']==args and
                    old[0]['cwd']==str(ROOT) and old[0]['exit_code']==expected,
                    'saved command does not match explicit resume: '+name)
            require(all((output/(name+suffix)).is_file() for suffix in ('.stdout','.stderr')),
                    'saved command logs missing: '+name)
            return
        with (output/(name+'.stdout')).open('x') as out,(output/(name+'.stderr')).open('x') as err:
            p=subprocess.run(args,cwd=ROOT,stdout=out,stderr=err,timeout=120)
        commands.append(dict(name=name,command=args,cwd=str(ROOT),exit_code=p.returncode))
        save(output/'commands.json',commands)
        require(p.returncode==expected,'unexpected public command exit: '+name)
    def cli(name,args,expected=0):
        run(name,[sys.executable,'-B','-m','moe_ub_cosim','scaleout',*args],expected)
    run('prepare',[sys.executable,'-B',ROOT/'examples/scaleout_switch_drops/prepare.py','--output',output/'inputs'])
    cli('topology',['topology','--config',output/'inputs/topology.json','--output',output/'fabric'])
    read=lambda p:json.loads(p.read_text())
    t=read(output/'fabric/topology.json');results={}
    for mode in ('off','on'):
        cli(mode,['rdma-probe','--topology',output/'fabric','--config',output/'inputs'/(mode+'.json'),
                  '--messages',output/'inputs/messages.json','--backend',probe,'--output',output/mode],expected=1)
        results[mode]=read(output/mode/'result.json')
    off,on=results['off'],results['on']
    # The first fixed incast reached real ingress refusal and retry exhaustion.
    # Preserve that failed run; observation must reproduce it, not hide failure.
    require(all(r['native_network_executed'] and r['passed'] is False and not r['drained'] and
                r['failure']['reason']=='recovery_retry_exhausted' and r['fault_drops']==0 and
                r['completed_messages']<len(r['messages']) for r in (off,on)),
            'fixed incast must retain its actual retry-exhaustion outcome')
    require('switch_drop_capture' not in off and {k:v for k,v in on.items() if k!='switch_drop_capture'}==off,
            'passive observation changed existing native results')
    audit=analyze(on,t,read(output/'inputs/on.json'),require_complete=True)
    require(audit['all_switch_reason_counts'][0]>0 and audit['all_switch_reason_counts'][1:]==[0,0,0],
            'fixed incast must exercise real ingress refusal without artificial or forwarding faults')
    capture=on['packet_capture'];require(capture['complete'],'packet evidence truncated')
    rx,enq,drop=Counter(),Counter(),Counter()
    switches={n['id'] for n in t['nodes'] if n['kind']!='gpu'}
    for e in capture['events']:
        if e[1] in switches and e[6]==17 and e[3] in (1,3):
            (rx if e[3]==1 else enq)[e[1],e[4],e[7]]+=1
    for e in on['switch_drop_capture']['events']:
        if e[8]==17:drop[e[1],e[6],e[9]]+=1
    require(rx==enq+drop,'actual switch data receive != enqueue + forwarding/admission drop')
    failed_id=on['failure']['message_id']
    failed_message=next(m for m in on['messages'] if m['id']==failed_id)
    progress=[e[0] for e in on['recovery']['events'] if e[3]==failed_id and e[1]==1]
    last_progress=max(progress,default=0)
    timeout_requests=[e[0] for e in on['recovery']['events']
                      if e[3]==failed_id and e[1]==2 and e[0]>last_progress]
    source_tx=[e for e in capture['events'] if e[1]==failed_message['src'] and e[3]==0 and
               e[5]==failed_id and e[6]==17]
    failure_timeline=dict(message_id=failed_id,last_ack_progress_ns=last_progress,
        retry_requests_after_last_progress_ns=timeout_requests,
        last_source_data_tx_ns=max((e[0] for e in source_tx),default=None),
        source_data_packets_after_last_progress=sum(e[0]>last_progress for e in source_tx),
        scope='complete source TX and source timer records; timeout requests are not actual retransmitted packets; pacing/PFC attribution still requires separate evidence')
    cli('service-topology',['topology','--config',ROOT/'examples/astra_stream_service/topology.json',
                            '--output',output/'service-fabric'])
    cli('service',['rdma-run','--config',ROOT/'examples/astra_stream_service/service.json',
                   '--topology',output/'service-fabric','--protocol',output/'inputs/service-rdma.json',
                   '--backend',service,'--protoc',protoc,'--output',output/'service'])
    cli('service-check',['rdma-check','--run',output/'service'])
    r=read(output/'service/result.json')
    old=read(reference/'result.json') if reference else None
    if old is not None:
        # Relinking changed loader enumeration order, not the loaded path set.
        # Every simulation field must still compare exactly; keep both raw lists.
        require(sorted(r['loaded_libraries'])==sorted(old['loaded_libraries']),
                'saved service loaded library paths differ')
        require({k:r[k] for k in old if k!='loaded_libraries'}==
                {k:v for k,v in old.items() if k!='loaded_libraries'} and set(r)-set(old)<=
                {'switch_drop_capture','execution_limits','queue_capture','queue_observations'},
                'new dependency/observer changed saved original service fields')
    summary=dict(passed=True,network_conditions=3,incast_messages=len(on['messages']),
        incast_payload_bytes=sum(m['bytes'] for m in on['messages']),incast_switch_drops=audit,
        incast_packet_events=len(capture['events']),data_switch_receive_events=sum(rx.values()),
        data_switch_enqueue_events=sum(enq.values()),data_switch_drop_events=sum(drop.values()),
        incast_success=False,incast_completed_messages=on['completed_messages'],incast_failure=on['failure'],
        incast_failure_timeline=failure_timeline,
        existing_native_fields_on_off_equal=True,service_matches_saved_reference=old is not None,
        service_library_order_changed=old is not None and r['loaded_libraries']!=old['loaded_libraries'],
        service_library_comparison='same paths with multiplicity; enumeration order is loader metadata',
        service_finish_ns=r['finish_ns'],service_messages=len(r['service_transport']['messages']),
        service_switch_drops=r['switch_drop_capture']['all_switch_reason_counts'],
        scope='synthetic negative incast pair plus positive ASTRA service; branch prestate test separate; no production loss frequency or gains')
    save(output/'verification.json',summary)
    print(json.dumps(summary))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('probe','service','protoc','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--reference',type=Path)
    p.add_argument('--resume',action='store_true',help='reuse exact recorded terminal commands; never retry them')
    a=p.parse_args();check(a.probe.resolve(),a.service.resolve(),a.protoc.resolve(),a.output.resolve(),a.reference,a.resume)
