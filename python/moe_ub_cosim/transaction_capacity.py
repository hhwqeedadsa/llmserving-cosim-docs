"""Explicit transaction software capacities; never hardware specifications."""
from .scaleout_topology import fields, integer, require

PROFILE = 'scaleout_ep32/1'


def execution_limits(config):
    """Lifetime admission counts; independent of queue depth and byte budgets."""
    require(isinstance(config, dict), 'transaction input must be an object')
    defaults = dict(max_connections=8, max_remote_operations=32, max_local_operations=16)
    if 'execution_limits' not in config:
        return defaults
    require(config.get('schema_version') == 'moe-rdma-transactions/5', 'execution limits require transactions/5')
    value = config['execution_limits']
    require(isinstance(value, dict), 'execution limits must be an object')
    scaled = 'profile' in value
    fields(value, 'max_connections max_remote_operations max_local_operations'+(' profile' if scaled else ''), 'execution limits')
    require(not scaled or value['profile'] == PROFILE, 'unsupported transaction capacity profile')
    for key, low, high in [('max_connections', 1, 2048 if scaled else 64),
                           ('max_remote_operations', 1, 16384 if scaled else 1024),
                           ('max_local_operations', 0, 1024 if scaled else 256)]:
        integer(value[key], key, low, high)
    return {key: value[key] for key in defaults}


def capacity(config):
    execution_limits(config)  # Validate before interpreting the optional discriminator.
    scaled = config.get('execution_limits', {}).get('profile') == PROFILE
    return dict(scaled=scaled, owner=511 if scaled else 31, windows=128 if scaled else 16,
                registrations=256 if scaled else 32, waits=32 if scaled else 8,
                dependencies=128 if scaled else 32, decisions=1024 if scaled else 256)


def host_budget(config, protocol_budget):
    scaled = capacity(config)['scaled']
    result = dict(protocol_budget)
    caps = dict(wall_seconds=180 if scaled else 60, rss_bytes=(3 if scaled else 1)*2**30,
                address_bytes=(6 if scaled else 2)*2**30, artifact_bytes=(512 if scaled else 64)*2**20)
    result.update({key: min(result[key], cap) for key, cap in caps.items()})
    return result
