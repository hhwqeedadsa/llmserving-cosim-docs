"""Explicit extra attempts on shared simulated GPU/NIC resources.

The original EP graph/ACK dependencies are retained. An earlier auxiliary
contribution is not evidence of earlier application completion.
"""
from collections import defaultdict
import json
from pathlib import Path

from .scaleout_topology import fields, integer, require


def configuration(config):
    if 'inflight_helpers' not in config:return None
    value = config['inflight_helpers']
    fields(value, 'schema_version policy max_attempts max_events max_compute_tasks max_decisions', 'inflight helpers')
    require(value['schema_version'] == 'moe-inflight-helpers/1' and
            value['policy'] in ('observe_only/1', 'first_missing_bundle/1'), 'unsupported inflight helper policy')
    for name, hi in [('max_attempts',128),('max_events',65536),('max_compute_tasks',131072),('max_decisions',128)]:
        integer(value[name], name, 1, hi)
    require(config.get('schema_version') in ('moe-scaleout-service/1','moe-scaleout-service/2') and
            all(k in config for k in ('source_ack_watchdog','execution_events','contribution_tracking','transport','route_replay')) and
            config.get('phase_completion',{}).get('mode') == 'ep_transport_flush/1' and
            config.get('token_transport',{}).get('combine') == 'rank_reduced/1',
            'inflight helpers require ready-KV contribution tracking, source watchdog, persistent rank-reduced EP completion')
    require(len(config['parallel']['groups']) >= 2, 'helpers require an explicitly declared other complete EP replica')
    require(value['max_events'] >= 12*value['max_attempts'], 'helper event budget below attempt lifecycle bound')
    return value


def candidates(config, wave, replay):
    """Same declared experts on another EP replica, no network/fault truth."""
    if not configuration(config):return []
    from .token_transport import payload_sizes
    sizes = payload_sizes(config)
    nodes = {(int(rank),node['name']):node['id'] for rank,rows in wave['rank_nodes'].items() for node in rows}
    messages = {(m['layer'],m['phase'],m['src'],m['dst']):m for m in wave['messages']}
    rows = []
    for layer in wave['routes']:
        lid = layer['layer']
        for token in layer['tokens']:
            source = token['rank'];owners = defaultdict(list)
            for e in token['experts']:owners[replay.owner(wave['rank_ids'],lid,e)].append(e)
            for owner, experts in sorted(owners.items()):
                if owner == source:continue
                providers = []
                for group in config['parallel']['groups']:
                    if owner in group:continue
                    cover = {replay.owner(group,lid,e) for e in experts}
                    if len(cover) == 1 and source not in cover:providers.extend(cover)
                if not providers:continue
                provider = min(providers)
                dispatch = messages[lid,'dispatch',source,owner]
                combine = messages[lid,'combine',owner,source]
                call = f'{wave["graph_id"]}.l{lid}.r{source}.s{token["slot"]}'
                identity = f'{call}.r{owner}.h{provider}'
                mid = 2**40+2*(dispatch['id']*16+token['slot'])
                rows.append(dict(id=identity,call=call,bundle=f'r{owner}',graph_id=wave['graph_id'],logical_layer=lid,
                    source=source,owner=owner,provider=provider,experts=sorted(experts),
                    pack_node=nodes[source,f'L{lid}.pack'],dispatch_message=dispatch['id'],combine_message=combine['id'],
                    plane=dispatch['plane'],input_message=mid,result_message=mid+1,
                    input_bytes=sizes['dispatch'],result_bytes=sizes['combine'],
                    compute_ns=len(experts)*config['compute']['expert_ns_per_contribution'],
                    reduce_ns=(len(experts)-1)*config['token_transport']['local_reduce_ns_per_extra']))
    return sorted(rows, key=lambda row:row['id'])


