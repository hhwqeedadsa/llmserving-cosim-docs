"""FIFO between-iterations handoffs and an independent group on one UB clock."""
from copy import deepcopy
import hashlib
import json
import sys

from .generate import export_plan
from .kv_plan import build_kv_plan
from .pd_service import kv_input, validate_pd
from .plan import RunIdentities, build_plan, integer, require
from .service import LLM, group_layer


def validate_queue(config, layer):
    require(layer.get("schema_version") == "moe-ub-layer-fixture/1", "P/D currently requires layer schema 1")
    require(config.get('schema_version') == 'moe-ub-pd-queue-fixture/1', 'Unsupported queued P/D schema')
    require(isinstance(config.get('requests'), list) and len(config['requests']) <= 8 and
            isinstance(config.get('independent_requests'), list) and len(config['independent_requests']) <= 8,
            'Require bounded P/D and independent request lists')
    groups = config['groups']
    require(len(groups) == 3, 'Require P, D and one independent EP group')
    names = set()
    for index, group in enumerate(groups):
        ranks = list(range(4*index, 4*index+4))
        require(set(group) == {'group_id','rank_ids','rank_to_ub_node','expert_placement'} and
                group['rank_ids'] == group['rank_to_ub_node'] == ranks and
                group['expert_placement'] == [r for r in ranks for _ in range(2)], 'Invalid queued group mapping')
        require(all(type(r) is int for k in ('rank_ids','rank_to_ub_node','expert_placement') for r in group[k]),
                'Group ranks must be integers')
        import re
        require(isinstance(group['group_id'], str) and re.fullmatch(r'[A-Za-z0-9_.-]+', group['group_id']) and
                group['group_id'] not in names, 'Invalid or repeated group identity')
        names.add(group['group_id'])
    for key in ('npu_memory_bytes','workspace_bytes','communication_buffer_bytes'):
        require(isinstance(config[key],list) and len(config[key]) == 12, 'Require twelve per-rank budgets')
        for value in config[key]: integer(value, key, 1, 2**30)
    # Reuse the single-request boundary checks without constructing extra pools.
    single = {k:deepcopy(v) for k,v in config.items() if k not in ('requests','independent_requests')}
    single['schema_version'] = 'moe-ub-pd-fixture/1'
    single['groups'] = single['groups'][:2]
    for key in ('npu_memory_bytes','workspace_bytes','communication_buffer_bytes'): single[key] = single[key][:8]
    template = dict(request_id=0,arrival_ns=0,prompt_tokens=2,output_tokens=2,prefill_rank=0,decode_rank=4,
                    prefill_routes=[[0,1]]*2,decode_routes=[[0,1]])
    single['request'] = template
    model_path, model = validate_pd(single, layer)
    ids = set()
    for request in config['requests']:
        single['request'] = request
        validate_pd(single, layer)
        require(request['request_id'] not in ids, 'Duplicate request_id')
        ids.add(request['request_id'])
    for r in config['independent_requests']:
        require(set(r) == {'request_id','rank_id','arrival_ns','prompt_tokens','generated_tokens','decode_routes'},
                'Invalid independent request fields')
        integer(r['request_id'],'request_id'); integer(r['rank_id'],'independent rank',8,11)
        integer(r['arrival_ns'],'arrival_ns',0,2**63-1000001)
        integer(r['prompt_tokens'],'prompt_tokens',1,16); integer(r['generated_tokens'],'generated_tokens',1,16)
        require(r['request_id'] not in ids, 'Duplicate request_id'); ids.add(r['request_id'])
        require(isinstance(r['decode_routes'],list) and 1 <= len(r['decode_routes']) <= 8 and
                r['prompt_tokens']+r['generated_tokens']+len(r['decode_routes']) <= model['max_position_embeddings'],
                'Invalid independent route length')
        for offset, route in enumerate(r['decode_routes']):
            candidate = group_layer(layer,groups[2])
            candidate.update(role='D', tokens=[dict(request_id=str(r['request_id']),src_rank=r['rank_id'],
                phase='decode',token_index=r['prompt_tokens']+r['generated_tokens']-1+offset,layer_id=0,expert_ids=route)])
            build_plan(candidate)
    require(all(v >= max(config['kv_chunk_bytes'],layer['chunk_bytes']) for v in config['communication_buffer_bytes']),
            'Communication buffer smaller than task chunk')
    return model_path, model


