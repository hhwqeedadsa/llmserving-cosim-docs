"""Pinned native MMU input arithmetic, before allocating ns-3 switch arrays."""
from .scaleout_topology import integer,require

UINT32_MAX=2**32-1
UINT64_MAX=2**64-1
PORT_RESERVE_BYTES=4096


def buffer_report(topology,protocol):
    """Initial reserves only; Fabric and locked SwitchMmu integer semantics."""
    require(topology.get('schema_version') in ('moe-scaleout-physical-topology/1','moe-scaleout-physical-topology/2'),'wrong physical graph schema')
    nodes,links=topology['nodes'],topology['links']
    require(isinstance(nodes,list) and nodes and isinstance(links,list),'physical nodes/links must be arrays')
    capacity=protocol['switch_buffer_bytes'];integer(capacity,'switch_buffer_bytes',1,UINT32_MAX)
    ports={};switches=[];seen_switch=False
    for identity,node in enumerate(nodes):
        integer(node['id'],'physical node id',0,len(nodes)-1)
        require(node['id']==identity,'node IDs must be contiguous')
        require(isinstance(node['kind'],str),'physical node kind must be text')
        gpu=node['kind']=='gpu';require(not gpu or not seen_switch,'GPU IDs must precede switches')
        if not gpu:switches.append(identity);seen_switch=True
        ports[identity]={}
    for link in links:
        src,dst=link['src'],link['dst']
        integer(src,'link source',0,len(nodes)-1);integer(dst,'link destination',0,len(nodes)-1)
        require(src<dst,'require upward-oriented physical edges')
        rate,delay=link['rate_bps'],link['delay_ns']
        integer(rate,'link rate_bps',1,UINT64_MAX);integer(delay,'link delay_ns',0,2**63-1)
        require(str(rate) in protocol['ecn_by_rate'],'missing ECN threshold for link rate')
        for host,side in ((src,'src'),(dst,'dst')):
            port=link[side+'_port'];integer(port,'physical port',1,1024)
            require(port not in ports[host],f'duplicate physical port {host}:{port}')
            intermediate=(rate//1000000)*delay;headroom=(intermediate//8000)*3
            ports[host][port]=dict(port=port,peer=dst if host==src else src,rate_bps=rate,delay_ns=delay,
                headroom_bytes=headroom,reserve_bytes=PORT_RESERVE_BYTES,
                arithmetic_valid=intermediate<=UINT64_MAX and headroom<=UINT32_MAX)
    for host,entries in ports.items():
        require(sorted(entries)==list(range(1,len(entries)+1)),f'non-contiguous physical ports on node {host}')
    rows=[]
    for host in switches:
        entries=list(ports[host].values());headroom=sum(p['headroom_bytes'] for p in entries)
        reserve=len(entries)*PORT_RESERVE_BYTES;required=headroom+reserve
        arithmetic=all(p['arithmetic_valid'] for p in entries) and headroom<=UINT32_MAX and reserve<=UINT32_MAX
        passed=arithmetic and required<capacity
        rows.append(dict(node=host,ports=sorted(entries,key=lambda p:p['port']),headroom_bytes=headroom,
            reserve_bytes=reserve,required_bytes=required,buffer_bytes=capacity,arithmetic_valid=arithmetic,
            shared_capacity_bytes=capacity-required if passed else None,
            initial_pfc_threshold_bytes=(capacity-required)>>3 if passed else None,passed=passed))
    return dict(schema_version='moe-rdma-fabric-buffers/1',passed=all(r['passed'] for r in rows),
        switches=rows,scope='pinned initial MMU reserves only; no dynamic losslessness, execution or hardware calibration guarantee',
        model=dict(headroom_formula='((rate_bps // 1000000) * delay_ns // 8000) * 3',reserve_bytes_per_physical_port=PORT_RESERVE_BYTES,pfc_a_shift=3))


def require_buffers(topology,protocol):
    report=buffer_report(topology,protocol)
    for row in report['switches']:
        if not row['passed']:
            main=sorted(row['ports'],key=lambda p:p['headroom_bytes'],reverse=True)[:3]
            details=', '.join(f"port {p['port']}={p['headroom_bytes']}B ({p['rate_bps']}bit/s, {p['delay_ns']}ns)" for p in main)
            require(False,f"switch {row['node']} headroom/reserve requires {row['required_bytes']}B "
                f"(headroom {row['headroom_bytes']}B + reserve {row['reserve_bytes']}B), "
                f"must be strictly below switch_buffer_bytes={row['buffer_bytes']}B; "
                f"native arithmetic valid={row['arithmetic_valid']}; {details}")
    return report


def preflight(topology,protocol,output,transactions=None):
    from pathlib import Path
    from .scaleout_rdma import checked_json,digest,save,validate_protocol
    topology,protocol,output=map(Path,(topology,protocol,output))
    t=checked_json(topology/'topology.json',8388608);p=checked_json(protocol,65536)
    validate_protocol(p);report=buffer_report(t,p)
    report['input_hashes']={str(topology/'topology.json'):digest(topology/'topology.json'),str(protocol):digest(protocol)}
    report['network_executed']=False;report['transactions_checked']=transactions is not None
    if transactions is not None:
        from .scaleout_transactions import validate
        transactions=Path(transactions);config=checked_json(transactions,4*2**20)
        report['input_hashes'][str(transactions)]=digest(transactions)
        try:validate(t,p,config,allow_no_remote=True)
        except ValueError as error:report['transaction_check']=dict(passed=False,error=str(error));report['passed']=False
        else:report['transaction_check']=dict(passed=True)
    require(not output.exists(),'preflight output already exists');save(output,report)
    return report
