"""Explicit execution limits; legacy small runs retain their original defaults."""
from .scaleout_topology import fields, integer, require

DEFAULTS = dict(max_gpus=32, max_switches=16, max_links=256, max_messages=8192,
                max_queue_events=200000, queue_capture='events', max_sim_ns=5000000,
                wall_seconds=60, rss_bytes=1073741824, address_bytes=2147483648,
                artifact_bytes=536870912, result_json_bytes=16777216)


def limits(protocol):
    if protocol.get('schema_version') == 'moe-scaleout-rdma/1':
        return dict(DEFAULTS)
    require(protocol.get('schema_version') == 'moe-scaleout-rdma/2', 'unsupported protocol limits version')
    value = protocol['limits']
    fields(value, ' '.join(DEFAULTS), 'execution limits')
    bounds = dict(max_gpus=(2,1024), max_switches=(1,256), max_links=(1,16384),
                  max_messages=(1,1000000), max_queue_events=(1,2000000),
                  max_sim_ns=(1,1000000000), wall_seconds=(1,600),
                  rss_bytes=(268435456,17179869184), address_bytes=(536870912,34359738368),
                  artifact_bytes=(16777216,4294967296), result_json_bytes=(16777216,1073741824))
    for key, (low, high) in bounds.items():
        integer(value[key], key, low, high)
    require(value['queue_capture'] in ('events','summary'), 'invalid queue capture mode')
    require(value['address_bytes'] >= value['rss_bytes'], 'address limit below RSS limit')
    return dict(value)
