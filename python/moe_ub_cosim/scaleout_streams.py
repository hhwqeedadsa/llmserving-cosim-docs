"""Public bounded persistent native byte-write probe; not a modern GIN model."""
import os
from pathlib import Path

from .scaleout_rdma import checked_json, digest, limits, monitored, save, validate_probe, ROOT
from .native_identity import locked_native_commit
from .scaleout_topology import fields, integer, require


def stream_limits(config):
    schema=config.get('schema_version')
    require(schema in ('moe-rdma-stream-work/1','moe-rdma-stream-work/2','moe-rdma-stream-work/3','moe-rdma-stream-work/4'),'unsupported stream work schema')
    fields(config,'schema_version streams work'+(' limits' if not schema.endswith('/1') else '')
           +(' source_rate_capture' if 'source_rate_capture' in config else '')
           +(' dcqcn_reference' if 'dcqcn_reference' in config else '')
           +(' transport_recovery' if 'transport_recovery' in config else '')
           +(' local_model' if schema.endswith(('/3','/4')) else ''),'stream work root')
    if schema.endswith('/1'):
        return dict(max_streams=16,max_work=64)
    value=config['limits']
    scaled=schema.endswith('/4')
    fields(value,'max_streams max_work'+(' max_link_transmissions' if scaled else ''),'stream limits')
    integer(value['max_streams'],'max_streams',1,4096 if scaled else 128)
    integer(value['max_work'],'max_work',1,65536 if scaled else 8192)
    if scaled:integer(value['max_link_transmissions'],'max_link_transmissions',1,2000000)
    return dict(value)


