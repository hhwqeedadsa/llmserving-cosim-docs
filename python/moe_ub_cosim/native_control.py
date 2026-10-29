"""Strict control-frame decoding, shared by the native ASTRA frontends.

Do not salvage a JSON prefix from a corrupted frame: the original frame is
already written to native.stdout.log and execution must fail explicitly.
"""
import json

PREFIX='MOE_RDMA_EVENT '


def event(line):
    if not line.startswith(PREFIX):return None
    payload=line[len(PREFIX):]
    if len(payload.encode('utf-8'))>1048577 or not payload.endswith('\n'):
        raise ValueError('truncated or oversized native control frame; inspect native.stdout.log')
    try:
        result=json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError('invalid native control frame; inspect native.stdout.log') from error
    if not isinstance(result,dict) or result.get('type')!='DECIDE':
        raise ValueError('unexpected native control event; inspect native.stdout.log')
    return result
