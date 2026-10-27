"""Untimed, bounded WRITE/fetch-add RC reference, independent of MRT1/ns-3.

The explicit atomic_return_required/1 policy rejects a plain ACK at a pending
atomic PSN. It is a safety reference, not an exact implementation of Linux RXE.
No times, NIC costs, retries, congestion control or hardware claims are inferred.
"""
from collections import OrderedDict
from dataclasses import dataclass

PSN_MAX = (1 << 24) - 1
WORD_MAX = (1 << 64) - 1
WRITE = ('WRITE_ONLY', 'WRITE_FIRST', 'WRITE_MIDDLE', 'WRITE_LAST')


def uint(value, maximum, name):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f'{name} must be an integer in [0, {maximum}]')
    return value


class UnsupportedTransition(ValueError):
    """The reference refuses to invent a result or silently infer missing state."""


@dataclass(frozen=True)
class Request:
    psn: int
    opcode: str
    ack_requested: bool = True
    payload: bytes = b''
    address: int | None = None
    key: int | None = None
    length: int | None = None
    addend: int | None = None

    def __post_init__(self):
        uint(self.psn, PSN_MAX, 'PSN')
        if self.opcode not in (*WRITE, 'FETCH_ADD') or type(self.ack_requested) is not bool:
            raise ValueError('unsupported request opcode or ACK flag')
        if type(self.payload) is not bytes:
            raise ValueError('payload must be immutable bytes')
        first = self.opcode in ('WRITE_ONLY', 'WRITE_FIRST', 'FETCH_ADD')
        if first:
            uint(self.address, WORD_MAX, 'address')
            uint(self.key, (1 << 32)-1, 'rkey')
        elif self.address is not None or self.key is not None:
            raise ValueError('middle/last WRITE has no RETH')
        if self.opcode == 'FETCH_ADD':
            uint(self.addend, WORD_MAX, 'addend')
            if self.payload or self.length is not None:
                raise ValueError('atomic operand is in ATMETH, not data payload')
        else:
            if not self.payload or self.addend is not None:
                raise ValueError('only nonempty WRITE is supported')
            if first:
                if uint(self.length, 65536, 'WRITE length') == 0:
                    raise ValueError('zero-length WRITE unsupported')
            elif self.length is not None:
                raise ValueError('middle/last WRITE has no RETH length')


@dataclass(frozen=True)
class Response:
    psn: int
    opcode: str
    old_value: int | None = None

    def __post_init__(self):
        uint(self.psn, PSN_MAX, 'response PSN')
        if self.opcode not in ('ACK', 'ATOMIC_ACK', 'SEQ_NAK'):
            raise ValueError('unsupported response opcode')
        if self.opcode == 'ATOMIC_ACK':
            uint(self.old_value, WORD_MAX, 'atomic old value')
        elif self.old_value is not None:
            raise ValueError('ordinary ACK/NAK cannot carry an atomic value')


