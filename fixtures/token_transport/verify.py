"""Independent raw-choice accounting and actual callback audit; no network execution."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_service import make_wave
from moe_ub_cosim.scaleout_verify import node_events, event_time


def load(path):
    return json.loads(path.read_bytes())


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def originals(config, base):
    reference = config['route_replay']['manifest']
    path = base/reference['path']
    require(digest(path) == reference['sha256'], 'manifest identity changed')
    manifest = load(path)
    files = {}
    identities = []
    for item in manifest['files']:
        path = base/item['file']['path']
        sha = digest(path)
        require(sha == item['file']['sha256'] and sha not in identities, 'original changed or cloned')
        identities.append(sha)
        files[item['file_id']] = load(path)
    return files, manifest


def independent_wave(wave, config, files):
    """Derive units, all expert work and cost directly from source JSON choices."""
    group = wave['rank_ids']
    step = wave['decode_step']
    spec = config.get('token_transport')
    hidden = config['model']['hidden_size']*config['model']['activation_bytes']
    topk = config['model']['top_k']
    sizes = dict(dispatch=hidden, combine=hidden)
    if spec:
        # Independent arithmetic from the fixed source's two alignment constants.
        metadata = 8*topk+4*(1+topk)
        sizes['dispatch'] = 64*((hidden+32*((metadata+31)//32)+63)//64)
        if spec['combine'] == 'rank_reduced/1':
            sizes['combine'] = 64*((hidden+32*((8*topk+31)//32)+63)//64)
    expected = {}
    total_experts = remote_experts = remote_units = reduction_ns = 0
    placement = config['route_replay'].get('expert_to_group_rank')
    for lid, source_lid in enumerate(config['route_replay']['layer_ids']):
        work, counts, extras, contributions, units = (Counter() for _ in range(5))
        source_choices = {}
        for request in config['route_replay']['requests']:
            src = request['rank_id']
            if src not in group:
                continue
            entry = request['first_decode_entry']+step
            choices = files[request['file_id']][entry][str(source_lid)][0]
            require(len(set(choices)) == len(choices) == topk, 'bad original expert choices')
            source_choices[src, request['slot']] = choices
            destinations = Counter()
            for expert in choices:
                owner = placement[lid][expert] if placement else expert//(config['model']['experts']//len(group))
                dst = group[owner]
                work[dst] += 1
                counts[expert] += 1
                destinations[dst] += 1
                contributions[src, dst] += 1
            for dst, number in destinations.items():
                units[src, dst] += 1
                extras[dst] += number-1
        route = next(row for row in wave['routes'] if row['layer'] == lid)
        require({(t['rank'], t['slot']): t['experts'] for t in route['tokens']} == source_choices,
                'wave choices differ from original JSON')
        require({int(e): n for e, n in route['expert_counts'].items()} == dict(counts), 'expert count changed')
        total_experts += sum(work.values())
        for (src, dst), count in contributions.items():
            if src == dst:
                continue
            n = units[src, dst]
            remote_experts += count
            remote_units += n
            expected[lid, 'dispatch', src, dst] = (count, n if spec else None,
                                                   (n if spec else count)*sizes['dispatch'])
            reduced = spec and spec['combine'] == 'rank_reduced/1'
            expected[lid, 'combine', dst, src] = (count, (n if reduced else count) if spec else None,
                                                  (n if reduced else count)*sizes['combine'])
        for rank in group:
            tasks = {n['name']: n for n in wave['rank_nodes'].get(rank, wave['rank_nodes'].get(str(rank)))}
            expert = tasks[f'L{lid}.experts']
            require(expert['duration_ns'] == work[rank]*config['compute']['expert_ns_per_contribution'],
                    'expert compute reduced with transport units')
            reduction = tasks.get(f'L{lid}.combine_reduce')
            reduced = spec and spec['combine'] == 'rank_reduced/1'
            if reduced:
                cost = extras[rank]*spec['local_reduce_ns_per_extra']
                require(reduction is not None and reduction['duration_ns'] == cost and
                        reduction['deps'] == [expert['id']], 'incorrect reduction work/dependency')
                reduction_ns += cost
            else:
                require(reduction is None, 'undeclared reduction node')
            producer = reduction if reduced else expert
            for task in tasks.values():
                if task['kind'] == 'SEND' and task['message']['layer'] == lid and task['message']['phase'] == 'combine':
                    require(task['deps'] == [producer['id']], 'send omitted producer')
            if reduced:
                require(reduction['id'] in tasks[f'L{lid}.combine.flush']['deps'], 'gate omitted reduction')
    actual = {(m['layer'], m['phase'], m['src'], m['dst']):
              (m['contributions'], m.get('transport_tokens'), m['bytes']) for m in wave['messages']}
    require(actual == expected and len(actual) == len(wave['messages']), 'independent payload accounting differs')
    return dict(messages=len(actual), layout_payload_bytes=sum(v[2] for v in expected.values()),
                expert_contributions=total_experts, remote_expert_contributions=remote_experts,
                remote_token_rank_units=remote_units, local_reduce_declared_ns=reduction_ns)


def totals(rows):
    return {key: sum(row[key] for row in rows) for key in rows[0]}


def offline(repo, prior_runs):
    sources = load(repo/'docs/review_artifacts/invocation_semantics_20261023/sources.json')['sources']
    checked = []
    for row in sources:
        data = (repo/row['cache_path']).read_bytes()
        require(len(data) == row['bytes'] and hashlib.sha256(data).hexdigest() == row['sha256'] and
                hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest() == row['git_blob_sha'],
                'fixed-source identity mismatch')
        checked.append(row)
    compatibility = []
    for native in prior_runs:
        c = load(native/'input.json')
        replay = Replay(c, native)
        frontend = load(native/'frontend.json')
        for history in frontend['history']:
            old = load(native/'graphs'/history['graph_id']/'wave.json')
            expected = make_wave(c, old['rank_ids'], old['group_index'], old['decode_step'], 1, replay)
            require(json.loads(json.dumps(expected)) == old, 'default graph changed')
        compatibility.append(dict(run=str(native), graphs=len(frontend['history']), identical=True))
    base = prior_runs[0]
    original = load(base/'input.json')
    files, manifest = originals(original, base)
    require(len(files) == len(original['route_replay']['requests']) == 15, 'expected fifteen originals')
    modes = {}
    for mode in ('default', 'expanded_per_expert/1', 'rank_reduced/1'):
        c = deepcopy(original)
        if mode != 'default':
            c['token_transport'] = dict(mode='deepep_direct_bf16/1', combine=mode,
                                        local_reduce_ns_per_extra=100 if mode == 'rank_reduced/1' else 0)
        replay = Replay(c, base)
        waves = [make_wave(c, g, i, step, 1, replay) for i, g in enumerate(c['parallel']['groups'])
                 for step in range(c['workload']['decode_steps'])]
        modes[mode] = totals([independent_wave(w, c, files) for w in waves])
    return dict(passed=True, network_executed=False, source_identities=checked,
                default_compatibility=compatibility, real_originals=len(files),
                source_kind=manifest['source']['kind'], dataset_revision=manifest['source']['dataset_revision'],
                offline_real_route_modes=modes, new_algorithm_gain_measured=False)


def actual(native):
    c = load(native/'input.json')
    result = load(native/'result.json')
    public = load(native/'verification.json')
    frontend = load(native/'frontend.json')
    require(result['gpu_count'] == 128 and result['passed'] and result['drained'] and public['passed'],
            'functional 128-card run failed')
    require([len(g) for g in c['parallel']['groups']] == [64, 64], 'wrong EP deployment')
    files, manifest = originals(c, native)
    require(len(files) == 4 and manifest['source']['kind'] == 'synthetic_test', 'functional originals changed')
    events = node_events(native)
    gates = {(g['graph_id'], g['id']): g for g in result['service']['phase_completion']['gates']}
    messages = {m['id']: m for m in result['service_transport']['messages']}
    rows, timings, gate_count, gate_members = [], [], 0, 0
    for history in frontend['history']:
        w = load(native/'graphs'/history['graph_id']/'wave.json')
        rows.append(independent_wave(w, c, files))
        for rank in w['rank_ids']:
            for node in w['rank_nodes'][str(rank)]:
                if node['name'].endswith('combine_reduce'):
                    issue = event_time(events, w['graph_id'], rank, node['id'], 'issue')
                    done = event_time(events, w['graph_id'], rank, node['id'], 'callback')
                    require(done-issue == node['duration_ns'], 'actual reduction duration differs')
                    parent = event_time(events, w['graph_id'], rank, node['deps'][0], 'callback')
                    require(parent <= issue, 'reduction started before expert completion')
                    dependents = [n for n in w['rank_nodes'][str(rank)] if node['id'] in n['deps']]
                    require(all(event_time(events, w['graph_id'], rank, n['id'], 'issue') >= done
                                for n in dependents), 'consumer crossed actual reduction completion')
                    if node['duration_ns']:
                        timings.append(dict(graph_id=w['graph_id'], rank=rank, node=node['id'],
                                            duration_ns=node['duration_ns'], issue_ns=issue, completed_ns=done))
        for gate in w['phase_completion']['gates']:
            row = gates[w['graph_id'], gate['id']]
            arrived = [event_time(events, w['graph_id'], m['rank'], m['node'], 'issue') for m in gate['members']]
            callbacks = [event_time(events, w['graph_id'], m['rank'], m['node'], 'callback') for m in gate['members']]
            require([m['rank'] for m in row['members']] == w['rank_ids'] and
                    [m['arrived_ns'] for m in row['members']] == arrived and
                    set(callbacks) == {max(arrived)} and row['released_ns'] == max(arrived), 'invalid actual gate')
            gate_count += 1
            gate_members += len(arrived)
        for m in w['messages']:
            submitted = messages[m['id']]
            require(all(submitted[k] == m[k] for k in m), 'actual submitted message differs')
            require(submitted['receive_ready_ns'] <= submitted['ack_ns'], 'ACK preceded receive acceptance')
    summed = totals(rows)
    require(summed['layout_payload_bytes'] == public['remote_payload_bytes'] == public['source_payload_bytes'] ==
            public['receiver_accepted_bytes'], 'source/receiver/layout conservation failed')
    require(summed['messages'] == len(messages) == public['messages'], 'message conservation failed')
    return dict(passed=True, gpu_endpoints=128, ep_sizes=[64, 64], synthetic_originals=4,
                combine=c['token_transport']['combine'], **summed,
                verified_nodes=public['verified_nodes'], packet_events_checked=public['packet_events_checked'],
                gates_checked=gate_count, gate_members_checked=gate_members,
                positive_reduction_callbacks=timings, application_finished_ns=public['application_finished_ns'],
                network_drained_ns=public['network_drained_ns'], hardware_calibrated=False,
                complete_DeepEP=False, new_algorithm_gain_measured=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--prior-run', type=Path, action='append', default=[])
    parser.add_argument('--native', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(bool(args.native) != bool(args.prior_run), 'choose native or offline prior runs')
    summary = actual(args.native.resolve()) if args.native else offline(args.repo.resolve(), [p.resolve() for p in args.prior_run])
    with args.output.open('x') as stream:
        json.dump(summary, stream, indent=2)
        stream.write('\n')
    print(json.dumps({k: v for k, v in summary.items() if k not in
                      ('source_identities', 'positive_reduction_callbacks', 'default_compatibility')}))
