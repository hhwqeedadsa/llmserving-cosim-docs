"""Finish the bounded SR acceptance after the pre-COMMIT control corruption.

Four NEW conditions only. Does not rerun the five closed successful networks,
read private routes, or substitute a saved-DAG executor for ASTRA.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import time

from moe_ub_cosim.scaleout_rdma import ROOT,digest,save
from moe_ub_cosim.scaleout_topology import require,generate
from moe_ub_cosim.scaleout_service import make_wave
from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.service_terminal import service_terminal


def check(backend,runtime,output,protoc):
    output=output.resolve();output.mkdir(parents=True,exist_ok=False)
    commands=[];cases=[];started=time.monotonic()
    def cli(name,args,success=True):
        require(time.monotonic()-started<600,'completion acceptance host budget exhausted')
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,args)]
        with (output/(name+'.stdout')).open('x') as log,(output/(name+'.stderr')).open('x') as errors:
            result=subprocess.run(command,cwd=ROOT,stdout=log,stderr=errors,timeout=120)
        commands.append(dict(name=name,command=command,exit_code=result.returncode,cwd=str(ROOT)))
        save(output/'commands.json',commands)
        require((result.returncode==0)==success,'unexpected public command outcome: '+name)
    folder=ROOT/'examples/astra_stream_service'
    original=json.loads((folder/'service.json').read_bytes())
    original_protocol=json.loads((folder/'rdma.json').read_bytes())
    cli('topology',['topology','--config',folder/'topology.json','--output',output/'fabric'])
    for name in ('tail-ack','tail-data','blackhole-after-control-fix'):
        c,p=deepcopy(original),deepcopy(original_protocol)
        if name.startswith('tail-'):
            c['model']['layers']=1;c['route_replay']['layer_ids']=c['route_replay']['layer_ids'][:1]
            c['workload']['decode_steps']=1
        wave=make_wave(c,[0,1,2,3],0,0,1,Replay(c,ROOT))
        target=next(m for m in wave['messages'] if m['phase']=='dispatch' and m['bytes']>=512)
        binding=next(s for s in c['transport']['streams'] if all(s[k]==target[k] for k in ('src','dst','plane')))
        if name.startswith('tail-'):
            total=sum(m['bytes'] for m in wave['messages'] if all(m[k]==binding[k] for k in ('src','dst','plane')))
            require(total%p['payload_bytes']==0,'tail case requires exact final packet boundary')
            p['faults']=[dict(node=target['src'] if name=='tail-ack' else target['dst'],port=0,
                message_id=binding['id'],kind='ack' if name=='tail-ack' else 'data',
                sequence=total if name=='tail-ack' else total-p['payload_bytes'],
                start_ns=0,end_ns=5000000,count=1)]
        else:
            p['data_loss']=dict(kind='periodic_destination_data/1',numerator=1,denominator=1,offset=0,
                start_ns=0,end_ns=5000000,nodes=[target['dst']])
        config_path=output/(name+'-service.json');protocol_path=output/(name+'-rdma.json')
        save(config_path,c);save(protocol_path,p)
        cli(name,['rdma-run','--config',config_path,'--topology',output/'fabric','--protocol',protocol_path,
            '--backend',backend,'--protoc',protoc,'--output',output/name],success=name.startswith('tail-'))
        d=output/name
        require((d/'result.json').is_file(),'no native result: infrastructure/control failure, not expected recovery exhaustion')
        r=json.loads((d/'result.json').read_bytes());f=json.loads((d/'frontend.json').read_bytes())
        native=r['service_transport']['native'];recovery=native['recovery'];terminal=service_terminal(r,f)
        require(f['transcript'] and f['transcript'][0]['reply']['submissions'],'task never reached first COMMIT')
        require(recovery['complete'] and not recovery['omitted_events'],'incomplete recovery evidence')
        if name.startswith('tail-'):
            state=next(s for s in recovery['states'] if s['stream']==binding['id'])
            stream=next(s for s in native['streams'] if s['id']==binding['id'])
            require(r['passed'] and native['closed'] and r['fault_drops']==1 and state['timeout_expiries']>=1,
                    'tail fault must trigger a real timeout and finish once')
            require(stream['published_bytes']==total and stream['source_payload_bytes']>total,
                    'tail connection has later work or no actual retransmitted payload')
            last=max((w for w in native['work'] if w['stream']==binding['id']),key=lambda w:w['end'])
            require(last['end']==total and last['accepted_ns']<=last['acked_ns'],'final work did not finish')
            if name=='tail-ack':require(last['acked_ns']>last['accepted_ns']+c['transport']['transport_recovery']['initial_rto_ns'],
                                      'tail ACK case did not wait for retransmission feedback')
            extra=dict(target_stream=binding['id'],terminal_sequence=total,timeout_expiries=state['timeout_expiries'],
                retransmitted_payload_bytes=stream['source_payload_bytes']-total,last_work=last)
        else:
            require(not r['passed'] and r['failure']['reason']=='recovery_retry_exhausted' and recovery['failed'],
                    'blackhole did not stop on actual retry exhaustion')
            require(terminal['application_finished_ns'] is None and terminal['network_drained_ns'] is None,
                    'failed service has a successful completion time')
            extra=dict(failure=r['failure'])
        cli(name+'-arrivals',['rdma-arrivals','--run',d,'--graph','g0.w0','--layer','0','--phase','dispatch',
            '--output',output/(name+'-arrivals')])
        arrival=json.loads((output/(name+'-arrivals/summary.json')).read_bytes())
        cases.append(dict(name=name,condition_verified=True,native_passed=r['passed'],
            packet_events=len(r['packet_capture']['events']),accepted_bytes=arrival['accepted_bytes'],
            **terminal,**extra))
        save(output/'cases.json',cases)
    # Actual sparse SimAI/Vidur frontend -> ASTRA -> the same persistent native service.
    from moe_ub_cosim.simai_service import validate
    base=ROOT/'examples/simai_service';c=json.loads((base/'service.json').read_bytes())
    p=json.loads((base/'rdma.json').read_bytes());p.pop('recovery',None)
    p['packet_capture']=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=100000)
    p['limits'].update(rss_bytes=1073741824,address_bytes=2147483648,artifact_bytes=67108864)
    t,_=generate(json.loads((base/'topology.json').read_bytes()))
    _,planes,replay=validate(c,t,p,base);pairs=set()
    for step in range(c['service']['workload']['decode_steps']):
        for index,group in enumerate(c['service']['parallel']['groups']):
            wave=make_wave(c['service'],group,index,step,planes,replay)
            pairs.update(tuple(m[k] for k in ('src','dst','plane')) for m in wave['messages'])
    ports={}
    for edge in t['links']:
        for node,port,peer in ((edge['src'],edge['src_port'],edge['dst']),(edge['dst'],edge['dst_port'],edge['src'])):
            if t['nodes'][node]['kind']=='gpu':ports[node,t['nodes'][peer]['plane']]=port
    transport=deepcopy(original['transport']);transport['streams']=[]
    for identity,(src,dst,plane) in enumerate(sorted(pairs)):
        transport['streams'].append(dict(id=identity,src=src,dst=dst,plane=plane,wire_port=21000+identity,
            source_nic=ports[src,plane],destination_nic=ports[dst,plane],queue_depth=8))
    transport['limits']['max_streams']=len(pairs)
    transport['transport_recovery'].update(receive_buffer_bytes=65536,inflight_bytes=32768)
    c['service']['transport']=transport
    for request in c['routes']['requests']:request['file']['path']=str((base/request['file']['path']).resolve())
    save(output/'simai-service.json',c);save(output/'simai-rdma.json',p)
    cli('simai-topology',['topology','--config',base/'topology.json','--output',output/'simai-fabric'])
    cli('simai',['simai-run','--config',output/'simai-service.json','--topology',output/'simai-fabric',
        '--protocol',output/'simai-rdma.json','--runtime',runtime,'--backend',backend,'--protoc',protoc,'--output',output/'simai'])
    verdict=json.loads((output/'simai/verification.json').read_bytes());cases.append(dict(name='simai',condition_verified=True,**verdict))
    report=dict(schema_version='moe-astra-stream-completion/1',passed=True,new_network_runs=4,cases=cases,commands=commands,
        binary_sha256=digest(backend),source_checked_synthetic=True,hardware_calibrated=False,
        host_seconds=time.monotonic()-started,scope='functional acceptance after control isolation; not algorithm gains')
    save(output/'verification.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('backend','runtime','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--protoc',default='protoc')
    args=parser.parse_args();print(json.dumps(check(args.backend.resolve(),args.runtime.resolve(),args.output,args.protoc)))
