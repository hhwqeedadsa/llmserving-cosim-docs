"""Modern operation controller driven by actual native functional callbacks.

No readiness creates a completion. Local control/slot steps have declared zero
cost; memory copies and declared compute run on the native serial-per-GPU lane.
The explicit native-apply visibility model does not implement GPU coherence.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

from .deepep_plan import require
from .modern_deepep_windows import ModernDeepEPWindowRuntime
from .modern_native_layout import ModernNativeLayout, MAIN_BASE, SIGNAL_BASE
from .scaleout_rdma import checked_json, digest, limits, monitored, save
from .transaction_session import NativeTransactionSession


class ModernNativeController:
    def __init__(self,topology,protocol,config):
        self.layout=ModernNativeLayout(topology,protocol,config)
        self.runtime=ModernDeepEPWindowRuntime(config['modern'])
        self.events={e['id']:e for e in self.layout.plan['events']}
        self.operations={o['id']:o for o in self.layout.plan['operations']}
        self.native_nodes=[];self.tasks={};self.waiting={};self.callbacks={};self.proofs={};self.coverage={}
        self.count_preparations={};self.log=[];self.now=0;self.decisions=0

    def _proof(self,event):
        pairs=[]
        for p in self.events[event]['deps']:pairs.extend(self.proofs[p])
        return self._compact(pairs)

    def _compact(self,pairs):
        merged={}
        for identity,event in pairs:
            require(event in ('applied','completed'),'internal unsupported native evidence')
            merged[identity]=max(merged.get(identity,0),1 if event=='completed' else 0)
        return [(i,'completed' if level else 'applied') for i,level in sorted(merged.items())
            if not any(j!=i and self.coverage[j].get(i,-1)>=level for j in merged)]

    def _schedule(self,node,task,proof):
        identity=len(self.native_nodes);require(identity<65536,'native node identity limit')
        proof=self._compact(proof);require(len(proof)<=32,'native proof frontier exceeds 32 dependencies')
        node=dict(node,id=identity,after=[dict(operation=i,event=e) for i,e in proof])
        if node['kind']=='remote':require(sum(n['kind']=='remote' for n in self.native_nodes)<1024,'remote operation limit')
        else:require(sum(n['kind']=='local' for n in self.native_nodes)<256,'local operation limit')
        closure={}
        for i,event in proof:
            for p,level in self.coverage[i].items():closure[p]=max(closure.get(p,-1),level)
            closure[i]=max(closure.get(i,-1),int(event=='completed'))
        self.coverage[identity]=closure;self.native_nodes.append(node);self.tasks[identity]=deepcopy(task)
        return identity

    def _local(self,rank,duration,copy=None,wait=None):
        node=dict(kind='local',rank=self.layout.node(rank),duration_ns=duration)
        if copy is not None:node['copy']=copy
        if wait:node['local_wait']=wait
        return node

    @staticmethod
    def _copy(source,target):
        return dict(source=deepcopy(source),target={k:target[k] for k in ('key','address')})

    def _prepare_counts(self,op,proof):
        key=op['call'],op['src']
        if key not in self.count_preparations:
            target=self.layout.main(self.layout.windows.count_send_range(op['src'],op['call']))
            source=self.layout.count_sources[key]
            node=self._local(op['src'],self.layout.config['costs']['source_prepare_ns'],self._copy(source,target))
            self.count_preparations[key]=self._schedule(node,dict(role='count_prepare',call=key[0],rank=key[1]),proof)
        native=self.count_preparations[key]
        if (native,'completed') not in self.callbacks:return None
        return (native,'completed')

    def _issue(self,event):
        op=self.runtime.operation(event['operation']);proof=self._proof(event['id'])
        if op['kind'] in ('rank_count','expert_count_block'):
            prepared=self._prepare_counts(op,proof)
            if prepared is None:return False
            proof.append(prepared)
        target=self.layout.target(op)
        if op['path']=='local':
            source=None
            if op['kind']=='rank_count':
                source=dict(self.layout.count_sources[op['call'],op['src']]);source['address']+=op['dst']*8;source['bytes']=8
            elif op['phase']=='dispatch':source=self.layout.inputs[self.layout.token_key(op)]
            # Local Combine's reduction has already written its actual target.
            node=self._local(op['src'],self.layout.config['costs']['local_copy_ns'],
                self._copy(source,target) if source is not None else None)
        else:
            node=dict(kind='remote',connection=self.layout.connection(op),key=target['key'],address=target['address'],
                aggregate_requests=op.get('aggregate_requests',False))
            if op['kind']=='barrier_signal':node.update(verb='fetch_add',value=op['value'])
            elif op['kind']=='rank_count':node.update(verb='put',hex=op['value'].to_bytes(8,'little',signed=True).hex())
            else:node.update(verb='put',source=self.layout.main(op['memory']['source']))
        identity=self._schedule(node,dict(role='operation',modern_operation=op['id'],path=op['path'],event=event['id']),proof)
        self.waiting[event['id']]=identity
        return True

    def _local_event(self,event):
        name=event['id'];rank=event['rank'];kind=event['kind'];call=int(name.split('.')[0][1:]);proof=self._proof(name)
        costs=self.layout.config['costs'];copy=None;wait=None;duration=0
        if kind=='token_prepared':
            op=next(o for o in self.operations.values() if o['phase']=='dispatch' and o['kind']=='token_data' and
                o['call']==call and o['src']==rank and name==f"m{call}.dispatch.s{rank}.t{o['token']}.prepared")
            source=self.layout.inputs[self.layout.token_key(op)]
            target=dict(key=1,address=MAIN_BASE+self.layout.description['main_window']['ep_buffer_base']+op['source']['byte_offset'])
            copy=self._copy(source,target);duration=costs['source_prepare_ns']
        elif kind=='local_expert_reduction_done':
            op=self.operations[name.replace('.combine.s','.combine.data.s').removesuffix('.reduced')]
            resolved=self.layout.windows.resolve(op)
            target=self.layout.main(resolved['target'] if op['path']=='local' else resolved['source'])
            copy=self._copy(self.layout.inputs[self.layout.token_key(op)],target);duration=costs['reduction_ns']
        elif kind=='expert_stage_done':duration=costs['expert_ns']
        elif kind=='counts_ready':
            source=self.layout.zero_sources[rank]
            target=dict(key=1,address=MAIN_BASE+self.layout.description['counts']['recv_base'])
            copy=self._copy(source,target)
        elif kind=='barrier_ready':
            wait=[dict(key=2,address=SIGNAL_BASE+8*sender,compare='ge',value=event['rolling_target'])
                  for sender in range(self.layout.ranks)]
        else:return False
        native=self._schedule(self._local(rank,duration,copy,wait),dict(role='event',event=name),proof)
        self.waiting[name]=native
        return True

    def _fire(self,name,proof,native=None,native_event=None):
        row=self.runtime.fire(name);self.proofs[name]=self._compact(proof)
        row.update(time_ns=self.now,origin='native_callback' if native is not None else 'zero_cost_control')
        if native is not None:
            row.update(native_operation=native,native_event=native_event,native_time_ns=self.callbacks[native,native_event])
        self.log.append(row)

    def advance(self,decision):
        require(type(decision['now_ns']) is int and self.now<=decision['now_ns'],'nonmonotonic native decision')
        self.now=decision['now_ns'];self.decisions+=1
        require(self.decisions<=256,'native session decision limit reached')
        before=len(self.native_nodes)
        for row in decision['events']:
            identity=row.get('operation');event=row.get('event');key=identity,event
            require(identity in self.tasks and key not in self.callbacks,'unknown or duplicate native callback')
            require(type(row['time_ns']) is int and 0<=row['time_ns']<=self.now,'future native callback')
            local=self.native_nodes[identity]['kind']=='local'
            require(event in (('local_admitted','local_started','completed') if local else
                ('submitted','prepared','published','applied','request_acked','response','completed')),'invalid native callback kind')
            parents={'local_started':['local_admitted'],'prepared':['submitted'],'published':['prepared'],
                     'applied':['published'],'request_acked':['published'],'response':['applied']}.get(event,[])
            if event=='completed':
                parents=['local_started'] if local else ['applied']
                if not local:
                    node=self.native_nodes[identity];connection=self.layout.connections[node['connection']]
                    if 'self_service' not in connection:
                        parents+=['request_acked']
                        if node['verb']=='fetch_add':parents+=['response']
            require(all((identity,p) in self.callbacks and self.callbacks[identity,p]<=row['time_ns'] for p in parents),
                'native callback precedes its actual prerequisite')
            self.callbacks[key]=row['time_ns']
        changed=True
        while changed:
            changed=False
            for name in self.runtime.ready():
                # An earlier event may dynamically add a channel dependency.
                if name not in self.runtime.ready():continue
                event=self.events[name];kind=event['kind'];proof=self._proof(name)
                if kind in ('issued','accepted','visible','local_complete'):
                    issued=self.operations[event['operation']]['events']['issued']
                    if issued not in self.waiting:
                        require(kind=='issued','completion without native submission')
                        changed=self._issue(event) or changed
                        continue
                    native=self.waiting[issued];local=self.native_nodes[native]['kind']=='local'
                    callback=('local_started' if local else 'submitted') if kind=='issued' else (
                        'completed' if local or kind=='local_complete' else 'applied')
                    if (native,callback) not in self.callbacks:continue
                    if callback in ('applied','completed'):proof.append((native,callback))
                    self._fire(name,proof,native,callback);changed=True
                elif name in self.waiting:
                    native=self.waiting[name]
                    if (native,'completed') not in self.callbacks:continue
                    self._fire(name,proof+[(native,'completed')],native,'completed');changed=True
                elif self._local_event(event):changed=True
                else:self._fire(name,proof);changed=True
        return deepcopy(self.native_nodes[before:])

    def snapshot(self):
        return dict(schema_version='moe-modern-native-execution/1',functional_provider='functional_mrt1/1',
            target_visibility='native_apply_functional/1',runtime=self.runtime.snapshot(),events=deepcopy(self.log),
            native_tasks={str(i):deepcopy(t) for i,t in self.tasks.items()},bindings=deepcopy(self.layout.bindings),
            native_operations=deepcopy(self.native_nodes),decisions=self.decisions,
            gpu_visibility_modeled=False,hardware_calibrated=False,numeric_expert_computation=False,
            scope='native callback-driven modern functional graph and synthetic full-size operands; not GIN/GPU timing')


def worker(topology,protocol,config,backend,output):
    t,p,c=(checked_json(Path(path),maximum) for path,maximum in ((topology,8*2**20),(protocol,65536),(config,4*2**20)))
    output=Path(output);controller=ModernNativeController(t,p,c)
    save(output/'modern-plan.json',controller.layout.plan);save(output/'functional-windows.json',controller.layout.description)
    try:
        with NativeTransactionSession(topology,protocol,controller.layout.initial,backend,output) as session:
            while (event:=session.receive()) is not None:
                nodes=controller.advance(event)
                finished=not nodes and event['drained'] and controller.runtime.snapshot()['all_events_fired']
                require(nodes or finished or not event['drained'],'modern graph blocked while native network idle; preserve partial state')
                session.commit(nodes,finish=finished)
    finally:
        save(output/'modern-execution.json',controller.snapshot())


def run(topology,protocol,config,backend,output):
    topology,protocol,config,backend,output=(Path(p).resolve() for p in (topology,protocol,config,backend,output))
    t,p,c=(checked_json(path,maximum) for path,maximum in ((topology/'topology.json',8*2**20),(protocol,65536),(config,4*2**20)))
    ModernNativeLayout(t,p,c)  # Pure admission before creating output or native network.
    require(backend.is_file() and os.access(backend,os.X_OK),'missing native transaction-session executable')
    output.mkdir(exist_ok=False)
    for name,value in (('topology.json',t),('protocol.json',p),('input.json',c)):save(output/name,value)
    save(output/'provenance.json',dict(frontend='modern_native_functional/1',gpu_visibility_modeled=False,
        hashes={str(path):digest(path) for path in (topology/'topology.json',protocol,config,backend,Path(__file__))}))
    command=[sys.executable,'-P','-B','-m','moe_ub_cosim.modern_native','--worker',output/'topology.json',
        output/'protocol.json',output/'input.json',backend,output]
    budget=limits(p);budget.update(wall_seconds=min(60,budget['wall_seconds']),rss_bytes=min(2**30,budget['rss_bytes']),
        address_bytes=min(2**31,budget['address_bytes']),artifact_bytes=min(64*2**20,budget['artifact_bytes']))
    monitored(command,output/'execution',[output],runtime=budget)
    result=checked_json(output/'result.json',budget['result_json_bytes']);modern=checked_json(output/'modern-execution.json',16*2**20)
    require(result['passed'] and result['session']['finished'] and modern['runtime']['all_events_fired'] and
        result['transactions']['transport']['physical_drained'],'modern native execution incomplete')
    return dict(passed=True,output=str(output),finish_ns=result['finish_ns'],functional_modern_graph_executed=True,
        gpu_visibility_modeled=False,numeric_expert_computation=False)


if __name__=='__main__':
    require(sys.argv[1:2]==['--worker'] and len(sys.argv)==7,'internal modern worker requires five paths')
    worker(*sys.argv[2:])
