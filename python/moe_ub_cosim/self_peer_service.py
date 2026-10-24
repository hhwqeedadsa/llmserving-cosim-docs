"""Explicit functional self-provider service; no wire/CQ or hardware claim."""
from .scaleout_topology import fields,integer,require


def validate(topology,protocol,config):
    connections=config['connections'];by_id={c['id']:c for c in connections}
    selves=[c for c in connections if c.get('src')==c.get('dst') or 'self_service' in c]
    enabled='self_peer_model' in config
    require(bool(selves)==enabled, 'self-peer connections require explicit self_peer_model and vice versa')
    if not enabled:return
    require(config['schema_version']=='moe-rdma-transactions/5' and 'buffer_model' not in config,
            'self-peer service requires v5 without bounded copied buffers')
    require(config['self_peer_model']==dict(mode='declared_service_fifo/1'), 'invalid self-peer model')
    require(len(selves)<len(connections), 'self-peer service currently requires at least one remote connection')
    nodes={n['id']:n for n in topology['nodes']}
    ports={(e[side],e[side+'_port']):e['dst' if side=='src' else 'src'] for e in topology['links'] for side in ('src','dst')}
    for c in selves:
        fields(c,'id src dst plane source_nic destination_nic queue_depth self_service','self-peer connection')
        require(c['src']==c['dst'] and c['source_nic']==c['destination_nic'], 'self-peer owner/NIC must match')
        integer(c['plane'],'self-peer plane',0,15)
        integer(c['source_nic'],'self-peer NIC',1,1024)
        require(c['src'] in nodes and nodes[c['src']]['kind']=='gpu', 'self-peer owner is not a GPU')
        peer=ports.get((c['src'],c['source_nic']))
        require(peer in nodes and nodes[peer].get('plane')==c['plane'], 'self-peer NIC absent or on another plane')
        fields(c['self_service'],'apply_ns complete_ns','self-peer service')
        maximum=protocol.get('limits',{}).get('max_sim_ns',5000000)
        integer(c['self_service']['apply_ns'],'self-peer apply_ns',0,maximum)
        integer(c['self_service']['complete_ns'],'self-peer complete_ns',c['self_service']['apply_ns'],maximum)
    specs={o['id']:o for o in config['operations']}
    for op in config['operations']:
        for d in op.get('after',[]):
            parent=specs.get(d.get('operation'),{})
            connection=by_id.get(parent.get('connection'),{})
            require(d.get('event')!='request_acked' or 'self_service' not in connection,
                    'self-peer service has no network request ACK; depend on applied/completed')
