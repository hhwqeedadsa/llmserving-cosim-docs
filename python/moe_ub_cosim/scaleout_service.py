"""Small ready-KV MoE service: pinned LLMServingSim -> Chakra/ASTRA -> native RDMA.

Synthetic or hash-checked source routes and declared compute costs are explicit inputs. This is
a network experiment with a finite MoE segment, not a calibrated full LLM.
"""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import sys

from .generate import _schema, write_message
from .scaleout_rdma import (ASTRA, ROOT, checked_json, digest, monitored, save,
                            validate_probe, validate_protocol)
from .scaleout_topology import fields, integer, require
from .scaleout_limits import limits
from .scaleout_llm import EXTRA_COSTS, capacity, validate_llm
from .host_timing import HostTiming
from .native_control import event as control_event
from .phase_completion import enabled as phase_enabled
from .token_transport import configuration as token_configuration, payload_sizes
from .execution_events import configuration as event_configuration, ExecutionObserver
from .contribution_binding import configuration as binding_configuration, ContributionBinding
from .source_ack_watchdog import configuration as watchdog_configuration, SourceAckObserver
from .inflight_helpers import configuration as helper_configuration, HelperObserver
from .whole_call import configuration as whole_configuration, consumer as whole_consumer


def validate(c, topology, rdma, replay=None):
    fields(c, 'schema_version model workload parallel compute'+(' route_replay' if 'route_replay' in c else '')+
           (' transport' if 'transport' in c else '')+(' phase_completion' if 'phase_completion' in c else '')+
           (' token_transport' if 'token_transport' in c else '')+
           (' execution_events' if 'execution_events' in c else '')+
           (' contribution_tracking' if 'contribution_tracking' in c else '')+
           (' source_ack_watchdog' if 'source_ack_watchdog' in c else '')+
           (' inflight_helpers' if 'inflight_helpers' in c else '')+
           (' whole_call_replication' if 'whole_call_replication' in c else ''), 'service')
    phase_enabled(c)
    token_configuration(c)
    event_option = event_configuration(c)
    require(not event_option or event_option['schema_version'] != 'moe-execution-events/2' or 'inflight_helpers' in c,
            'execution events/2 requires explicitly shared inflight helper compute')
    binding_configuration(c)
    watchdog_configuration(c)
    helper_configuration(c)
    whole_option=whole_configuration(c)
    extended=c['schema_version']=='moe-scaleout-service/2'
    require(c['schema_version'] in ('moe-scaleout-service/1','moe-scaleout-service/2'), 'unsupported service schema')
    budget=limits(rdma)
    if whole_option and whole_option['policy']=='delayed_complete_segment/1':
        from .whole_call import deadlines
        require(all(t<budget['max_sim_ns'] for t in deadlines(whole_option).values()),
                'whole replica deadline must precede simulation stop')
    m, w, p, cost = (c[k] for k in ('model', 'workload', 'parallel', 'compute'))
    fields(m, 'name hidden_size ffn_hidden_size experts top_k activation_bytes layers gpu_type'+(' llm' if extended else ''), 'model')
    require(isinstance(m['name'], str) and 0 < len(m['name']) <= 128, 'invalid model name')
    for k, lo, hi in [('hidden_size', 64, 8192), ('ffn_hidden_size', 64, 32768),
                     ('experts', 2, 256), ('top_k', 1, 8), ('activation_bytes', 1, 4), ('layers', 1, 128 if extended else 4)]:
        integer(m[k], k, lo, hi)
    require(m['top_k'] <= m['experts'], 'Top-K exceeds experts')
    require(m['gpu_type'] in ('A100', 'H100', 'A800', 'H800', 'NONE'), 'invalid descriptive GPU label')
    fields(w, 'batch_per_rank decode_steps context_tokens route_seed hotspot_fraction', 'workload')
    for k, lo, hi in [('batch_per_rank', 1, 16), ('decode_steps', 1 if 'route_replay' in c else 2, 128 if 'route_replay' in c else 4),
                     ('context_tokens', 16, 65536), ('route_seed', 1, 2**31-1)]:
        integer(w[k], k, lo, hi)
    require(type(w['hotspot_fraction']) in (int, float) and w['hotspot_fraction'] in (0, 0.5),
            'first service supports balanced or exactly 50% hot tokens')
    fields(p, 'groups', 'parallel')
    gpus = sorted(n['id'] for n in topology['nodes'] if n['kind'] == 'gpu')
    require(isinstance(p['groups'], list) and 1 <= len(p['groups']) <= budget['max_gpus']//2, 'invalid EP groups')
    flat = []
    for group in p['groups']:
        require(isinstance(group, list) and len(group)>=2, 'EP groups need at least two ranks')
        require(len(group)<=budget['max_gpus'],'EP group exceeds configured GPU budget')
        for rank in group:
            integer(rank, 'rank', 0, budget['max_gpus']-1)
        require(group == sorted(set(group)) and m['experts'] % len(group) == 0, 'EP group or expert placement invalid')
        flat.extend(group)
    require(sorted(flat) == gpus, 'EP groups must partition physical GPUs exactly')
    if phase_enabled(c):
        require(2*m['layers']*w['decode_steps']*len(flat)<=131072 and
                2*m['layers']*w['decode_steps']*len(p['groups'])<=65536,'phase completion event budget exceeded')
    fields(cost, 'source attention_ns_per_token pack_ns_per_token expert_ns_per_contribution merge_ns_per_token'+
           (' '+' '.join(k+'_ns_per_token' for k in EXTRA_COSTS) if extended else ''), 'compute')
    require(cost['source'] == 'declared_reference', 'this service requires declared, uncalibrated compute costs')
    for k in cost:
        if k != 'source':
            integer(cost[k], k, 0, 1000000)
    validate_protocol(rdma)
    if 'transport' in c:
        from .service_transport import validate as validate_transport
        validate_transport(c['transport'],topology,rdma)
    if 'route_replay' in c:
        require(w['hotspot_fraction']==0, 'synthetic hotspot cannot modify replay choices')
        if replay is None:
            from .scaleout_replay import Replay
            replay=Replay(c,ROOT)
    if extended:
        validate_llm(m,cost)
        for group in p['groups']:
            for rank in group if replay else group[:1]:
                capacity(c,len(group),replay.contexts(rank) if replay else None)
    if binding_configuration(c):
        from .contribution_binding import preflight
        preflight(c,replay)
    # Every pair still needs a common switch component; GPUs never bridge
    # components. Avoid rebuilding the whole graph for each 128-pair batch.
    from .scaleout_connectivity import validate_groups
    planes=validate_groups(topology,rdma,p['groups'])
    if helper_configuration(c):
        from .inflight_helpers import preflight
        preflight(c,replay,len(planes))
    return len(gpus), len(planes)


def routes(config, group, step, layer):
    """A balanced synthetic choice multiset; hot case changes expert ownership.

    IDs and permutations are deterministic for a condition/seed, independent of
    network completion order. Top-K choices within a token never repeat.
    """
    m, w = config['model'], config['workload']
    rng = random.Random(f"{w['route_seed']}:{group}:{step}:{layer}")
    tokens = [(rank, slot) for rank in group for slot in range(w['batch_per_rank'])]
    rng.shuffle(tokens)
    order = list(range(m['experts'])); rng.shuffle(order)
    hot_tokens = len(tokens)//2 if w['hotspot_fraction'] else 0
    result = []
    for i, (rank, slot) in enumerate(tokens):
        choices = [order[(i*m['top_k']+j) % m['experts']] for j in range(m['top_k'])]
        if w['hotspot_fraction']:
            pool = [e for e in choices if e != 0]
            pool += [e for e in order if e != 0 and e not in pool]
            choices = ([0] + pool[:m['top_k']-1]) if i < hot_tokens else pool[:m['top_k']]
        result.append(dict(rank=rank, slot=slot, experts=choices))
    return sorted(result, key=lambda x:(x['rank'],x['slot']))


def make_wave(config, group, group_index, step, planes, replay=None):
    if replay and config.get('route_replay',{}).get('request_layout')=='sparse_active_groups/1':
        require(group_index in replay.active_group_indices,'inactive EP group has no admitted graph')
    m, w, cost = config['model'], config['workload'], config['compute']
    graph = f'g{group_index}.w{step}'
    nodes = {r: [] for r in group}; next_node = {r: 1 for r in group}; previous = {r: [] for r in group}
    messages, route_rows = [], []
    full=config['schema_version']=='moe-scaleout-service/2'
    local_batch={r:len(replay.contexts(r)) if full and replay else w['batch_per_rank'] for r in group}
    gated=phase_enabled(config);gates=[]
    token_spec=token_configuration(config);sizes=payload_sizes(config)
    # Identity depends on logical work, never submission/completion order.
    pair_counts=[len(g)*(len(g)-1) for g in config['parallel']['groups']]
    pairs=len(group)*(len(group)-1)
    base=step*m['layers']*2*sum(pair_counts)+m['layers']*2*sum(pair_counts[:group_index])

    def node(rank, kind, name, deps, duration=0, message=None):
        identity = next_node[rank]; next_node[rank] += 1
        item = dict(id=identity, kind=kind, name=name, deps=sorted(set(deps)), duration_ns=duration)
        if message is not None:
            item['message'] = message
        nodes[rank].append(item)
        return identity

    def leg(layer, name, matrix, dependencies, leg_index, contributions=None):
        incoming = {r: [] for r in group};outgoing={r:[] for r in group}
        for src in group:
            for dst in group:
                count = matrix.get((src, dst), 0)
                if src == dst or not count:
                    continue
                si,di=group.index(src),group.index(dst)
                pair_index=si*(len(group)-1)+(di if di<si else di-1)
                identity = base + layer*2*pairs + leg_index*pairs + pair_index
                stable = f'{graph}:{layer}:{name}:{src}:{dst}:{w["route_seed"]}'.encode()
                plane = int.from_bytes(hashlib.sha256(stable).digest()[:8], 'big') % planes
                message = dict(id=identity, tag=identity, src=src, dst=dst, bytes=count*sizes[name],
                               plane=plane, layer=layer, phase=name, contributions=count)
                if token_spec:
                    message.update(contributions=contributions[src,dst],transport_tokens=count)
                messages.append(message)
                outgoing[src].append(node(src, 'SEND', f'L{layer}.{name}.{src}-{dst}.send', [dependencies[src]], message=message))
                incoming[dst].append(node(dst, 'RECV', f'L{layer}.{name}.{src}-{dst}.recv', [], message=message))
        return incoming,outgoing

    def completion(layer,phase,incoming,outgoing,producers):
        if not gated:return producers
        members={r:node(r,'GATE',f'L{layer}.{phase}.flush',incoming[r]+outgoing[r]+[producers[r]]) for r in group}
        gates.append(dict(id=f'L{layer}.{phase}',layer=layer,phase=phase,
                          members=[dict(rank=r,node=members[r]) for r in group]))
        return members

    if full:
        previous={r:[node(r,'COMP','embedding',[],cost['embedding_ns_per_token']*local_batch[r])] for r in group}
    for layer in range(m['layers']):
        selected = replay.routes(group,step,layer) if replay else routes(config, group, step, layer)
        source_tokens=Counter(token['rank'] for token in selected)
        matrix, expert_counts, received = Counter(), Counter(), Counter()
        token_units, reduction_extras = Counter(), Counter()
        for token in selected:
            destinations=Counter()
            for expert in token['experts']:
                dst = replay.owner(group,layer,expert) if replay else group[expert // (m['experts']//len(group))]
                matrix[token['rank'], dst] += 1; expert_counts[expert] += 1; received[dst] += 1
                destinations[dst]+=1
            for dst,count in destinations.items():
                token_units[token['rank'],dst]+=1;reduction_extras[dst]+=count-1
        route_rows.append(dict(layer=layer, tokens=selected, expert_counts=dict(expert_counts),
            contributions=[dict(src=s,dst=d,count=n) for (s,d),n in sorted(matrix.items())]))
        if replay:route_rows[-1]['source_layer_id']=replay.layers[layer]
        packed = {}
        for r in group:
            deps=previous[r]
            if full:
                deps=[node(r,'COMP',f'L{layer}.attention_norm',deps,cost['norm_ns_per_token']*local_batch[r])]
            attention_cost=cost['attention_ns_per_token']
            if full and not replay:attention_cost+=cost['attention_context_ns_per_token']*(w['context_tokens']+step)
            attention_duration=attention_cost*(local_batch[r] if full else source_tokens[r])
            if full and replay:attention_duration+=cost['attention_context_ns_per_token']*sum(x+step for x in replay.contexts(r))
            attention = node(r,'COMP',f'L{layer}.attention',deps,attention_duration)
            deps=[attention]
            if full:
                residual=node(r,'COMP',f'L{layer}.attention_residual',deps,cost['residual_ns_per_token']*local_batch[r])
                deps=[node(r,'COMP',f'L{layer}.ffn_norm',[residual],cost['norm_ns_per_token']*local_batch[r])]
            packed[r] = node(r,'COMP',f'L{layer}.pack',deps,cost['pack_ns_per_token']*(local_batch[r] if full else source_tokens[r]))
        incoming,outgoing = leg(layer,'dispatch',token_units if token_spec else matrix,packed,0,matrix)
        dispatched=completion(layer,'dispatch',incoming,outgoing,packed)
        experts = {r:node(r,'COMP',f'L{layer}.experts',incoming[r]+[packed[r],dispatched[r]],
                         cost['expert_ns_per_contribution']*received[r]) for r in group}
        combine = Counter({(dst,src):n for (src,dst),n in matrix.items()})
        combine_units=combine;combine_producers=experts
        if token_spec and token_spec['combine']=='rank_reduced/1':
            combine_units=Counter({(dst,src):n for (src,dst),n in token_units.items()})
            combine_producers={r:node(r,'COMP',f'L{layer}.combine_reduce',[experts[r]],
                reduction_extras[r]*token_spec['local_reduce_ns_per_extra']) for r in group}
        returned,outgoing = leg(layer,'combine',combine_units,combine_producers,1,combine)
        combined=completion(layer,'combine',returned,outgoing,combine_producers)
        previous = {r:[node(r,'COMP',f'L{layer}.merge',returned[r]+[experts[r],combined[r]],
                           cost['merge_ns_per_token']*(local_batch[r] if full else source_tokens[r]))] for r in group}
        if full:
            previous={r:[node(r,'COMP',f'L{layer}.ffn_residual',previous[r],cost['residual_ns_per_token']*local_batch[r])] for r in group}
    if full:
        for r in group:
            deps=previous[r]
            for stage in ('final_norm','lm_head','sampling'):
                key='norm' if stage=='final_norm' else stage
                deps=[node(r,'COMP',stage,deps,cost[key+'_ns_per_token']*local_batch[r])]
    require(all(m['id'] < 2**31 for m in messages), 'ASTRA message tag range exceeded')
    wave=dict(schema_version='moe-scaleout-wave/2' if gated else 'moe-scaleout-wave/1', graph_id=graph, rank_ids=group,
                group_index=group_index, decode_step=step, messages=messages, rank_nodes=nodes,
                route_source=replay.manifest['source']['kind'] if replay else 'synthetic_balanced_or_explicit_hotspot', routes=route_rows)
    if gated:wave['phase_completion']=dict(config['phase_completion'],gates=gates)
    if token_spec:wave['token_transport']=dict(token_spec,payload_bytes_per_token=sizes)
    if helper_configuration(config):
        from .inflight_helpers import candidates
        wave['shared_compute']=dict(mode='serial_per_gpu/1')
        wave['helper_candidates']=candidates(config,wave,replay)
    return wave


def export_wave(wave, directory, proto):
    directory.mkdir()
    save(directory/'wave.json', wave)
    for rank in wave['rank_ids']:
        with (directory/f'layer.{rank}.et').open('xb') as stream:
            write_message(stream, proto.GlobalMetadata(version=wave['schema_version']))
            for task in wave['rank_nodes'][rank]:
                n = proto.Node(id=task['id'], name=task['name'], data_deps=task['deps'])
                n.attr.add(name='is_cpu_op', bool_val=False)
                n.attr.add(name='num_ops', int64_val=0)
                n.attr.add(name='tensor_size', uint64_val=0)
                if task['kind'] in ('COMP','GATE'):
                    n.type = proto.COMP_NODE
                    n.duration_micros = 0 if 'shared_compute' in wave else task['duration_ns']  # External FIFO or locked ns.
                else:
                    msg = task['message']
                    n.type = proto.COMM_SEND_NODE if task['kind'] == 'SEND' else proto.COMM_RECV_NODE
                    n.attr.add(name='comm_size', int64_val=msg['bytes'])
                    for k in ('src','dst','tag'):
                        n.attr.add(name='comm_'+k, int32_val=msg[k])
                write_message(stream,n)


class Frontend:
    def __init__(self, config, ranks, planes, replay=None, timing=None, *, message_preflight):
        sys.path.insert(0, str(ROOT/'third_party/LLMServingSim'))
        from .configured_scheduler import ConfiguredScheduler
        from .lazy_uncached_pool import LazyUncachedBlockPool
        from serving.core.request import Request
        self.config, self.planes, self.replay = config, planes, replay
        self.message_preflight=message_preflight
        self.timing=timing or HostTiming()
        m, w = config['model'], config['workload']; block_size = 16
        self.schedulers, self.requests = {}, []
        self.groups = config['parallel']['groups']; self.active = {}; self.steps = [0]*len(self.groups)
        self.admitted_groups=(replay.active_group_indices if replay and replay.request_layout=='sparse_active_groups/1'
                              else list(range(len(self.groups))))
        whole=whole_configuration(config)
        self.delayed=whole is not None and whole['policy']=='delayed_complete_segment/1'
        self.held_groups={call['replica_group']:{} for call in whole['calls']} if self.delayed else {}
        self.skipped_groups=set();self.skipped_requests={};self.standby_releases=[]
        self.history = []; self.calls = 0
        # Explicit local KV reservation for a finite ready-KV segment. This is a
        # resource model declaration, not full-model A100 capacity validation.
        for rank in range(ranks):
            contexts=replay.contexts(rank) if replay else [w['context_tokens']]*w['batch_per_rank']
            if not contexts:
                continue  # Expert-only ranks still execute their complete ASTRA graph.
            identities=replay.request_ids(rank) if replay else [rank*w['batch_per_rank']+s for s in range(w['batch_per_rank'])]
            blocks=[math.ceil((context+w['decode_steps'])/block_size) for context in contexts]
            group = next(g for g in self.groups if rank in g)
            declared=capacity(config,len(group),contexts);weights=declared['weight_bytes_per_rank']
            kv_per_token=declared['kv_bytes_per_token']
            kv_blocks = sum(blocks)
            device_bytes=declared['device_bytes'] or (weights+kv_blocks*kv_per_token*block_size)
            available_blocks=(device_bytes-weights)//(kv_per_token*block_size)
            audit = dict(rank_id=rank,device_bytes=device_bytes,
                         weight_bytes=weights,non_kv_bytes=weights,kv_bytes_per_token=kv_per_token,
                         kv_block_bytes=kv_per_token*block_size,kv_capacity_blocks=available_blocks)
            model = dict(name=m['name'],max_position_embeddings=w['context_tokens']+w['decode_steps'])
            scheduler = ConfiguredScheduler(model,audit,block_size,w['batch_per_rank'],w['batch_per_rank'],w['batch_per_rank'],
                                            pool_factory=LazyUncachedBlockPool)
            for position,identity in enumerate(identities):
                context=contexts[position]
                request = Request(identity,m['name'],context,
                                  context+w['decode_steps'],0,rank,is_init=False)
                request.num_computed_tokens=context-1
                request.num_tokens_reached=context;request.recent_end=0
                require(scheduler.kv.allocate_slots(request, blocks[position]*block_size-request.num_computed_tokens) is not None,
                        'ready-KV reservation failed')
                index=self.groups.index(group)
                if index in self.held_groups:self.held_groups[index].setdefault(rank,[]).append(request)
                else:scheduler.add_decode(request)
                self.requests.append(request)
            self.schedulers[rank]=scheduler
        self.standby_initial_bytes=sum(self.schedulers[r].kv.npu_pool.used_bytes()
            for held in self.held_groups.values() for r in held)

    def decide(self, event, output, proto):
        now=event['now_ns']
        for completed in event['completed']:
            graph=completed['graph_id']; index,batches,history = self.active.pop(graph)
            require(completed['finished_ns']==now, 'completion clock mismatch')
            reserved=sum(self.schedulers[r].kv.npu_pool.used_bytes() for r in batches)
            for rank,batch in batches.items():
                prompt,generated,_=self.schedulers[rank].add_done(batch.batch_id+1,rank,now)
                require(prompt==0 and generated==len(batch.requests) and not self.schedulers[rank].inflight,
                        'LLMServingSim completion mismatch')
            history['finished_ns']=now; self.steps[index]+=1
            if self.delayed and any(call['replica_group']==index for call in self.config['whole_call_replication']['calls']):
                self.standby_releases.append(dict(group_index=index,time_ns=now,kind='executed_release',bytes=reserved))
        permitted=set(self.admitted_groups);wake=None
        if self.delayed:
            chosen,wake=self.whole.choose(event);permitted=set(chosen)
            for call in self.whole.calls.values():
                attempt=call['attempts']['replica'];index=attempt['group_index']
                if attempt['skipped_ns'] is not None and index not in self.skipped_groups:
                    held=self.held_groups.pop(index);reserved=sum(self.schedulers[r].kv.npu_pool.used_bytes() for r in held)
                    for rank,requests in held.items():
                        for request in requests:
                            self.schedulers[rank].kv.free(request);self.skipped_requests[request.id]=now
                    self.skipped_groups.add(index)
                    self.standby_releases.append(dict(group_index=index,time_ns=now,kind='skipped_release',bytes=reserved))
            for index in chosen:
                if index in self.held_groups:
                    for rank,requests in self.held_groups.pop(index).items():
                        for request in requests:self.schedulers[rank].add_decode(request)
        submissions=[]
        for index,group in enumerate(self.groups):
            if index not in permitted:continue
            if self.steps[index]>=self.config['workload']['decode_steps'] or any(i==index for i,_,_ in self.active.values()):
                continue
            require(all(r in event['available_rank_ids'] for r in group), 'backend group unavailable')
            batches={r:self.schedulers[r].schedule(now,r) for r in group if r in self.schedulers}; self.calls+=len(batches)
            for rank,batch in batches.items():
                expected=set(self.replay.request_ids(rank)) if self.replay else {
                    rank*self.config['workload']['batch_per_rank']+s for s in range(self.config['workload']['batch_per_rank'])}
                require(batch is not None and batch.num_prefill==0 and
                        len(batch.requests)==len(expected) and {q.id for q in batch.requests}==expected and
                        set(batch.scheduled_tokens.values())=={1}, 'invalid real Decode batch')
            with self.timing.span('wave_build'):
                wave=make_wave(self.config,group,index,self.steps[index],self.planes,self.replay)
            from .phase_completion import structure
            structure(wave,self.config)
            self.message_preflight.accept_wave(wave,now)
            directory=output/'graphs'/wave['graph_id']
            with self.timing.span('wave_export'):export_wave(wave,directory,proto)
            history=dict(graph_id=wave['graph_id'],group_index=index,step=self.steps[index],start_ns=now,
                batch_request_ids={str(r):[q.id for q in b.requests] for r,b in batches.items()},
                batch_ids={str(r):b.batch_id for r,b in batches.items()})
            self.history.append(history); self.active[wave['graph_id']]=(index,batches,history)
            submissions.append(str(directory))
        finished=not self.active and all(self.steps[i]==self.config['workload']['decode_steps'] or i in self.skipped_groups for i in self.admitted_groups)
        if finished:
            require(not self.held_groups and all(s.is_request_empty() and s.kv.npu_pool.used_bytes()==0 for s in self.schedulers.values()),
                    'final scheduler/KV state is not empty')
        reply=dict(type='COMMIT',decision_id=event['decision_id'],now_ns=now,submissions=submissions,service_finished=finished)
        if self.delayed:reply['wake_at_ns']=wake
        return reply


def worker(config_path, topology_path, protocol_path, backend, protoc, output, host_timing=False):
    timing=HostTiming(Path(output)/'worker_host.jsonl' if host_timing else None)
    complete=False
    try:
        with timing.span('worker_total'):
            _worker(config_path,topology_path,protocol_path,backend,protoc,output,timing,host_timing)
        complete=True
    finally:timing.close(complete)


def _worker(config_path,topology_path,protocol_path,backend,protoc,output,timing,host_timing):
    output=Path(output)
    with timing.span('input_read'):
        c=checked_json(config_path,1048576)
        t=checked_json(Path(topology_path)/'topology.json',8388608); rdma=checked_json(protocol_path,65536)
    from .scaleout_replay import Replay
    replay=Replay(c,output) if 'route_replay' in c else None
    with timing.span('input_validate'):ranks,planes=validate(c,t,rdma,replay)
    with timing.span('schema_setup'):proto,_=_schema(output,protoc)
    bootstrap=output/'bootstrap';bootstrap.mkdir();(output/'graphs').mkdir()
    for r in range(ranks):
        with (bootstrap/f'layer.{r}.et').open('xb') as stream:
            write_message(stream,proto.GlobalMetadata(version='moe-scaleout-bootstrap/1'))
    system=json.loads((ROOT/'examples/native_first_round/system.json').read_text())
    save(output/'system.json',system)
    from .service_transport import message_preflight,native_arguments
    with timing.span('frontend_init'):
        frontend=Frontend(c,ranks,planes,replay,timing,message_preflight=message_preflight(t,rdma,c))
    capacities=dict(groups=[dict(rank_ids=g,**capacity(c,len(g))) for g in c['parallel']['groups']]) if not replay else dict(
        per_rank=[dict(rank_id=r,**capacity(c,len(g),replay.contexts(r))) for g in c['parallel']['groups'] for r in g])
    save(output/'capacity.json',capacities)
    command=[backend,str(Path(topology_path)/'topology.json'),protocol_path,str(output/'system.json'),
             str(bootstrap/'layer'),str(output/'result.json')]
    if host_timing:command.append(str(output/'native_host.jsonl'))
    command.extend(native_arguments(c,output))
    transcript=[]; child=None
    observation=ExecutionObserver(event_configuration(c)) if event_configuration(c) else None
    binding=ContributionBinding(c,replay,observation) if binding_configuration(c) else None
    watchdog=SourceAckObserver(c,observation) if watchdog_configuration(c) else None
    helpers=HelperObserver(c,binding) if helper_configuration(c) else None
    whole=whole_consumer(c,replay) if whole_configuration(c) else None
    frontend.whole=whole
    event_file=(output/'execution_events.jsonl').open('x',buffering=1) if observation else None
    timeout_file=(output/'source_ack_timeouts.jsonl').open('x',buffering=1) if watchdog else None
    helper_file=(output/'helper_events.jsonl').open('x',buffering=1) if helpers else None
    try:
        # A host-budget kill must not lose already-read complete control lines
        # in Python's block buffer. This is not fsync/power-loss durability.
        with (output/'native.stderr.log').open('w') as errors,(output/'native.stdout.log').open('w',buffering=1) as logs:
            child=subprocess.Popen(command,cwd=output,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors,text=True)
            while True:
                with timing.span('native_output_wait'):line=child.stdout.readline()
                if not line:break
                logs.write(line)
                event=control_event(line,observations=observation is not None,source_ack_timeouts=watchdog is not None,helpers=helpers is not None)
                if event is None:continue
                if event['type']=='OBSERVE':
                    if binding:binding.accept(event)
                    else:observation.accept(event)
                    if watchdog:watchdog.observe(event)
                    if helpers:helpers.source_execution(event)
                    if whole:whole.accept(event)
                    event_file.write(json.dumps(event,separators=(',',':'))+'\n')
                    continue
                if event['type']=='SOURCE_ACK_TIMEOUT':
                    watchdog.accept(event)
                    if helpers:helpers.source_notice(event)
                    timeout_file.write(json.dumps(event,separators=(',',':'))+'\n')
                    continue
                if event['type']=='HELPER_DECIDE':
                    reply=helpers.decide(event)
                    child.stdin.write(json.dumps(reply)+'\n');child.stdin.flush()
                    continue
                if event['type']=='HELPER_OBSERVE':
                    helpers.accept(event)
                    helper_file.write(json.dumps(event,separators=(',',':'))+'\n')
                    continue
                with timing.span('frontend_decide'):reply=frontend.decide(event,output,proto)
                if observation:
                    for submission in reply['submissions']:
                        wave=checked_json(Path(submission)/'wave.json',268435456)
                        if binding:binding.register(wave)
                        else:observation.register(wave)
                        if helpers:helpers.register(wave)
                        if whole:whole.register(wave,reply['now_ns'])
                transcript.append(dict(event=event,reply=reply))
                child.stdin.write(json.dumps(reply)+'\n');child.stdin.flush()
            code=child.wait();require(code==0, 'native service failed; inspect native.stderr.log')
    finally:
        if helper_file is not None:
            helper_file.close();helpers.write(output)
        if timeout_file is not None:
            timeout_file.close()
            save(output/'source_ack_watchdog.json',watchdog.snapshot())
        if event_file is not None:
            event_file.close()
            save(output/'execution_observation.json',observation.snapshot())
        if child is not None and child.poll() is None:
            child.terminate();child.wait(timeout=5)
        if binding:binding.write(output)
        if whole:whole.write(output)
        report=dict(history=frontend.history,transcript=transcript,scheduler_calls=frontend.calls,
            active_source_ranks=sorted(frontend.schedulers),
            scheduler_source='pinned_LLMServingSim_ConfiguredScheduler_initialization_only',
            requests=[dict(id=q.id,state=q.status.name,computed_tokens=q.num_computed_tokens,
                           reached_tokens=q.num_tokens_reached,intertoken_ns=q.itl) for q in frontend.requests])
        if whole:report['whole_call_replication']=whole.snapshot()
        if frontend.delayed:
            report['standby_memory']=dict(initial_reserved_bytes=frontend.standby_initial_bytes,
                releases=frontend.standby_releases,
                final_used_bytes=sum(frontend.schedulers[r].kv.npu_pool.used_bytes()
                    for call in c['whole_call_replication']['calls']
                    for r in c['parallel']['groups'][call['replica_group']] if r in frontend.schedulers))
        if replay and replay.request_layout=='sparse_active_groups/1':
            report['admitted_group_indices']=list(frontend.admitted_groups)
        if replay:
            report.update(route_source=replay.manifest['source']['kind'],completion_semantics='observation_window_ended_not_EOS')
            for q in report['requests']:
                q['scheduler_state']=q['state']
                if q['id'] in frontend.skipped_requests:
                    q['state']='NOT_STARTED';q['skipped_before_execution_ns']=frontend.skipped_requests[q['id']]
                elif q['state']=='FINISHED':q['state']='OBSERVATION_ENDED'
                q['initial_context_tokens']=replay.records[q['id']]['context_tokens']
        with timing.span('frontend_report_write'):save(output/'frontend.json',report)
    with timing.span('native_result_read'):result=checked_json(output/'result.json',limits(rdma)['result_json_bytes'])
    require(result['passed'] and result['service']['service_finished'], 'service did not complete')
    if observation:require(observation.drained_ns is not None,'native service omitted execution drain observation')


def run(config, topology, protocol, backend, output, protoc='protoc', *, host_timing=False):
    config,topology,protocol,backend,output=(Path(x).resolve() for x in (config,topology,protocol,backend,output))
    c=checked_json(config,1048576); t=checked_json(topology/'topology.json',8388608);rdma=checked_json(protocol,65536)
    from .scaleout_replay import Replay
    replay=Replay(c,ROOT) if 'route_replay' in c else None
    validate(c,t,rdma,replay); require(backend.is_file(), 'missing native service executable')
    output.mkdir(exist_ok=False)
    if replay:
        require(replay.input_bytes<limits(rdma)['artifact_bytes'], 'route snapshots exhaust artifact budget')
        c,evidence=replay.snapshot(output)
        save(output/'route_evidence.json',evidence)
    save(output/'input.json',c)
    save(output/'topology.json',t)
    save(output/'rdma.json',rdma)
    save(output/'provenance.json',dict(network='single_native_ns3_no_UB',compute_source=c['compute']['source'],
        hardware_calibrated=False,route_source=replay.manifest['source']['kind'] if replay else 'synthetic',
        hashes={str(p):digest(p) for p in [config,topology/'topology.json',protocol,backend,
             ROOT/'third_party/LLMServingSim/serving/core/scheduler.py',Path(__file__)]}))
    if replay:
        provenance=checked_json(output/'provenance.json',65536)
        provenance['hashes'][str(ROOT/'python/moe_ub_cosim/scaleout_replay.py')]=digest(ROOT/'python/moe_ub_cosim/scaleout_replay.py')
        save(output/'provenance.json',provenance)
    if host_timing:
        provenance=checked_json(output/'provenance.json',65536)
        provenance['host_timing']=dict(enabled=True,schema_version='moe-host-phase-trace/1')
        module=ROOT/'python/moe_ub_cosim/host_timing.py'
        provenance['hashes'][str(module)]=digest(module)
        save(output/'provenance.json',provenance)
    if event_configuration(c):
        provenance=checked_json(output/'provenance.json',65536)
        provenance['execution_events']=dict(c['execution_events'])
        for name in ('execution_events.py','native_control.py'):
            module=ROOT/'python/moe_ub_cosim'/name
            provenance['hashes'][str(module)]=digest(module)
        save(output/'provenance.json',provenance)
    if binding_configuration(c):
        provenance=checked_json(output/'provenance.json',65536)
        provenance['contribution_tracking']=dict(c['contribution_tracking'])
        for name in ('contribution_binding.py','contribution_attempts.py'):
            module=ROOT/'python/moe_ub_cosim'/name
            provenance['hashes'][str(module)]=digest(module)
        save(output/'provenance.json',provenance)
    if watchdog_configuration(c):
        provenance=checked_json(output/'provenance.json',65536)
        provenance['source_ack_watchdog']=dict(c['source_ack_watchdog'])
        module=ROOT/'python/moe_ub_cosim/source_ack_watchdog.py'
        provenance['hashes'][str(module)]=digest(module)
        save(output/'provenance.json',provenance)
    if helper_configuration(c):
        provenance=checked_json(output/'provenance.json',65536)
        provenance['inflight_helpers']=dict(c['inflight_helpers'])
        module=ROOT/'python/moe_ub_cosim/inflight_helpers.py'
        provenance['hashes'][str(module)]=digest(module)
        save(output/'provenance.json',provenance)
    if whole_configuration(c):
        provenance=checked_json(output/'provenance.json',65536)
        provenance['whole_call_replication']=dict(c['whole_call_replication'])
        module=ROOT/'python/moe_ub_cosim/whole_call.py'
        provenance['hashes'][str(module)]=digest(module)
        if c['whole_call_replication']['policy']=='delayed_complete_segment/1':
            for name in ('delayed_whole.py','service_wakeup.py'):
                module=ROOT/'python/moe_ub_cosim'/name
                provenance['hashes'][str(module)]=digest(module)
        save(output/'provenance.json',provenance)
    command=[sys.executable,'-m','moe_ub_cosim.scaleout_service','--worker',str(output/'input.json'),str(output),str(output/'rdma.json'),
             str(backend),str(protoc),str(output)]
    if host_timing:command.append('--host-timing')
    monitored(command,output/'execution',[output],runtime=limits(rdma))
    result=checked_json(output/'result.json',limits(rdma)['result_json_bytes'])
    require(result['passed'], 'service result failed')
    from .scaleout_verify import verify
    audit=verify(output)
    save(output/'verification.json',audit)
    from .service_terminal import service_terminal
    return dict(passed=True,output=str(output),finish_ns=result['finish_ns'],waves=len(result['service']['waves']),
                messages=audit['messages'],verified_nodes=audit['verified_nodes'],
                state='finite_ready_KV_MoE_segment_online_service',
                **service_terminal(result,checked_json(output/'frontend.json',4*2**20),require_complete=True,config=c))


if __name__=='__main__':
    timed=sys.argv[-1:] == ['--host-timing']
    require(sys.argv[1:2]==['--worker'] and len(sys.argv)==(9 if timed else 8), 'internal worker requires six paths and optional --host-timing')
    paths=sys.argv[2:-1] if timed else sys.argv[2:]
    worker(*paths,host_timing=timed)