def preflight(config, replay, planes):
    option = configuration(config)
    if option is None:return
    from .scaleout_service import make_wave
    count = tasks = original_messages = 0
    bindings = {(s['src'],s['dst'],s['plane']) for s in config['transport']['streams']}
    helper_ids = set();original_ids = set()
    for index,group in enumerate(config['parallel']['groups']):
        for step in range(config['workload']['decode_steps']):
            wave = make_wave(config,group,index,step,planes,replay)
            count += len(wave['helper_candidates'])
            original_messages += len(wave['messages'])
            original_ids.update(m['id'] for m in wave['messages'])
            for row in wave['helper_candidates']:
                require(0<=row['compute_ns']<=10**9 and 0<=row['reduce_ns']<=10**9,
                        'helper bundle declared compute exceeds shared-engine duration bound')
                for src,dst,mid in [(row['source'],row['provider'],row['input_message']),
                                    (row['provider'],row['source'],row['result_message'])]:
                    require((src,dst,row['plane']) in bindings, 'helper candidate has no explicit shared transport binding')
                    require(mid not in helper_ids, 'helper candidate message identity collision')
                    helper_ids.add(mid)
            tasks += sum(n['kind']=='COMP' for rows in wave['rank_nodes'].values() for n in rows)
    require(count <= 32768 and tasks+2*option['max_attempts'] <= option['max_compute_tasks'],
            'helper catalog or shared compute cumulative budget exceeded')
    require(not helper_ids & original_ids and original_messages+2*option['max_attempts']<=config['transport']['limits']['max_work'],
            'helper transport identity or cumulative work budget exceeded')


