"""Complete finite Decode sequences; an intermediate graph is not a result."""
from .delayed_whole import DelayedWholeConsumer
from .whole_call import WholeCallConsumer,deadlines
from .scaleout_topology import fields,integer,require


class SequentialWholeConsumer(DelayedWholeConsumer):
    def __init__(self,config,replay):
        super().__init__(config,replay)
        require(self.option['schema_version'] in ('moe-whole-call-replication/4','moe-whole-call-replication/5'),'missing sequential format')
        self.steps=config['workload']['decode_steps']
        for name,call in self.calls.items():
            for role,a in call['attempts'].items():
                a['waves']=[]
                for step in range(self.steps):self.graphs[f'g{a["group_index"]}.w{step}']=(name,role)

    def choose(self,event):
        now=event['now_ns'];integer(now,'whole decision time',0,2**63-1)
        require(event['type']=='DECIDE' and now>=self.now and 'wakeup_ns' in event and
                (event['wakeup_ns'] is None or (type(event['wakeup_ns']) is int and
                 event['wakeup_ns']==now and now in self.deadlines.values())), 'invalid sequential decision')
        groups=[];wake=None
        for name,call in self.calls.items():
            deadline=self.deadlines[name];p,r=(call['attempts'][role] for role in ('primary','replica'))
            if p['submitted_ns'] is None:
                require(now==0,'primary sequence must start at0');groups.append(p['group_index'])
            if r['submitted_ns'] is None and r['skipped_ns'] is None:
                if p['finished_ns'] is not None:
                    require(p['finished_ns']==now<=deadline,'skip requires complete primary sequence')
                    final=f'g{p["group_index"]}.w{self.steps-1}'
                    self.emit('attempt_skipped',name,'replica',now,self.retirement_sequences[final]);r['skipped_ns']=now
                elif now>=deadline:
                    require(now==deadline and event['wakeup_ns']==now,'replica requires exact deadline')
                    groups.append(r['group_index'])
                else:wake=deadline if wake is None else min(wake,deadline)
            for a in (p,r):
                if a['submitted_ns'] is not None and a['finished_ns'] is None and a['waves'][-1]['finished_ns'] is not None:
                    require(a['waves'][-1]['finished_ns']==now,'next Decode must follow actual preceding completion')
                    groups.append(a['group_index'])
        return sorted(groups),wake

    def register(self,wave,now):
        graph=wave['graph_id'];require(graph in self.graphs,'undeclared sequential graph')
        name,role=self.graphs[graph];call=self.calls[name];a=call['attempts'][role];step=len(a['waves'])
        integer(now,'sequential submit time',0,2**63-1)
        require(step<self.steps and wave['decode_step']==step and graph==f'g{a["group_index"]}.w{step}' and
                wave['group_index']==a['group_index'] and wave['rank_ids']==self.config['parallel']['groups'][a['group_index']] and
                len(wave['routes'])==self.config['model']['layers'] and a.get('skipped_ns') is None and a['finished_ns'] is None,
                'sequential graph scope/order differs')
        if step==0:
            require(a['submitted_ns'] is None and (now==0 if role=='primary' else self.replica_allowed_time(name,now)) and
                    (role=='primary' or (call['attempts']['primary']['submitted_ns']==0 and call['winner'] is None)),
                    'sequential initial submission outside legal decision')
            self.emit('attempt_submitted',name,role,now);a['submitted_ns']=now
        else:
            require(a['waves'][-1]['finished_ns']==now and now>=self.now,'next graph before previous actual completion')
        a['waves'].append(dict(graph_id=graph,step=step,submitted_ns=now,finished_ns=None))

    def replica_allowed_time(self,name,now):
        return now==self.deadlines[name]

    def next_graphs(self,groups):
        """Names of the actual next graphs, before registering these submissions."""
        attempts={a['group_index']:a for call in self.calls.values() for a in call['attempts'].values()}
        require(all(i in attempts and len(attempts[i]['waves'])<self.steps for i in groups),'no next sequential graph')
        return [f'g{i}.w{len(attempts[i]["waves"])}' for i in groups]

    def accept(self,event):
        if event['kind']!='graph_retired':return
        fields(event,'schema_version type sequence time_ns kind graph_id','sequential callback')
        require(event['schema_version']=='moe-execution-event/1' and event['type']=='OBSERVE','invalid sequential callback')
        integer(event['time_ns'],'sequential result time',0,2**63-1);integer(event['sequence'],'source sequence',0,262143)
        graph=event['graph_id'];require(graph in self.graphs,'foreign sequential graph')
        name,role=self.graphs[graph];a=self.calls[name]['attempts'][role]
        require(a['waves'] and a['waves'][-1]['graph_id']==graph and a['waves'][-1]['finished_ns'] is None and
                a['waves'][-1]['submitted_ns']<=event['time_ns'] and event['time_ns']>=self.now and
                event['sequence']>self.last_source_sequence,'sequential callback without live graph/order')
        if len(a['waves'])==self.steps:
            # The mature consumer sees only the actual FINAL graph. It retains
            # the full-attempt winner and late-loser semantics unchanged.
            WholeCallConsumer.accept(self,event)
        else:self.last_source_sequence=event['sequence'];self.now=event['time_ns']
        a['waves'][-1]['finished_ns']=event['time_ns'];self.retirement_sequences[graph]=event['sequence']

    def snapshot(self):
        r=super().snapshot();r['schema_version']='moe-whole-call-report/3';r['decode_steps']=self.steps
        r['scope']='finite_multi_decode_segment; actual_predecessor_completion; preexisting_replicas_KV; no_tensor_equivalence_or_arrival_process'
        return r


