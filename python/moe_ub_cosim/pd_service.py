"""One synthetic prefill request, a between-iterations KV handoff, then decode."""
from copy import deepcopy
import hashlib
import json
import sys

from .generate import export_plan
from .kv_plan import build_kv_plan, validate_kv_input
from .plan import RunIdentities, build_plan, integer, require
from .service import LLM, ROOT, group_layer


def validate_pd(config, layer):
    require(layer.get("schema_version") == "moe-ub-layer-fixture/1", "P/D currently requires layer schema 1")
    fields = {'schema_version', 'run_id', 'mode', 'execution', 'model_config', 'groups', 'request',
        'npu_memory_bytes', 'workspace_bytes', 'communication_buffer_bytes', 'block_size',
        'weight_dtype', 'compute_dtype', 'kv_dtype', 'source_layout', 'destination_layout',
        'kv_chunk_bytes', 'kv_handoff_policy'}
    require(set(config) == fields and config['schema_version'] == 'moe-ub-pd-fixture/1' and
        config['mode'] == 'fixture' and config['execution'] == {'entry_mode':'service'}, 'Unsupported P/D fixture')
    import re
    require(isinstance(config['run_id'], str) and re.fullmatch(r'[A-Za-z0-9_.-]+',config['run_id']), 'Invalid run_id')
    require(config['weight_dtype'] == config['compute_dtype'] == 'bfloat16', 'Only bfloat16 functional compute/weights')
    model_path = (ROOT / config['model_config']).resolve()
    require(model_path.is_relative_to(ROOT / 'fixtures') and model_path.is_file(), 'Model must be an existing root fixture')
    model = json.loads(model_path.read_text())
    require(model.get('model_type') == 'cosim_tiny_moe' and model.get('num_hidden_layers') == 1 and
        model.get('num_local_experts') == 8 and model.get('num_experts_per_tok') == 2,
        'Require explicitly named one-layer tiny MoE model')
    for field in ('hidden_size','intermediate_size','vocab_size','max_position_embeddings','head_dim','num_attention_heads','num_key_value_heads'):
        integer(model[field], 'model '+field, 1, 4096)
    require(model['hidden_size'] == layer['hidden_dim'] == model['num_attention_heads']*model['head_dim'] and
        layer['layer_id'] == 0, 'Model and layer dimensions disagree')
    integer(config['block_size'], 'block_size', 1, 16)
    groups = config['groups']
    require(isinstance(groups,list) and len(groups)==2, 'Require one P and one D group')
    members=[]
    for group in groups:
        require(set(group)=={'group_id','rank_ids','rank_to_ub_node','expert_placement'}, 'Invalid P/D group')
        ranks=group['rank_ids']
        require(isinstance(ranks,list) and len(ranks)==4 and ranks==sorted(set(ranks)) and
            all(type(r) is int and 0<=r<8 for r in ranks), 'Invalid P/D group ranks')
        require(group['rank_to_ub_node']==ranks and all(type(r) is int for r in group['rank_to_ub_node']) and
            group['expert_placement']==[r for r in ranks for _ in range(2)] and
            all(type(r) is int for r in group['expert_placement']), 'Invalid P/D placement')
        require(isinstance(group['group_id'],str) and re.fullmatch(r'[A-Za-z0-9_.-]+',group['group_id']), 'Invalid group_id')
        members.extend(ranks)
    require(sorted(members)==list(range(8)) and groups[0]['group_id']!=groups[1]['group_id'], 'Overlapping P/D groups')
    for field in ('npu_memory_bytes','workspace_bytes','communication_buffer_bytes'):
        require(isinstance(config[field],list) and len(config[field])==8, 'Require per-rank '+field)
        for value in config[field]: integer(value,field,1,2**30)
    r=config['request']
    require(set(r)=={'request_id','arrival_ns','prompt_tokens','output_tokens','prefill_rank','decode_rank',
        'prefill_routes','decode_routes'}, 'Invalid P/D request fields')
    integer(r['request_id'],'request_id'); integer(r['arrival_ns'],'arrival_ns',0,2**63-1000001)
    integer(r['prompt_tokens'],'prompt_tokens',2,16);integer(r['output_tokens'],'output_tokens',2,8)
    require(r['prompt_tokens']+r['output_tokens']<=model['max_position_embeddings'],'Request exceeds tiny model context')
    require(r['prefill_rank'] in groups[0]['rank_ids'] and r['decode_rank'] in groups[1]['rank_ids'] and
        type(r['prefill_rank']) is int and type(r['decode_rank']) is int, 'P/D attention rank mismatch')
    require(len(r['prefill_routes'])==r['prompt_tokens'] and len(r['decode_routes'])==r['output_tokens']-1,
        'Missing explicit prefill or decode routes')
    for group,role,routes,start,rank in ((groups[0],'P',r['prefill_routes'],0,r['prefill_rank']),
        (groups[1],'D',r['decode_routes'],r['prompt_tokens'],r['decode_rank'])):
        candidate=group_layer(layer,group);candidate['role']=role
        tokens=[dict(request_id=str(r['request_id']),src_rank=rank,phase='prefill' if role=='P' else 'decode',
            token_index=start+i,layer_id=0,expert_ids=experts) for i,experts in enumerate(routes)]
        for batch in ([tokens] if role=='P' else [[t] for t in tokens]):
            candidate['tokens']=batch;build_plan(candidate)
    kv=kv_input(config,model,'validate','KV.validate',0)
    validate_kv_input(kv)
    require(all(v>=max(config['kv_chunk_bytes'],layer['chunk_bytes']) for v in config['communication_buffer_bytes']),
        'Communication buffer smaller than a task chunk')
    return model_path, model