class HelperObserver:
    def __init__(self, config, binding):
        self.config, self.option, self.binding = config, configuration(config), binding
        self.execution = binding.observer
        self.catalog, self.selected, self.states = {}, set(), {}
        self.admitted_ns = {}
        self.events = self.decisions = 0
        self.now = -1
        self.notice = None
        self.transcript = []

    def register(self, wave):
        expected = candidates(self.config,wave,self.binding.replay)
        require(wave['helper_candidates'] == expected, 'helper candidate differs from declared source/replica')
        for row in expected:
            require(row['id'] not in self.catalog, 'helper candidate reused')
            self.catalog[row['id']] = row

    def source_notice(self, notice):
        self.notice = notice

    def source_execution(self, event):
        require(event['time_ns']>=self.now, 'helper/execution cross-stream time moved backwards')
        self.now=event['time_ns']

    def decide(self, event):
        fields(event, 'type decision_id now_ns notice_sequence message observed_events', 'helper decision')
        require(event['type']=='HELPER_DECIDE' and self.notice is not None and
                type(event['decision_id']) is int and event['decision_id']==self.decisions and
                self.decisions < self.option['max_decisions'] and
                type(event['now_ns']) is int and event['now_ns']==self.notice['time_ns'] and
                type(event['notice_sequence']) is int and event['notice_sequence']==self.notice['sequence'] and
                type(event['message']) is int and event['message']==self.notice['message'] and
                type(event['observed_events']) is int and event['observed_events']==self.execution.count,
                'helper synchronous decision prefix/time/identity mismatch')
        chosen = []
        if self.option['policy']=='first_missing_bundle/1' and len(self.selected) < self.option['max_attempts']:
            for id,row in sorted(self.catalog.items()):
                if id in self.selected or row['dispatch_message'] != event['message']:continue
                runtime = self.binding.calls[row['call']]['runtime']
                # Result presence at this request's source is the only progress
                # used here. Do not inspect remote input/compute or fault truth.
                if row['bundle'] in runtime.snapshot()['winners']:continue
                if self.execution.nodes[row['graph_id'],row['source'],row['pack_node']]['complete'] is None:continue
                chosen = [id];break
        reply = dict(type='HELPER_COMMIT',decision_id=self.decisions,now_ns=event['now_ns'],attempts=chosen)
        self.selected.update(chosen);self.decisions += 1;self.notice = None
        self.admitted_ns.update((id,event['now_ns']) for id in chosen)
        self.transcript.append(dict(event=event,reply=reply))
        return reply

    def accept(self, event):
        fields(event, 'type schema_version sequence observed_events time_ns id kind', 'helper event')
        integer(event['time_ns'],'helper time',0,2**63-1)
        integer(event['sequence'],'helper event sequence',0,65535)
        integer(event['observed_events'],'helper execution prefix',0,262144)
        require(event['type']=='HELPER_OBSERVE' and event['schema_version']=='moe-helper-event/1' and
                event['sequence']==self.events and self.events<self.option['max_events'] and
                event['observed_events']==self.execution.count and event['time_ns']>=max(self.now,self.execution.now) and
                self.execution.drained_ns is None and event['id'] in self.selected,
                'helper event prefix/identity/time/budget violation')
        id,kind,now = event['id'],event['kind'],event['time_ns']
        row = self.catalog[id]
        require(kind in ('accepted','input_post','input_ack','input_ready','compute_begin','compute_done',
                         'reduce_begin','reduce_done','result_post','result_ack','result_ready','retired'), 'unknown helper lifecycle event')
        state = self.states.get(id,{})
        require(kind not in state, 'duplicate helper lifecycle event')
        if kind=='accepted':
            require(now==self.admitted_ns[id], 'helper admission differs from synchronous commit time')
        if kind=='input_post':
            require(now==state.get('accepted'), 'helper input post differs from immediate admission')
        dependencies = dict(accepted=(),input_post=('accepted',),input_ack=('input_post',),input_ready=('input_post',),
            compute_begin=('input_ready',),compute_done=('compute_begin',),reduce_begin=('compute_done',),
            reduce_done=('reduce_begin',),result_post=('reduce_done',),result_ack=('result_post',),
            result_ready=('result_post',),retired=('input_ack','result_ack','result_ready'))
        require(all(k in state for k in dependencies[kind]), 'helper lifecycle prerequisite missing')
        if kind in ('compute_done','reduce_done'):
            begin = 'compute_begin' if kind=='compute_done' else 'reduce_begin'
            require(now-state[begin]==row['compute_ns' if kind=='compute_done' else 'reduce_ns'], 'helper declared compute duration differs')
        # The verified actual event now drives the same contribution core.
        runtime = self.binding.calls[row['call']]['runtime']
        attempt = 'helper.'+row['bundle'];ref = 'helper.'+row['bundle']
        actions = {
            'accepted':[dict(kind='start',bundle=row['bundle'],rank=row['provider']),dict(kind='resource_open',resource=ref+'.graph')],
            'input_post':[dict(kind='resource_open',resource=ref+'.input')],
            'input_ack':[dict(kind='resource_close',resource=ref+'.input')],
            'input_ready':[dict(kind='input_ready')], 'compute_begin':[dict(kind='compute_begin')],
            'compute_done':[dict(kind='compute_done')], 'reduce_begin':[], 'reduce_done':[],
            'result_post':[dict(kind='resource_open',resource=ref+'.result')],
            'result_ack':[dict(kind='resource_close',resource=ref+'.result')],
            'result_ready':[dict(kind='result',key_sha256=runtime.bundle_key(row['bundle']))],
            'retired':[dict(kind='resource_close',resource=ref+'.graph'),dict(kind='retire')]}
        for action in actions[kind]:
            require(self.binding.lifecycle_events < self.binding.option['max_lifecycle_events'], 'helper contribution event budget exceeded')
            runtime.apply(dict(action,attempt=attempt,time_ns=now));self.binding.lifecycle_events += 1
        self.states.setdefault(id,{})[kind] = now
        self.events += 1;self.now = now

    def snapshot(self):
        return dict(schema_version='moe-helper-observation/1',configuration=dict(self.option),decisions=self.decisions,
                    events=self.events,selected=sorted(self.selected),states=self.states,
                    retired=sum('retired' in s for s in self.states.values()),
                    scope='extra attempts on shared resources; original application gates/ACKs retained')

    def write(self, directory):
        directory=Path(directory)
        for name,value in [('helper_observation.json',self.snapshot()),('helper_control.json',self.transcript)]:
            with (directory/name).open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')