class QueuedPdService:
    def __init__(self, config, layer):
        model_path, self.model = validate_queue(config, layer)
        sys.path.insert(0,str(LLM))
        from serving.core.scheduler import Scheduler
        from serving.core.memory_model import Device
        from serving.core.request import Request
        self.config,self.layer = config,layer
        self.groups = deepcopy(config['groups']); self.ranks = list(range(12))
        self.p,self.d,self.ep = self.groups
        self.owners = {r:g for g in self.groups for r in g['rank_ids']}
        self.identities = RunIdentities()
        self.current_ns = self.decision_id = self.scheduler_calls = 0
        self.waves,self.history,self.arrivals,self.decisions = {},[],[],[]
        self.handoff_queue,self.memory_events = [],[]
        self.pending = {r:[] for r in self.ranks}
        self.inputs = {r['request_id']:r for r in config['requests']+config['independent_requests']}
        self.states,self.requests,self.schedulers = {},[],[]
        name = str(model_path.with_suffix(''))
        for rank in self.ranks:
            total = config['npu_memory_bytes'][rank]
            reserved = config['workspace_bytes'][rank]+config['communication_buffer_bytes'][rank]
            require(total > reserved,'Memory budget consumed by workspace/buffers')
            s = Scheduler(name,0,rank,8,16 if rank < 4 else 1,1,1,1,total/2**30,0,rank,'colocated',16,
                config['block_size'],16,False,False,None,None,ep_size=4,kv_cache_dtype=config['kv_dtype'],
                npu_memory_utilization=(total-reserved)/total)
            s.memory.allocate(reserved,Device.NPU); self.schedulers.append(s)
        for source in config['requests']+config['independent_requests']:
            pd = 'prefill_rank' in source
            rank = source['prefill_rank'] if pd else source['rank_id']
            reached = source['prompt_tokens'] if pd else source['prompt_tokens']+source['generated_tokens']
            target = source['prompt_tokens']+source['output_tokens'] if pd else reached+len(source['decode_routes'])
            request = Request(source['request_id'],name,source['prompt_tokens'],target,source['arrival_ns'],rank,is_init=pd)
            if not pd:
                request.num_tokens_reached = reached; request.num_computed_tokens = reached-1
                request.recent_end = request.arrival
            self.requests.append(request); self.states[request.id] = 'WAITING'
            for member, tokens in ((rank,source['prompt_tokens']), (source['decode_rank'],target-1)) if pd else ((rank,target-1),):
                require(self.blocks(tokens) <= self.schedulers[member].kv.npu_pool.num_blocks,
                        f'Request {request.id} exceeds mapped rank {member} total KV capacity')
        self.requests.sort(key=lambda r:(r.arrival,r.instance_id,r.id))
        self.future = list(self.requests)
        self.model_sha256 = hashlib.sha256(model_path.read_bytes()).hexdigest()
        self.initial = self.snapshot(); self.record_memory('initialized')

    def blocks(self,tokens):
        size=self.config['block_size']; return (tokens+size-1)//size

    def snapshot(self):
        return [dict(request_id=r.id,rank_id=r.instance_id,status=r.status.name,pd_state=self.states[r.id],
            num_computed_tokens=r.num_computed_tokens,num_tokens_reached=r.num_tokens_reached,target_tokens=r.output,
            intertoken_latencies_ns=list(r.itl),ttft_ns=r.ttft,preemptions=r.num_preemptions) for r in self.requests]

    def record_memory(self,event,request=None,**extra):
        self.memory_events.append(dict(event=event,time_ns=self.current_ns,request_id=None if request is None else request.id,
            handoff_queue=[r.id for r in self.handoff_queue],states=dict(self.states),
            ranks=[dict(rank_id=r,weight_bytes=s.memory.weight,workspace_bytes=self.config['workspace_bytes'][r],
                communication_buffer_bytes=self.config['communication_buffer_bytes'][r],total_bytes=self.config['npu_memory_bytes'][r],
                used_bytes=s.memory.npu_used,kv_block_bytes=s.memory._npu_bytes_per_block,
                kv_capacity_blocks=s.kv.npu_pool.num_blocks,free_blocks=s.kv.npu_pool.get_num_free_blocks(),
                request_blocks={str(k):len(v) for k,v in s.kv.req_to_blocks.items() if v},
                kv_used_bytes=s.kv.npu_used_bytes(),running=[q.id for q in s.running],inflight=len(s.inflight))
                for r,s in enumerate(self.schedulers)],**extra))

    def idle(self,group):
        return not any(set(group['rank_ids']) & set(w['rank_ids']) for w in self.waves.values())

    def publish(self,plan,role,directory,**extra):
        graph=plan['graph_id'];step=len(self.history)
        export_plan(plan,directory/'graphs'/graph)
        self.waves[graph]=dict(graph_id=graph,wave_id=plan['wave_id'],rank_ids=plan['rank_ids'],role=role,
            kind='KV_DONE' if role=='KV' else 'WAVE_DONE',history_index=step,**extra)
        self.history.append(dict(graph_id=graph,wave_id=plan['wave_id'],rank_ids=plan['rank_ids'],role=role,
            submitted_ns=self.current_ns,tokens=plan['input'].get('tokens',[]),
            request_ids=[extra['request'].id] if role=='KV' else [b.requests[0].id for b in extra['batches'].values()]))
        return dict(graph_id=graph,wave_id=plan['wave_id'],rank_ids=plan['rank_ids'],
            workload_prefix=f'graphs/{graph}/layer',semantic_manifest=f'graphs/{graph}/plan.json')

    def compute(self,group,role,directory):
        active=[r for r in group['rank_ids'] if not self.schedulers[r].is_request_empty()]
        if not active:return None
        candidate=group_layer(self.layer,group);tokens=[];batches={}
        for rank in active:
            s=self.schedulers[rank];batch=s.schedule(self.current_ns,rank);self.scheduler_calls+=1
            require(batch is not None and len(batch.requests)==1 and batch.load==batch.evict==batch.pd_kv_send_tokens==0,
                    'Unexpected native scheduler work')
            request=batch.requests[0];source=self.inputs[request.id]
            count=source['prompt_tokens'] if role=='P' else 1
            require(batch.scheduled_tokens=={request.id:count} and request.num_preemptions==0,'Unexpected preemption or Token count')
            begin=request.num_computed_tokens-count
            offset=begin-(source['prompt_tokens'] if role=='D' else source['prompt_tokens']+source.get('generated_tokens',1)-1)
            routes=source['prefill_routes'] if role=='P' else [source['decode_routes'][offset]]
            tokens.extend(dict(request_id=str(request.id),src_rank=rank,phase='prefill' if role=='P' else 'decode',
                token_index=begin+i,layer_id=0,expert_ids=route) for i,route in enumerate(routes))
            self.states[request.id]='PREFILL' if role=='P' else 'DECODING';batches[rank]=batch
        step=len(self.history)
        candidate.update(role='P' if role=='P' else 'D',graph_id=f'g{step}',wave_id=f'{role}.w{step}',
            ready_ns=self.current_ns,tokens=tokens)
        submission=self.publish(build_plan(candidate,self.identities),role,directory,batches=batches)
        self.record_memory('compute_submitted',role=role,graph_id=submission['graph_id'])
        return submission

    def handoff(self,directory):
        if not self.handoff_queue or not self.idle(self.p) or not self.idle(self.d):return None
        request=self.handoff_queue[0];source=self.inputs[request.id];s=self.schedulers[source['decode_rank']]
        # Reserve through the final input Token so an admitted request cannot
        # preempt an older request while another source is waiting for capacity.
        needed=self.blocks(request.output-1)
        if s.kv.npu_pool.get_num_free_blocks() < needed:
            self.record_memory('capacity_wait',request,required_blocks=needed)
            return None
        require(s.kv.allocate_slots(request,request.output-1-request.num_computed_tokens) is not None,'KV reservation failed')
        require(s.kv.take_traffic()==(0,0),'Reservation billed analytic KV traffic')
        self.record_memory('destination_reserved',request)
        c={k:v for k,v in self.config.items() if k not in ('requests','independent_requests')}
        c.update(groups=self.groups[:2],request=source)
        step=len(self.history)
        plan=build_kv_plan(kv_input(c,self.model,f'g{step}',f'KV.w{step}',self.current_ns),self.identities)
        self.handoff_queue.pop(0);self.states[request.id]='KV_INFLIGHT'
        submission=self.publish(plan,'KV',directory,request=request)
        self.record_memory('handoff_submitted',request,graph_id=plan['graph_id'])
        return submission

    def decide(self,message,directory):
        require(set(message)=={'schema_version','type','run_id','decision_id','now_ns','completed','available_rank_ids'} and
            message['schema_version']=='cosim/1' and message['type']=='DECIDE' and message['run_id']==self.config['run_id'],
            'Invalid queued DECIDE')
        integer(message['decision_id'],'decision_id',self.decision_id,self.decision_id)
        now=integer(message['now_ns'],'now_ns',self.current_ns)
        require(self.decision_id!=0 or (now==0 and message['completed']==[]),'Invalid initial decision')
        finished=[]
        for event in message['completed']:
            graph=event.get('graph_id');w=self.waves.get(graph)
            require(w is not None and graph not in finished and event==dict(kind=w['kind'],graph_id=graph,
                wave_id=w['wave_id'],finished_ns=now),'Unexpected queued completion')
            finished.append(graph)
        busy={r for g,w in self.waves.items() if g not in finished for r in w['rank_ids']}
        require(message['available_rank_ids']==[r for r in self.ranks if r not in busy] and
                all(type(r) is int for r in message['available_rank_ids']),'Queued group availability mismatch')
        self.current_ns=now
        for graph in finished:
            w=self.waves.pop(graph)
            if w['role']=='KV':
                r=w['request'];source=self.inputs[r.id];p=self.schedulers[source['prefill_rank']];d=self.schedulers[source['decode_rank']]
                self.record_memory('handoff_complete',r)
                p.kv.free(r);p.running.remove(r);self.record_memory('source_released',r)
                blocks=tuple(d.kv.req_to_blocks[r.id]);d.add_decode(r)
                require(tuple(d.kv.req_to_blocks[r.id])==blocks and d.kv.take_traffic()==(0,0),'Decode changed reservation')
                self.states[r.id]='DECODE_READY';self.record_memory('decode_admitted',r)
            else:
                completed=[]
                for rank,batch in w['batches'].items():
                    s=self.schedulers[rank];r=batch.requests[0];prompt,generated,ended=s.add_done(batch.batch_id+1,rank,now)
                    require(generated==1 and not s.inflight,'Invalid scheduler completion')
                    if w['role']=='P':
                        require(prompt==self.inputs[r.id]['prompt_tokens'] and not ended,'Wrong prefill progress')
                        self.states[r.id]='HANDOFF_WAIT';completed.append(r)
                    else:
                        require(prompt==0,'Unexpected prompt in decode')
                        self.states[r.id]='FINISHED' if ended else 'DECODE_READY'
                for r in sorted(completed,key=lambda r:r.id):
                    self.handoff_queue.append(r);self.record_memory('handoff_enqueued',r)
                self.record_memory('compute_complete',role=w['role'],graph_id=graph)
            self.history[w['history_index']].update(finished_ns=now,after=self.snapshot())
        while self.future and self.future[0].arrival<=now:
            r=self.future.pop(0);require(r.arrival==now,'Skipped request arrival')
            self.pending[r.instance_id].append(r)
            self.arrivals.append(dict(request_id=r.id,arrival_ns=now,active_graphs=list(self.waves)))
        response=dict(schema_version='cosim/1',type='COMMIT',run_id=self.config['run_id'],decision_id=self.decision_id,
            now_ns=now,submissions=[],next_wakeup_ns=self.future[0].arrival if self.future else None,
            input_exhausted=not self.future,service_finished=False)
        kv=self.handoff(directory)
        if kv:response['submissions'].append(kv)
        # A waiting source keeps its whole EP group reserved, including other
        # source ranks. It never occupies the busy D group's execution graph.
        if self.idle(self.p) and not self.handoff_queue:
            budget=16
            for rank in self.p['rank_ids']:
                s=self.schedulers[rank]
                if s.is_request_empty() and self.pending[rank]:
                    r=self.pending[rank][0];count=self.inputs[r.id]['prompt_tokens']
                    if count<=budget:
                        self.pending[rank].pop(0);s.waiting.append(r);budget-=count
            result=self.compute(self.p,'P',directory)
            if result:response['submissions'].append(result)
        for group,role in ((self.d,'D'),(self.ep,'EP')):
            if not self.idle(group):continue
            if role=='EP':
                for rank in group['rank_ids']:
                    s=self.schedulers[rank]
                    if s.is_request_empty() and self.pending[rank]:
                        r=self.pending[rank].pop(0)
                        require(s.kv.allocate_slots(r,r.output-1-r.num_computed_tokens) is not None,'Independent capacity unavailable')
                        s.add_decode(r)
            result=self.compute(group,role,directory)
            if result:response['submissions'].append(result)
        response['service_finished']=not self.future and not self.waves and not self.handoff_queue and not any(self.pending.values()) and all(s.is_request_empty() for s in self.schedulers)
        if response['service_finished']:
            require(all(s.kv.is_free() for s in self.schedulers),'Finished with allocated KV');self.record_memory('finished')
        self.decisions.append(dict(now_ns=now,active_graphs=list(self.waves),handoff_queue=[r.id for r in self.handoff_queue],requests=self.snapshot()))
        self.decision_id+=1
        return response

    def result_fields(self):
        return dict(memory_events=self.memory_events,model=self.model,model_sha256=self.model_sha256,
            kv_handoff_policy=self.config['kv_handoff_policy'],kv_dtype=self.config['kv_dtype'],
            handoff_queue=[r.id for r in self.handoff_queue])
