"""Lossless plan JSON storage, with memory bounded by a single table record.

The old pretty encoding remains available. Compact encoding only removes JSON
whitespace: field/list order, numbers and all semantic fields are retained.
"""
import hashlib
import json


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def compact_plan_chunks(value, key=None):
    """Encode large tables one row at a time using the native JSON encoder.

    Layers and rank graphs contain nested tables and must themselves be walked.
    All other arrays contain individual records/scalars, not whole graph tables.
    Input is an already validated plan, never an untrusted streaming parser.
    """
    encode = lambda item: json.dumps(item, separators=(',', ':'), ensure_ascii=True)
    if isinstance(value, dict):
        yield '{'
        for i, (name, item) in enumerate(value.items()):
            if i:
                yield ','
            yield encode(name) + ':'
            yield from compact_plan_chunks(item, name)
        yield '}'
    elif isinstance(value, list):
        yield '['
        for i, item in enumerate(value):
            if i:
                yield ','
            if key in ('layers', 'rank_graphs'):
                yield from compact_plan_chunks(item)
            else:
                yield encode(item)
        yield ']'
    else:
        yield encode(value)


def write_plan_json(plan, path, *, compact=False, max_bytes=None):
    pieces = compact_plan_chunks(plan) if compact else json.JSONEncoder(indent=2).iterencode(plan)
    written = 0
    digest = hashlib.sha256()
    with path.open('wb') as stream:
        for piece in pieces:
            data = piece.encode('utf-8')
            written += len(data)
            if max_bytes is not None and written + 1 > max_bytes:
                raise ValueError('Graph resource budget exceeded: max_plan_bytes')
            stream.write(data)
            digest.update(data)
        stream.write(b'\n')
        digest.update(b'\n')
    return dict(bytes=written + 1, sha256=digest.hexdigest())