class RcResponder:
    """One registered little-endian memory image and one established QP.

    Duplicate atomic responses come from a finite FIFO ring. An evicted old
    atomic is dropped, never re-executed. No response is itself acknowledged.
    """
    def __init__(self, initial, *, mtu=256, replay_slots=1, initial_psn=0, key=1):
        if type(initial) is not bytes or not 8 <= len(initial) <= 65536:
            raise ValueError('provide an 8..65536 byte immutable memory image')
        if mtu not in (256, 512, 1024, 2048, 4096) or type(mtu) is not int:
            raise ValueError('unsupported RC payload MTU')
        if uint(replay_slots, 64, 'replay slots') == 0:
            raise ValueError('at least one replay slot required')
        self.expected = uint(initial_psn, PSN_MAX, 'initial PSN')
        self.key = uint(key, (1 << 32)-1, 'rkey')
        self.memory = bytearray(initial)
        self.mtu, self.replay_slots = mtu, replay_slots
        self._replies = OrderedDict()
        self._write = None
        self._sent_nak = False
        self.effects = []

    def _range(self, address, length, key):
        if key != self.key or address + length > len(self.memory):
            raise UnsupportedTransition('invalid registered range/key; error-QP behavior is unsupported')

    def receive(self, packet):
        if type(packet) is not Request:
            raise ValueError('expected Request')
        if packet.psn > self.expected:
            if self._sent_nak:
                return None
            self._sent_nak = True
            return Response(self.expected, 'SEQ_NAK')
        if packet.psn < self.expected:
            if packet.opcode in WRITE:
                return Response(self.expected-1, 'ACK')
            old = self._replies.get(packet.psn)
            return None if old is None else Response(packet.psn, 'ATOMIC_ACK', old)
        # Reject unsupported wrap instead of guessing modulo ordering.
        if packet.psn == PSN_MAX:
            raise UnsupportedTransition('PSN wrap unsupported by this bounded reference')
        if packet.opcode == 'FETCH_ADD':
            if self._write is not None or packet.address % 8:
                raise UnsupportedTransition('atomic inside a WRITE or at an unaligned address')
            self._range(packet.address, 8, packet.key)
            old = int.from_bytes(self.memory[packet.address:packet.address+8], 'little')
            self.memory[packet.address:packet.address+8] = ((old+packet.addend) & WORD_MAX).to_bytes(8, 'little')
            self._replies[packet.psn] = old
            if len(self._replies) > self.replay_slots:
                self._replies.popitem(last=False)
            self.effects.append(dict(kind='fetch_add', psn=packet.psn, old=old))
            answer = Response(packet.psn, 'ATOMIC_ACK', old)
        else:
            first = packet.opcode in ('WRITE_ONLY', 'WRITE_FIRST')
            last = packet.opcode in ('WRITE_ONLY', 'WRITE_LAST')
            if first:
                if self._write is not None:
                    raise UnsupportedTransition('new operation inside an incomplete WRITE')
                self._range(packet.address, packet.length, packet.key)
                address, remaining = packet.address, packet.length
            else:
                if self._write is None:
                    raise UnsupportedTransition('WRITE continuation without FIRST')
                address, remaining = self._write
            n = len(packet.payload)
            if (last and (n != remaining or n > self.mtu)) or (not last and (n != self.mtu or remaining <= n)):
                raise ValueError('WRITE segmentation/length mismatch')
            self.memory[address:address+n] = packet.payload
            self._write = None if last else (address+n, remaining-n)
            self.effects.append(dict(kind='write', psn=packet.psn, address=address, bytes=n))
            answer = Response(packet.psn, 'ACK') if packet.ack_requested else None
        self.expected += 1
        self._sent_nak = False
        return answer


