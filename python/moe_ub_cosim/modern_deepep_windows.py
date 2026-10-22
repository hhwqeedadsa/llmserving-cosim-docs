"""Functional modern direct window mapping and source-lifetime checks.

Offsets are relative to an abstract registered allocation, never GPU addresses
or remote keys. Provider indexed signals live in a separate domain. This module
does not encode packets, implement GPU coherence, or supply completion events.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import shutil

from .deepep_plan import encoded, fields, integer, read, require
from .modern_deepep_plan import MAX_INPUT, MAX_OUTPUT, align, compile_plan, token_bytes, validate
from .modern_deepep_runtime import ModernDeepEPRuntime


def workspace_layout():
    """Fixed-source EPSignals, with 32/64-bit C++ ABI and 64-byte struct alignment."""
    sizes = [('barrier_signals', 64), ('notify_reduction', (1024+2048)*8),
             ('scaleup_rank_expert_count', 2*(1024+2048)*8),
             ('scaleup_atomic_sender_count', 1024*4),
             ('scaleout_rank_expert_count', 2*(1024+2048)*4),
             ('scaleout_channel_signaled_tail', 1280*1024*8),
             ('channel_scaleup_tail', 1280*1024*4), ('lb_chunk_idx_counter', 4)]
    offset, members = 0, {}
    for name, size in sizes:
        alignment = 64 if name == 'barrier_signals' else 8 if name in (
            'notify_reduction', 'scaleup_rank_expert_count', 'scaleout_channel_signaled_tail') else 4
        offset = align(offset, alignment)
        members[name] = dict(byte_offset=offset, bytes=size)
        offset += size
    return dict(members=members, struct_bytes=align(offset, 64),
                allocation_bytes=align(offset, 2*2**20), abi='int32/int64, 64-byte aligned structs')


class ModernDeepEPWindows:
    """Resolve an already-ready runtime operation without choosing a slot/NIC."""

    def __init__(self, config):
        validate(config)
        self._config = deepcopy(config)
        self._workspace = workspace_layout()
        self._ep_base = self._workspace['allocation_bytes']
        m, x = config['model'], config['execution']
        self._ranks, self._cap = x['ranks'], x['max_tokens_per_rank']
        self._experts, self._local_experts = m['experts'], m['experts']//self._ranks
        self._dbytes, self._cbytes = (token_bytes(m['hidden_size'], m['top_k'], d) for d in (True, False))
        self._ep_bytes = align(max((self._ranks+1)*self._cap*self._dbytes,
            (min(self._ranks, m['top_k'])+self._ranks)*self._cap*self._cbytes), 2*2**20)
        base = self._workspace['members']['scaleup_rank_expert_count']['byte_offset']
        self._send, self._recv = base, base+(1024+2048)*8

    def describe(self):
        return dict(schema_version='moe-modern-deepep-windows/1', workspace=self._workspace,
            main_window=dict(workspace_base=0, ep_buffer_base=self._ep_base, ep_buffer_bytes=self._ep_bytes,
                             bytes=self._ep_base+self._ep_bytes, reused_across_all_calls_and_phases=True),
            counts=dict(send_base=self._send, recv_base=self._recv, elements=self._ranks+self._experts,
                        element_bytes=8, initial_value=0, reset_after_consumption=True),
            signals=dict(domain='provider_indexed_signals', per_context_slots=self._ranks+4,
                         width_bits=64, initial_value=0, reset_between_calls=False,
                         physical_address_and_rkey_known=False),
            scope='functional relative mapping; no registration, physical loopback, transport or GPU visibility')

    def main_range(self, owner, offset, length, epoch):
        integer(owner, 0, self._ranks-1, 'window owner')
        integer(offset, 0, self._ep_base+self._ep_bytes, 'window offset')
        integer(length, 1, self._ep_base+self._ep_bytes-offset, 'window length')
        return dict(domain='main_window', owner=owner, byte_offset=offset, bytes=length, epoch=epoch)

    def count_send_range(self, owner, call):
        return self.main_range(owner, self._send, 8*(self._ranks+self._experts), 2*call)

    def resolve(self, operation):
        op = operation
        src, dst, nbytes, target = (op[k] for k in ('src', 'dst', 'operand_bytes', 'target'))
        if op['kind'] == 'barrier_signal':
            binding = op['binding']
            location = dict(domain='provider_indexed_signals', owner=dst,
                context_index=binding['context_index'], connection=binding['connection'],
                local_context=binding['local_context'], slot=target['slot'], bytes=8)
            return dict(target=location, source=None)
        if op['kind'] in ('rank_count', 'expert_count_block'):
            index = target['element_offset']+(self._ranks if op['kind']=='expert_count_block' else 0)
            require(0 <= index and index+nbytes//8 <= self._ranks+self._experts, 'count target outside active range')
            location = self.main_range(dst, self._recv+index*8, nbytes, target['epoch'])
            source = None
            if op['kind']=='expert_count_block':
                source = self.main_range(src, self._send+(self._ranks+op['source']['element_offset'])*8,
                                         nbytes, op['source']['epoch'])
            return dict(target=location, source=source)
        require(op['kind']=='token_data' and 'byte_offset' in target, 'unresolved token target')
        require(0 <= target['byte_offset'] and target['byte_offset']+nbytes <= self._ep_bytes,
                'token target outside EP allocation')
        location = self.main_range(dst, self._ep_base+target['byte_offset'], nbytes, target['epoch'])
        if op['path']=='local':
            # Local Dispatch TMA copies read shared memory, not the staged RDMA
            # source. Local Combine reduces directly into its destination.
            source = dict(domain='local_tma' if op['phase']=='dispatch' else 'local_reduction',
                          owner=src, call=op['call'], token=op['token'], bytes=nbytes)
        else:
            source = self.main_range(src, self._ep_base+op['source']['byte_offset'], nbytes, op['source']['epoch'])
        return dict(target=location, source=source)


class WindowReadLeases:
    """A source read lives until a declared local-complete callback.

    Epoch is provenance, not a distinct allocation. Concurrent readers may
    overlap. Nothing here equates source completion to target visibility/CQ.
    """

    def __init__(self):
        self._leases = {}

    @staticmethod
    def _validate(location):
        fields(location, 'domain owner byte_offset bytes epoch', 'source lease range')
        require(location['domain']=='main_window', 'only main-window sources acquire read leases')
        integer(location['owner'], 0, 31, 'lease owner')
        integer(location['byte_offset'], 0, 2**63-1, 'lease offset')
        integer(location['bytes'], 1, 2**63-location['byte_offset'], 'lease length')
        integer(location['epoch'], 0, 7, 'lease epoch')

    def check_write(self, location):
        if location['domain'] != 'main_window':
            return
        self._validate(location)
        start, end = location['byte_offset'], location['byte_offset']+location['bytes']
        for source in self._leases.values():
            if source['owner']==location['owner']:
                require(end <= source['byte_offset'] or source['byte_offset']+source['bytes'] <= start,
                        'write overlaps an active source read lease')

    def acquire(self, identity, location):
        require(isinstance(identity, str) and 0 < len(identity) <= 256, 'invalid source lease identity')
        require(identity not in self._leases, 'duplicate source read lease')
        self._validate(location)
        self._leases[identity] = deepcopy(location)

    def release(self, identity):
        require(identity in self._leases, 'unknown or already released source read lease')
        del self._leases[identity]

    def snapshot(self):
        return deepcopy(dict(sorted(self._leases.items())))


class ModernDeepEPWindowRuntime:
    """Check relative windows, source leases, count resets and rolling signals.

    Caller events drive every transition. Visible callbacks update *shadow*
    integer state from the compiled operands; this is not evidence that a NIC
    delivered payload bytes. Token tensors and GPU memory ordering are absent.
    """

    def __init__(self, config):
        self._config = deepcopy(config)
        self._runtime = ModernDeepEPRuntime(config)
        plan = compile_plan(config)
        self._events = {e['id']:e for e in plan['events']}
        self._ops = {o['id']:o for o in plan['operations']}
        self._windows = ModernDeepEPWindows(config)
        self._leases = WindowReadLeases()
        self._signals, self._shadows, self._counts = {}, {}, {}
        self._prepared, self._leased = set(), set()
        self._consumed, self._barriers = [], []
        self._memory_log = []
        self._peak_leases, self._writes = 0, 0

    def ready(self):
        return self._runtime.ready()

    def operation(self, identity):
        result = self._runtime.operation(identity)
        result['memory'] = self._windows.resolve(result)
        return result

    def fire(self, identity):
        require(identity in self.ready(), 'window runtime event unknown, duplicate or not ready')
        event = self._events[identity]
        kind, rank = event['kind'], event['rank']
        call = int(identity.split('.')[0][1:])
        operation = self.operation(event['operation']) if 'operation' in event else None
        writes, acquire, release = [], None, None
        prepare = None
        count_updates, signal_update, consume, barrier = [], None, None, None
        if kind == 'issued':
            op = operation
            if op['kind'] in ('rank_count', 'expert_count_block'):
                prepare = (call, rank)
                if prepare not in self._prepared:
                    writes.append(self._windows.count_send_range(rank, call))
            if op['kind']=='token_data' and op['phase']=='dispatch':
                prepare = (call, rank, op['token'])
                if prepare not in self._prepared:
                    # Materialize one staged source version before first issue.
                    # This is a functional boundary, not a measured TMA time.
                    writes.append(self._windows.main_range(rank,
                        self._windows._ep_base+(self._windows._ranks*self._windows._cap+op['token'])*self._windows._dbytes,
                        self._windows._dbytes, 2*call))
            source = op['memory']['source']
            if op['path']!='local' and source is not None and source['domain']=='main_window':
                acquire = (op['id'], source)
        elif kind == 'local_expert_reduction_done':
            opid = identity.replace('.combine.s', '.combine.data.s').replace('.reduced', '')
            op = self._ops[opid]
            # Source offset is fixed; target and reduction of local bypass are
            # also fixed. No early slot/channel is chosen by this guard.
            resolved = self._windows.resolve(op)
            writes.append(resolved['target'] if op['path']=='local' else resolved['source'])
        elif kind == 'visible':
            op = operation
            target = op['memory']['target']
            if op['kind']=='barrier_signal':
                key = (op['dst'], target['context_index'], target['slot'])
                value = self._signals.get(key, 0)+op['value']
                signal_update = (key, value)
            else:
                writes.append(target)
                if op['kind'] in ('rank_count', 'expert_count_block'):
                    values = [op['value']] if op['kind']=='rank_count' else op['values']
                    for index, value in enumerate(values):
                        key = (op['dst'], target['byte_offset']+index*8)
                        require(self._counts.get(key, 0)==0, 'count destination was not consumed/reset')
                        count_updates.append((key, value))
        elif kind == 'local_complete':
            if operation['id'] in self._leased:
                release = operation['id']
        elif kind == 'counts_ready':
            locations = []
            for op in self._ops.values():
                if op['call']==call and op['dst']==rank and op['kind'] in ('rank_count', 'expert_count_block'):
                    target = self._windows.resolve(op)['target']
                    expected = [op['value']] if op['kind']=='rank_count' else op['values']
                    for index, value in enumerate(expected):
                        key = (rank, target['byte_offset']+8*index)
                        require(self._counts.get(key, 0)==value and value < 0, 'counts_ready lacks a published count')
                        locations.append(key)
                    writes.append(target)
            consume = locations
        elif kind == 'barrier_ready':
            target = event['rolling_target']
            keys = [(rank, 0, sender) for sender in range(self._config['execution']['ranks'])]
            for key in keys:
                require(self._signals.get(key, 0)>=target, 'barrier lacks a visible signal')
                require(self._shadows.get(key, 0)==target-1, 'barrier shadow skipped a generation')
            barrier = (keys, target)
        for location in writes:
            self._leases.check_write(location)
        # All checks precede event commitment. A rejected call leaves both the
        # causal core and window state unchanged.
        row = self._runtime.fire(identity)
        if prepare is not None:
            self._prepared.add(prepare)
        if acquire is not None:
            self._leases.acquire(*acquire);self._leased.add(acquire[0])
        if release is not None:
            self._leases.release(release);self._leased.remove(release)
        for key, value in count_updates:
            self._counts[key] = value
        if signal_update is not None:
            self._signals[signal_update[0]] = signal_update[1]
        if consume is not None:
            for key in consume:
                self._counts[key] = 0
            self._consumed.append(dict(call=call, rank=rank, elements=len(consume)))
        if barrier is not None:
            keys, target = barrier
            for key in keys:
                self._shadows[key] = target
            self._barriers.append(dict(rank=rank, target=target))
        self._writes += len(writes)
        self._peak_leases = max(self._peak_leases, len(self._leased))
        if operation is not None:
            row['memory'] = operation['memory']
            if kind == 'issued':
                self._memory_log.append(dict(event=identity, operation=operation['id'],
                    path=operation['path'], **deepcopy(operation['memory'])))
        return row

    def snapshot(self):
        state = self._runtime.snapshot()
        state['schema_version'] = 'moe-modern-deepep-window-execution/1'
        state['windows'] = self._windows.describe()
        state['memory_state'] = dict(active_source_leases=self._leases.snapshot(),
            peak_source_leases=self._peak_leases, checked_writes=self._writes,
            consumed_counts=deepcopy(self._consumed), completed_barriers=deepcopy(self._barriers),
            nonzero_count_elements=sum(value!=0 for value in self._counts.values()),
            signals=[dict(owner=k[0], context_index=k[1], slot=k[2], value=v, shadow=self._shadows.get(k, 0))
                     for k,v in sorted(self._signals.items())])
        state['memory_log'] = deepcopy(self._memory_log)
        state.update(relative_windows_validated=True, tracked_source_lifetime_validated=True,
            scope='caller-event window/lease and integer shadow checks; no delivered bytes or GPU ordering proof',
            transport_payload_validated=False, numeric_token_data_validated=False,
            gpu_visibility_validated=False, buffer_lifetime_validated=False)
        return state


def replay(config_path, actions_path, output):
    raw, config = read(Path(config_path).resolve(), MAX_INPUT)
    validate(config)
    actions_raw, actions = read(Path(actions_path).resolve(), MAX_OUTPUT)
    fields(actions, 'schema_version events', 'modern execution transcript')
    require(actions['schema_version']=='moe-modern-deepep-actions/1', 'unsupported action schema')
    require(isinstance(actions['events'], list) and len(actions['events'])<=config['limits']['max_events'],
            'window runtime action count exceeds event budget')
    maximum = config['limits']['max_output_bytes']
    require(len(raw)<=config['limits']['max_input_bytes'] and len(actions_raw)<=maximum,
            'window runtime input budget exceeded')
    output = Path(output).resolve()
    require(not output.exists() and output.parent.is_dir(), 'require new window output with existing parent')
    require(shutil.disk_usage(output.parent).free>=8*2**30+maximum, 'reserve 8GiB')
    runtime = ModernDeepEPWindowRuntime(config)
    for identity in actions['events']:
        runtime.fire(identity)
    result = runtime.snapshot()
    result.update(passed=True, input_sha256=hashlib.sha256(raw).hexdigest(),
                  actions_sha256=hashlib.sha256(actions_raw).hexdigest())
    result_raw = encoded(result)
    require(len(result_raw)+len(raw)+len(actions_raw)<=maximum, 'window runtime output budget exceeded')
    output.write_bytes(result_raw)
    return dict(passed=True, output=str(output), fired_events=result['fired_events'],
        all_events_fired=result['all_events_fired'], simulated_packets=False,
        active_source_leases=len(result['memory_state']['active_source_leases']))
