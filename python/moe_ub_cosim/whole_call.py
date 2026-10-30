"""Explicit whole-segment replicas and a first-complete client consumer.

No partial result replaces a collective dependency. Numerical tensors and
equivalent initial KV are declarations, not verified by routing records.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .scaleout_topology import fields, integer, require


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def configuration(config):
    if 'whole_call_replication' not in config:return None
    value=config['whole_call_replication']
    require(isinstance(value,dict),'invalid whole call replication object')
    sequential=value.get('schema_version')=='moe-whole-call-replication/4'
    per_call=value.get('schema_version') in ('moe-whole-call-replication/3','moe-whole-call-replication/4')
    delayed=value.get('schema_version') in ('moe-whole-call-replication/2','moe-whole-call-replication/3','moe-whole-call-replication/4')
    fields(value,'schema_version policy initial_state max_events calls'+
           (' max_wakeup_events' if per_call else ' hedge_after_ns max_wakeup_events' if delayed else ''),'whole call replication')
    require(((value['schema_version']=='moe-whole-call-replication/1' and value['policy']=='eager_complete_segment/1') or
             (delayed and value['policy']=='delayed_complete_segment/1')) and
            value['initial_state']=='declared_equivalent_ready_kv/1','unsupported whole call replication semantics')
    if delayed:
        if not per_call:integer(value['hedge_after_ns'],'hedge deadline',1,4999999)
        integer(value['max_wakeup_events'],'wakeup event budget',2,65536)
        require('source_ack_watchdog' not in config and 'contribution_tracking' not in config,
                'delayed whole replicas do not combine watchdog or contribution observers')
    require(config.get('schema_version')=='moe-scaleout-service/1' and
            type(config.get('workload',{}).get('decode_steps')) is int and
            (1<=config['workload']['decode_steps']<=128 if sequential else config['workload']['decode_steps']==1) and
            config.get('route_replay',{}).get('request_layout')=='sparse_explicit/1' and
            config.get('phase_completion',{}).get('mode')=='ep_transport_flush/1' and
            config.get('token_transport',{}).get('combine')=='rank_reduced/1' and
            config.get('execution_events',{}).get('schema_version')=='moe-execution-events/1' and
            'transport' in config and 'inflight_helpers' not in config,
            'whole replicas require explicit finite replay steps (multiple only in /4), full EP completion, rank-reduced transport and events/1; no partial helpers')
    integer(value['max_events'],'whole call max events',1,65536)
    calls=value['calls'];groups=config['parallel']['groups'];used=set();names=set()
    require(isinstance(groups,list) and 2<=len(groups)<=64,'invalid whole EP groups')
    flat=[]
    for group in groups:
        require(isinstance(group,list) and 2<=len(group)<=256 and
                all(type(r) is int and 0<=r<=511 for r in group) and group==sorted(set(group)),
                'invalid whole physical EP group')
        flat.extend(group)
    require(sorted(flat)==list(range(len(flat))),'whole EP groups must partition physical ranks')
    require(isinstance(calls,list) and 1<=len(calls)<=32,'invalid whole call count')
    for call in calls:
        fields(call,'id primary_group replica_group'+(' hedge_after_ns' if per_call else ''),'whole call')
        if per_call:integer(call['hedge_after_ns'],'call hedge deadline',1,4999999)
        require(isinstance(call['id'],str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',call['id']) and
                call['id'] not in names,'invalid or duplicate whole call identity')
        names.add(call['id'])
        for key in ('primary_group','replica_group'):
            index=call[key];integer(index,key,0,len(groups)-1)
            require(index not in used,'reused whole call EP group');used.add(index)
        require(len(groups[call['primary_group']])==len(groups[call['replica_group']]),'whole replica EP sizes differ')
    require(used==set(range(len(groups))) and 7*len(calls)<=value['max_events'],
            'whole calls must partition groups and fit lifecycle budget')
    return value


def deadlines(option):
    """Absolute native deadlines, keeping the original global schema intact."""
    require(option['policy']=='delayed_complete_segment/1','deadlines require delayed whole calls')
    return {call['id']:(call['hedge_after_ns'] if option['schema_version'] in ('moe-whole-call-replication/3','moe-whole-call-replication/4')
                        else option['hedge_after_ns']) for call in option['calls']}


def source_groups(config):
    value=configuration(config)
    return [config['parallel']['groups'][c['primary_group']] for c in value['calls']] if value else config['parallel']['groups']


def derive_replicas(config, records):
    """Keep unique source loading strict; explicitly derive execution attempts."""
    value=configuration(config)
    if not value:return
    groups=config['parallel']['groups'];batch=config['workload']['batch_per_rank']
    originals=list(records.items());allowed={r for g in source_groups(config) for r in g}
    require(originals and all(row['rank_id'] in allowed for _,row in originals),
            'whole replica source requests must belong only to primary groups')
    for call in value['calls']:
        primary,replica=(groups[call[k]] for k in ('primary_group','replica_group'))
        selected=[(identity,row) for identity,row in originals if row['rank_id'] in primary]
        require(selected,'whole primary group has no source requests')
        for identity,row in selected:
            rank=replica[primary.index(row['rank_id'])];new_id=rank*batch+row['slot']
            require(new_id not in records,'whole replica request slot reused')
            records[new_id]=dict(row,rank_id=rank,replica_of=identity)


class WholeCallConsumer:
    """Submit a client result only after an entire actual attempt graph retires."""
    def __init__(self,config,replay):
        self.config=config;self.option=configuration(config)
        require(self.option is not None and replay is not None,'whole call consumer missing replay')
        self.events=[];self.graphs={};self.calls={};self.now=-1;self.last_source_sequence=-1
        groups=config['parallel']['groups'];batch=config['workload']['batch_per_rank']
        for call in self.option['calls']:
            primary=groups[call['primary_group']];replica=groups[call['replica_group']]
            ids=sorted(i for i,r in replay.records.items() if r['rank_id'] in primary)
            pairs=[];inputs=[]
            for identity in ids:
                row=replay.records[identity];copy_id=replica[primary.index(row['rank_id'])]*batch+row['slot']
                other=replay.records[copy_id]
                require(other.get('replica_of')==identity and all(row[k]==other[k] for k in
                    ('slot','file_id','first_decode_entry','context_tokens','choices','source_file','original_path')),
                    'whole replica input differs from declared primary source')
                pairs.append(dict(primary_request=identity,replica_request=copy_id))
                inputs.append(dict(source_position=primary.index(row['rank_id']),slot=row['slot'],file_id=row['file_id'],
                    source_sha256=row['source_file']['sha256'],original_path=row['original_path'],
                    first_decode_entry=row['first_decode_entry'],context_tokens=row['context_tokens']))
            key=fingerprint(dict(model=config['model'],compute=config['compute'],token_transport=config['token_transport'],
                source_layers=replay.layers,requests=inputs,initial_state=self.option['initial_state']))
            if self.option['schema_version']=='moe-whole-call-replication/4':
                key=fingerprint(dict(route_and_initial_state=key,decode_steps=config['workload']['decode_steps']))
            attempts={role:dict(group_index=call[role+'_group'],graph_id=f'g{call[role+"_group"]}.w0',
                submitted_ns=None,finished_ns=None) for role in ('primary','replica')}
            self.calls[call['id']]=dict(input_identity=dict(kind='declared_route_and_ready_kv_lineage/1',sha256=key),
                request_pairs=pairs,attempts=attempts,winner=None,result_ready_ns=None)
            for role,a in attempts.items():self.graphs[a['graph_id']]=(call['id'],role)

    def emit(self,kind,call,role,now,source_sequence=None):
        require(len(self.events)<self.option['max_events'] and now>=self.now,'whole call lifecycle time/budget violation')
        row=dict(schema_version='moe-whole-call-event/1',sequence=len(self.events),time_ns=now,
                 kind=kind,call=call,attempt=role)
        if source_sequence is not None:row['execution_sequence']=source_sequence
        self.events.append(row);self.now=now

    def register(self,wave,now):
        graph=wave['graph_id'];require(graph in self.graphs,'undeclared whole attempt graph')
        call,role=self.graphs[graph];a=self.calls[call]['attempts'][role]
        require(a['submitted_ns'] is None and wave['decode_step']==0 and
                wave['group_index']==a['group_index'] and
                wave['rank_ids']==self.config['parallel']['groups'][a['group_index']] and
                len(wave['routes'])==self.config['model']['layers'],'whole attempt graph scope differs')
        integer(now,'whole submission',0,2**63-1)
        require(now==0,'eager whole attempts must be submitted in initial decision')
        self.emit('attempt_submitted',call,role,now);a['submitted_ns']=now

    def accept(self,event):
        if event['kind']!='graph_retired':return
        fields(event,'schema_version type sequence time_ns kind graph_id','whole result callback')
        require(event['schema_version']=='moe-execution-event/1' and event['type']=='OBSERVE','invalid whole callback schema/type')
        integer(event['time_ns'],'whole result time',0,2**63-1)
        integer(event['sequence'],'whole source sequence',0,262143)
        require(event['sequence']>self.last_source_sequence,'whole source event order differs')
        graph=event['graph_id'];require(graph in self.graphs,'foreign whole result graph')
        call,role=self.graphs[graph];state=self.calls[call];a=state['attempts'][role];now=event['time_ns']
        require(a['submitted_ns'] is not None and a['finished_ns'] is None,'whole result without live attempt')
        require(len(self.events)+(3 if state['winner'] is None else 2)<=self.option['max_events'],
                'whole result exceeds lifecycle budget')
        self.emit('attempt_result',call,role,now,event['sequence']);a['finished_ns']=now
        if state['winner'] is None:
            self.emit('client_result_ready',call,role,now,event['sequence'])
            state['winner']=role;state['result_ready_ns']=now
        self.emit('attempt_retired',call,role,now,event['sequence'])
        self.last_source_sequence=event['sequence']

    def snapshot(self):
        values=list(self.calls.values());ready=all(c['result_ready_ns'] is not None for c in values)
        retired=all(a['finished_ns'] is not None for c in values for a in c['attempts'].values())
        return dict(schema_version='moe-whole-call-report/1',configuration=deepcopy(self.option),calls=deepcopy(self.calls),
            lifecycle_events=len(self.events),logical_requests=sum(len(c['request_pairs']) for c in values),
            execution_attempt_requests=2*sum(len(c['request_pairs']) for c in values),
            result_ready_ns=max(c['result_ready_ns'] for c in values) if ready else None,
            all_attempts_retired=retired,
            scope='finite_declared_segment_client_result; eager_preprovisioned_replicas; no_tensor_or_KV_equivalence_validation')

    def write(self,directory):
        directory=Path(directory)
        (directory/'whole_call_events.jsonl').write_text(''.join(json.dumps(e,separators=(',',':'))+'\n' for e in self.events))
        (directory/'whole_call_report.json').write_text(json.dumps(self.snapshot(),indent=2)+'\n')


def logical_finish(report,history):
    """Timing interpretation only; public run audit also rebuilds source bindings."""
    if report.get('schema_version')=='moe-whole-call-report/3':
        from .sequential_whole import logical_finish as sequential_finish
        return sequential_finish(report,history)
    if report.get('schema_version')=='moe-whole-call-report/2':
        from .delayed_whole import logical_finish as delayed_finish
        return delayed_finish(report,history)
    require(report.get('schema_version')=='moe-whole-call-report/1' and report.get('all_attempts_retired') is True,
            'incomplete whole call report')
    graphs=set();ends=[]
    require(isinstance(report.get('calls'),dict) and bool(report['calls']),'missing whole calls')
    for call in report['calls'].values():
        a=call['attempts'];require(set(a)=={'primary','replica'},'whole attempt roles differ')
        for role in a:
            row=a[role];g=row['graph_id'];require(g not in graphs and g in history,'missing/reused whole attempt')
            require(type(row['submitted_ns']) is int and type(row['finished_ns']) is int,'invalid whole attempt timestamp')
            require((row['submitted_ns'],row['finished_ns'])==history[g] and row['submitted_ns']==0,
                    'whole attempt differs from actual graph lifetime');graphs.add(g)
        winner=call['winner'];require(winner in a,'invalid whole result winner')
        end=min(row['finished_ns'] for row in a.values())
        require(call['result_ready_ns']==end and a[winner]['finished_ns']==end,'whole result precedes complete attempt')
        ends.append(end)
    require(graphs==set(history) and report['result_ready_ns']==max(ends),'whole call graph coverage or final result differs')
    return max(ends)


def audit(directory,config,result,frontend):
    from .scaleout_rdma import checked_json
    from .scaleout_replay import Replay
    directory=Path(directory);option=configuration(config)
    present='whole_call_replication' in frontend
    if option is None:
        require(not present and not (directory/'whole_call_report.json').exists() and
                not (directory/'whole_call_events.jsonl').exists(),'undeclared whole call results')
        return None
    require(present,'missing whole call frontend results')
    if option['policy']=='delayed_complete_segment/1':
        from .delayed_whole import audit as audit_delayed
        return audit_delayed(directory,config,result,frontend)
    consumer=WholeCallConsumer(config,Replay(config,directory))
    for h in frontend['history']:
        consumer.register(checked_json(directory/'graphs'/h['graph_id']/'wave.json',16*2**20),h['start_ns'])
    with (directory/'execution_events.jsonl').open() as stream:
        for line in stream:consumer.accept(json.loads(line))
    expected=consumer.snapshot()
    require(expected==frontend['whole_call_replication']==checked_json(directory/'whole_call_report.json',2**20),
            'whole call state differs from actual complete attempt callbacks')
    path=directory/'whole_call_events.jsonl'
    require(path.stat().st_size<=option['max_events']*1024,'whole call lifecycle byte budget exceeded')
    lines=path.read_text().splitlines(keepends=True)
    require(all(line.endswith('\n') and len(line.encode())<=1024 for line in lines) and
            [json.loads(line) for line in lines]==consumer.events,'whole call lifecycle differs')
    native={w['graph_id']:(w['start_ns'],w['finished_ns']) for w in result['service']['waves']}
    logical_finish(expected,native)
    return expected


def consumer(config,replay):
    option=configuration(config)
    if option['schema_version']=='moe-whole-call-replication/4':
        from .sequential_whole import SequentialWholeConsumer
        return SequentialWholeConsumer(config,replay)
    if option['policy']=='delayed_complete_segment/1':
        from .delayed_whole import DelayedWholeConsumer
        return DelayedWholeConsumer(config,replay)
    return WholeCallConsumer(config,replay)
