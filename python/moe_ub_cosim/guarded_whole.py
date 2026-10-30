"""Polled source-confirmation timeouts admit complete replicas in native time.

No SOURCE_ACK_TIMEOUT frames are manufactured. One existing native control
timer polls actual post/completion prefixes and retains a full-call fallback.
"""
from copy import deepcopy

from .sequential_whole import SequentialWholeConsumer,logical_finish as sequential_finish
from .scaleout_topology import fields,integer,require


class GuardedWholeConsumer(SequentialWholeConsumer):
    def __init__(self,config,replay):
        super().__init__(config,replay)
        require(self.option['schema_version']=='moe-whole-call-replication/5','missing source confirmation guard format')
        self.timeouts={c['id']:c['source_ack_timeout_ns'] for c in self.option['calls']}
        self.bindings={};self.confirmations={};self.guard_decisions=[];self.authorized={}
        self.observed_sequence=-1;self.observed_time=-1

    def replica_allowed_time(self,name,now):
        return self.authorized.get(name)==now

    def register(self,wave,now):
        name,role=self.graphs.get(wave['graph_id'],(None,None))
        tracked=role=='primary' and self.timeouts.get(name) is not None
        if tracked:
            require(len(self.bindings)+len(wave['messages'])<=self.option['max_guard_messages'] and
                    all(m['id'] not in self.bindings for m in wave['messages']),'guard message binding budget/identity violation')
        super().register(wave,now)
        if tracked:
            for m in wave['messages']:
                self.bindings[m['id']]=dict(call=name,graph=wave['graph_id'],rank=m['src'])

    def accept(self,event):
        require(event.get('schema_version')=='moe-execution-event/1' and event.get('type')=='OBSERVE','invalid guarded observation')
        sequence=event['sequence'];now=event['time_ns']
        integer(sequence,'guard source sequence',0,262143);integer(now,'guard observed time',0,2**63-1)
        require(sequence>self.observed_sequence and now>=self.observed_time,'guard observation order violation')
        graph=event.get('graph_id');identity=self.graphs.get(graph);kind=event['kind']
        if kind in ('message_send_post','message_send_complete') and identity and identity[1]=='primary' and self.timeouts[identity[0]] is not None:
            fields(event,'schema_version type sequence time_ns kind graph_id rank message','guard source callback')
            mid=event['message'];require(mid in self.bindings and self.bindings[mid]==dict(call=identity[0],graph=graph,rank=event['rank']),
                                         'foreign or unregistered guard message')
            if kind=='message_send_post':
                require(mid not in self.confirmations,'duplicate guard post')
                self.confirmations[mid]=dict(call=identity[0],graph=graph,rank=event['rank'],post_ns=now,ack_ns=None)
            else:
                require(mid in self.confirmations and self.confirmations[mid]['ack_ns'] is None,'guard confirmation without live post')
                self.confirmations[mid]['ack_ns']=now
        super().accept(event)
        self.observed_sequence=sequence;self.observed_time=now

    def choose(self,event):
        now=event['now_ns'];integer(now,'guard decision time',0,2**63-1)
        require(event['type']=='DECIDE' and now>=max(self.now,self.observed_time) and 'wakeup_ns' in event and
                (event['wakeup_ns'] is None or (type(event['wakeup_ns']) is int and event['wakeup_ns']==now)),
                'invalid guarded native decision')
        enabled=sum(t is not None for t in self.timeouts.values())
        require(len(self.guard_decisions)+enabled<=self.option['max_guard_events'],'guard decision budget exceeded')
        groups=[];wake=[]
        for name,call in self.calls.items():
            p,r=(call['attempts'][role] for role in ('primary','replica'));fallback=self.deadlines[name];timeout=self.timeouts[name]
            if p['submitted_ns'] is None:
                require(now==0,'guarded primary must start at0');groups.append(p['group_index'])
            pending=sorted(mid for mid,m in self.confirmations.items() if m['call']==name and m['ack_ns'] is None)
            overdue=[mid for mid in pending if timeout is not None and self.confirmations[mid]['post_ns']+timeout<=now]
            reason='already_started';next_check=None
            if r['submitted_ns'] is None and r['skipped_ns'] is None:
                if p['finished_ns'] is not None:
                    require(p['finished_ns']==now<=fallback,'guard skip requires complete primary before fallback')
                    final=f'g{p["group_index"]}.w{self.steps-1}'
                    self.emit('attempt_skipped',name,'replica',now,self.retirement_sequences[final]);r['skipped_ns']=now
                    reason='primary_complete'
                elif now>=fallback or overdue:
                    require(now<=fallback,'guard missed full-call fallback')
                    reason='fallback' if now==fallback else 'source_timeout'
                    self.authorized[name]=now;groups.append(r['group_index'])
                else:
                    reason='poll'
                    next_check=min([fallback]+([now+timeout]+[self.confirmations[mid]['post_ns']+timeout for mid in pending] if timeout is not None else []))
                    require(next_check>now,'guard next poll is not future');wake.append(next_check)
            elif r['skipped_ns'] is not None:reason='primary_complete'
            if timeout is not None:
                self.guard_decisions.append(dict(sequence=len(self.guard_decisions),time_ns=now,call=name,reason=reason,
                    observed_sequence=self.observed_sequence,pending_messages=pending,overdue_messages=overdue,next_deadline_ns=next_check))
            for attempt in (p,r):
                if attempt['submitted_ns'] is not None and attempt['finished_ns'] is None and attempt['waves'][-1]['finished_ns'] is not None:
                    require(attempt['waves'][-1]['finished_ns']==now,'guarded next Decode must follow actual completion')
                    groups.append(attempt['group_index'])
        return sorted(groups),min(wake) if wake else None

    def snapshot(self):
        result=super().snapshot();result['schema_version']='moe-whole-call-report/4'
        result['source_ack_guard']=dict(schema_version='moe-source-ack-guard-report/1',decisions=deepcopy(self.guard_decisions),
            messages={str(mid):deepcopy(value) for mid,value in sorted(self.confirmations.items())})
        return result


