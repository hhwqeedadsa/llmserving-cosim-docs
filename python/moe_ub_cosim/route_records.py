"""Immutable source-indexed route view with packed expert choices per request."""
from array import array
from collections.abc import Mapping
from copy import deepcopy

from .plan import require


class RouteRecords(Mapping):
    """Materialize only records being used; shared file evidence is copied on read.

    Normalized scheduler routes remain ordinary lists. This independent packed
    copy detects changes to those lists without millions of repeated file/path
    dictionaries. Public selection can still request an ordinary dict.
    """
    def __init__(self, layers):
        self._offsets = {}
        self._stride = 0
        for layer in layers:
            identity = layer['layer_id']
            require(identity not in self._offsets, 'Duplicate selected route')
            self._offsets[identity] = self._stride, layer['top_k']
            self._stride += layer['top_k']
        self._rows = []
        self._by_request = {}
        self._count = 0

    def add(self, ordinal, request, normalized, source):
        identity = str(request['request_id'])
        first = normalized['prompt_tokens'] + request['first_decode_entry'] - 1
        steps = len(normalized['decode_routes'])
        existing = self._by_request.setdefault(identity, [])
        if self._offsets:
            require(all(first + steps <= row['first'] or row['first'] + row['steps'] <= first
                        for row in existing), 'Duplicate selected route')
        choices = array('I')
        for route in normalized['decode_routes']:
            for layer in self._offsets:
                choices.extend(route[str(layer)])
        row = dict(ordinal=ordinal, request_id=identity, first=first, steps=steps,
                   first_entry=request['first_decode_entry'], choices=choices,
                   file_id=request['file_id'], source_file=deepcopy(source['file']))
        self._rows.append(row)
        existing.append(row)
        self._count += steps * len(self._offsets)

    def __len__(self):
        return self._count

    def __iter__(self):
        for row in sorted(self._rows, key=lambda row: row['ordinal']):
            for index in range(row['first'], row['first'] + row['steps']):
                for layer in self._offsets:
                    yield row['request_id'], index, layer

    def __getitem__(self, key):
        try:
            request, index, layer = key
            start, count = self._offsets[layer]
            for row in self._by_request[request]:
                step = index - row['first']
                if not 0 <= step < row['steps']:
                    continue
                begin = step * self._stride + start
                entry = row['first_entry'] + step
                return dict(request_id=request, phase='decode', token_index=index, layer_id=layer,
                    expert_ids=list(row['choices'][begin:begin+count]), source_file=deepcopy(row['source_file']),
                    file_id=row['file_id'], source_entry=entry, source_layer_id=layer, source_token_row=0,
                    decode_index=entry-1, source_index_convention='entry_0_prefill_entry_1_first_decode')
        except (KeyError, TypeError, ValueError):
            pass
        raise KeyError(key)

    def storage_counts(self):
        return dict(source_records=len(self._rows), logical_records=self._count,
                    expert_storage_bytes=sum(len(row['choices']) * row['choices'].itemsize for row in self._rows))


class RouteDomain:
    """Exact contiguous request/token/layer domain, without a tuple per route."""
    def __init__(self, requests, layers):
        self.layers = {layer: index for index, layer in enumerate(layers)}
        require(len(self.layers) == len(layers), 'Duplicate route layer')
        self.requests = {}
        self.count = 0
        for request in requests:
            key = str(request['request_id'])
            require(key not in self.requests, 'Duplicate request_id')
            first = request['prompt_tokens'] + request['generated_tokens'] - 1
            steps = len(request['decode_routes'])
            self.requests[key] = first, steps, self.count
            self.count += steps * len(self.layers)

    def __len__(self):
        return self.count

    def index(self, key):
        try:
            request, token, layer = key
            first, steps, base = self.requests[request]
            column = self.layers[layer]
            if type(token) is int and first <= token < first + steps:
                return base + (token - first) * len(self.layers) + column
        except (KeyError, TypeError, ValueError):
            pass
        raise KeyError(key)

    def __contains__(self, key):
        try:
            self.index(key)
            return True
        except KeyError:
            return False

    def __iter__(self):
        for request, (first, steps, _) in self.requests.items():
            for token in range(first, first + steps):
                for layer in self.layers:
                    yield request, token, layer


class RouteUsage:
    """One bit per declared route; unknown or duplicate consumption still fails."""
    def __init__(self, domain):
        self.domain = domain
        self.bits = bytearray((len(domain) + 7) // 8)
        self.count = 0

    def __len__(self):
        return self.count

    def __contains__(self, key):
        try:
            index = self.domain.index(key)
            return bool(self.bits[index // 8] & (1 << (index % 8)))
        except KeyError:
            return False

    def update(self, keys):
        for key in keys:
            index = self.domain.index(key)
            byte, mask = index // 8, 1 << (index % 8)
            require(not self.bits[byte] & mask, 'Segment route consumed twice')
            self.bits[byte] |= mask
            self.count += 1

    def __iter__(self):
        return (key for key in self.domain if key in self)

    def __eq__(self, other):
        if other is self.domain:
            return self.count == len(self.domain)
        if isinstance(other, RouteUsage) and other.domain is self.domain:
            return self.bits == other.bits
        return set(self) == other
