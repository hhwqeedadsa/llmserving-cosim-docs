"""Input-driven Hybrid routing and Dispatch progress obligations, without time.

This is a logical subset of pinned DeepEP, not an executable collective DAG.
Final GPU slots, barriers, Combine scheduling and provider costs are unresolved.
"""
from collections import defaultdict
import hashlib
from pathlib import Path

from .deepep_plan import encoded, fields, integer, read, require
from .modern_deepep_plan import REVISION, token_bytes

MAX_INPUT, MAX_OUTPUT, MAX_ROWS = 2**20, 16*2**20, 16384
SCOPE = dict(network_executed=False, hardware_calibrated=False,
             complete_collective_dag=False, final_gpu_slots_resolved=False,
             source_lifetime_modeled=False, combine_progress_modeled=False)


def validate(config):
    fields(config, 'schema_version model deployment execution calls limits', 'Hybrid root')
    require(config['schema_version'] == 'moe-hybrid-logical-input/1', 'unsupported Hybrid schema')
    m, p, x, limits = (config[k] for k in ('model', 'deployment', 'execution', 'limits'))
    fields(m, 'experts top_k hidden_size precision', 'Hybrid model')
    integer(m['experts'], 4, 2048, 'experts')
    integer(m['top_k'], 1, min(32, m['experts']), 'top_k')
    integer(m['hidden_size'], 256, 8192, 'hidden_size')
    require(m['precision'] == 'bf16' and m['hidden_size'] % 256 == 0, 'require BF16/H divisible by 256')
    fields(p, 'domains rails', 'Hybrid deployment')
    require(isinstance(p['domains'], list) and 2 <= len(p['domains']) <= 4, 'require 2..4 domains')
    require(isinstance(p['rails'], list) and 2 <= len(p['rails']) <= 8, 'require 2..8 rails')
    domains, scaleup = len(p['domains']), len(p['rails'])
    ranks = domains*scaleup
    require(ranks <= 32 and m['experts'] % ranks == 0, 'invalid rank/expert partition')
    for label, rows in (('domain', p['domains']), ('rail', p['rails'])):
        names = set()
        for i, row in enumerate(rows):
            fields(row, 'name ranks', 'Hybrid '+label)
            name = row['name']
            require(isinstance(name, str) and 0 < len(name) <= 64 and name not in names, 'invalid '+label+' name')
            names.add(name)
            expected = list(range(i*scaleup, (i+1)*scaleup)) if label == 'domain' else list(range(i, ranks, scaleup))
            require(isinstance(row['ranks'], list) and all(type(r) is int for r in row['ranks'])
                    and row['ranks'] == expected, 'domain/rail must match contiguous rank layout and same-position rail')
    fields(x, 'mode num_sms scaleout_warps max_tokens_per_rank expanded_layout allow_multiple_reduction', 'Hybrid execution')
    require(x['mode'] == 'hybrid_fresh', 'only fresh Hybrid logical layout supported')
    integer(x['num_sms'], 1, 8, 'num_sms')
    integer(x['scaleout_warps'], 1, 8, 'scaleout_warps')
    integer(x['max_tokens_per_rank'], 1, 16, 'max_tokens_per_rank')
    require(type(x['expanded_layout']) is bool and type(x['allow_multiple_reduction']) is bool, 'require Boolean layout/reduction')
    fields(limits, 'max_rows max_input_bytes max_output_bytes', 'Hybrid limits')
    for key, maximum in (('max_rows', MAX_ROWS), ('max_input_bytes', MAX_INPUT), ('max_output_bytes', MAX_OUTPUT)):
        integer(limits[key], 1, maximum, key)
    require(isinstance(config['calls'], list) and 1 <= len(config['calls']) <= 4, 'require 1..4 calls')
    names = set()
    for call in config['calls']:
        fields(call, 'name tokens', 'Hybrid call')
        require(isinstance(call['name'], str) and 0 < len(call['name']) <= 64 and call['name'] not in names, 'invalid call name')
        names.add(call['name'])
        require(isinstance(call['tokens'], list) and len(call['tokens']) == ranks, 'incomplete source rank rows')
        for tokens in call['tokens']:
            require(isinstance(tokens, list) and len(tokens) <= x['max_tokens_per_rank'], 'rank token budget exceeded')
            for choices in tokens:
                require(isinstance(choices, list) and len(choices) == m['top_k'] and
                        all(type(e) is int and 0 <= e < m['experts'] for e in choices) and
                        len(set(choices)) == len(choices), 'invalid/masked/repeated expert choices')


