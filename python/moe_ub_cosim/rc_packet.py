"""Public, bounded one-QP operation-packet network probe; no MRT1 dependency."""
import os
from pathlib import Path
import shutil

from .scaleout_topology import fields, integer, require
from .scaleout_rdma import checked_json, validate_probe, monitored, save, digest, ROOT
from .scaleout_limits import limits


def validate(t, protocol, work):
    fields(work, 'schema_version src dst source_nic destination_nic plane mtu rto_ns memory_hex operations loss', 'RC probe')
    require(work['schema_version'] == 'moe-rc-packet-probe/1', 'unsupported RC schema')
    require(not set(protocol).intersection(('faults','drop_once','recovery','admission','link_failures','local_failover')),
            'RC probe excludes legacy faults/recovery/admission')
    require(not t.get('intra_host', {}).get('enabled'), 'RC probe excludes local bypass')
    for name in ('src','dst','source_nic','destination_nic','plane'):
        integer(work[name], name, 0, 65535)
    require(type(work['mtu']) is int and work['mtu'] in (256,512,1024,2048,4096), 'illegal RC MTU')
    require(protocol['payload_bytes'] == work['mtu'], 'declare identical RC/protocol payload MTU')
    integer(work['rto_ns'], 'rto_ns', 1, limits(protocol)['max_sim_ns']//5)

    def unhex(s, cap):
        require(isinstance(s,str) and 0 < len(s) <= cap*2 and len(s)%2 == 0 and
                all(c in '0123456789abcdef' for c in s), 'invalid bounded lowercase hex')
        return bytes.fromhex(s)

    memory=unhex(work['memory_hex'], 65536)
    require(len(memory) >= 8, 'RC memory must contain at least 8 bytes')
    require(isinstance(work['operations'],list) and 1 <= len(work['operations']) <= 64, 'RC operation count limit')
    total=0;psn=0;packets=[]
    for op in work['operations']:
        kind=op.get('kind') if isinstance(op,dict) else None
        require(kind in ('write','fetch_add'), 'unsupported RC operation')
        fields(op,'kind address '+('data_hex' if kind=='write' else 'addend'),'RC operation')
        integer(op['address'],'address',0,len(memory)-1)
        if kind=='write':
            data=unhex(op['data_hex'],65536);total+=len(data)
            count=(len(data)+work['mtu']-1)//work['mtu']
            for i in range(count):
                packets.append((work['dst'],10 if count==1 else 6 if i==0 else 8 if i==count-1 else 7,psn+i))
            packets.append((work['src'],17,psn+count-1))
            require(op['address']+len(data) <= len(memory), 'WRITE outside registered range')
        else:
            integer(op['addend'],'addend',0,2**64-1);count=1
            require(op['address']%8==0 and op['address']+8<=len(memory), 'invalid atomic registered range')
            packets.extend(((work['dst'],20,psn),(work['src'],18,psn)))
        psn+=count
    require(total<=65536 and psn<0xffffff, 'RC byte/PSN limit')
    if work['loss'] is not None:
        fields(work['loss'],'node opcode psn','RC receive loss')
        for name in ('node','opcode','psn'):
            integer(work['loss'][name],name,0,0xffffff)
        require(tuple(work['loss'][k] for k in ('node','opcode','psn')) in packets,
                'RC loss must identify an actual endpoint request or final response')
    # Existing graph/MMU/protocol checks do not interpret RC operation bytes.
    validate_probe(t,protocol,[dict(id=0,src=work['src'],dst=work['dst'],bytes=1,plane=work['plane'],ready_ns=0)],match_faults=False)
    # Address/path checks also run natively before QP creation.
    return dict(operations=len(work['operations']), request_packets=psn, write_bytes=total)


def probe(topology,protocol,config,backend,output):
    topology,protocol,config,backend,output=map(lambda p:Path(p).resolve(),(topology,protocol,config,backend,output))
    t=checked_json(topology/'topology.json',8388608);p=checked_json(protocol,65536);c=checked_json(config,524288)
    summary=validate(t,p,c)
    require(backend.is_file() and os.access(backend,os.X_OK),'pass rc_probe executable from rdma-build')
    require(shutil.disk_usage(output.parent).free>=8589934592,'run volume below 8GiB reserve')
    budget=limits(p)
    require(budget['wall_seconds']<=60 and budget['rss_bytes']<=1073741824 and
            budget['address_bytes']<=2147483648 and budget['artifact_bytes']<=67108864,
            'RC probe requires at most 60s/1GiB RSS/2GiB address/64MiB artifacts')
    output.mkdir(exist_ok=False)
    command=[backend,topology/'topology.json',protocol,config,output/'result.json']
    save(output/'inputs.json',dict(command=list(map(str,command)),summary=summary,
        hashes={str(f):digest(f) for f in (topology/'topology.json',protocol,config,backend)}))
    monitored(command,output/'execution',[output],runtime=budget)
    result=checked_json(output/'result.json',budget['result_json_bytes'])
    require(result.get('passed') and result['rc_reference']['drained'],'incomplete RC network execution')
    return dict(passed=True,output=str(output),finish_ns=result['finish_ns'],summary=summary)
