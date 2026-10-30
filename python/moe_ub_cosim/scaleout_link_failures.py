"""Switch-link failures/restoration with explicitly declared route convergence."""
from .scaleout_topology import fields, integer, require


def validate_options(config):
    if 'local_failover' in config:
        policy = config['local_failover']
        fields(policy, 'mode', 'local failover')
        require(policy['mode'] == 'live_ecmp/1' and 'link_failures' in config,
                'local failover requires live_ecmp/1 and an explicit link failure schedule')
    if 'link_failures' not in config:
        return
    from .scaleout_limits import limits
    maximum = limits(config)['max_sim_ns']
    model = config['link_failures']
    fields(model, 'mode events', 'link failures')
    require(model['mode'] in ('switch_links/1', 'switch_links/2'), 'unsupported link failure mode')
    temporary = model['mode'] == 'switch_links/2'
    events = model['events']
    require(isinstance(events, list) and 1 <= len(events) <= 8, 'require 1..8 switch link failures')
    seen = set()
    for event in events:
        fields(event, 'src dst time_ns route_update_delay_ns'+
               (' restore_time_ns restore_route_update_delay_ns' if temporary else ''), 'link failure event')
        integer(event['src'], 'failure source switch', 0, 1279)
        integer(event['dst'], 'failure destination switch', event['src']+1, 1279)
        integer(event['time_ns'], 'link failure time', 0, maximum-1)
        integer(event['route_update_delay_ns'], 'route update delay', 0, maximum-1-event['time_ns'])
        if temporary:
            integer(event['restore_time_ns'], 'restore time', event['time_ns']+1, maximum-1)
            integer(event['restore_route_update_delay_ns'], 'restore route update delay',
                    0, maximum-1-event['restore_time_ns'])
        pair = event['src'], event['dst']
        require(pair not in seen, 'duplicate link failure')
        seen.add(pair)


def validate_topology(topology, config):
    validate_options(config)
    nodes = {n['id']: n for n in topology['nodes']}
    edges = {(e['src'], e['dst']) for e in topology['links']}
    for event in config.get('link_failures', {}).get('events', []):
        a, b = event['src'], event['dst']
        require((a, b) in edges and nodes[a]['kind'] != 'gpu' and nodes[b]['kind'] != 'gpu',
                'link failure must select an actual switch-switch edge')
        require(nodes[a]['plane'] == nodes[b]['plane'], 'failure edge must stay in one plane')