def validate(topology, protocol, config, *, transaction_peer_copies=False):
    require(type(transaction_peer_copies) is bool, 'transaction peer mode must be boolean')
    if transaction_peer_copies:
        require(topology.get('schema_version') == 'moe-scaleout-physical-topology/2' and
                topology.get('intra_host', {}).get('enabled') is True and
                topology['intra_host'].get('kind') == 'nonblocking_chunked_crossbar',
                'transaction peer mode requires topology/2 enabled crossbar')
        local = topology['intra_host']
        fields(local, 'kind enabled rate_bps delay_ns chunk_bytes', 'transaction intra-host fabric')
        integer(local['rate_bps'], 'intra-host rate', 1, 10**15)
        integer(local['delay_ns'], 'intra-host delay', 0, 1000000)
        integer(local['chunk_bytes'], 'intra-host chunk', 64, 1048576)
    counts=stream_limits(config)
    budget=limits(protocol)
    recovery='transport_recovery' in config
    if recovery:
        c=config['transport_recovery']
        require(isinstance(c,dict) and c.get('mode') in ('go_back_n/1','selective_repeat/1'),'invalid persistent recovery mode')
        require('history' not in c,'persistent recovery retains per-connection evidence; history seeds unsupported')
        from .scaleout_recovery_config import validate as validate_recovery
        legacy=dict(c,mode='go_back_n')
        if c['mode']=='selective_repeat/1':
            require('dcqcn_reference' in config,'selective reference requires independent DCQCN reference')
            integer(c.get('receive_buffer_bytes'),'receive_buffer_bytes',protocol['payload_bytes'],2*1024*1024)
            integer(c.get('inflight_bytes'),'inflight_bytes',protocol['payload_bytes'],2*1024*1024)
            require(c['receive_buffer_bytes']>=c['inflight_bytes']+protocol['payload_bytes']-1,
                    'selective capacity must cover packet window overshoot')
            require(type(c.get('max_events')) is int and c['max_events']>0,'selective mode requires complete event records')
            del legacy['receive_buffer_bytes'];del legacy['inflight_bytes']
            if 'reordering' in c:
                r=c['reordering']
                fields(r,'mode wait_ns max_events','reordering')
                require(r['mode']=='nack_grace/1','invalid reordering mode')
                integer(c.get('initial_rto_ns'),'initial_rto_ns',1,budget['max_sim_ns'])
                integer(r['wait_ns'],'reordering wait_ns',1,min(c['initial_rto_ns'],1000000000))
                integer(r['max_events'],'reordering max_events',1,100000)
                del legacy['reordering']
        policy=legacy.pop('on_exhausted','stop_simulation')
        require(policy in ('stop_simulation','isolate_stream'),'invalid persistent on_exhausted policy')
        validate_recovery(legacy,budget['max_sim_ns'])
    local_mode=config['schema_version'].endswith(('/3','/4'))
    if local_mode:
        fields(config['local_model'],'mode duration_source','local model')
        require(config['local_model']==dict(mode='serial_per_gpu/1',duration_source='declared_ns'),
                'local model requires serial_per_gpu/1 and declared_ns')
    if 'dcqcn_reference' in config:
        reference=config['dcqcn_reference']
        fields(reference,'mode np_interval_ns alpha_interval_ns recovery_interval_ns recovery_bytes min_rate_bps '
               'additive_bps hyper_bps fast_recovery_rounds gain cnp_model_bytes max_events','DCQCN reference')
        require(reference['mode']=='figure7_reference/1','unknown DCQCN reference mode')
        for key in ('np_interval_ns','alpha_interval_ns','recovery_interval_ns'):
            integer(reference[key],key,1,budget['max_sim_ns'])
        require(reference['alpha_interval_ns']>reference['np_interval_ns'],'alpha interval must exceed NP interval')
        integer(reference['recovery_bytes'],'recovery_bytes',512,1073741824)
        integer(reference['fast_recovery_rounds'],'fast_recovery_rounds',1,100)
        integer(reference['cnp_model_bytes'],'cnp_model_bytes',30,512)
        integer(reference['max_events'],'reference max_events',1,100000)
        for key in ('min_rate_bps','additive_bps','hyper_bps'):
            integer(reference[key],key,1,1000000000000000)
        gain=reference['gain']
        require(type(gain) in (int,float) and 0<gain<=1,'reference gain must be numeric in (0,1]')
    if 'source_rate_capture' in config:
        capture=config['source_rate_capture']
        fields(capture,'start_ns end_ns max_events','source rate capture')
        integer(capture['start_ns'],'source rate start_ns',0,budget['max_sim_ns']-1)
        integer(capture['end_ns'],'source rate end_ns',capture['start_ns']+1,budget['max_sim_ns'])
        integer(capture['max_events'],'source rate max_events',1,100000)
    max_gpus=min(32,budget['max_gpus']) if config['schema_version'].endswith('/1') else budget['max_gpus']
    require(not any(k in protocol for k in ('admission','recovery','drop_once','lifecycle_capture')) and
            (recovery or 'faults' not in protocol),
            'stream probe excludes legacy admission/recovery/fault/lifecycle modes')
    require(transaction_peer_copies or not topology.get('intra_host',{}).get('enabled',False),
            'stream probe does not use intra-host bypass')
    require(isinstance(config['streams'],list) and 1<=len(config['streams'])<=counts['max_streams'],
            'stream count exceeds declared budget')
    physical_ports={}
    by_node={n['id']:n for n in topology['nodes']}
    for link in topology['links']:
        physical_ports[link['src'],link['src_port']]=link['dst']
        physical_ports[link['dst'],link['dst_port']]=link['src']
    ids,ports,checks=set(),set(),[]
    for s in config['streams']:
        fields(s,'id src dst plane wire_port source_nic destination_nic queue_depth','stream declaration')
        for k,lo,hi in [('id',0,65535),('src',0,max_gpus-1),('dst',0,max_gpus-1),('plane',0,15),
                        ('wire_port',10000,65535),('source_nic',1,1024),('destination_nic',1,1024),('queue_depth',1,4096)]:
            integer(s[k],k,lo,hi)
        require(s['queue_depth']&(s['queue_depth']-1)==0,'queue depth must be power of two')
        for endpoint,nic in ((s['src'],s['source_nic']),(s['dst'],s['destination_nic'])):
            peer=physical_ports.get((endpoint,nic))
            require(peer is not None and by_node[peer].get('plane')==s['plane'], 'stream NIC is absent or belongs to another plane')
        require(s['id'] not in ids and s['wire_port'] not in ports,'stream id or wire port repeated')
        ids.add(s['id']);ports.add(s['wire_port'])
        # These descriptors validate topology only and are NEVER simulated.
        checks.append(dict(id=s['id'],src=s['src'],dst=s['dst'],plane=s['plane'],bytes=1,ready_ns=0))
    validate_probe(topology,protocol,checks,match_faults=False,
                   max_probe_messages=counts['max_streams'] if config['schema_version'].endswith('/4') else 128)
    # Check both actual NIC attachments before output creation/MMU allocation;
    # native local-address bindings no longer require a singleton GPU route.
    from .scaleout_stream_routes import validate_nic_routes
    validate_nic_routes(topology,config['streams'])
    for rule in protocol.get('faults',[]):
        require(rule['message_id'] in ids,'fault names an unopened persistent stream')
        require(rule['sequence']<=64*2**20,'persistent fault sequence exceeds stream budget')
    require(isinstance(config['work'],list) and 1<=len(config['work'])<=counts['max_work'],
            'work count exceeds declared budget')
    seen,totals={}, {i:0 for i in ids}
    gpus={n['id'] for n in topology['nodes'] if n['kind']=='gpu'}
    for w in config['work']:
        kind=w.get('kind') if local_mode else 'write'
        require(kind in ('write','local'),'unknown work kind')
        fields(w,('id kind rank duration_ns after' if kind=='local' else 'id stream bytes after'+(' kind' if local_mode else '')),'stream work')
        integer(w['id'],'work id',0,65535)
        require(w['id'] not in seen,'repeated work id')
        if kind=='local':
            integer(w['rank'],'local rank',0,budget['max_gpus']-1)
            require(w['rank'] in gpus,'local rank is not a topology GPU')
            integer(w['duration_ns'],'local duration_ns',0,budget['max_sim_ns'])
        else:
            integer(w['stream'],'work stream',0,65535);integer(w['bytes'],'work bytes',1,1024*1024)
            require(w['stream'] in ids,'unknown stream')
        require(isinstance(w['after'],list) and len(w['after'])<=64,'invalid causal dependencies')
        deps=set()
        for d in w['after']:
            fields(d,'work event','stream dependency')
            integer(d['work'],'dependency work',0,65535)
            require(d['work'] in seen,'dependency must name an earlier work')
            require(d['event'] in (('completed',) if seen[d['work']]=='local' else ('accepted','acked')),
                    'dependency event does not match predecessor kind')
            key=(d['work'],d['event']);require(key not in deps,'repeated dependency');deps.add(key)
        seen[w['id']]=kind
        if kind=='write':
            totals[w['stream']]+=w['bytes']
            require(totals[w['stream']]<=64*1024*1024,'stream sequence budget exceeded')


def probe(topology, protocol, config, backend, output):
    topology,protocol,config,backend,output=(Path(p).resolve() for p in (topology,protocol,config,backend,output))
    t,c,w=(checked_json(p,n) for p,n in ((topology/'topology.json',8388608),(protocol,65536),(config,8388608)))
    validate(t,c,w)
    require(backend.is_file() and os.access(backend,os.X_OK),'pass the existing native stream-probe executable')
    budget=limits(c)
    if w['schema_version']!='moe-rdma-stream-work/4':
        budget.update(rss_bytes=min(2**30,budget['rss_bytes']),address_bytes=min(2*2**30,budget['address_bytes']))
    budget.update(wall_seconds=min(60,budget['wall_seconds']),artifact_bytes=min(64*2**20,budget['artifact_bytes']))
    native_commit=locked_native_commit(ROOT)
    output.mkdir(exist_ok=False)
    command=[backend,topology/'topology.json',protocol,config,output/'result.json']
    save(output/'inputs.json',dict(command=list(map(str,command)),native_commit=native_commit,
         native_commit_role='expected_source_checkout_not_binary_build_identity',
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