def compile_plan(config):
    validate(config)
    m, p, x = (config[k] for k in ('model', 'deployment', 'execution'))
    domains, scaleup = len(p['domains']), len(p['rails'])
    ranks, channels = domains*scaleup, x['num_sms']*x['scaleout_warps']
    epr = m['experts']//ranks
    tables = {k: [] for k in ('dispatch', 'forwards', 'combine', 'count_blocks', 'channels')}
    row_count = 0

    def add(table, row):
        nonlocal row_count
        row_count += 1
        require(row_count <= config['limits']['max_rows'], 'Hybrid row budget exceeded')
        tables[table].append(row)

    def size(dispatch):
        return dict(hidden_bytes=2*m['hidden_size'],
                    token_operand_bytes=token_bytes(m['hidden_size'], m['top_k'], dispatch))

    branch = ('domain_reduction' if x['allow_multiple_reduction'] else
              'expert_contributions' if x['expanded_layout'] else 'gpu_contributions')
    for ci, call in enumerate(config['calls']):
        groups = defaultdict(list)
        for src, tokens in enumerate(call['tokens']):
            rail = src % scaleup
            for ti, choices in enumerate(tokens):
                channel = ti % channels
                by_domain = defaultdict(list)
                for k, expert in enumerate(choices):
                    by_domain[expert//(epr*scaleup)].append(k)
                for domain, positions in sorted(by_domain.items()):
                    ingress = domain*scaleup+rail
                    identity = f'h{ci}.s{src}.t{ti}.d{domain}'
                    cid = f'h{ci}.s{src}.d{domain}.c{channel}'
                    group = groups[src, domain, channel]
                    add('dispatch', dict(id=identity, call=ci, source=src, token=ti,
                        target_domain=domain, ingress=ingress, rail=rail, channel=channel,
                        domain_channel_slot=len(group), channel_id=cid, topk_positions=positions,
                        path='local_ingress_copy' if src == ingress else 'rail_put', **size(True)))
                    group.append(identity)
                    by_gpu = defaultdict(list)
                    for k in positions:
                        by_gpu[choices[k]//epr].append(k)
                    for dst, selected in sorted(by_gpu.items()):
                        add('forwards', dict(id=f'{identity}.gpu{dst}', dispatch=identity, source=ingress,
                            target=dst, topk_positions=selected, gpu_slot=None,
                            path='ingress_self_copy' if ingress == dst else 'intra_domain_forward', **size(True)))
                    partitions = ([positions] if branch == 'domain_reduction' else
                                  [[k] for k in positions] if branch == 'expert_contributions' else list(by_gpu.values()))
                    for index, selected in enumerate(partitions):
                        add('combine', dict(id=f'{identity}.return{index}', dispatch=identity, branch=branch,
                            source=ingress, target=src, rail=rail, topk_positions=selected,
                            contributor_ranks=sorted({choices[k]//epr for k in selected}),
                            path='local_scaleout_bypass' if ingress == src else 'rail_put',
                            schedule=None, **size(False)))
            for domain in range(domains):
                ingress = domain*scaleup+rail
                rank_values = [-1-sum(any(e//epr == rank for e in row) for row in tokens)
                               for rank in range(domain*scaleup, (domain+1)*scaleup)]
                expert_values = [-1-sum(expert in row for row in tokens)
                                 for expert in range(domain*scaleup*epr, (domain+1)*scaleup*epr)]
                for kind, values in (('rank', rank_values), ('expert', expert_values)):
                    add('count_blocks', dict(id=f'h{ci}.s{src}.d{domain}.{kind}_count', call=ci,
                        source=src, target=ingress, rail=rail, kind=kind, values=values,
                        operand_bytes=4*len(values), path='gin_self' if src == ingress else 'rail_put',
                        verb='gin_put', encoding='negative_count_minus_one_int32'))
                for channel in range(channels):
                    transfers = groups[src, domain, channel]
                    # Exact multiples still require a separate finish update.
                    pubs = [dict(tail=n, finished=False) for n in range(6, len(transfers)+1, 6)]
                    pubs.append(dict(tail=len(transfers), finished=True))
                    for _ in pubs:
                        row_count += 1
                        require(row_count <= config['limits']['max_rows'], 'Hybrid row budget exceeded')
                    add('channels', dict(id=f'h{ci}.s{src}.d{domain}.c{channel}', call=ci,
                        source=src, source_domain=src//scaleup, target=ingress, target_domain=domain,
                        rail=rail, channel=channel, transfers=list(transfers), publications=pubs,
                        tail_verb='gin_red_add_rel', tail_path='gin_self' if src == ingress else 'rail_put'))
    plan = dict(schema_version='moe-hybrid-logical-plan/1', library_revision=REVISION,
        scope=dict(SCOPE), deployment=p, ranks=ranks, domains=domains, ranks_per_domain=scaleup,
        channels_per_rank=channels, progress_interval=6, combine_branch=branch,
        row_count=row_count, **tables)
    require(len(encoded(plan)) <= config['limits']['max_output_bytes'], 'Hybrid plan byte budget exceeded')
    return plan


def manifest(raw, plan_raw):
    return dict(schema_version='moe-hybrid-logical-manifest/1', library_revision=REVISION,
        scope=dict(SCOPE), sha256={k: hashlib.sha256(v).hexdigest() for k, v in
                                 (('input.json', raw), ('plan.json', plan_raw))})


def execute(config_path, output):
    output = Path(output)
    require(not output.exists(), 'Hybrid output already exists')
    raw, config = read(Path(config_path), MAX_INPUT)
    plan = compile_plan(config)
    require(len(raw) <= config['limits']['max_input_bytes'], 'Hybrid input byte budget exceeded')
    plan_raw = encoded(plan)
    record = manifest(raw, plan_raw)
    require(len(raw)+len(plan_raw)+len(encoded(record)) <= config['limits']['max_output_bytes'], 'Hybrid total output byte budget exceeded')
    output.mkdir(parents=True)
    for name, data in (('input.json', raw), ('plan.json', plan_raw), ('manifest.json', encoded(record))):
        (output/name).write_bytes(data)
    return record


def load_plan(directory):
    directory = Path(directory)
    raw, config = read(directory/'input.json', MAX_INPUT)
    saved_raw, saved = read(directory/'plan.json', MAX_OUTPUT)
    record_raw, record = read(directory/'manifest.json', MAX_INPUT)
    require(record_raw == encoded(manifest(raw, saved_raw)), 'Hybrid manifest/scope/hash mismatch')
    require(saved_raw == encoded(compile_plan(config)), 'Hybrid plan differs from saved input semantics')
    require(len(raw) <= config['limits']['max_input_bytes'], 'Hybrid saved input exceeds budget')
    require(len(raw)+len(saved_raw)+len(record_raw) <= config['limits']['max_output_bytes'], 'Hybrid saved output exceeds budget')
    return saved, config
