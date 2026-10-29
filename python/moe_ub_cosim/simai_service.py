"""Real SimAI/Vidur scheduling with sparse source routes on one native ns-3 fabric."""
from collections import Counter
from copy import deepcopy
import json
import logging
from pathlib import Path
import shutil
import subprocess
import sys

from .generate import _schema, write_message
from .route_evidence import unique_object, validate_raw
from .scaleout_rdma import ROOT, checked_json, digest, monitored, save
from .scaleout_limits import limits
from .scaleout_service import export_wave, make_wave, validate as validate_service
from .scaleout_topology import fields, integer, require
from .simai_frontend import load, verify_runtime
from .native_control import event as control_event


class SparseRoutes:
    def __init__(self,config,base):
        self.config=config;self.base=Path(base);s=config['service'];m=s['model'];w=s['workload'];r=config['routes']
        fields(r,'source layer_ids requests','SimAI routes')
        fields(r['source'],'kind dataset_id collector_model_id directory download_revision remote_identity_verified','SimAI route source')
        source=r['source'];require(source['kind'] in ('synthetic_test','user_provided_model_routes'),'invalid SimAI route source')
        require(type(source['remote_identity_verified']) is bool,'invalid route provenance flag')
        if source['remote_identity_verified']:
            require(isinstance(source['download_revision'],str) and len(source['download_revision'])==40,'verified source requires revision')
        require(m['name']=='deepseek-ai/DeepSeek-R1' and m['hidden_size']==7168 and m['ffn_hidden_size']==2048 and
                m['experts']==256 and m['top_k']==8,'SimAI sparse route adapter currently requires R1 shape')
        if source['kind']!='synthetic_test':
            require(source['collector_model_id']=='cognitivecomputations/DeepSeek-R1-AWQ','do not relabel real model routes')
        self.manifest=dict(source=deepcopy(source));self.layers=r['layer_ids']
        require(isinstance(self.layers,list) and len(self.layers)==m['layers'] and bool(self.layers) and
                all(type(i) is int for i in self.layers) and self.layers==list(range(self.layers[0],self.layers[0]+m['layers'])) and
                3<=self.layers[0]<=self.layers[-1]<=60,'invalid R1 finite source layers')
        inventory=[dict(layer_id=i,kind='dense' if i<3 else 'moe',num_experts=0 if i<3 else 256,top_k=0 if i<3 else 8) for i in range(61)]
        require(isinstance(r['requests'],list) and 1<=len(r['requests'])<=64,'SimAI source request budget exceeded')
        self.records={};self.input_bytes=0;slots=set();paths=set();hashes=set();file_ids=set()
        ranks={rank for group in s['parallel']['groups'] for rank in group}
        for identity,request in enumerate(r['requests']):
            fields(request,'rank_id slot file_id file first_decode_entry','SimAI request')
            fields(request['file'],'path sha256','SimAI request file')
            rank,slot=request['rank_id'],request['slot'];integer(slot,'SimAI slot',0,w['batch_per_rank']-1)
            require(type(rank) is int and rank in ranks and (rank,slot) not in slots,'invalid or repeated SimAI request slot')
            file_id=request['file_id'];require(isinstance(file_id,str) and file_id and file_id not in file_ids,'repeated source file ID')
            p=(self.base/request['file']['path']).resolve()
            require(p.is_file() and p.stat().st_size<=16*2**20,'missing/oversized SimAI route file')
            require(p not in paths and request['file']['sha256'] not in hashes,'cycling/cloning source routes is unsupported')
            raw_bytes=p.read_bytes();self.input_bytes+=len(raw_bytes)
            require(self.input_bytes<=64*2**20 and digest(p)==request['file']['sha256'],'SimAI route byte/hash budget failed')
            raw=json.loads(raw_bytes,object_pairs_hook=unique_object);prompt,available=validate_raw(raw,inventory)
            first=request['first_decode_entry'];integer(first,'first Decode source entry',1,65536)
            require(first+w['decode_steps']-1<=available,'source window exhausted; no EOF-to-EOS inference')
            context=prompt+first-1;require(context<=w['context_tokens'],'source context exceeds declared maximum')
            self.records[identity]=dict(**deepcopy(request),context_tokens=context,
                choices=[[raw[first+step][str(layer)][0] for layer in self.layers] for step in range(w['decode_steps'])],
                path=p,prompt_tokens=prompt)
            slots.add((rank,slot));paths.add(p);hashes.add(request['file']['sha256']);file_ids.add(file_id)
        require(all(any(r['rank_id'] in group for r in self.records.values()) for group in s['parallel']['groups']),
                'each declared EP group needs at least one real source request')
        self.active_ids=set(self.records)

    def routes(self,group,step,layer):
        return [dict(rank=r['rank_id'],slot=r['slot'],experts=list(r['choices'][step][layer]),
            source=dict(request_id=i,file_id=r['file_id'],source_entry=r['first_decode_entry']+step,
                        source_token_row=0,source_layer_id=self.layers[layer],sha256=r['file']['sha256']))
                for i,r in self.records.items() if i in self.active_ids and r['rank_id'] in group]

    def owner(self,group,layer,expert):
        return group[expert//(256//len(group))]

    def snapshot(self,output):
        config=deepcopy(self.config);folder=Path(output)/'route_sources';folder.mkdir()
        for i,record in self.records.items():
            target=folder/f'{i}.json';shutil.copyfile(record['path'],target)
            config['routes']['requests'][i]['file']['path']=str(target.relative_to(output))
        return config


def validate(config,topology,protocol,base):
    fields(config,'schema_version service simai routes','SimAI service')
    require(config['schema_version']=='moe-simai-service/1','unsupported SimAI service schema')
    service=config['service'];require(service['schema_version']=='moe-scaleout-service/1' and 'route_replay' not in service,
            'SimAI adapter requires finite service/1, with its own sparse routes')
    require(service['workload']['hotspot_fraction']==0,'synthetic hotspot cannot modify source choices')
    ranks,planes=validate_service(service,topology,protocol)
    fields(config['simai'],'model_name device max_sequence_tokens','SimAI scheduler config')
    require(config['simai']['model_name']=='deepseek-671B' and config['simai']['device'] in ('a100','h100'),
            'unsupported upstream SimAI model/device config')
    integer(config['simai']['max_sequence_tokens'],'max sequence tokens',16,65536)
    require(config['simai']['max_sequence_tokens']%16==0 and
            config['simai']['max_sequence_tokens']>=service['workload']['context_tokens']+service['workload']['decode_steps']+1,
            'upstream SimAI sequence capacity must include the complete observation window')
    replay=SparseRoutes(config,base)
    return ranks,planes,replay


class Frontend:
    def __init__(self,config,planes,replay,runtime,output, *, message_preflight):
        self.config=config;self.service=config['service'];self.replay=replay;self.planes=planes
        self.message_preflight=message_preflight
        self.groups=self.service['parallel']['groups'];self.steps=[0]*len(self.groups)
        self.active={};self.history=[];self.calls=0;self.requests={};self.schedulers={};self.clusters=[]
        self.origin_seconds=1.0;classes=load(runtime);self.receipt=classes['receipt'];logging.getLogger('vidur').setLevel(logging.ERROR)
        metrics=classes['MetricsConfig'](output_dir=str(Path(output)/'simai_metrics'),write_json_trace=False)
        gen=classes['SyntheticRequestGeneratorConfig'](length_generator_config=classes['FixedRequestLengthGeneratorConfig'](
            max_tokens=config['simai']['max_sequence_tokens']))
        for group in self.groups:
            rc=classes['ReplicaConfig'](model_name=config['simai']['model_name'],device=config['simai']['device'],
                                        tensor_parallel_size=1,num_pipeline_stages=1)
            cluster=classes['Cluster'](classes['ClusterConfig'](num_replicas=len(group),replica_config=rc),metrics,gen)
            self.clusters.append(cluster);replicas=list(cluster.replicas.values())
            require(rc.expert_model_parallel_size==len(group),'SimAI Cluster EP differs from physical placement')
            for rank,replica in zip(group,replicas):
                records=[(i,r) for i,r in replay.records.items() if r['rank_id']==rank]
                if not records:continue
                scheduler=classes['OrcaReplicaScheduler'](rc,classes['OrcaSchedulerConfig'](
                    batch_size_cap=self.service['workload']['batch_per_rank']),gen,replica,1,None)
                self.schedulers[rank]=scheduler
                require(scheduler._max_batch_size>=len(records),'SimAI model memory planner cannot admit declared batch')
                for identity,r in records:
                    # A ready-KV observation window starts after declared
                    # prefill, including the token produced at prefill end.
                    q=classes['Request'](0.0,r['context_tokens'],self.service['workload']['decode_steps']+1)
                    q.on_batch_schedule(self.origin_seconds);q.on_batch_end(self.origin_seconds,r['context_tokens'])
                    scheduler.add_request(q);self.requests[identity]=q
        self.request_identity={id(q):i for i,q in self.requests.items()}

    def decide(self,event,output,proto):
        now=event['now_ns'];time=self.origin_seconds+now/1e9
        for completed in event['completed']:
            graph=completed['graph_id'];require(completed['finished_ns']==now,'SimAI completion clock mismatch')
            require(graph in self.active,'native completed unknown SimAI graph')
            index,batches,history=self.active.pop(graph)
            for rank,batch in batches.items():
                batch.on_batch_end(time);self.schedulers[rank].on_batch_end(batch)
            history['finished_ns']=now;self.steps[index]+=1
        submissions=[]
        for index,group in enumerate(self.groups):
            if self.steps[index]>=self.service['workload']['decode_steps'] or any(i==index for i,_,_ in self.active.values()):continue
            require(set(group)<=set(event['available_rank_ids']),'SimAI group unavailable in native fabric')
            batches={};selected=[]
            for rank in group:
                if rank not in self.schedulers:continue
                self.calls+=1;ready=self.schedulers[rank].on_schedule();require(len(ready)==1,'expected one upstream SimAI batch per active source')
                batch=ready[0];require(batch.num_prefill_tokens==0 and batch.num_tokens==[1]*batch.size,'SimAI did not schedule pure Decode')
                batch.on_schedule(time);batches[rank]=batch
                selected.extend(self.request_identity[id(q)] for q in batch.requests)
            expected={i for i,r in self.replay.records.items() if r['rank_id'] in group}
            require(set(selected)==expected and len(selected)==len(expected),'SimAI batch omitted or duplicated declared request')
            self.replay.active_ids=set(selected)
            wave=make_wave(self.service,group,index,self.steps[index],self.planes,self.replay)
            self.message_preflight.accept_wave(wave,now)
            directory=Path(output)/'graphs'/wave['graph_id'];export_wave(wave,directory,proto)
            history=dict(graph_id=wave['graph_id'],group_index=index,step=self.steps[index],start_ns=now,
                         selected_request_ids=sorted(selected),batch_request_ids={str(r):[self.request_identity[id(q)] for q in b.requests] for r,b in batches.items()})
            self.history.append(history);self.active[wave['graph_id']]=(index,batches,history);submissions.append(str(directory))
        finished=not self.active and all(n==self.service['workload']['decode_steps'] for n in self.steps)
        if finished:require(all(q.completed for q in self.requests.values()) and
            all(s.is_empty() and s.num_allocated_blocks==0 for s in self.schedulers.values()),'SimAI final requests or blocks did not drain')
        return dict(type='COMMIT',decision_id=event['decision_id'],now_ns=now,submissions=submissions,service_finished=finished)

    def report(self,transcript):
        return dict(history=self.history,transcript=transcript,scheduler_calls=self.calls,
            scheduler_source='SimAI_Vidur_real_Orca_Cluster_Batch_Request',simai_revision=self.receipt['revision'],
            initial_state='declared_ready_KV_not_executed_prefill',time_origin_seconds=self.origin_seconds,
            native_time_unit='ns',frontend_time_unit='seconds',
            cluster_gpu_counts=[len(c.replicas) for c in self.clusters],active_source_ranks=sorted(self.schedulers),
            final_allocated_blocks={str(r):s.num_allocated_blocks for r,s in self.schedulers.items()},
            requests=[dict(id=i,upstream_request_id=q.id,state='OBSERVATION_ENDED' if q.completed else 'INCOMPLETE',
                context_tokens=self.replay.records[i]['context_tokens'],num_processed_tokens=q.num_processed_tokens,
                initial_processed_tokens=self.replay.records[i]['context_tokens']+1,
                file_id=self.replay.records[i]['file_id']) for i,q in self.requests.items()])


def worker(output,backend,protoc):
    output=Path(output);config=checked_json(output/'input.json',1048576)
    topology=checked_json(output/'topology.json',8388608);protocol=checked_json(output/'rdma.json',65536)
    ranks,planes,replay=validate(config,topology,protocol,output)
    proto,_=_schema(output,protoc);bootstrap=output/'bootstrap';bootstrap.mkdir();(output/'graphs').mkdir()
    for rank in range(ranks):
        with (bootstrap/f'layer.{rank}.et').open('xb') as stream:write_message(stream,proto.GlobalMetadata(version='moe-simai-bootstrap/1'))
    save(output/'system.json',json.loads((ROOT/'examples/native_first_round/system.json').read_bytes()))
    from .service_transport import message_preflight,native_arguments
    frontend=Frontend(config,planes,replay,output/'simai_runtime',output,
                      message_preflight=message_preflight(topology,protocol,config['service']));transcript=[];child=None
    command=[backend,str(output/'topology.json'),str(output/'rdma.json'),str(output/'system.json'),str(bootstrap/'layer'),str(output/'result.json')]
    command.extend(native_arguments(config['service'],output))
    save(output/'native-command.json',command)
    try:
        with (output/'native.stderr.log').open('w') as errors,(output/'native.stdout.log').open('w',buffering=1) as logs:
            child=subprocess.Popen(command,cwd=output,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors,text=True)
            for line in child.stdout:
                logs.write(line)
                event=control_event(line)
                if event is None:continue
                reply=frontend.decide(event,output,proto)
                transcript.append(dict(event=event,reply=reply));child.stdin.write(json.dumps(reply)+'\n');child.stdin.flush()
            require(child.wait()==0,'native SimAI service failed; inspect native.stderr.log')
    finally:
        if child is not None and child.poll() is None:child.terminate();child.wait(timeout=5)
        save(output/'frontend.json',frontend.report(transcript))


def run(config,topology,protocol,runtime,backend,output,protoc='protoc'):
    config,topology,protocol,runtime,backend,output=map(lambda p:Path(p).resolve(),(config,topology,protocol,runtime,backend,output))
    c=checked_json(config,1048576);t=checked_json(topology/'topology.json',8388608);p=checked_json(protocol,65536)
    _,_,replay=validate(c,t,p,config.parent);verify_runtime(runtime)
    require(backend.is_file() and not output.exists(),'native backend absent or output exists')
    output.mkdir(parents=True);c=replay.snapshot(output)
    shutil.copytree(runtime,output/'simai_runtime',ignore=shutil.ignore_patterns('__pycache__'))
    save(output/'input.json',c);save(output/'topology.json',t);save(output/'rdma.json',p)
    save(output/'provenance.json',dict(frontend='actual_SimAI_Vidur',network='single_native_ns3_no_UB',
        compute_source=c['service']['compute']['source'],hardware_calibrated=False,scope='finite_MoE_segment',
        hashes={str(path):digest(path) for path in (config,topology/'topology.json',protocol,backend,Path(__file__))}))
    from .run_summary import write as write_summary
    write_summary(output)
    try:
        monitored([sys.executable,'-m','moe_ub_cosim.simai_service','--worker',str(output),str(backend),str(protoc)],
                  output/'execution',[output],runtime=limits(p))
    finally:
        write_summary(output)
    from .simai_verify import verify
    verdict=verify(output);save(output/'verification.json',verdict)
    return verdict


if __name__=='__main__':
    require(sys.argv[1:2]==['--worker'] and len(sys.argv)==5,'internal SimAI worker requires three paths')
    worker(*sys.argv[2:])