def audit_compute(result, execution):
    report=result['service']['shared_compute']
    require(report['mode']=='serial_per_gpu/1' and report['empty'] is True, 'shared compute did not drain')
    tasks=report['tasks'];by_rank=defaultdict(list);originals={};helper_tasks={}
    require(sorted(t['sequence'] for t in tasks)==list(range(len(tasks))), 'shared compute task sequence differs')
    for task in tasks:
        rank=task['rank'];integer(rank,'shared compute rank',0,result['gpu_count']-1)
        require(all(type(task[k]) is int for k in ('ready_ns','start_ns','done_ns','duration_ns','sequence')) and
                0<=task['ready_ns']<=task['start_ns']<=task['done_ns']<=result['finish_ns'] and
                task['done_ns']-task['start_ns']==task['duration_ns'], 'invalid shared compute interval')
        by_rank[rank].append(task)
        if task['kind']=='original':
            key=task['graph_id'],rank,task['node']
            require(key in execution.nodes and key not in originals, 'unknown or duplicate original shared work')
            node=execution.nodes[key]
            require(node['spec']['kind']=='COMP' and node['spec']['duration_ns']==task['duration_ns'] and
                    node['issue']==task['ready_ns'] and node['complete']==task['done_ns'], 'shared original differs from actual ASTRA callbacks')
            originals[key]=task
        else:
            require(task['kind'] in ('helper_experts','helper_reduce'), 'unknown shared compute task kind')
            key=task['helper_id'],task['kind']
            require(key not in helper_tasks, 'duplicate helper shared work')
            helper_tasks[key]=task
    expected={key for key,node in execution.nodes.items() if node['spec']['kind']=='COMP'}
    require(set(originals)==expected, 'shared compute omitted original COMP')
    for rank,rows in by_rank.items():
        end=0
        for task in sorted(rows,key=lambda row:row['sequence']):
            require(task['start_ns']==max(end,task['ready_ns']), 'shared GPU FIFO overlap or gratuitous idle')
            end=task['done_ns']
    return helper_tasks


def audit_transport(row, state, messages):
    """Bridge helper callbacks to actual shared connection acceptance and ACKs."""
    for result in (False,True):
        prefix='result' if result else 'input'
        mid=row[prefix+'_message']
        require(mid in messages, 'helper lifecycle has no actual transport message')
        actual=messages[mid]
        expected=dict(id=mid,src=row['provider' if result else 'source'],
            dst=row['source' if result else 'provider'],bytes=row[prefix+'_bytes'],
            plane=row['plane'],phase='helper_'+prefix,helper_id=row['id'])
        require(all(actual[k]==v for k,v in expected.items()), 'helper actual transport binding differs')
        for kind,field in [('post','ready_ns'),('ready','receive_ready_ns'),('ack','ack_ns')]:
            require(type(actual[field]) is int and state[prefix+'_'+kind]==actual[field],
                    'helper callback differs from actual shared transport '+field)
        require(state['accepted']<=actual['ready_ns']<=actual['published_ns']<=actual['receive_ready_ns']<=
                actual['ack_ns']<=state['retired'], 'helper transport lifecycle order differs')


