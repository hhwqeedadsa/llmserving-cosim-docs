"""Explicit bounded physical mapping for modern operations on functional MRT1.

Synthetic byte markers are operands, never numerical expert activations. No
completion event, GPU visibility, physical self-loop or measured cost is inferred.
"""
from collections import Counter
from copy import deepcopy
import hashlib

from .deepep_plan import encoded, fields, integer, require
from .modern_deepep_plan import compile_plan
from .modern_native_capacity import (connection_keys, require_node_capacity,
    NATIVE_RANK_LIMIT, NATIVE_CONNECTION_LIMIT, NATIVE_REMOTE_LIMIT, NATIVE_LOCAL_LIMIT)
from .modern_deepep_windows import ModernDeepEPWindows
from .scaleout_transactions import validate

MAIN_BASE=4096
SIGNAL_BASE=32*2**20
INPUT_BASE=40*2**20


class ModernNativeLayout:
    def __init__(self, topology, protocol, config):
        require(isinstance(config,dict),'invalid modern native input')
        version=config.get('schema_version')
        require(version in ('moe-modern-native-input/1','moe-modern-native-input/2'),'unsupported modern native schema')
        fields(config,'schema_version modern deployment provider costs'+
            (' expert_stage' if version=='moe-modern-native-input/2' else ''),'modern native input')
        self.config=deepcopy(config);self.modern=self.config['modern'];self.plan=compile_plan(self.modern)
        require(len(encoded(self.modern))<=self.modern['limits']['max_input_bytes'],'modern serialized input budget exceeded')
        require(len(encoded(self.plan))<=self.modern['limits']['max_output_bytes'],'modern serialized plan budget exceeded')
        self.windows=ModernDeepEPWindows(self.modern);self.description=self.windows.describe()
        x=self.modern['execution'];self.ranks=x['ranks']
        require(2<=self.ranks<=NATIVE_RANK_LIMIT,'functional native bridge currently supports 2..4 ranks')
        require_node_capacity(self.plan)
        fields(config['provider'],'mode target_visibility queue_depth self_service transport_recovery'+
               (' dcqcn_reference' if 'dcqcn_reference' in config['provider'] else ''),'functional provider')
        p=config['provider'];require(p['mode']=='functional_mrt1/1' and p['target_visibility']=='native_apply_functional/1',
            'explicit functional apply visibility model required; not GPU visibility')
        integer(p['queue_depth'],2,32,'provider queue depth')
        require(p['queue_depth']&(p['queue_depth']-1)==0,'queue depth must be power of two')
        fields(p['self_service'],'apply_ns complete_ns','self service')
        horizon=protocol.get('limits',{}).get('max_sim_ns',5000000)
        integer(p['self_service']['apply_ns'],0,horizon,'self apply')
        integer(p['self_service']['complete_ns'],p['self_service']['apply_ns'],horizon,'self complete')
        fields(config['costs'],'source_prepare_ns reduction_ns local_copy_ns'+
            (' expert_ns' if version=='moe-modern-native-input/1' else ''),'declared local costs')
        for name,value in config['costs'].items():integer(value,0,horizon,name)
        calls=len(self.modern['calls'])
        if version=='moe-modern-native-input/1':
            self._expert_durations=tuple((config['costs']['expert_ns'],)*self.ranks for _ in range(calls))
        else:
            stage=self.config['expert_stage']
            fields(stage,'mode durations_ns source','declared expert stage')
            require(stage['mode']=='per_call_rank_declared_ns/1','unsupported expert stage mode')
            require(type(stage['source']) is str and stage['source'].strip() and len(stage['source'])<=512,
                'expert stage requires a source description of 1..512 characters')
            rows=stage['durations_ns']
            require(type(rows) is list and len(rows)==calls,'expert stage must cover every call')
            for row in rows:
                require(type(row) is list and len(row)==self.ranks,'expert stage must cover every rank')
                for value in row:integer(value,0,horizon,'expert stage duration')
            self._expert_durations=tuple(tuple(row) for row in rows)
        require(isinstance(config['deployment'],list) and len(config['deployment'])==self.ranks,'incomplete deployment')
        gpus={n['id'] for n in topology['nodes'] if n['kind']=='gpu'};self.deployment={};nodes=set()
        for row in config['deployment']:
            fields(row,'rank node devices','rank deployment')
            integer(row['rank'],0,self.ranks-1,'rank');integer(row['node'],0,31,'physical node')
            require(row['rank'] not in self.deployment and row['node'] in gpus and row['node'] not in nodes,'invalid or repeated rank/node')
            require(isinstance(row['devices'],list) and 1<=len(row['devices'])<=4,'require physical devices')
            devices={}
            for device in row['devices']:
                fields(device,'device nic plane','physical device')
                for key,high,low in (('device',65535,0),('nic',1024,1),('plane',15,0)):integer(device[key],low,high,key)
                require(device['device'] not in devices,'repeated physical device id');devices[device['device']]=device
            require(set(x['local_devices'][row['rank']])<=devices.keys(),'logical device lacks physical NIC declaration')
            self.deployment[row['rank']]=dict(node=row['node'],devices=devices);nodes.add(row['node'])
        keys=connection_keys(self.plan,x)
        require(1<=len(keys)<=NATIVE_CONNECTION_LIMIT,'prospective context connections exceed 64; no silent merging')
        self.connections=[];self.bindings=[];self.by_identity={}
        for src,context,dst in sorted(keys):
            identity=len(self.connections);connection=context%x['connections']
            devices=[]
            for rank in (src,dst):
                device=x['local_devices'][rank][connection%len(x['local_devices'][rank])]
                devices.append(self.deployment[rank]['devices'][device])
            a,b=devices;require(a['plane']==b['plane'],'source and destination NIC planes differ')
            c=dict(id=identity,src=self.node(src),dst=self.node(dst),plane=a['plane'],
                source_nic=a['nic'],destination_nic=b['nic'],queue_depth=p['queue_depth'])
            if src==dst:c['self_service']=deepcopy(p['self_service'])
            else:c.update(request_port=24000+2*identity,response_port=24001+2*identity)
            self.connections.append(c);self.by_identity[src,context,dst]=identity
            self.bindings.append(dict(transaction_connection=identity,source_rank=src,peer_rank=dst,context_index=context,
                gin_connection=connection,local_context=context//x['connections'],communicator='modern',**{k:v for k,v in c.items() if k!='id'}))
        self.inputs={};self.count_sources={};self.zero_sources={};self.segments={r:[] for r in range(self.ranks)}
        self.lengths={r:0 for r in range(self.ranks)}
        count_bytes=8*(self.ranks+self.modern['model']['experts'])
        for rank in range(self.ranks):self.zero_sources[rank]=self._allocate(rank,count_bytes,b'')
        for ci,call in enumerate(self.modern['calls']):
            for rank,tokens in enumerate(call['tokens']):
                pairs=Counter();experts=Counter();local=self.modern['model']['experts']//self.ranks
                for choices in tokens:
                    pairs.update(set(e//local for e in choices));experts.update(choices)
                values=[-pairs[d]-1 for d in range(self.ranks)]+[-experts[e]-1 for e in range(self.modern['model']['experts'])]
                self.count_sources[ci,rank]=self._allocate(rank,count_bytes,b''.join(v.to_bytes(8,'little',signed=True) for v in values))
        for op in self.plan['operations']:
            if op['kind']!='token_data':continue
            key=self.token_key(op)
            if key not in self.inputs:
                marker=hashlib.sha256(('modern-native-marker/1:'+key).encode()).digest()
                self.inputs[key]=self._allocate(op['src'],op['operand_bytes'],marker)
        native_windows=[]
        for rank in range(self.ranks):
            require(len(self.segments[rank])<=32 and sum(len(s['hex'])//2 for s in self.segments[rank])<=65536,
                'immutable operand initialization exceeds existing sparse segment/byte limit')
            for key,address,size,segments,atomic in (
                (1,MAIN_BASE,self.description['main_window']['bytes'],[],False),
                (2,SIGNAL_BASE,self.plan['layout']['allocated_contexts']*(self.ranks+4)*8,[],True),
                (3,INPUT_BASE,max(1,self.lengths[rank]),self.segments[rank],False)):
                native_windows.append(dict(owner=self.node(rank),key=key,address=address,bytes=size,segments=segments,
                    initialization='zero_fill/1',allow_atomic=atomic))
        registrations=[]
        for owner,nic in sorted({(c['src'],c['source_nic']) for c in self.connections}|{(c['dst'],c['destination_nic']) for c in self.connections}):
            for w in native_windows:
                if w['owner']==owner and w['key'] in (1,2):
                    registrations.append(dict(id=len(registrations),owner=owner,nic=nic,key=w['key'],window_key=w['key'],
                        address=w['address'],bytes=w['bytes'],allow_atomic=w['allow_atomic']))
        self.initial=dict(schema_version='moe-rdma-transactions/5',connections=self.connections,windows=native_windows,operations=[],
            local_model=dict(mode='serial_per_gpu/1',duration_source='declared_ns'),
            source_model=dict(capture='registered_window_at_publish/1',release='transaction_completed/1'),
            local_memory_model=dict(mode='guarded_copy/1',acquire='admission/1',capture='start/1',commit='completion/1'),
            submission_model=dict(mode='explicit_prefix/1'),self_peer_model=dict(mode='declared_service_fifo/1'),
            transport_recovery=deepcopy(p['transport_recovery']),
            registration_model=dict(mode='nic_scoped_aliases/1'),registrations=registrations,
            execution_limits=dict(max_connections=NATIVE_CONNECTION_LIMIT,
                max_remote_operations=NATIVE_REMOTE_LIMIT,max_local_operations=NATIVE_LOCAL_LIMIT))
        if 'dcqcn_reference' in p:
            self.initial['dcqcn_reference']=deepcopy(p['dcqcn_reference'])
        validate(topology,protocol,self.initial,allow_no_remote=True)

    def node(self,rank):return self.deployment[rank]['node']

    def expert_duration(self,call,rank):
        integer(call,0,len(self._expert_durations)-1,'expert stage call')
        integer(rank,0,self.ranks-1,'expert stage rank')
        return self._expert_durations[call][rank]

    @staticmethod
    def token_key(op):
        return f"dispatch:{op['call']}:{op['src']}:{op['token']}" if op['phase']=='dispatch' else op['id']

    def _allocate(self,owner,nbytes,content):
        offset=(self.lengths[owner]+63)//64*64;self.lengths[owner]=offset+nbytes
        require(self.lengths[owner]<=64*2**20 and len(content)<=nbytes,'immutable operand capacity exceeded')
        if content:self.segments[owner].append(dict(offset=offset,hex=content.hex()))
        return dict(key=3,address=INPUT_BASE+offset,bytes=nbytes)

    def main(self,location):
        require(location['domain']=='main_window','require application window range')
        return dict(key=1,address=MAIN_BASE+location['byte_offset'],bytes=location['bytes'])

    def target(self,op):
        location=op['memory']['target']
        if location['domain']=='main_window':return self.main(location)
        require(location['domain']=='provider_indexed_signals','unknown signal domain')
        return dict(key=2,address=SIGNAL_BASE+8*(location['context_index']*(self.ranks+4)+location['slot']),bytes=8)

    def connection(self,op):return self.by_identity[op['src'],op['binding']['context_index'],op['dst']]
