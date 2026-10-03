"""Explicit physical ownership; never infer chips from rank or endpoint numbers."""
LOCAL_PROFILE = 'same-chip-local-reference'
FABRIC_PROFILE = 'urma-write-rtp-default'


def validate_deployment(deployment, ranks):
    from .plan import require, integer
    require(isinstance(deployment, dict) and set(deployment) ==
            {'network_scope', 'ranks', 'local_prepare_ns_per_vector'}, 'Invalid deployment fields')
    require(deployment['network_scope'] == 'cross_chip', 'Unsupported network scope')
    entries = deployment['ranks']
    require(isinstance(entries, list) and len(entries) == len(ranks), 'Incomplete physical ownership')
    dies, chips = set(), {}
    for rank, entry in zip(ranks, entries):
        require(isinstance(entry, dict) and set(entry) == {'rank_id', 'die_id', 'chip_id', 'server_id'},
                'Invalid physical ownership fields')
        integer(entry['rank_id'], 'physical rank', 0, 2**31-2)
        require(entry['rank_id'] == rank, 'Physical ownership must follow rank order')
        for key in ('die_id', 'chip_id', 'server_id'):
            require(isinstance(entry[key], str) and bool(entry[key]), 'Empty physical ' + key)
        require(entry['die_id'] not in dies, 'Duplicate global die identity')
        dies.add(entry['die_id'])
        require(chips.setdefault(entry['chip_id'], entry['server_id']) == entry['server_id'],
                'Global chip identity belongs to multiple servers')
    profile = deployment['local_prepare_ns_per_vector']
    require(isinstance(profile, dict) and set(profile) == {'dispatch', 'combine'}, 'Invalid local prepare profile')
    for kind, value in profile.items():
        integer(value, kind + ' local prepare time')


def locality(config, src, dst):
    if src == dst:
        return 'same_rank'
    if 'deployment' in config:
        chips = {e['rank_id']: e['chip_id'] for e in config['deployment']['ranks']}
        if chips[src] == chips[dst]:
            return 'same_chip'
    return 'cross_chip'


def contributions(config):
    result = []
    for index, token in enumerate(config['tokens']):
        destinations = {}
        for expert in token['expert_ids']:
            destinations.setdefault(config['expert_placement'][expert], []).append(expert)
        for dst, experts in sorted(destinations.items()):
            result.append(dict(source_record_index=index, src_rank=token['src_rank'], dst_rank=dst,
                expert_ids=sorted(experts), locality=locality(config, token['src_rank'], dst),
                dispatch_bytes=config['hidden_dim']*config['dispatch_element_bytes'],
                combine_bytes=config['hidden_dim']*config['combine_element_bytes']))
    return result