def logical_finish(report,history):
    guarded=report.get('schema_version')=='moe-whole-call-report/4'
    require(report.get('schema_version') in ('moe-whole-call-report/3','moe-whole-call-report/4') and report.get('all_attempts_retired') is True,
            'incomplete sequential whole report')
    option=report['configuration'];by_call=deadlines(option);steps=report['decode_steps'];integer(steps,'Decode steps',1,128)
    require(option['schema_version']==('moe-whole-call-replication/5' if guarded else 'moe-whole-call-replication/4') and set(report['calls'])==set(by_call),
            'sequential call coverage differs')
    specs={c['id']:c for c in option['calls']};graphs=set();ends=[]
    for name,call in report['calls'].items():
        attempts=call['attempts'];require(set(attempts)=={'primary','replica'},'invalid sequential roles');done={}
        for role,a in attempts.items():
            index=specs[name][role+'_group'];require(a['group_index']==index and a['graph_id']==f'g{index}.w0','foreign sequential group')
            if role=='replica' and a['skipped_ns'] is not None:
                require(a['submitted_ns'] is None and a['finished_ns'] is None and a['waves']==[] and
                        a['skipped_ns']==attempts['primary']['finished_ns']<=by_call[name], 'skipped sequence executed')
                continue
            require(type(a['submitted_ns']) is int and type(a['finished_ns']) is int and len(a['waves'])==steps and
                    ((a['submitted_ns']==0) if role=='primary' else
                     (0<=a['submitted_ns']<=by_call[name] if guarded else a['submitted_ns']==by_call[name])), 'incomplete sequential lifetime')
            previous=a['submitted_ns']
            for step,wave in enumerate(a['waves']):
                g=f'g{index}.w{step}'
                require(wave['graph_id']==g and wave['step']==step and g not in graphs and
                        type(wave['submitted_ns']) is int and type(wave['finished_ns']) is int and
                        wave['submitted_ns']==previous<=wave['finished_ns'] and
                        history.get(g)==(wave['submitted_ns'],wave['finished_ns']), 'sequence differs from actual graph history')
                graphs.add(g);previous=wave['finished_ns']
            require(a['finished_ns']==previous,'attempt finished before final Decode');done[role]=previous
        end=min(done.values());winner=call['winner']
        require(winner in done and done[winner]==end==call['result_ready_ns'],'result before entire sequence')
        ends.append(end)
    require(graphs==set(history) and report['result_ready_ns']==max(ends),'sequential graph coverage/final result differs')
    return max(ends)
