"""Mature delayed complete-segment hedge, driven by native control deadlines."""
from copy import deepcopy
import json
from pathlib import Path

from .scaleout_topology import integer,require
from .whole_call import WholeCallConsumer


class DelayedWholeConsumer(WholeCallConsumer):
    def __init__(self,config,replay):
        super().__init__(config,replay)
        self.replay=replay
        require(self.option['policy']=='delayed_complete_segment/1','missing delayed whole policy')
        self.retirement_sequences={}
        for call in self.calls.values():call['attempts']['replica']['skipped_ns']=None

    def choose(self,event):
        """Use only complete results already observed before this DECIDE."""
        now=event['now_ns'];integer(now,'whole decision time',0,2**63-1)
        require(event['type']=='DECIDE' and now>=self.now,'invalid delayed decision time/type')
        require('wakeup_ns' in event and (event['wakeup_ns'] is None or
                (type(event['wakeup_ns']) is int and event['wakeup_ns']==now==self.option['hedge_after_ns'])),
                'missing or foreign native deadline decision')
        groups=[];wake=None
        for name,call in self.calls.items():
            primary,replica=(call['attempts'][role] for role in ('primary','replica'))
            if primary['submitted_ns'] is None:
                require(now==0,'delayed primary must start in initial decision')
                groups.append(primary['group_index'])
            if replica['submitted_ns'] is not None or replica['skipped_ns'] is not None:continue
            if primary['finished_ns'] is not None:
                require(primary['finished_ns']==now<=self.option['hedge_after_ns'],
                        'replica skip must follow primary completion at or before deadline')
                self.emit('attempt_skipped',name,'replica',now,self.retirement_sequences[primary['graph_id']])
                replica['skipped_ns']=now
            elif now>=self.option['hedge_after_ns']:
                require(now==self.option['hedge_after_ns'] and event.get('wakeup_ns')==now,
                        'replica launch requires exact native deadline decision')
                groups.append(replica['group_index'])
            else:wake=self.option['hedge_after_ns']
        return sorted(groups),wake

    def register(self,wave,now):
        graph=wave['graph_id'];require(graph in self.graphs,'undeclared delayed graph')
        name,role=self.graphs[graph];call=self.calls[name];attempt=call['attempts'][role]
        require(attempt['submitted_ns'] is None and attempt.get('skipped_ns') is None and
                wave['decode_step']==0 and wave['group_index']==attempt['group_index'] and
                wave['rank_ids']==self.config['parallel']['groups'][attempt['group_index']] and
                len(wave['routes'])==self.config['model']['layers'],'invalid delayed graph scope/lifecycle')
        integer(now,'delayed submit time',0,2**63-1)
        require(now==(0 if role=='primary' else self.option['hedge_after_ns']) and
                (role=='primary' or (call['attempts']['primary']['submitted_ns']==0 and call['winner'] is None)),
                'delayed submission outside legal decision')
        self.emit('attempt_submitted',name,role,now);attempt['submitted_ns']=now

    def accept(self,event):
        super().accept(event)
        if event['kind']=='graph_retired':self.retirement_sequences[event['graph_id']]=event['sequence']

    def snapshot(self):
        result=super().snapshot();result['schema_version']='moe-whole-call-report/2'
        result['execution_attempt_requests']=sum(len(c['request_pairs']) for c in self.calls.values()
            for a in c['attempts'].values() if a['submitted_ns'] is not None)
        result['reserved_attempt_requests']=2*result['logical_requests']
        result['all_attempts_retired']=all(a['finished_ns'] is not None or a.get('skipped_ns') is not None
            for c in self.calls.values() for a in c['attempts'].values())
        result['scope']='finite_declared_segment_client_result; delayed_preprovisioned_replicas; skipped_is_not_executed; no_tensor_or_KV_equivalence_validation'
        return result


def skipped_requests(config,frontend):
    option=config.get('whole_call_replication',{})
    if option.get('policy')!='delayed_complete_segment/1':return {}
    result={}
    for call in frontend['whole_call_replication']['calls'].values():
        skipped=call['attempts']['replica']['skipped_ns']
        if skipped is not None:
            for pair in call['request_pairs']:result[pair['replica_request']]=skipped
    return result


