"""One session's pure streams admission reuse; never transport/runtime state."""
import math

from . import scaleout_streams


class _Unkeyable(Exception):
    pass


def _freeze(value):
    """Detach built-in JSON values, retaining exact types and container order.

    Unknown types must run the original validator. In particular, Python's
    bool/int/float equality and tuple/list JSON coercions cannot create hits.
    No hashes, user-defined equality, deepcopy hooks or caller aliases remain.
    """
    kind = type(value)
    if value is None:
        return ('null',)
    if kind is bool:
        return ('bool', value)
    if kind is int:
        return ('int', value)
    if kind is float and math.isfinite(value):
        return ('float', value.hex())
    if kind is str:
        return ('str', value)
    if kind is list:
        return ('list', tuple(_freeze(item) for item in value))
    if kind is dict and all(type(key) is str for key in value):
        return ('dict', tuple((key, _freeze(item)) for key, item in value.items()))
    raise _Unkeyable()


class StaticStreamValidationCache:
    """Retain exactly one successful static key; failed checks never replace it.

    This cache is intentionally independent of dynamic transaction admission.
    A later dynamic rejection cannot invalidate a successful pure static check.
    """

    def __init__(self):
        self._key = None
        self.calls = self.hits = self.validations = self.unkeyable = 0

    def validate(self, topology, protocol, descriptor, *, transaction_peer_copies=False):
        self.calls += 1
        try:
            key = (_freeze(topology), _freeze(protocol), _freeze(descriptor), _freeze(transaction_peer_copies))
        except (_Unkeyable, RecursionError):
            key = None
            self.unkeyable += 1
        if key is not None and key == self._key:
            self.hits += 1
            return
        self.validations += 1
        scaleout_streams.validate(topology, protocol, descriptor, transaction_peer_copies=transaction_peer_copies)
        # Keep the previous successful key on failure or unsupported inputs.
        if key is not None:
            self._key = key

    def snapshot(self):
        return dict(schema_version='moe-static-stream-validation/1', host_only=True,
                    calls=self.calls, hits=self.hits, validations=self.validations,
                    unkeyable=self.unkeyable, entries=int(self._key is not None))