def kv_input(config, model, graph, wave, now):
    r=config['request'];p,d=config['groups']
    return dict(schema_version='moe-ub-kv-fixture/1',graph_id=graph,wave_id=wave,ready_ns=now,
        service_id='tiny-pd',request_id=str(r['request_id']),
        source_group={k:p[k] for k in ('group_id','rank_ids')},
        destination_group={k:d[k] for k in ('group_id','rank_ids')},
        src_rank=r['prefill_rank'],dst_rank=r['decode_rank'],
        model={k:model[k] for k in ('cache_format','num_hidden_layers','num_attention_heads','num_key_value_heads','head_dim')},
        token_range=[0,r['prompt_tokens']],source_layout=config['source_layout'],destination_layout=config['destination_layout'],
        kv_dtype=config['kv_dtype'],chunk_bytes=config['kv_chunk_bytes'],send_slots_per_rank=1,
        kv_handoff_policy=config['kv_handoff_policy'])


class PdService:
    def __init__(self, config, layer):
        model_path,self.model=validate_pd(config,layer)
        sys.path.insert(0,str(LLM))
        from serving.core.scheduler import Scheduler
        from serving.core.memory_model import Device
        self.config,self.layer=config,layer
        self.groups=deepcopy(config['groups']);self.ranks=list(range(8))
        self.p_group,self.d_group=self.groups
        source=config['request'];self.p_rank=source['prefill_rank'];self.d_rank=source['decode_rank']
        self.identities=RunIdentities()
        self.current_ns=self.decision_id=self.scheduler_calls=0
        self.waves,self.history,self.arrivals,self.decisions={},[],[],[]
        self.pending={r:[] for r in self.ranks};self.future=[]
        self.schedulers=[]
        # get_config accepts this absolute extensionless path. No upstream files
        # or global loader caches are replaced to install the functional model.
        name=str(model_path.with_suffix(''))
        for rank in self.ranks:
            total=config['npu_memory_bytes'][rank]
            reserved=config['workspace_bytes'][rank]+config['communication_buffer_bytes'][rank]
            require(total>reserved,'Memory budget consumed by workspace/buffers')
            scheduler=Scheduler(name,0,rank,1,16,1,1,1,total/2**30,0,rank,'colocated',16,
                config['block_size'],1,False,False,None,None,ep_size=4,kv_cache_dtype=config['kv_dtype'],
                npu_memory_utilization=(total-reserved)/total)
            # MemoryModel sized its one KV pool after these reservations; record
            # the same non-KV bytes in the byte-level view, without a second pool.
            scheduler.memory.allocate(reserved,Device.NPU)
            self.schedulers.append(scheduler)
        p=self.schedulers[self.p_rank]
        p.add_request((source['request_id'],name,source['prompt_tokens'],source['prompt_tokens']+source['output_tokens'],
            source['arrival_ns'],self.p_rank))
        self.request=p.waiting[0];self.requests=[self.request]
        self.future=[self.request]
        self.state='WAITING';self.memory_events=[]
        self.initial=self.snapshot()
        self.model_sha256=hashlib.sha256(model_path.read_bytes()).hexdigest()
        bpt=p.memory.get_kv(1)
        require(bpt==(2*self.model['num_key_value_heads']*self.model['head_dim']*(2 if config['kv_dtype']=='bfloat16' else 1)),
            'Scheduler and explicit MHA KV byte sizes disagree')
        needed=(source['prompt_tokens']+config['block_size']-1)//config['block_size']*bpt*config['block_size']
        d=self.schedulers[self.d_rank]
        group_capacity=sum(self.schedulers[r].kv.npu_pool.num_blocks*self.schedulers[r].memory._npu_bytes_per_block
            for r in self.d_group['rank_ids'])
        require(needed<=group_capacity,'Request KV exceeds total D group capacity')
        for rank,length in ((self.p_rank,source['prompt_tokens']), (self.d_rank,source['prompt_tokens']+source['output_tokens']-1)):
            pool=self.schedulers[rank].kv.npu_pool
            require((length+config['block_size']-1)//config['block_size']<=pool.num_blocks,
                'Request exceeds mapped rank KV capacity')
        self.record_memory('initialized')

    def snapshot(self):
        r=self.request
        return [dict(request_id=r.id,rank_id=r.instance_id,status=r.status.name,pd_state=self.state,
            num_computed_tokens=r.num_computed_tokens,num_tokens_reached=r.num_tokens_reached,target_tokens=r.output,
            intertoken_latencies_ns=list(r.itl),ttft_ns=r.ttft,preemptions=r.num_preemptions)]

    def record_memory(self,event):
        self.memory_events.append(dict(event=event,time_ns=self.current_ns,state=self.state,ranks=[dict(rank_id=r,
            weight_bytes=s.memory.weight,workspace_bytes=self.config['workspace_bytes'][r],
            communication_buffer_bytes=self.config['communication_buffer_bytes'][r],
            total_bytes=self.config['npu_memory_bytes'][r],used_bytes=s.memory.npu_used,
            kv_capacity_blocks=s.kv.npu_pool.num_blocks,kv_block_bytes=s.memory._npu_bytes_per_block,
            kv_used_bytes=s.kv.npu_used_bytes(),request_blocks=len(s.kv.req_to_blocks.get(self.request.id,[])),
            running=[q.id for q in s.running],inflight=len(s.inflight)) for r,s in enumerate(self.schedulers)]))

    def submit_compute(self,role,now,directory):
        rank,group=(self.p_rank,self.p_group) if role=='P' else (self.d_rank,self.d_group)
        s=self.schedulers[rank];before=self.snapshot();batch=s.schedule(now,rank);self.scheduler_calls+=1
        r=self.request;source=self.config['request']
        count=source['prompt_tokens'] if role=='P' else 1
        require(batch is not None and batch.requests==[r] and batch.scheduled_tokens=={r.id:count} and
            batch.load==batch.evict==batch.pd_kv_send_tokens==0,'Unexpected native scheduler work or duplicate KV traffic')
        begin=r.num_computed_tokens-count
        routes=source['prefill_routes'] if role=='P' else [source['decode_routes'][begin-source['prompt_tokens']]]
        step=len(self.history);graph=f'g{step}';wave=f'{role}.w{step}'
        candidate=group_layer(self.layer,group)
        candidate.update(role=role,graph_id=graph,wave_id=wave,ready_ns=now,tokens=[dict(request_id=str(r.id),
            src_rank=rank,phase='prefill' if role=='P' else 'decode',token_index=begin+i,layer_id=0,expert_ids=route)
            for i,route in enumerate(routes)])
        plan=build_plan(candidate,self.identities);export_plan(plan,directory/'graphs'/graph)
        self.state='PREFILL' if role=='P' else 'DECODE'
        self.waves={group['group_id']:dict(graph_id=graph,wave_id=wave,rank_ids=group['rank_ids'],kind='WAVE_DONE',batch=batch,rank=rank,role=role)}
        self.history.append(dict(graph_id=graph,wave_id=wave,role=role,group_id=group['group_id'],rank_ids=group['rank_ids'],
            submitted_ns=now,before=before,scheduled=self.snapshot(),tokens=candidate['tokens']))
        self.record_memory('prefill_submitted' if role=='P' else 'decode_submitted')
        return plan

    def decide(self,message,directory):
        require(set(message)=={'schema_version','type','run_id','decision_id','now_ns','completed','available_rank_ids'} and
            message['schema_version']=='cosim/1' and message['type']=='DECIDE' and message['run_id']==self.config['run_id'], 'Invalid P/D DECIDE')
        integer(message['decision_id'],'decision_id',self.decision_id,self.decision_id)
        now=integer(message['now_ns'],'now_ns',self.current_ns)
        require(self.decision_id!=0 or (now==0 and message['completed']==[]),'Invalid initial decision')
        expected=[]
        if self.waves:
            require(len(self.waves)==1,'M4.1 requires one active wave')
            w=next(iter(self.waves.values()))
            expected=[dict(kind=w['kind'],graph_id=w['graph_id'],wave_id=w['wave_id'],finished_ns=now)]
        require(message['completed']==expected,'Unexpected or incomplete P/D completion')
        require(message['available_rank_ids']==self.ranks and all(type(r) is int for r in message['available_rank_ids']),
            'P/D groups must both be available at an iteration boundary')
        self.current_ns=now
        if expected:
            self.waves.clear()
            if w['role']=='KV':
                self.record_memory('handoff_complete')
                p,d=self.schedulers[self.p_rank],self.schedulers[self.d_rank]
                require(self.request in p.running and not p.inflight and not d.running and not d.inflight,
                    'P/D scheduler changed during handoff')
                p.kv.free(self.request);p.running.remove(self.request)
                self.record_memory('source_released')
                blocks=tuple(d.kv.req_to_blocks[self.request.id])
                d.add_decode(self.request)
                require(tuple(d.kv.req_to_blocks[self.request.id])==blocks and d.kv.take_traffic()==(0,0),
                    'Decode admission changed reservation or billed KV twice')
                self.state='DECODE_READY';self.record_memory('decode_admitted')
            else:
                s=self.schedulers[w['rank']]
                prompt,generated,ended=s.add_done(w['batch'].batch_id+1,w['rank'],now)
                require(not s.inflight and generated==1,'P/D completion did not produce exactly one token')
                if w['role']=='P':
                    require(prompt==self.config['request']['prompt_tokens'] and not ended and self.request in s.running,
                        'Prefill completed with wrong progress or released source')
                    self.state='HANDOFF_READY';self.record_memory('prefill_complete')
                else:
                    require(prompt==0,'Unexpected prompt completion during decode')
                    self.state='FINISHED' if ended else 'DECODE_READY';self.record_memory('decode_complete')
            self.history[-1].update(finished_ns=now,after=self.snapshot())
        response=dict(schema_version='cosim/1',type='COMMIT',run_id=self.config['run_id'],decision_id=self.decision_id,
            now_ns=now,submissions=[],next_wakeup_ns=None,input_exhausted=True,service_finished=False)
        plan=None
        if self.future:
            arrival=self.request.arrival
            require(now<=arrival,'Skipped request arrival')
            if now<arrival:response.update(next_wakeup_ns=arrival,input_exhausted=False)
            else:
                self.future.clear();self.arrivals.append(dict(request_id=self.request.id,arrival_ns=now))
                plan=self.submit_compute('P',now,directory)
        elif self.state=='HANDOFF_READY':
            d=self.schedulers[self.d_rank]
            require(d.kv.allocate_slots(self.request,1) is not None,'D KV capacity unavailable at handoff')
            require(not d.running and not d.inflight and d.kv.take_traffic()==(0,0),'Reservation scheduled decode or billed transfer')
            self.record_memory('destination_reserved')
            step=len(self.history);graph=f'g{step}';wave=f'KV.w{step}'
            plan=build_kv_plan(kv_input(self.config,self.model,graph,wave,now),self.identities)
            export_plan(plan,directory/'graphs'/graph)
            self.state='KV_INFLIGHT'
            self.waves={'handoff':dict(graph_id=graph,wave_id=wave,rank_ids=self.ranks,kind='KV_DONE',role='KV')}
            self.history.append(dict(graph_id=graph,wave_id=wave,role='KV',rank_ids=self.ranks,submitted_ns=now,
                kv_payload_bytes=plan['kv_payload_bytes']))
            self.record_memory('handoff_submitted')
        elif self.state=='DECODE_READY':plan=self.submit_compute('D',now,directory)
        elif self.state=='FINISHED':
            require(all(s.is_request_empty() and s.kv.is_free() for s in self.schedulers),'P/D finished with live requests or KV')
            response['service_finished']=True;self.record_memory('finished')
        else:require(bool(response['next_wakeup_ns']),'P/D state cannot progress')
        if plan:
            graph=plan['graph_id']
            response['submissions']=[dict(graph_id=graph,wave_id=plan['wave_id'],rank_ids=plan['rank_ids'],
                workload_prefix=f'graphs/{graph}/layer',semantic_manifest=f'graphs/{graph}/plan.json')]
        self.decisions.append(dict(now_ns=now,state=self.state,requests=self.snapshot()))
        self.decision_id+=1
        return response

    def result_fields(self):
        return dict(memory_events=self.memory_events,model=self.model,model_sha256=self.model_sha256,
            kv_handoff_policy=self.config['kv_handoff_policy'],kv_dtype=self.config['kv_dtype'])