def logical_finish(report,history):
    require(report.get('schema_version')=='moe-whole-call-report/4','missing guarded whole report')
    guard=report['source_ack_guard'];fields(guard,'schema_version decisions messages','source guard report')
    require(guard['schema_version']=='moe-source-ack-guard-report/1','invalid source guard report')
    rows=guard['decisions'];require([r['sequence'] for r in rows]==list(range(len(rows))) and
            len(rows)<=report['configuration']['max_guard_events'],'guard decision coverage/budget differs')
    for spec in report['configuration']['calls']:
        call=report['calls'][spec['id']];replica=call['attempts']['replica'];submitted=replica['submitted_ns']
        launches=[r for r in rows if r['call']==spec['id'] and r['reason'] in ('source_timeout','fallback')]
        if spec['source_ack_timeout_ns'] is not None:
            require(len(launches)==(submitted is not None),'guard launch count differs')
            if launches:
                row=launches[0];require(row['time_ns']==submitted,'guard launch time differs')
                if row['reason']=='fallback':require(submitted==spec['hedge_after_ns'],'early fallback')
                else:
                    require(row['overdue_messages'] and submitted<spec['hedge_after_ns'],'source timeout without overdue work')
                    for mid in row['overdue_messages']:
                        m=guard['messages'][str(mid)]
                        require(m['call']==spec['id'] and m['post_ns']+spec['source_ack_timeout_ns']<=submitted and
                                (m['ack_ns'] is None or m['ack_ns']>=submitted),'guard deadline without pending source confirmation')
        elif submitted is not None:require(submitted==spec['hedge_after_ns'],'unguarded call started early')
    return sequential_finish(report,history)
