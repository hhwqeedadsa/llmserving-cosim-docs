"""Public bounded persistent native byte-write probe; not a modern GIN model."""
import os
from pathlib import Path

from .scaleout_rdma import checked_json, digest, limits, monitored, save, validate_probe, NATIVE_COMMIT
from .scaleout_topology import fields, integer, require


def stream_limits(config):
    schema=config.get('schema_version')
    require(schema in ('moe-rdma-stream-work/1','moe-rdma-stream-work/2'),'unsupported stream work schema')
    fields(config,'schema_version streams work'+(' limits' if schema.endswith('/2') else '')
           +(' source_rate_capture' if 'source_rate_capture' in config else ''),'stream work root')
    if schema.endswith('/1'):
        return dict(max_streams=16,max_work=64)
    value=config['limits']
    fields(value,'max_streams max_work','stream limits')
    integer(value['max_streams'],'max_streams',1,128)
    integer(value['max_work'],'max_work',1,8192)
    return dict(value)


def validate(topology, protocol, config):
    counts=stream_limits(config)
    budget=limits(protocol)
    if 'source_rate_capture' in config:
        capture=config['source_rate_capture']
        fields(capture,'start_ns end_ns max_events','source rate capture')
        integer(capture['start_ns'],'source rate start_ns',0,budget['max_sim_ns']-1)
        integer(capture['end_ns'],'source rate end_ns',capture['start_ns']+1,budget['max_sim_ns'])
        integer(capture['max_events'],'source rate max_events',1,100000)
    max_gpus=min(32,budget['max_gpus']) if config['schema_version'].endswith('/1') else budget['max_gpus']
    require(not any(k in protocol for k in ('admission','recovery','faults','drop_once','lifecycle_capture')),
            'stream probe excludes legacy admission/recovery/fault/lifecycle modes')
    require(not topology.get('intra_host',{}).get('enabled',False),'stream probe does not use intra-host bypass')
    require(isinstance(config['streams'],list) and 1<=len(config['streams'])<=counts['max_streams'],
            'stream count exceeds declared budget')
    ids,ports,checks=set(),set(),[]
    for s in config['streams']:
        fields(s,'id src dst plane wire_port source_nic destination_nic queue_depth','stream declaration')
        for k,lo,hi in [('id',0,65535),('src',0,max_gpus-1),('dst',0,max_gpus-1),('plane',0,15),
                        ('wire_port',10000,65535),('source_nic',1,1024),('destination_nic',1,1024),('queue_depth',1,4096)]:
            integer(s[k],k,lo,hi)
        require(s['queue_depth']&(s['queue_depth']-1)==0,'queue depth must be power of two')
        require(s['id'] not in ids and s['wire_port'] not in ports,'stream id or wire port repeated')
        ids.add(s['id']);ports.add(s['wire_port'])
        # These descriptors validate topology only and are NEVER simulated.
        checks.append(dict(id=s['id'],src=s['src'],dst=s['dst'],plane=s['plane'],bytes=1,ready_ns=0))
    validate_probe(topology,protocol,checks)
    require(isinstance(config['work'],list) and 1<=len(config['work'])<=counts['max_work'],
            'work count exceeds declared budget')
    seen,totals=set(),{i:0 for i in ids}
    for w in config['work']:
        fields(w,'id stream bytes after','stream work')
        integer(w['id'],'work id',0,65535);integer(w['stream'],'work stream',0,65535)
        integer(w['bytes'],'work bytes',1,1024*1024)
        require(w['id'] not in seen and w['stream'] in ids,'repeated work or unknown stream')
        require(isinstance(w['after'],list) and len(w['after'])<=64,'invalid causal dependencies')
        deps=set()
        for d in w['after']:
            fields(d,'work event','stream dependency')
            integer(d['work'],'dependency work',0,65535)
            require(d['work'] in seen and d['event'] in ('accepted','acked'),'dependency must name earlier accepted/acked work')
            key=(d['work'],d['event']);require(key not in deps,'repeated dependency');deps.add(key)
        seen.add(w['id']);totals[w['stream']]+=w['bytes']
        require(totals[w['stream']]<=64*1024*1024,'stream sequence budget exceeded')


def probe(topology, protocol, config, backend, output):
    topology,protocol,config,backend,output=(Path(p).resolve() for p in (topology,protocol,config,backend,output))
    t,c,w=(checked_json(p,n) for p,n in ((topology/'topology.json',8388608),(protocol,65536),(config,8388608)))
    validate(t,c,w)
    require(backend.is_file() and os.access(backend,os.X_OK),'pass the existing native stream-probe executable')
    budget=limits(c)
    budget.update(wall_seconds=min(60,budget['wall_seconds']),rss_bytes=min(2**30,budget['rss_bytes']),
                  address_bytes=min(2*2**30,budget['address_bytes']),artifact_bytes=min(64*2**20,budget['artifact_bytes']))
    output.mkdir(exist_ok=False)
    command=[backend,topology/'topology.json',protocol,config,output/'result.json']
    save(output/'inputs.json',dict(command=list(map(str,command)),native_commit=NATIVE_COMMIT,
         scope='persistent native byte-write probe, not GIN or GPU visibility',
         hashes={str(p):digest(p) for p in (topology/'topology.json',protocol,config,backend)}))
    monitored(command,output/'execution',[output],runtime=budget)
    result=checked_json(output/'result.json',budget['result_json_bytes'])
    transport=result['persistent_transport']
    require(result['passed'] and result['native_network_executed'] and result['drained'] and
            transport['physical_drained'] and transport['closed'] and
            result['completed_work']==len(w['work']),'persistent stream probe incomplete')
    return dict(passed=True,output=str(output),streams=len(w['streams']),completed_work=result['completed_work'],
                state='native_persistent_byte_writes_executed_not_GIN',hardware_calibrated=False)
