"""Pure admission for explicitly controlled provider stages; no device timing."""
from copy import deepcopy

from .scaleout_topology import require
from .scaleout_transactions import execution_limits


def staged(program):
    return program.get('submission_model') == dict(mode='staged_prefix/1')


def stage_plan(program, delivered, requested, prepare, publish):
    """Return a candidate only. Rejection must leave a pending COMMIT usable."""
    next_requested = {key: set(requested[key]) for key in ('prepare', 'publish')}
    extra = {}
    known = {op['id']: op for op in program['operations']}
    for name, values, key, event in (
        ('prepare_operations', prepare, 'prepare', 'submitted'),
        ('publish_operations', publish, 'publish', 'prepared'),
    ):
        if values is None:
            continue
        require(staged(program), 'provider stage controls require staged_prefix/1')
        require(isinstance(values, list) and len(values) <= execution_limits(program)['max_remote_operations'],
                'invalid provider stage control count')
        seen = set()
        for identity in values:
            require(type(identity) is int and identity in known and known[identity]['kind'] == 'remote',
                    'provider stage must reference an existing remote operation')
            require((identity, event) in delivered,
                    'provider stage requires a previously delivered '+event+' callback')
            require(identity not in seen and identity not in requested[key],
                    'duplicate provider stage request')
            require(key != 'publish' or known[identity]['aggregate_requests'] is False,
                    'aggregated operation cannot ring a publication')
            seen.add(identity)
        next_requested[key].update(seen)
        extra[name] = deepcopy(values)
    return next_requested, extra
