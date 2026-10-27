"""Input-only route projection and ordinary per-link temporal estimates."""
from collections import Counter
import hashlib
import json
from pathlib import Path

from experiments.ecmp_baseline_audit.model import graph, paths, select

NAMES = ('900', '901', '905', '1003', '1004', '1007', '1008', '1011')
NETWORKS = ('core100', 'core25')
ALIGNMENTS = ('aligned', 'offset')
TOKEN_BYTES = 14336
PAYLOAD = 2000
DATA_HEADER = 2+20+8+6  # PPP, IPv4, UDP, SeqTs; IntHeader NONE
ACK_BYTES = 48  # native minimum Ethernet padding minus Ethernet plus PPP


def load(path): return json.loads(Path(path).read_bytes())
def save(path, value): Path(path).write_text(json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2)+'\n')
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def conditions():
    return [(n, a, p) for n in NETWORKS for a in ALIGNMENTS for p in range(8)]


def case_name(network, alignment, phase): return f'{network}-{alignment}-{phase}'


def sequence(routes, alignment, phase):
    return [[routes[r][(t+phase+(r if alignment == 'offset' else 0)) % 8]
             for r in range(8)] for t in range(8)]


def program(routes, alignment, phase, dcqcn):
    pairs = [(s,d) for s in range(8) for d in range(8) if s != d]
    pair_id = {p:i for i,p in enumerate(pairs)}
    streams = [dict(id=i, src=s, dst=d, plane=0, wire_port=21000+i,
                    source_nic=1, destination_nic=1, queue_depth=2) for i,(s,d) in enumerate(pairs)]
    work, metadata, rounds = [], [], []
    def add(meta, **node):
        node['id'] = len(work); work.append(node); metadata.append(dict(id=node['id'], **meta))
        return node['id']
    def dep(i, e): return dict(work=i, event=e)
    previous = None
    for step, choices in enumerate(sequence(routes, alignment, phase)):
        demands = [(src,dst) for src,token in enumerate(choices)
                   for dst in sorted({e//32 for e in token}-{src})]
        dispatch = {(s,d):add(dict(step=step, stage='dispatch', src=s, dst=d),
            kind='write', stream=pair_id[s,d], bytes=TOKEN_BYTES,
            after=[] if previous is None else [dep(previous,'completed')]) for s,d in demands}
        compute = {}
        for rank in range(8):
            dependencies = [dep(i,'accepted') for (s,d),i in dispatch.items() if d == rank]
            dependencies += [dep(i,'acked') for (s,d),i in dispatch.items() if s == rank]
            if not dependencies and previous is not None: dependencies = [dep(previous,'completed')]
            compute[rank] = add(dict(step=step, stage='expert', rank=rank), kind='local', rank=rank,
                                duration_ns=1000, after=dependencies)
        combine = {(d,s):add(dict(step=step, stage='combine', src=d, dst=s),
            kind='write', stream=pair_id[d,s], bytes=TOKEN_BYTES,
            after=[dep(compute[d],'completed')]) for s,d in demands}
        finished = []
        for rank in range(8):
            dependencies = [dep(i,'accepted') for (s,d),i in combine.items() if d == rank]
            dependencies += [dep(i,'acked') for (s,d),i in combine.items() if s == rank]
            dependencies.append(dep(compute[rank],'completed'))
            finished.append(add(dict(step=step, stage='rank_done', rank=rank), kind='local', rank=rank,
                                duration_ns=0, after=dependencies))
        previous = add(dict(step=step,stage='barrier'), kind='local', rank=0, duration_ns=0,
                       after=[dep(i,'completed') for i in finished])
        rounds.append(dict(step=step, dispatch=list(dispatch.values()), compute=list(compute.values()),
                           combine=list(combine.values()), finished=finished, barrier=previous))
    assert len(work) <= 1024
    return dict(schema_version='moe-rdma-stream-work/4',
        limits=dict(max_streams=56, max_work=1024, max_link_transmissions=250000),
        local_model=dict(mode='serial_per_gpu/1',duration_source='declared_ns'),
        dcqcn_reference=dcqcn, streams=streams, work=work), dict(work=metadata, rounds=rounds)


def connection_paths(topology, streams, seed):
    _,_,edges = graph(topology)
    extra = {}
    for link in topology['links']:
        extra[link['src'],link['src_port']] = extra[link['dst'],link['dst_port']] = link['delay_ns']
    result = {}
    for s in streams:
        forward, backward = [], []
        for src,dst,collection in ((s['src'],s['dst'],forward),(s['dst'],s['src'],backward)):
            chosen,_ = select(paths(topology,src,1,dst,1),0x0b000001+src*32,
                              0x0b000001+dst*32,s['wire_port'],seed)
            for e in chosen: collection.append(dict(e, delay_ns=extra[e['src'],e['port']]))
        result[s['id']] = dict(forward=forward, backward=backward)
    return result, edges


def stage_estimate(loads, route_map):
    """Plain serialization plus propagation, without queue or feedback fitting."""
    links, volume = {}, Counter()
    propagation = frame_serial = 0
    for sid,count in loads.items():
        if not count: continue
        route = route_map[sid]
        for direction,size in (('forward',TOKEN_BYTES+8*DATA_HEADER),('backward',8*ACK_BYTES)):
            path = route[direction]
            for e in path:
                key = e['src'],e['port']; links[key] = e; volume[key] += count*size
        propagation = max(propagation,sum(e['delay_ns'] for e in route['forward']+route['backward']))
        frame_serial = max(frame_serial,max((PAYLOAD+DATA_HEADER)*8e9/e['rate_bps'] for e in route['forward'])+
                           max(ACK_BYTES*8e9/e['rate_bps'] for e in route['backward']))
    service = max([0,*[n*8e9/links[k]['rate_bps'] for k,n in volume.items()]])
    return dict(service_ns=service, propagation_ns=propagation, frame_serial_ns=frame_serial,
                estimated_ns=service+propagation+frame_serial,
                loads=[dict(node=n,port=p,bytes=b) for (n,p),b in sorted(volume.items())])


def predict(work, meta, topology, seed):
    route_map,_ = connection_paths(topology,work['streams'],seed)
    by_id = {w['id']:w for w in work['work']}
    stages = []
    pooled = {name:Counter() for name in ('dispatch','combine')}
    for step in meta['rounds']:
        for name in ('dispatch','combine'):
            count = Counter(by_id[i]['stream'] for i in step[name]); pooled[name].update(count)
            stages.append(dict(step=step['step'],stage=name,**stage_estimate(count,route_map)))
    marginal = {name:stage_estimate({s:n/8 for s,n in values.items()},route_map)
                for name,values in pooled.items()}
    return dict(temporal_link_ns=8000+sum(s['estimated_ns'] for s in stages),
                marginal_link_ns=8000+8*sum(s['estimated_ns'] for s in marginal.values()),
                temporal_stages=stages,marginal_stages=marginal,
                routes={str(k):v for k,v in route_map.items()},
                truth_read=False,fitted_constants=False,hardware_calibrated=False)
