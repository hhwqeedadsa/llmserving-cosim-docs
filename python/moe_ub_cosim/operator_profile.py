"""Exact operator measurements; unmeasured phases remain explicit references."""
from collections import Counter
from copy import deepcopy
import math
import statistics

from .plan import integer, require


def validate_profile(profile, config):
    require(isinstance(profile, dict) and set(profile) == {
        'schema_version', 'basis', 'device', 'software', 'measurement', 'domain', 'rows'},
        'Invalid operator profile fields')
    require(profile['schema_version'] == 'moe-ub-operator-profile/1' and
            profile['basis'] in ('measured_operator', 'synthetic_test'), 'Unsupported operator profile')
    for key in ('device', 'software', 'measurement'):
        require(isinstance(profile[key], dict) and profile[key], 'Missing profile metadata: '+key)
    method = profile['measurement']
    require(method.get('statistic') == 'ceil_median_ns' and method.get('out_of_domain') == 'reject' and
            method.get('implementation') == 'torch_eager_serial_gated_ffn_and_sdpa_v1',
            'Unsupported measurement or extrapolation policy')
    if profile['basis'] == 'measured_operator':
        require(method.get('timer') == 'cuda_event' and method.get('weights') == 'seeded_random_operator_tensors' and
                isinstance(method.get('script_sha256'), str) and len(method['script_sha256']) == 64,
                'Missing measured operator evidence')
        require(all(profile['device'].get(k) for k in ('name', 'compute_capability', 'driver_version')) and
                all(profile['software'].get(k) for k in ('torch', 'cuda', 'python')), 'Missing hardware/software evidence')
    domain = profile['domain']
    require(isinstance(domain, dict) and set(domain) == {
        'hidden_dim', 'intermediate_dim', 'dtype', 'num_heads', 'num_kv_heads', 'head_dim', 'tp'},
        'Invalid operator domain')
    for key in ('hidden_dim', 'intermediate_dim', 'num_heads', 'num_kv_heads', 'head_dim'):
        integer(domain[key], key, 1, 2**31-1)
    integer(domain['tp'], 'profile tp', 1, 1)
    require(domain['num_heads'] % domain['num_kv_heads'] == 0 and
            domain['hidden_dim'] == domain['num_heads']*domain['head_dim'], 'Invalid GQA operator dimensions')
    require(domain['hidden_dim'] == config['hidden_dim'] and domain['dtype'] in ('float16', 'bfloat16') and
            domain['dtype'] == config['compute_dtypes']['compute'] == config['compute_dtypes']['kv'],
            'Profile dimensions/dtype differ from execution')
    rows = profile['rows']
    require(isinstance(rows, list) and 1 <= len(rows) <= 16384, 'Profile row budget exceeded')
    seen = set()
    for row in rows:
        require(isinstance(row, dict) and set(row) == {'operator', 'shape', 'duration_ns', 'samples_ns'} and
                row['operator'] in ('attention_decode', 'expert_ffn'), 'Invalid profile row')
        shape = row['shape']
        require(isinstance(shape, list) and 1 <= len(shape) <= 4096, 'Invalid profile shape')
        for value in shape:
            integer(value, 'context/expert input count', 1 if row['operator']=='attention_decode' else 0, 2**31-1)
        require(any(shape), 'Zero work does not require a profile row')
        key = row['operator'], tuple(shape)
        require(key not in seen, 'Duplicate operator profile shape'); seen.add(key)
        samples = row['samples_ns']
        require(isinstance(samples, list) and 3 <= len(samples) <= 10000, 'Missing repeat samples')
        for sample in samples: integer(sample, 'profile sample ns', 1, 10**12)
        require(row['duration_ns'] == math.ceil(statistics.median(samples)), 'Profile statistic differs from samples')
        integer(row['duration_ns'], 'operator duration', 1, 10**12)
    return profile


def evaluate(config):
    profile = validate_profile(config['operator_profile'], config)
    table = {(r['operator'], tuple(r['shape'])):(i, r) for i,r in enumerate(profile['rows'])}
    expert_counts = Counter(e for t in config['tokens'] for e in t['expert_ids'])
    ranks = []
    for rank in config['rank_ids']:
        contexts = [t['token_index']+1 for t in config['tokens'] if t['src_rank']==rank]
        counts = [expert_counts[e] for e,owner in enumerate(config['expert_placement']) if owner==rank]
        selected = {}
        for op,shape in [('attention_decode', contexts), ('expert_ffn', counts)]:
            if not any(shape):
                selected[op] = dict(shape=shape, row_index=None, duration_ns=0)
                continue
            require((op, tuple(shape)) in table, f'Operator profile outside measured domain: {op} {shape}')
            index,row = table[op, tuple(shape)]
            selected[op] = dict(shape=shape, row_index=index, duration_ns=row['duration_ns'])
        ranks.append(dict(rank_id=rank, operators=selected))
    return dict(basis=profile['basis'], policy='exact_shape_no_extrapolation', extrapolation_count=0,
        domain=deepcopy(profile['domain']), ranks=ranks,
        phase_policy='expert_replaced_attention_added_to_explicit_prepare_residual',
        reference_phases=['prepare_residual', 'expert_local_merge', 'combine_unpack_merge', 'local_prepare'])


def durations(config):
    if 'operator_profile' not in config: return {}
    return {r['rank_id']:r['operators'] for r in evaluate(config)['ranks']}