class RcRequester:
    """Bounded packet generator and value-aware prefix completion reference.

    One send() emits one whole WQE as an untimed batch. Atomic credit and an
    explicit fence constrain admission. Partial NIC packet windows, pacing,
    timeout/retry policies and send-queue doorbells are deliberately absent.
    """
    policy = 'atomic_return_required/1'

    def __init__(self, *, mtu=256, atomic_depth=1, initial_psn=0):
        if mtu not in (256, 512, 1024, 2048, 4096) or type(mtu) is not int:
            raise ValueError('unsupported RC payload MTU')
        if uint(atomic_depth, 64, 'atomic depth') == 0:
            raise ValueError('at least one atomic credit required')
        self.mtu, self.atomic_depth = mtu, atomic_depth
        self.next_psn = uint(initial_psn, PSN_MAX, 'initial PSN')
        self.operations = []
        self._packets = {}
        self._outstanding_atomics = 0
        self._retired = 0
        self._noack = 0

    def post(self, kind, address, *, data=None, addend=None, key=1, fence=False):
        uint(address, WORD_MAX, 'address')
        uint(key, (1 << 32)-1, 'rkey')
        if type(fence) is not bool or len(self.operations) >= 64:
            raise ValueError('invalid fence or bounded operation limit')
        if kind == 'write':
            if type(data) is not bytes or not 0 < len(data) <= 65536 or addend is not None:
                raise ValueError('WRITE requires 1..65536 immutable bytes')
        elif kind == 'fetch_add':
            uint(addend, WORD_MAX, 'addend')
            if data is not None or address % 8:
                raise ValueError('fetch-add requires an aligned address and no data payload')
        else:
            raise ValueError('only write/fetch_add are supported')
        if sum(len(o['data'] or b'') for o in self.operations)+(len(data) if data else 0) > 65536:
            raise ValueError('total WRITE payload exceeds reference budget')
        self.operations.append(dict(kind=kind,address=address,key=key,data=data,addend=addend,
                                    fence=fence,started=False,completed=False,old_value=None))
        return len(self.operations)-1  # Simulator association only; absent from packets.

    def send(self):
        op = next((o for o in self.operations if not o['started']), None)
        if op is None or (op['fence'] and self._outstanding_atomics):
            return ()
        atomic = op['kind'] == 'fetch_add'
        if atomic and self._outstanding_atomics == self.atomic_depth:
            return ()
        count = 1 if atomic else (len(op['data'])+self.mtu-1)//self.mtu
        if self.next_psn+count > PSN_MAX:
            raise UnsupportedTransition('PSN wrap unsupported by this bounded reference')
        packets = []
        for i in range(count):
            # rxe_req.c: END || (noack_pkts++ > RXE_MAX_PKT_PER_ACK).
            # END short-circuits the increment; an ACK resets the counter.
            ack = atomic or i == count-1 or self._noack > 64
            self._noack = 0 if ack else self._noack+1
            common = dict(psn=self.next_psn+i,ack_requested=ack)
            if atomic:
                p = Request(**common,opcode='FETCH_ADD',address=op['address'],key=op['key'],addend=op['addend'])
            else:
                opcode = 'WRITE_ONLY' if count == 1 else 'WRITE_FIRST' if i == 0 else 'WRITE_LAST' if i == count-1 else 'WRITE_MIDDLE'
                metadata = dict(address=op['address'],key=op['key'],length=len(op['data'])) if i == 0 else {}
                p = Request(**common,opcode=opcode,payload=op['data'][i*self.mtu:(i+1)*self.mtu],**metadata)
            packets.append(p)
            self._packets[p.psn] = p
        op.update(started=True,first_psn=self.next_psn,last_psn=self.next_psn+count-1)
        self.next_psn += count
        self._outstanding_atomics += atomic
        return tuple(packets)

    def receive(self, response):
        if type(response) is not Response:
            raise ValueError('expected Response')
        if response.psn >= self.next_psn:
            raise ValueError('response references a request not issued by this QP')
        if response.opcode == 'SEQ_NAK':
            return 'retry_required'
        while self._retired < len(self.operations):
            op = self.operations[self._retired]
            if not op['started'] or response.psn < op['first_psn']:
                return 'old_response'
            if op['kind'] == 'fetch_add':
                if response.psn > op['last_psn']:
                    return 'retry_required'
                if response.opcode != 'ATOMIC_ACK':
                    raise UnsupportedTransition('ordinary ACK cannot supply the pending atomic return value')
                op['old_value'] = response.old_value
                self._outstanding_atomics -= 1
            else:
                if response.psn < op['last_psn']:
                    return 'partial_write_ack'
                if response.psn == op['last_psn'] and response.opcode != 'ACK':
                    raise UnsupportedTransition('Atomic ACK does not complete a WRITE at the same PSN')
            op['completed'] = True
            self._retired += 1
            if response.psn == op['last_psn']:
                return 'completed'
        return 'old_response'

    def retransmit(self):
        """Return pending WQEs for a caller-declared retry; no timer is modeled."""
        return tuple(self._packets[p] for o in self.operations if o['started'] and not o['completed']
                     for p in range(o['first_psn'],o['last_psn']+1))

    def snapshot(self):
        return dict(policy=self.policy,next_psn=self.next_psn,atomic_credit=self.atomic_depth-self._outstanding_atomics,
                    source_held=[i for i,o in enumerate(self.operations) if o['started'] and not o['completed']],
                    operations=[{k:v for k,v in o.items() if k != 'data'} for o in self.operations],
                    network_executed=False,hardware_calibrated=False)
