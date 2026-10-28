"""Concurrent RC operation packets on one native fabric; no MRT1 or GPU model."""
import os
from pathlib import Path
import shutil
import subprocess

from .scaleout_topology import fields, integer, require
from .scaleout_rdma import ROOT, checked_json, digest, monitored, save, validate_probe
from .scaleout_limits import limits


def unhex(value, cap):
    require(isinstance(value,str) and 0<len(value)<=2*cap and len(value)%2==0 and
            all(c in '0123456789abcdef' for c in value),'invalid bounded lowercase RC hex')
    return bytes.fromhex(value)


def validate(topology, protocol, work):
    fields(work,'schema_version mtu rto_ns memories connections loss','RC concurrent input')
    require(work['schema_version']=='moe-rc-concurrent-input/1','unsupported concurrent RC schema')
    require(not set(protocol).intersection(('faults','drop_once','recovery','admission','link_failures','local_failover')),
            'concurrent RC excludes legacy transport controls')
    require(not topology.get('intra_host',{}).get('enabled'),'concurrent RC excludes local bypass')
    require(type(work['mtu']) is int and work['mtu'] in (256,512,1024,2048,4096),'illegal RC MTU')
    require(protocol['payload_bytes']==work['mtu'],'RC/protocol MTU mismatch')
    integer(work['rto_ns'],'RC RTO',1,limits(protocol)['max_sim_ns']//5)
    require(isinstance(work['memories'],list) and 2<=len(work['memories'])<=8,'expected 2..8 RC registered GPUs')
    memories={}
    for row in work['memories']:
        fields(row,'gpu data_hex','RC registered memory')
        integer(row['gpu'],'registered GPU',0,65535)
        require(row['gpu'] not in memories,'duplicate RC registered GPU')
        data=unhex(row['data_hex'],65536)
        require(len(data)>=8,'RC registered region needs 8 bytes')
        memories[row['gpu']]=len(data)
    require(sum(memories.values())<=262144,'RC aggregate memory budget')
    gpus={row['id'] for row in topology['nodes'] if row['kind']=='gpu'}
    require(set(memories)<=gpus,'registered RC GPU outside topology')
    require(isinstance(work['connections'],list) and 2<=len(work['connections'])<=8,'expected 2..8 RC connections')
    ids=set();packets=set();messages=[];operations=write_bytes=request_packets=0
    for q in work['connections']:
        fields(q,'id src dst source_nic destination_nic plane operations','RC connection')
        integer(q['id'],'RC connection id',0,7)
        require(q['id'] not in ids,'duplicate RC connection id');ids.add(q['id'])
        for key in ('src','dst','source_nic','destination_nic','plane'):
            integer(q[key],key,0,65535)
        require(q['src']!=q['dst'] and q['src'] in memories and q['dst'] in memories,'RC endpoint memory missing')
        for gpu,nic in ((q['src'],q['source_nic']),(q['dst'],q['destination_nic'])):
            require(any(row['gpu']==gpu and row['gpu_port']==nic and row['plane']==q['plane']
                        for row in topology['nic_ports']),'RC NIC/plane binding absent from topology')
        require(isinstance(q['operations'],list) and 1<=len(q['operations'])<=64,'RC operation count')
        psn=0
        for op in q['operations']:
            kind=op.get('kind') if isinstance(op,dict) else None
            require(kind in ('write','fetch_add'),'unsupported RC operation')
            fields(op,'kind address '+('data_hex' if kind=='write' else 'addend'),'RC operation')
            integer(op['address'],'RC address',0,memories[q['dst']]-1)
            if kind=='write':
                data=unhex(op['data_hex'],65536);write_bytes+=len(data)
                require(op['address']+len(data)<=memories[q['dst']],'RC WRITE outside target region')
                count=(len(data)+work['mtu']-1)//work['mtu']
                for part in range(count):
                    opcode=10 if count==1 else 6 if part==0 else 8 if part==count-1 else 7
                    packets.add((q['id'],q['dst'],opcode,psn+part))
                packets.add((q['id'],q['src'],17,psn+count-1))
            else:
                integer(op['addend'],'RC addend',0,2**64-1);count=1
                require(op['address']%8==0 and op['address']+8<=memories[q['dst']],'RC atomic outside aligned target region')
                packets.update(((q['id'],q['dst'],20,psn),(q['id'],q['src'],18,psn)))
            psn+=count;operations+=1
        require(psn<0xffffff,'RC PSN budget')
        request_packets+=psn
        messages.append(dict(id=q['id'],src=q['src'],dst=q['dst'],bytes=1,plane=q['plane'],ready_ns=0))
    require(operations<=64 and write_bytes<=65536,'RC aggregate operation/WRITE budget')
    if work['loss'] is not None:
        fields(work['loss'],'connection node opcode psn','RC receive loss')
        for key in ('connection','node','opcode','psn'):
            integer(work['loss'][key],key,0,0xffffff)
        require(tuple(work['loss'][key] for key in ('connection','node','opcode','psn')) in packets,
                'RC loss must match an actual request/final feedback of the specified connection')
    validate_probe(topology,protocol,messages,match_faults=False)
    return dict(connections=len(ids),operations=operations,request_packets=request_packets,write_bytes=write_bytes,
                registered_bytes=sum(memories.values()),source_qpn_rule='2*id+1',destination_qpn_rule='2*id+2')


def build(runtime, identity, output):
    """Compile this driver only, with immutable pinned native libraries."""
    from .native_identity import verify_native_source
    from .native_compilation import inspect_commands
    runtime,identity,output=map(lambda p:Path(p).resolve(),(runtime,identity,output))
    require(not output.exists(),'RC build requires a new output directory')
    require(shutil.disk_usage(output.parent).free>=8589934592,'build volume below 8GiB reserve')
    native=verify_native_source(ROOT)
    provenance=checked_json(runtime/'provenance.json',2097152)
    original=checked_json(runtime/'backend.json',1048576)
    expected=checked_json(identity,1048576)
    require(provenance['native_commit']==original['native_commit']==expected['native_commit']==native,
            'RC prebuilt native version differs from current lock')
    compilation=inspect_commands(runtime/'cmake/compile_commands.json')
    # The prebuilt static Fabric and its exposed layout must match all recorded
    # old platform files. The new independent driver is the only new unit.
    for name,wanted in provenance['sources'].items():
        require((ROOT/name).is_file() and digest(ROOT/name)==wanted,'prebuilt platform source mismatch: '+name)
    libraries=expected['library_hashes']
    require(isinstance(libraries,dict) and len(libraries)==8 and
            all(name.startswith('libns3.42-') and name.endswith('.so') and '/' not in name for name in libraries),
            'expected exactly eight pinned native library hashes')
    paths=[runtime/'output/lib'/name for name in libraries]
    require(all(p.is_file() and digest(p)==libraries[p.name] for p in paths),'prebuilt native library hash mismatch')
    archive=runtime/'output/lib/libmoe-rdma-fabric.a'
    require(archive.is_file(),'prebuilt Fabric static library missing')
    if 'fabric_archive_sha256' in expected:
        require(digest(archive)==expected['fabric_archive_sha256'],'prebuilt Fabric archive hash mismatch')
    compiler=shutil.which('c++')
    require(compiler,'existing C++ compiler required; no installation attempted')
    output.mkdir()
    library_dir=output/'runtime/lib';library_dir.mkdir(parents=True)
    for source in paths+[archive]:
        shutil.copyfile(source,library_dir/source.name)
    source=ROOT/'backend/rdma/rc_concurrent_probe.cc'
    obj=output/'rc_concurrent.o';binary=output/'moe-rdma-rc-concurrent'
    command=[compiler,'-DNS3_BUILD_PROFILE_RELEASE','-D__LINUX__','-DNDEBUG','-Wall','-Wpedantic','-O3','-std=c++20',
             '-fPIE','-fno-semantic-interposition','-I'+str(runtime/'output/include'),
             '-I'+str(ROOT/'backend/rdma'),'-I'+str(ROOT/'third_party/LLMServingSim/astra-sim/extern/helper'),
             '-c',str(source),'-o',str(obj)]
    budget=dict(wall_seconds=120,rss_bytes=2147483648,address_bytes=4294967296,artifact_bytes=67108864)
    save(output/'build-inputs.json',dict(native_commit=native,original_runtime=str(runtime),identity_sha256=digest(identity),
        driver_sha256=digest(source),wire_sha256=digest(ROOT/'backend/rdma/rc_wire.hh'),
        fabric_archive_sha256=digest(archive),fabric_archive_identity_scope='current prebuilt intake; not an independent rebuild',
        library_hashes=libraries,old_compiler_options=compilation,command=command,ns3_builds=0))
    monitored(command,output/'compile',[output],runtime=budget)
    link=[compiler,str(obj),str(library_dir/archive.name),'-Wl,--no-as-needed',
          *map(str,[library_dir/p.name for p in paths]),'-ldl','-Wl,-rpath,$ORIGIN/runtime/lib','-o',str(binary)]
    monitored(link,output/'link',[output],runtime=dict(budget,wall_seconds=60))
    linked=subprocess.run(['ldd',str(binary)],capture_output=True,text=True,timeout=10,check=True).stdout
    (output/'ldd.txt').write_text(linked)
    actual={}
    for line in linked.splitlines():
        if 'libns3' in line and '=>' in line:
            name,right=line.split('=>',1);path=Path(right.split()[0]).resolve()
            require(path.parent==library_dir,'new driver loaded another native runtime')
            actual[name.strip()]=digest(path)
    require(actual==libraries and 'not found' not in linked,'native runtime loading differs from frozen hashes')
    save(output/'backend.json',dict(passed=True,backend=str(binary),native_commit=native,
        backend_sha256=digest(binary),library_hashes=libraries,driver_sha256=digest(source),ns3_builds=0,
        state='CPU driver compiled and linked; no network execution'))
    return dict(passed=True,backend=str(binary),output=str(output),ns3_builds=0)


def probe(topology,protocol,config,backend,output):
    topology,protocol,config,backend,output=map(lambda p:Path(p).resolve(),(topology,protocol,config,backend,output))
    t=checked_json(topology/'topology.json',8388608);p=checked_json(protocol,65536);c=checked_json(config,2097152)
    summary=validate(t,p,c)
    require(backend.is_file() and os.access(backend,os.X_OK),'pass executable from rc-fabric-build')
    budget=limits(p)
    require(budget['wall_seconds']<=60 and budget['rss_bytes']<=1073741824 and
            budget['address_bytes']<=2147483648 and budget['artifact_bytes']<=67108864,
            'RC concurrent probe requires at most 60s/1GiB RSS/2GiB address/64MiB artifacts')
    require(shutil.disk_usage(output.parent).free>=8589934592,'run volume below 8GiB reserve')
    output.mkdir(exist_ok=False)
    command=[backend,topology/'topology.json',protocol,config,output/'result.json']
    save(output/'inputs.json',dict(command=list(map(str,command)),summary=summary,
        hashes={str(path):digest(path) for path in (topology/'topology.json',protocol,config,backend)}))
    monitored(command,output/'execution',[output],runtime=budget)
    result=checked_json(output/'result.json',budget['result_json_bytes'])
    require(result.get('passed') and result['rc_concurrent']['drained'],'incomplete concurrent RC execution')
    return dict(passed=True,output=str(output),finish_ns=result['finish_ns'],summary=summary)
