"""Explicit fixed-source direct BF16 token/rank data projection.

No GIN control traffic, numerical tensors or calibrated GPU costs are modeled.
The independent graph audit below does not call the graph generator.
"""
from collections import Counter

from .phase_completion import enabled as phase_enabled
from .scaleout_topology import fields, integer, require

MODE = 'deepep_direct_bf16/1'
COMBINE_MODES = ('expanded_per_expert/1', 'rank_reduced/1')


def configuration(config):
    if 'token_transport' not in config:
        return None
    spec = config['token_transport']
    fields(spec, 'mode combine local_reduce_ns_per_extra', 'token transport')
    require(spec['mode'] == MODE and spec['combine'] in COMBINE_MODES,
            'unsupported token transport mode')
    integer(spec['local_reduce_ns_per_extra'], 'local reduction unit ns', 0, 10**9)
    require(spec['combine'] == 'rank_reduced/1' or spec['local_reduce_ns_per_extra'] == 0,
            'expanded combine cannot use local reduction cost')
    require(phase_enabled(config) and 'transport' in config,
            'direct token transport requires explicit phase completion and persistent transport')
    model = config['model']
    require(model['activation_bytes'] == 2 and model['hidden_size'] * 2 % 32 == 0,
            'direct BF16 projection needs two-byte activations and 32-byte hidden alignment')
    return spec


def payload_sizes(config):
    spec = configuration(config)
    if spec is None:
        size = config['model']['hidden_size'] * config['model']['activation_bytes']
        return dict(dispatch=size, combine=size)
    hidden = config['model']['hidden_size'] * 2
    topk = config['model']['top_k']
    def layout(metadata):
        return ((hidden + (metadata + 31)//32*32 + 63)//64)*64
    return dict(dispatch=layout(topk*8+(1+topk)*4),
                combine=hidden if spec['combine'] == 'expanded_per_expert/1' else layout(topk*8))


def verify_wave(wave, config):
    """Audit token units, original expert work and reduction edges independently."""
    spec = configuration(config)
    if spec is None:
        require('token_transport' not in wave and
                not any('transport_tokens' in m for m in wave['messages']),
                'undeclared token transport projection')
        return
    sizes = payload_sizes(config)
    require(wave.get('token_transport') == dict(spec, payload_bytes_per_token=sizes),
            'wave token transport differs from explicit input')
    ranks = wave['rank_ids']
    placement = config.get('route_replay', {}).get('expert_to_group_rank')
    expected = {}
    for layer in wave['routes']:
        lid = layer['layer']
        contributions, units, work, extras, experts = Counter(), Counter(), Counter(), Counter(), Counter()
        seen = set()
        for token in layer['tokens']:
            identity = token['rank'], token['slot']
            require(identity not in seen and token['rank'] in ranks, 'duplicate/invalid token identity')
            seen.add(identity)
            require(len(token['experts']) == len(set(token['experts'])) == config['model']['top_k'],
                    'invalid unique expert choices')
            destinations = Counter()
            for expert in token['experts']:
                require(type(expert) is int and 0 <= expert < config['model']['experts'], 'invalid expert')
                owner = placement[lid][expert] if placement is not None else expert//(config['model']['experts']//len(ranks))
                dst = ranks[owner]
                destinations[dst] += 1
                experts[expert] += 1
                work[dst] += 1
                contributions[token['rank'], dst] += 1
            for dst, count in destinations.items():
                units[token['rank'], dst] += 1
                extras[dst] += count - 1
        require({int(e): n for e, n in layer['expert_counts'].items()} == dict(experts),
                'transport projection changed expert work counts')
        require(layer['contributions'] == [dict(src=s, dst=d, count=n)
                for (s, d), n in sorted(contributions.items())], 'expert contribution table changed')
        for (src, dst), count in contributions.items():
            if src == dst:
                continue
            n = units[src, dst]
            expected[lid, 'dispatch', src, dst] = count, n, n*sizes['dispatch']
            n = n if spec['combine'] == 'rank_reduced/1' else count
            expected[lid, 'combine', dst, src] = count, n, n*sizes['combine']
        for rank in ranks:
            tasks = wave['rank_nodes'].get(rank, wave['rank_nodes'].get(str(rank)))
            by_name = {t['name']: t for t in tasks}
            expert = by_name[f'L{lid}.experts']
            require(expert['duration_ns'] == config['compute']['expert_ns_per_contribution']*work[rank],
                    'deduplication reduced expert compute')
            reduce_name = f'L{lid}.combine_reduce'
            if spec['combine'] == 'rank_reduced/1':
                reduce = by_name[reduce_name]
                require(reduce['kind'] == 'COMP' and reduce['deps'] == [expert['id']] and
                        reduce['duration_ns'] == extras[rank]*spec['local_reduce_ns_per_extra'],
                        'invalid local reduction dependency or cost')
                producer = reduce
            else:
                require(reduce_name not in by_name, 'undeclared local reduction work')
                producer = expert
            for task in tasks:
                if task['kind'] == 'SEND' and task['message']['layer'] == lid and task['message']['phase'] == 'combine':
                    require(task['deps'] == [producer['id']], 'combine send omitted local producer')
    actual = {(m['layer'], m['phase'], m['src'], m['dst']):
              (m['contributions'], m['transport_tokens'], m['bytes']) for m in wave['messages']}
    require(len(actual) == len(wave['messages']) and actual == expected,
            'token transport unit/contribution/payload mismatch')
