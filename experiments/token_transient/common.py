"""Local study I/O and independent source-JSON accounting; no graph generation."""
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
GROUPS = [list(range(0, 128, 2)), list(range(1, 128, 2))]


def load(path):
    if path.stat().st_size > 64*2**20:
        raise ValueError('JSON exceeds 64MiB: '+str(path))
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def originals(config, base):
    ref = config['route_replay']['manifest']
    path = base/ref['path']
    need(sha(path) == ref['sha256'], 'changed source manifest')
    manifest = load(path)
    files, hashes = {}, set()
    for row in manifest['files']:
        path = base/row['file']['path']
        digest = sha(path)
        need(digest == row['file']['sha256'] and digest not in hashes, 'changed/cloned source')
        hashes.add(digest)
        files[row['file_id']] = load(path)
    need(len(files) == 15 and manifest['source']['kind'] == 'user_provided_model_routes', 'wrong real originals')
    return files


def account(config, wave, files):
    need(config['model']['experts']==256 and config['model']['top_k']==8 and
         config['model']['hidden_size']==7168 and config['model']['activation_bytes']==2 and
         len(wave['rank_ids'])==64, 'wrong registered model or EP size')
    need(config['token_transport'] == dict(mode='deepep_direct_bf16/1', combine='rank_reduced/1',
         local_reduce_ns_per_extra=100), 'changed data representation')
    group, step = wave['rank_ids'], wave['decode_step']
    hidden = config['model']['hidden_size']*2
    k = config['model']['top_k']
    layout = lambda meta: 64*((hidden+32*((meta+31)//32)+63)//64)
    dispatch, combine = layout(8*k+4*(1+k)), layout(8*k)
    expected, all_work, extra_work = {}, 0, 0
    remote_experts = remote_units = 0
    for lid, original_lid in enumerate(config['route_replay']['layer_ids']):
        counts, units, work, extra, expert_counts = (Counter() for _ in range(5))
        chosen = {}
        for request in config['route_replay']['requests']:
            src = request['rank_id']
            if src not in group:
                continue
            raw = files[request['file_id']]
            experts = raw[request['first_decode_entry']+step][str(original_lid)][0]
            chosen[src, request['slot']] = experts
            need(len(set(experts)) == len(experts) == k, 'wrong original Top-K')
            owners = Counter(group[e//4] for e in experts)
            for expert in experts:
                expert_counts[expert] += 1
            for dst, n in owners.items():
                counts[src, dst] += n
                units[src, dst] += 1
                work[dst] += n
                extra[dst] += n-1
        route = wave['routes'][lid]
        need({(t['rank'], t['slot']):t['experts'] for t in route['tokens']} == chosen and
             {int(e):n for e,n in route['expert_counts'].items()} == dict(expert_counts), 'original choices/work differs')
        for (src, dst), n in counts.items():
            if src != dst:
                u = units[src, dst]
                expected[lid, 'dispatch', src, dst] = n, u, u*dispatch
                expected[lid, 'combine', dst, src] = n, u, u*combine
                remote_experts += n
                remote_units += u
        for rank in group:
            tasks = {n['name']:n for n in wave['rank_nodes'].get(rank, wave['rank_nodes'].get(str(rank)))}
            expert, reduction = tasks[f'L{lid}.experts'], tasks[f'L{lid}.combine_reduce']
            need(expert['duration_ns'] == work[rank]*config['compute']['expert_ns_per_contribution'] and
                 reduction['deps'] == [expert['id']] and reduction['duration_ns'] == extra[rank]*100,
                 'expert/reduction work or dependency differs')
            extra_work += extra[rank]*100
        all_work += sum(work.values())
    actual = {(m['layer'],m['phase'],m['src'],m['dst']):
              (m['contributions'],m['transport_tokens'],m['bytes']) for m in wave['messages']}
    need(actual == expected and len(actual) == len(wave['messages']), 'independent layout matrix differs')
    return dict(messages=len(expected), layout_bytes=sum(v[2] for v in expected.values()),
                expert_contributions=all_work, remote_expert_contributions=remote_experts,
                remote_token_rank_units=remote_units, declared_reduce_work_ns=extra_work)
