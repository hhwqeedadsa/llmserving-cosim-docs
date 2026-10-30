"""Explicit persistent bindings under the real ASTRA SEND/RECV scheduler."""
from collections import Counter
from copy import deepcopy

from .scaleout_messages import MessagePreflight
from .scaleout_streams import validate_transport
from .scaleout_topology import fields, require
from .scaleout_limits import limits


def validate(config, topology, protocol):
    fields(config,'schema_version streams limits transport_recovery dcqcn_reference'+
           (' source_rate_capture' if 'source_rate_capture' in config else ''),'ASTRA message transport')
    require(config['schema_version']=='moe-astra-message-transport/1','unsupported ASTRA transport schema')
    # Only the declarations are checked with the shared stream validator.
    # No synthetic work, local durations or saved-DAG executor is submitted.
    projection=dict(deepcopy(config),schema_version='moe-rdma-stream-work/4',work=[],
                    local_model=dict(mode='serial_per_gpu/1',duration_source='declared_ns'))
    counts=validate_transport(topology,protocol,projection)
    require(counts['max_work']<=limits(protocol)['max_messages'],'transport work exceeds native message budget')
    require(config['transport_recovery'].get('on_exhausted','stop_simulation')=='stop_simulation',
            'ASTRA service does not yet propagate isolated-stream failures')
    capture=protocol.get('packet_capture',{})
    require(capture.get('start_ns')==0 and capture.get('end_ns')==limits(protocol)['max_sim_ns'] and
            capture.get('nodes')==[], 'ASTRA persistent service requires full-scope bounded packet capture')
    bindings={}
    for stream in config['streams']:
        key=tuple(stream[k] for k in ('src','dst','plane'))
        require(key not in bindings,'ambiguous ASTRA stream binding')
        bindings[key]=stream['id']
    return bindings


class PersistentMessagePreflight:
    def __init__(self, topology, protocol, config):
        self.bindings=validate(config,topology,protocol)
        self.generic=MessagePreflight(topology,protocol,legacy_messages=False)
        self.maximum=config['limits']['max_work'];self.ids=set();self.totals=Counter()

    def accept_wave(self, wave, now_ns):
        fresh=set();totals=self.totals.copy()
        require(len(self.ids)+len(wave['messages'])<=self.maximum,'persistent service work budget exceeded')
        for message in wave['messages']:
            identity=message['id']
            require(identity not in self.ids and identity not in fresh,'duplicate persistent message')
            fresh.add(identity)
            key=tuple(message[k] for k in ('src','dst','plane'))
            require(key in self.bindings,'actual ASTRA message has no explicit stream binding')
            require(type(message['bytes']) is int and 0<message['bytes']<=2**20,
                    'persistent ASTRA message exceeds 1MiB; automatic splitting is not supported')
            totals[self.bindings[key]]+=message['bytes']
            require(totals[self.bindings[key]]<=64*2**20,'persistent ASTRA stream exceeds 64MiB sequence budget')
        self.generic.accept_wave(wave,now_ns)
        self.ids.update(fresh);self.totals=totals


def message_preflight(topology, protocol, config):
    if 'transport' not in config:return MessagePreflight(topology,protocol)
    return PersistentMessagePreflight(topology,protocol,config['transport'])


def native_arguments(config, output):
    from .phase_completion import enabled
    args=['--phase-completion','ep_transport_flush/1'] if enabled(config) else []
    from .execution_events import configuration
    option=configuration(config)
    if option:args+=['--execution-events',str(option['max_events'])]
    from .source_ack_watchdog import configuration as watchdog_configuration
    watchdog=watchdog_configuration(config)
    if watchdog:
        from .scaleout_rdma import save
        watchdog_path=output/'source-ack-watchdog-config.json';save(watchdog_path,watchdog)
        args+=['--source-ack-watchdog',str(watchdog_path)]
    from .inflight_helpers import configuration as helper_configuration
    helpers=helper_configuration(config)
    if helpers:
        from .token_transport import payload_sizes
        path=output/'inflight-helpers-config.json'
        save(path,dict(configuration=helpers,groups=config['parallel']['groups'],experts=config['model']['experts'],
            expert_to_group_rank=config['route_replay'].get('expert_to_group_rank'),
            expert_ns_per_contribution=config['compute']['expert_ns_per_contribution'],
            local_reduce_ns_per_extra=config['token_transport']['local_reduce_ns_per_extra'],payload_sizes=payload_sizes(config)))
        args+=['--inflight-helpers',str(path)]
    if 'transport' not in config:return args
    from .scaleout_rdma import save
    path=output/'service-transport.json';save(path,config['transport'])
    return args+['--transport',str(path)]


def semantic_messages(result):
    """Actual submitted messages; packet IDs remain stream IDs in this mode."""
    if 'service_transport' in result:return result['service_transport']['messages']
    return result['messages']
