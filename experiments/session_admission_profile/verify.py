"""Independent prefix accounting and original CPU roots; never calls validator."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from experiments.session_admission_profile.study import CASES, load, save, sha, digest


def need(value, message):
    if not value:
        raise ValueError(message)


def scan(run, case, profile=None, summary=None):
    root = run/'observed'/case
    profile = load(run/'profiles'/(case+'.json')) if profile is None else profile
    summary = load(run/'analysis/summary.json')['cases'][case] if summary is None else summary
    raw = [json.loads(line) for line in (root/'control.jsonl').read_text().splitlines()]
    need(len(raw) % 2 == 0, 'unpaired transcript')
    initial = load(root/'initial.json')
    all_nodes, prefixes = [], []
    visits = edges = remote_visits = pair_visits = empty = full_bytes = delta_bytes = 0
    for index in range(0, len(raw), 2):
        incoming, outgoing = raw[index:index+2]
        need(incoming['direction'] == 'native' and outgoing['direction'] == 'frontend', 'transcript directions changed')
        command = outgoing['message']
        need(command['decision_id'] == index//2 == incoming['message']['decision_id'], 'decision sequence changed')
        need(command['now_ns'] == incoming['message']['now_ns'], 'commit time changed')
        all_nodes.extend(command['submissions'])
        need(len({n['id'] for n in all_nodes}) == len(all_nodes), 'duplicate work id')
        candidate = dict(initial, operations=all_nodes)
        n, r = len(all_nodes), len([x for x in all_nodes if x['kind'] == 'remote'])
        visits += n
        edges += sum(len(x['after']) for x in all_nodes)
        remote_visits += r
        # Enumerate the index pairs rather than reusing the analyzer formula.
        pair_visits += sum(1 for j in range(r) for _ in range(j))
        empty += int(not command['submissions'])
        full_bytes += len(json.dumps(candidate, separators=(',', ':')).encode())
        delta_bytes += len(json.dumps(command['submissions'], separators=(',', ':')).encode())
        prefixes.append(dict(decision_id=index//2, nodes=n, added=len(command['submissions']), candidate_sha256=digest(candidate)))
    need(prefixes == [{k: p[k] for k in ('decision_id', 'nodes', 'added', 'candidate_sha256')} for p in profile['prefixes']],
         'measured prefixes differ from real transcript')
    need(digest(dict(initial, operations=all_nodes)) == profile['final_candidate_sha256'], 'final candidate changed')
    expected = dict(commands=len(prefixes), final_nodes=len(all_nodes), candidate_nodes=visits, new_nodes=len(all_nodes),
        candidate_dependencies=edges, candidate_remote_nodes=remote_visits, per_history_loop_visits=pair_visits,
        full_candidate_json_bytes=full_bytes, appended_operations_json_bytes=delta_bytes, empty_commands=empty,
        prefix_node_revisit_factor=visits/len(all_nodes), final_candidate_sha256=profile['final_candidate_sha256'],
        static_configuration_unchanged=True)
    need(summary['counts'] == expected, 'prefix count summary changed')
    for path in ('scaleout_transactions.py', 'scaleout_streams.py'):
        fn, = [r for r in profile['functions'] if r['path'] == 'python/moe_ub_cosim/'+path and r['function'] == 'validate']
        need(fn['calls'] == len(prefixes) and fn['primitive_calls'] == fn['calls'], 'profile call count differs')
    need(all(r['copy_cpu_ns'] >= 0 and r['copy_wall_ns'] >= 0 and r['profiled_validation_cpu_ns'] >= 0 and
             r['profiled_validation_wall_ns'] >= 0 for r in profile['prefixes']), 'negative measurement')
    roots = {}
    for writer in ('worker', 'native'):
        rows = [json.loads(line) for line in (root/(writer+'_host.jsonl')).read_text().splitlines()]
        need(rows[0]['type'] == 'header' and rows[-1]['type'] == 'footer' and rows[-1]['complete'], 'incomplete source clock')
        name = 'native_total' if writer == 'native' else case.split('-')[1]+'_worker'
        start, = [r for r in rows if r['type'] == 'begin' and r['name'] == name]
        end, = [r for r in rows if r['type'] == 'end' and r['id'] == start['id']]
        roots[writer] = end['cpu_ns']-start['cpu_ns']
    need(sum(roots.values()) == summary['original_total_cpu_ns'], 'old CPU sum differs')
    need(summary['original_candidate_cpu_ns']/sum(roots.values()) == summary['original_candidate_cpu_fraction'], 'old CPU fraction differs')
    need(summary['original_candidate_cpu_ns'] < sum(roots.values()), 'invalid optimistic CPU bound')
    return dict(commands=len(prefixes), nodes=len(all_nodes), cumulative_node_visits=visits,
        per_history_loop_pairs_enumerated=pair_visits, original_cpu_ns=sum(roots.values()), passed=True)


def source_checks(run):
    need(all(sha(run/name) == expected for name, expected in load(run/'inputs.json')['files'].items()), 'source hash changed')


def verify(run):
    ledger = load(run/'execution.json')
    need(ledger['closed'] and ledger['state'] == 'completed' and len(ledger['records']) == 5, 'measurement ledger incomplete')
    need(all(r['exit_code'] == 0 for r in ledger['records']), 'measurement failure')
    source_checks(run)
    repo = Path(__file__).resolve().parents[2]
    need(all(sha(repo/name) == expected for name, expected in load(run/'inputs.json')['source_files'].items()), 'platform source changed')
    checks = {case: scan(run, case) for case in CASES}
    rejected = []

    def reject(label, function):
        try:
            function()
        except (ValueError, AssertionError, KeyError):
            rejected.append(label)
        else:
            raise ValueError('corruption accepted: '+label)

    case = CASES[0]
    original = load(run/'profiles'/(case+'.json'))
    bad = deepcopy(original); bad['prefixes'][-1]['nodes'] += 1
    reject('prefix node count', lambda: scan(run, case, bad))
    bad = deepcopy(original)
    next(r for r in bad['functions'] if r['path'] == 'python/moe_ub_cosim/scaleout_transactions.py' and r['function'] == 'validate')['calls'] += 1
    reject('profile call count', lambda: scan(run, case, bad))
    bad_summary = load(run/'analysis/summary.json')['cases'][case]
    bad_summary['original_total_cpu_ns'] += 1
    reject('old CPU arithmetic', lambda: scan(run, case, summary=bad_summary))
    # Check a deliberately wrong expected identity without mutating source data.
    observed, expected = next(iter(load(run/'inputs.json')['files'].items()))
    reject('source hash', lambda: need(sha(run/observed) == '0'*64, 'source hash changed'))
    return dict(passed=True, cases=checks, rejected_corruption_classes=rejected,
        independent_prefix_scan=True, independent_original_CPU_roots=True, new_profiles=0, new_networks=0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    save(args.output, verify(args.run.resolve()))
