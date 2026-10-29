"""Bounded real frontend/ASTRA/native acceptance; each network condition once.

Uses public CLI and public saved-result readers. This is a functionality check,
not a recovery-optimization experiment or an inference performance benchmark.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from moe_ub_cosim.scaleout_rdma import ROOT,digest,save
from moe_ub_cosim.scaleout_topology import require,generate
from moe_ub_cosim.scaleout_service import make_wave
from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.service_terminal import service_terminal


def check(backend,runtime,reference,output,protoc):
    output=output.resolve();output.mkdir(parents=True,exist_ok=False)
    commands=[];cases=[];started=time.monotonic()
    def cli(name,args,success=True):
        require(time.monotonic()-started<600,'acceptance host budget exhausted')
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,args)]
        with (output/(name+'.stdout')).open('x') as log,(output/(name+'.stderr')).open('x') as err:
            result=subprocess.run(command,cwd=ROOT,stdout=log,stderr=err,timeout=120)
        commands.append(dict(name=name,command=command,exit_code=result.returncode,cwd=str(ROOT)))
        save(output/'commands.json',commands)
        require((result.returncode==0)==success,'unexpected public command outcome: '+name)
    folder=ROOT/'examples/astra_stream_service'
    s=json.loads((folder/'service.json').read_bytes());p=json.loads((folder/'rdma.json').read_bytes())
    cli('topology',['topology','--config',folder/'topology.json','--output',output/'fabric'])
    # Compatibility uses the same original example and checks numeric native
    # results against an explicitly supplied old run, never as required input.
    base=ROOT/'examples/scaleout_route_replay'
    cli('legacy-topology',['topology','--config',base/'topology.json','--output',output/'legacy-fabric'])
    cli('legacy',['rdma-run','--config',base/'service.json','--topology',output/'legacy-fabric','--protocol',base/'rdma.json',
                  '--backend',backend,'--protoc',protoc,'--output',output/'legacy'])
    legacy=json.loads((output/'legacy/result.json').read_bytes())
    compatibility=None
    if reference:
        old=json.loads((reference/'result.json').read_bytes())
        compatibility={k:old[k]==legacy[k] for k in old if k!='loaded_libraries'}
        require(all(compatibility.values()),'legacy native semantics changed')
    save(output/'legacy-compatibility.json',dict(compared=bool(reference),fields=compatibility))
    cases.append(dict(name='legacy',passed=True,finish_ns=legacy['finish_ns']))
    physical=json.loads((output/'fabric/topology.json').read_bytes())
    wave=make_wave(s,[0,1,2,3],0,0,1,Replay(s,ROOT))
    target=next(m for m in wave['messages'] if m['phase']=='dispatch' and m['bytes']>=512)
    binding=next(v for v in s['transport']['streams'] if all(v[k]==target[k] for k in ('src','dst','plane')))
    for name in ('normal','data-loss','ack-loss','two-groups','blackhole'):
        c,protocol=deepcopy(s),deepcopy(p)
        if name in ('data-loss','ack-loss'):
            protocol['faults']=[dict(node=target['dst'] if name=='data-loss' else target['src'],port=0,
                message_id=binding['id'],kind='data' if name=='data-loss' else 'ack',
                sequence=256 if name=='data-loss' else target['bytes'],start_ns=0,end_ns=5000000,count=1)]
        if name=='two-groups':c['parallel']['groups']=[[0,1],[2,3]]
        if name=='blackhole':
            protocol['data_loss']=dict(kind='periodic_destination_data/1',numerator=1,denominator=1,offset=0,
                                      start_ns=0,end_ns=5000000,nodes=[target['dst']])
        config_path=output/(name+'-service.json');protocol_path=output/(name+'-rdma.json')
        save(config_path,c);save(protocol_path,protocol)
        cli(name,['rdma-run','--config',config_path,'--topology',output/'fabric','--protocol',protocol_path,
                  '--backend',backend,'--protoc',protoc,'--output',output/name],success=name!='blackhole')
        d=output/name
        require((d/'result.json').is_file(),'no native result: infrastructure/control failure, not expected recovery exhaustion')
        r=json.loads((d/'result.json').read_bytes());f=json.loads((d/'frontend.json').read_bytes())
        if name=='blackhole':
            require(r['passed'] is False and r['failure']['reason']=='recovery_retry_exhausted',
                    'blackhole must fail on real source retry exhaustion')
            require(service_terminal(r,f)['application_finished_ns'] is None,'failed service has successful time')
        else:
            require(r['passed'] and r['service_transport']['native']['closed'],'persistent service did not close')
            if name in ('data-loss','ack-loss'):require(r['fault_drops']==1,'target fault did not occur once')
        cli(name+'-arrivals',['rdma-arrivals','--run',d,'--graph','g0.w0','--layer','0',
                              '--phase','dispatch','--output',output/(name+'-arrivals')])
        a=json.loads((output/(name+'-arrivals/summary.json')).read_bytes())
        cases.append(dict(name=name,passed=r['passed'],failure=r.get('failure'),
            messages=len(r['service_transport']['messages']),registered_messages=r['service_transport']['registered_messages'],
            native_streams=len(r['service_transport']['native']['streams']),
            packet_events=len(r['packet_capture']['events']),fault_drops=r['fault_drops'],
            arrival_bytes=a['accepted_bytes'],slice=a['slice'],**service_terminal(r,f)))
    # Sparse real SimAI scheduling uses the existing source-checked synthetic
    # example. Only NIC bindings are compiled; scheduling remains in ASTRA.
    from moe_ub_cosim.simai_service import validate
    base=ROOT/'examples/simai_service';config=json.loads((base/'service.json').read_bytes())
    protocol=json.loads((base/'rdma.json').read_bytes());protocol.pop('recovery',None)
    protocol['packet_capture']=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=100000)
    protocol['limits'].update(rss_bytes=1073741824,address_bytes=2147483648,artifact_bytes=67108864)
    topology,_=generate(json.loads((base/'topology.json').read_bytes()))
    _,planes,replay=validate(config,topology,protocol,base)
    pairs=set()
    for step in range(config['service']['workload']['decode_steps']):
        for index,group in enumerate(config['service']['parallel']['groups']):
            w=make_wave(config['service'],group,index,step,planes,replay)
            pairs.update(tuple(m[k] for k in ('src','dst','plane')) for m in w['messages'])
    ports={}
    for edge in topology['links']:
        for node,port,peer in ((edge['src'],edge['src_port'],edge['dst']),(edge['dst'],edge['dst_port'],edge['src'])):
            if topology['nodes'][node]['kind']=='gpu':ports[node,topology['nodes'][peer]['plane']]=port
    transport=deepcopy(s['transport']);transport['streams']=[]
    for identity,(src,dst,plane) in enumerate(sorted(pairs)):
        transport['streams'].append(dict(id=identity,src=src,dst=dst,plane=plane,wire_port=21000+identity,
            source_nic=ports[src,plane],destination_nic=ports[dst,plane],queue_depth=8))
    transport['limits']['max_streams']=len(pairs)
    transport['transport_recovery'].update(receive_buffer_bytes=65536,inflight_bytes=32768)
    config['service']['transport']=transport
    for request in config['routes']['requests']:request['file']['path']=str((base/request['file']['path']).resolve())
    save(output/'simai-service.json',config);save(output/'simai-rdma.json',protocol)
    cli('simai-topology',['topology','--config',base/'topology.json','--output',output/'simai-fabric'])
    cli('simai',['simai-run','--config',output/'simai-service.json','--topology',output/'simai-fabric',
                 '--protocol',output/'simai-rdma.json','--runtime',runtime,'--backend',backend,
                 '--protoc',protoc,'--output',output/'simai'])
    verdict=json.loads((output/'simai/verification.json').read_bytes())
    cases.append(dict(name='simai',**verdict))
    report=dict(schema_version='moe-astra-stream-service-acceptance/1',passed=True,new_network_runs=7,
                cases=cases,legacy_compatibility=compatibility,commands=commands,
                source_sha256={str(p.relative_to(ROOT)):digest(p) for p in (folder/'service.json',folder/'rdma.json',folder/'topology.json')},
                binary_sha256=digest(backend),host_seconds=time.monotonic()-started,
                scope='functional source-checked synthetic examples; not measured routing traffic, novel algorithm gains or full model calibration')
    save(output/'verification.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('backend','runtime','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--reference-legacy',type=Path)
    parser.add_argument('--protoc',default='protoc')
    args=parser.parse_args();check(args.backend.resolve(),args.runtime.resolve(),args.reference_legacy,args.output,args.protoc)
