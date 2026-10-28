"""Equivalent all-pairs reachability using switch-only connected components."""
from collections import defaultdict

from .scaleout_rdma import validate_probe
from .scaleout_topology import require


def validate_groups(topology,protocol,groups):
    nodes=topology['nodes']
    planes=sorted({n['plane'] for n in nodes if n['kind']!='gpu'})
    adjacency=defaultdict(set)
    for edge in topology['links']:
        adjacency[edge['src']].add(edge['dst']);adjacency[edge['dst']].add(edge['src'])
    # Retain the complete native resource/port/MMU/limit validation. No fault
    # matching is performed, exactly as in the previous all-pairs preflight.
    for plane in planes:
        validate_probe(topology,protocol,[dict(id=0,src=groups[0][0],dst=groups[0][1],
            bytes=1,plane=plane,ready_ns=0)],match_faults=False)
        remaining={n['id'] for n in nodes if n['kind']!='gpu' and n['plane']==plane}
        components={};identity=0
        while remaining:
            first=next(iter(remaining));remaining.remove(first);todo=[first];components[first]=identity
            while todo:
                for peer in adjacency[todo.pop()]:
                    if peer in remaining:
                        remaining.remove(peer);components[peer]=identity;todo.append(peer)
            identity+=1
        attached={n['id']:{components[p] for p in adjacency[n['id']] if p in components}
                  for n in nodes if n['kind']=='gpu'}
        for group in groups:
            for i,src in enumerate(group):
                for dst in group[i+1:]:
                    require(attached[src]&attached[dst],
                            f'EP pair {src}/{dst} has no switch-only path in plane {plane}')
    return planes