def logical_finish(report,history):
    require(report.get('schema_version')=='moe-whole-call-report/2' and report.get('all_attempts_retired') is True,
            'incomplete delayed whole report')
    deadline=report['configuration']['hedge_after_ns'];graphs=set();ends=[]
    require(isinstance(report.get('calls'),dict) and report['calls'],'missing delayed calls')
    for call in report['calls'].values():
        attempts=call['attempts'];require(set(attempts)=={'primary','replica'},'invalid delayed roles')
        completed={}
        for role,a in attempts.items():
            graph=a['graph_id'];require(graph not in graphs,'reused delayed graph')
            if role=='replica' and a['skipped_ns'] is not None:
                integer(a['skipped_ns'],'delayed skip time',0,2**63-1)
                require(a['submitted_ns'] is None and a['finished_ns'] is None and graph not in history and
                        a['skipped_ns']==attempts['primary']['finished_ns']<=deadline,
                        'skipped replica executed or skipped without primary result')
                continue
            require(type(a['submitted_ns']) is int and type(a['finished_ns']) is int and
                    (a['submitted_ns'],a['finished_ns'])==history.get(graph) and
                    a['submitted_ns']==(0 if role=='primary' else deadline),'delayed graph lifetime differs')
            graphs.add(graph);completed[role]=a['finished_ns']
        winner=call['winner'];end=min(completed.values())
        require(winner in completed and completed[winner]==end==call['result_ready_ns'],
                'delayed result precedes a whole attempt')
        ends.append(end)
    require(graphs==set(history) and report['result_ready_ns']==max(ends),'delayed graph coverage/final result differs')
    return max(ends)


def audit(directory,config,result,frontend):
    from .scaleout_rdma import checked_json
    from .scaleout_replay import Replay
    directory=Path(directory);consumer=DelayedWholeConsumer(config,Replay(config,directory))
    rows=iter(frontend['transcript']);decisions=0;observed=[]
    from .native_control import event as control_event
    for line in (directory/'native.stdout.log').read_text().splitlines(keepends=True):
        if not line.startswith('MOE_RDMA_EVENT '):continue
        event=control_event(line,observations=True)
        if event['type']=='OBSERVE':consumer.accept(event);observed.append(event)
        else:
            require(event['type']=='DECIDE','foreign delayed control event')
            row=next(rows,None);require(row is not None and row['event']==event,'delayed native transcript differs')
            groups,wake=consumer.choose(event);reply=row['reply']
            require(reply['wake_at_ns']==wake and reply['now_ns']==event['now_ns'] and
                    reply['decision_id']==event['decision_id'] and reply['type']=='COMMIT',
                    'delayed decision identity/deadline differs')
            graphs=[Path(s).name for s in reply['submissions']]
            require(graphs==[f'g{i}.w0' for i in groups],'delayed submission differs from actual prefix policy')
            for graph in graphs:consumer.register(checked_json(directory/'graphs'/graph/'wave.json',16*2**20),event['now_ns'])
            decisions+=1
    require(next(rows,None) is None and decisions==result['service']['decisions'],'extra/missing delayed decision')
    require(observed==[json.loads(line) for line in (directory/'execution_events.jsonl').read_text().splitlines()],
            'delayed raw OBSERVE stream differs')
    expected=consumer.snapshot()
    require(expected==frontend['whole_call_replication']==checked_json(directory/'whole_call_report.json',2**20),
            'delayed result differs from native prefix policy')
    path=directory/'whole_call_events.jsonl'
    require(path.stat().st_size<=config['whole_call_replication']['max_events']*1024 and
            [json.loads(line) for line in path.read_text().splitlines()]==consumer.events,'delayed lifecycle differs')
    logical_finish(expected,{w['graph_id']:(w['start_ns'],w['finished_ns']) for w in result['service']['waves']})
    from .service_wakeup import audit as audit_wakeup
    audit_wakeup(config,result,frontend)
    audit_standby(config,consumer.replay,frontend)
    return expected


def audit_standby(config,replay,frontend):
    """Rebuild reserved KV and actual releases from source contexts and decisions."""
    from .scaleout_llm import capacity
    groups=config['parallel']['groups'];reserved={};releases=[];released=set()
    for call in config['whole_call_replication']['calls']:
        index=call['replica_group'];group=groups[index]
        kv_token=capacity(config,len(group))['kv_bytes_per_token']
        reserved[index]=sum(((context+config['workload']['decode_steps']+15)//16)*16*kv_token
                            for rank in group for context in replay.contexts(rank))
    for row in frontend['transcript']:
        event=row['event'];now=event['now_ns']
        for done in event['completed']:
            for index in reserved:
                if done['graph_id']==f'g{index}.w0':
                    require(index not in released,'replica KV released twice')
                    releases.append(dict(group_index=index,time_ns=now,kind='executed_release',bytes=reserved[index]))
                    released.add(index)
        for call in frontend['whole_call_replication']['calls'].values():
            attempt=call['attempts']['replica'];index=attempt['group_index']
            if attempt['skipped_ns']==now and index not in released:
                releases.append(dict(group_index=index,time_ns=now,kind='skipped_release',bytes=reserved[index]))
                released.add(index)
    require(released==set(reserved) and frontend.get('standby_memory')==dict(
        initial_reserved_bytes=sum(reserved.values()),releases=releases,final_used_bytes=0),
        'standby KV reservation/release differs from source contexts and actual lifecycle')
