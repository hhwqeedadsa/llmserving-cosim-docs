"""Actual legacy Fabric::Send descriptors, checked before a service COMMIT.

Topology/protocol resources must already be validated by the caller. Retained
identities mirror the native flow history; completion does not free a wire port.
This checker never assigns identities, splits work, or changes a network path.
"""
from collections import defaultdict

from .scaleout_limits import limits
from .scaleout_topology import fields, integer, require


class MessagePreflight:
    def __init__(self, topology, protocol, *, legacy_messages=True):
        self.budget=limits(protocol)
        self.legacy_messages=legacy_messages
        self.gpus={n['id']:n.get('server') for n in topology['nodes'] if n['kind']=='gpu'}
        self.local_enabled=topology.get('intra_host',{}).get('enabled',False)
        require(not self.local_enabled or all(type(s) is int and s>=0 for s in self.gpus.values()),
                'intra-host topology needs an explicit server for each GPU')
        admission=protocol.get('admission',{})
        self.byte_cap=admission.get('max_active_bytes_per_source',0)
        self.peer_cap=admission.get('per_destination',{}).get('max_bytes',0)
        self._ids=set();self._wire={}
        adjacency=defaultdict(set)
        for edge in topology['links']:
            adjacency[edge['src']].add(edge['dst']);adjacency[edge['dst']].add(edge['src'])
        self.attached={}
        planes={n['plane'] for n in topology['nodes'] if n['kind']!='gpu'}
        for plane in planes:
            remaining={n['id'] for n in topology['nodes'] if n['kind']!='gpu' and n['plane']==plane}
            components={};identity=0
            while remaining:
                first=remaining.pop();todo=[first];components[first]=identity
                while todo:
                    for peer in adjacency[todo.pop()]:
                        if peer in remaining:
                            remaining.remove(peer);components[peer]=identity;todo.append(peer)
                identity+=1
            self.attached[plane]={gpu:{components[p] for p in adjacency[gpu] if p in components}
                                  for gpu in self.gpus}

    def accept(self, messages):
        """Validate the whole batch atomically; preserve history across waves."""
        require(isinstance(messages,list), 'message descriptors must be a list')
        require(not self.legacy_messages or len(self._ids)+len(messages)<=self.budget['max_messages'],
                'cumulative native message budget exceeded')
        fresh=set();wire={}
        for m in messages:
            fields(m,'id src dst bytes plane ready_ns','probe message')
            identity=m['id'];integer(identity,'message id',0,2**31-1)
            require(identity not in self._ids and identity not in fresh,'duplicate message id')
            fresh.add(identity)
            for key in ('src','dst'):
                integer(m[key],key+' GPU',0,self.budget['max_gpus']-1)
            src,dst,plane=m['src'],m['dst'],m['plane']
            require(src in self.gpus and dst in self.gpus and src!=dst,'invalid endpoint pair')
            integer(m['bytes'],'message bytes',maximum=67108864)
            local=self.local_enabled and self.gpus[src]==self.gpus[dst]
            require(not self.byte_cap or local or m['bytes']<=self.byte_cap,
                    'message exceeds max_active_bytes_per_source; change explicit budget or logical messages')
            require(not self.peer_cap or local or m['bytes']<=self.peer_cap,
                    'message exceeds per_destination.max_bytes; change explicit share or logical messages')
            integer(plane,'message plane',0,15)
            integer(m['ready_ns'],'message ready_ns',0,self.budget['max_sim_ns']-1)
            attached=self.attached.get(plane,{})
            require(attached.get(src,set()) & attached.get(dst,set()),
                    'message has no packet-switch path in the selected rail/plane')
            if self.legacy_messages and not local:
                # Fabric::Address is injective in (GPU, plane) in current limits.
                # Fabric::Send uses this unchanged modulo mapping, including
                # when an earlier flow has completed. Local sends skip the map.
                key=(src,dst,plane,10000+identity%50000)
                prior=self._wire.get(key,wire.get(key))
                require(prior is None,
                        f'RDMA endpoint/port identity collision: messages {prior}/{identity}, '
                        f'src={src} dst={dst} plane={plane} port={key[-1]}')
                wire[key]=identity
        self._ids.update(fresh);self._wire.update(wire)

    def accept_wave(self, wave, now_ns):
        messages=[dict((key,m[key]) for key in ('id','src','dst','bytes','plane'))
                  for m in wave['messages']]
        for m in messages:m['ready_ns']=now_ns
        self.accept(messages)
