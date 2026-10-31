"""Exact two-level ancestor coverage for append-only native operation ids.

This index stores declared prerequisites. It never observes or manufactures a
callback and it is not a network reachability or fault-recovery mechanism.
"""
import sys

from .deepep_plan import require


class NativeProofCoverage:
    __slots__ = ('_rows',)

    def __init__(self):
        self._rows = []

    def __len__(self):
        return len(self._rows)

    def _row(self, identity):
        require(type(identity) is int and 0 <= identity < len(self._rows),
                'native proof references unknown node')
        return self._rows[identity]

    @staticmethod
    def _level(event):
        require(event in ('applied', 'completed'), 'internal unsupported native evidence')
        return int(event == 'completed')

    def append(self, proof):
        """Add a node after validating all references; return its unchanged id."""
        identity = len(self._rows)
        require(identity < 65536, 'native node identity limit')
        applied = completed = 0
        for parent, event in proof:
            level = self._level(event)
            a, c = self._row(parent)
            applied |= a | (1 << parent)
            completed |= c | ((1 << parent) if level else 0)
        # Both integers are immutable. Only ids smaller than identity were
        # accepted; the row is consequently irreflexive and append-only.
        self._rows.append((applied, completed))
        return identity

    def compact(self, proof):
        """Match simultaneous legacy frontier reduction, including levels."""
        merged = {}
        applied = completed = 0
        for identity, event in proof:
            level = self._level(event)
            a, c = self._row(identity)
            merged[identity] = max(merged.get(identity, 0), level)
            applied |= a
            completed |= c
        # No row contains its own bit, so the union needs no self exception.
        # Test the original whole frontier, not an iteratively altered subset.
        return [(i, 'completed' if level else 'applied') for i, level in sorted(merged.items())
                if not ((completed if level else applied) >> i) & 1]

    def covers(self, node, ancestor, event):
        """Query declared coverage; no relation to observed completion time."""
        row = self._row(node)
        self._row(ancestor)
        return bool((row[self._level(event)] >> ancestor) & 1)

    def storage_bytes(self):
        """CPython retained index object estimate; excludes process/temporaries."""
        total = sys.getsizeof(self) + sys.getsizeof(self._rows)
        seen = set()
        for row in self._rows:
            total += sys.getsizeof(row)
            for value in row:
                if id(value) not in seen:
                    seen.add(id(value))
                    total += sys.getsizeof(value)
        return total