def audit(directory, result, frontend):
    from .scaleout_rdma import checked_json
    from .scaleout_replay import Replay
    from .execution_events import ExecutionObserver, configuration as event_configuration
    from .source_ack_watchdog import SourceAckObserver
    from .contribution_binding import ContributionBinding
    from .native_control import event as control_event
    directory=Path(directory);config=checked_json(directory/'input.json',2**20)
    option=configuration(config)
    require(option is not None, 'inflight audit requires explicit mode')
    execution=ExecutionObserver(event_configuration(config))
    binding=ContributionBinding(config,Replay(config,directory),execution)
    helper=HelperObserver(config,binding);watchdog=SourceAckObserver(config,execution)
    for row in frontend['history']:
        wave=checked_json(directory/'graphs'/row['graph_id']/'wave.json',2**28)
        binding.register(wave);helper.register(wave)
    require((directory/'helper_events.jsonl').stat().st_size<=option['max_events']*1024, 'helper events exceed byte budget')
    recorded_control=checked_json(directory/'helper_control.json',2**20)
    with (directory/'native.stdout.log').open() as raw,(directory/'helper_events.jsonl').open() as stream:
        for line in raw:
            require(len(line.encode())<=1048592,'oversized native helper log frame')
            frame=control_event(line,observations=True,source_ack_timeouts=True,helpers=True)
            if frame is None or frame['type']=='DECIDE':continue
            if frame['type']=='OBSERVE':
                binding.accept(frame);watchdog.observe(frame);helper.source_execution(frame)
            elif frame['type']=='SOURCE_ACK_TIMEOUT':
                watchdog.accept(frame);helper.source_notice(frame)
            elif frame['type']=='HELPER_DECIDE':
                reply=helper.decide(frame)
                require(helper.decisions<=len(recorded_control) and
                        recorded_control[helper.decisions-1]==dict(event=frame,reply=reply), 'helper decision differs from visible-prefix policy')
            else:
                require(json.loads(stream.readline())==frame, 'saved helper event differs from raw native frame')
                helper.accept(frame)
        require(not stream.read(), 'extra helper saved events')
    require(helper.decisions==len(recorded_control) and helper.snapshot()==checked_json(directory/'helper_observation.json',2**20),
            'helper control or observed summary differs')
    native=result['service']['inflight_helpers']
    require(native['configuration']==option and native['decisions']==helper.decisions and native['events']==helper.events and
            native['retired']==len(helper.selected)==len(helper.states)==len(native['attempts']) and
            helper.snapshot()['retired']==len(helper.states), 'native helper lifecycle incomplete')
    actual={row['id']:row for row in native['attempts']}
    helper_tasks=audit_compute(result,execution)
    messages={m['id']:m for m in result['service_transport']['messages']}
    require(set(actual)==helper.selected and len(helper_tasks)==2*len(actual), 'extra/missing helper tasks')
    for id,state in helper.states.items():
        row=helper.catalog[id]
        require(actual[id]['candidate']==row and all(actual[id][key] is True for key in ('input_ack','result_ack','result_ready','retired')),
                'native helper candidate/retirement differs')
        audit_transport(row,state,messages)
        for kind,begin,end,cost in [('helper_experts','compute_begin','compute_done','compute_ns'),
                                     ('helper_reduce','reduce_begin','reduce_done','reduce_ns')]:
            task=helper_tasks[id,kind]
            ready=state['input_ready' if kind=='helper_experts' else 'compute_done']
            require(task['rank']==row['provider'] and task['ready_ns']==ready and task['start_ns']==state[begin] and
                    task['done_ns']==state[end] and task['duration_ns']==row[cost], 'helper compute differs from shared FIFO')
    with (directory/'contribution_calls.jsonl').open('rb') as stream:
        for row in binding.records():
            expected=(json.dumps(row,sort_keys=True,separators=(',',':'),ensure_ascii=True)+'\n').encode()
            require(stream.readline(len(expected)+1)==expected,'helper-bound contribution differs from actual callbacks')
        require(not stream.read(1),'extra helper-bound contribution')
    summary=binding.summary()
    require(summary==checked_json(directory/'contribution_summary.json',65536) and summary['invocations']>0 and
            summary['logical_complete_calls']==summary['retired_calls']==summary['invocations'] and
            summary['lifecycle_events']==summary['reserved_lifecycle_events']-12*(option['max_attempts']-len(helper.states)) and
            execution.drained_ns==result['finish_ns'], 'incomplete or inconsistent helper contribution summary')
    return dict(contribution_binding=summary,inflight_helpers=helper.snapshot(),shared_compute_tasks=len(result['service']['shared_compute']['tasks']))
