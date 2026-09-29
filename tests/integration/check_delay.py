"""Verify spec 12.3 in original ASTRA, first given events, then real UB events."""
import argparse
from copy import deepcopy
import csv
import datetime
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_execution(plan, report, run, check_rank2_ahead=True, graph_id=None):
    """Use transfer event evidence to derive local waits, independently of deps."""
    nodes = {(g['rank_id'], n['node_id']): n for g in plan['rank_graphs'] for n in g['nodes']}
    actual = {(n['rank_id'], n['node_id']): n for n in report['node_execution']}
    transfers = {t['transfer_id']: t for t in report['transfers']}
    assert set(actual) == set(nodes) and len(actual) == 20 + 2 * len(plan['transfers'])
    assert set(transfers) == {t['transfer_id'] for t in plan['transfers']}
    assert report['loaded_ranks_before_activation'] == 4
    assert report['activation_order'] == [dict(rank_id=r, time_ns=plan['ready_ns']) for r in plan['rank_ids']]
    assert len(report['graph_completions']) == 4
    assert sum(t['committed_bytes'] for t in transfers.values()) == sum(t['payload_bytes'] for t in plan['transfers'])
    for t in plan['transfers']:
        got = transfers[t['transfer_id']]
        assert got['send_callbacks'] == got['receive_callbacks'] == 1 and not got['context_active']
        assert got['send_done_ns'] == got['transaction_done_ns']
        assert got['receive_done_ns'] == max(got['receive_start_ns'], got['data_ready_ns'])
        for side, rank, prefix in [('send', t['src_rank'], 'send'), ('recv', t['dst_rank'], 'receive')]:
            node = actual[rank, t[side + '_node_id']]
            assert node['start_ns'] == got[prefix + '_start_ns']
            assert node['end_ns'] == got[prefix + '_done_ns']
    phases = []
    for graph in plan['rank_graphs']:
        rank, ids = graph['rank_id'], graph['phase_nodes']
        phase = {k: actual[rank, node_id] for k, node_id in ids.items()}
        waits = {}
        for kind, initial in [('dispatch', phase['prepare']['end_ns']),
                              ('combine', phase['local_merge']['end_ns'])]:
            conditions = [dict(condition='local_input' if kind == 'dispatch' else 'local_result', time_ns=initial)]
            for t in plan['transfers']:
                if t['kind'] != kind:
                    continue
                got = transfers[t['transfer_id']]
                if t['src_rank'] == rank:
                    conditions.append(dict(condition='send_done', transfer_id=t['transfer_id'], time_ns=got['send_done_ns']))
                if t['dst_rank'] == rank:
                    conditions.append(dict(condition='receive_done', transfer_id=t['transfer_id'], time_ns=got['receive_done_ns']))
            waits[kind] = conditions
        for kind, target in [('dispatch', 'expert'), ('combine', 'finish')]:
            assert phase[target]['start_ns'] == max(c['time_ns'] for c in waits[kind]), (rank, kind, waits, phase)
        assert phase['expert']['end_ns'] == phase['local_merge']['start_ns']
        assert phase['finish']['end_ns'] == phase['next_layer']['start_ns']
        for t in plan['transfers']:
            if t['kind'] == 'combine' and t['src_rank'] == rank:
                assert transfers[t['transfer_id']]['send_start_ns'] >= phase['local_merge']['end_ns']
        assert phase['next_layer']['end_ns'] == next(c['time_ns'] for c in report['graph_completions'] if c['rank_id'] == rank)
        phases.append(dict(rank_id=rank, expert_start_ns=phase['expert']['start_ns'],
                           expert_end_ns=phase['expert']['end_ns'], local_merge_end_ns=phase['local_merge']['end_ns'],
                           combine_finish_ns=phase['finish']['end_ns'], graph_done_ns=phase['next_layer']['end_ns'],
                           required_conditions=waits))
    for key, node in nodes.items():
        if node['kind'] == 'COMP':
            assert actual[key]['end_ns'] - actual[key]['start_ns'] == node['duration_ns']
        assert all(actual[key]['start_ns'] >= actual[key[0], d]['end_ns'] for d in node['deps'])
    # Read the original executor log as well, not just the driver's JSON export.
    raw = (run / 'log/log.log').read_text()
    if graph_id is not None:
        raw = '\n'.join(line for line in raw.splitlines() if line.endswith(', graph_id=' + graph_id))
    events = re.findall(r'(issue|callback),sys->id=(\d+), tick=(\d+), node->id=(\d+),', raw)
    assert len(events) == 2 * len(nodes) and len({(e, r, n) for e, r, _, n in events}) == 2 * len(nodes)
    active, peak, concurrent_receive = [0] * (max(plan['rank_ids']) + 1), [0] * (max(plan['rank_ids']) + 1), 0
    for event, rank, time, node_id in events:
        rank, time, node_id = int(rank), int(time), int(node_id)
        key = rank, node_id
        assert actual[key]['start_ns' if event == 'issue' else 'end_ns'] == time
        if nodes[key]['kind'] == 'RECV' and event == 'issue' and active[rank]:
            concurrent_receive += 1
        if nodes[key]['kind'] == 'SEND':
            active[rank] += 1 if event == 'issue' else -1
            assert 0 <= active[rank] <= plan['send_slots_per_rank']
            peak[rank] = max(peak[rank], active[rank])
    assert not any(active) and all(0 < peak[r] <= plan['send_slots_per_rank'] for r in plan['rank_ids']) and concurrent_receive > 0
    assert not (run / 'log/err.log').read_text()
    latest_dispatch = max(p['expert_start_ns'] for p in phases)
    if check_rank2_ahead:
        assert phases[2]['expert_start_ns'] < latest_dispatch, 'rank 2 must not wait for a global Dispatch barrier'
    return dict(ranks=phases, global_dispatch_done_ns=latest_dispatch, peak_sends=peak)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    build, output = args.build_dir.resolve(), args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    test_binary = build / 'output/cosim/ns3.44-cosim-layer-delay-test'
    real_binary = build / 'output/cosim/ns3.44-cosim-layer'
    system = root / 'fixtures/m1_compute/system.json'
    commands = []

    def invoke(label, argv):
        started = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result = subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False)
        (output / (label + '.stdout.log')).write_text(result.stdout)
        (output / (label + '.stderr.log')).write_text(result.stderr)
        commands.append(dict(label=label, argv=argv, cwd=str(root), started_utc=started,
            finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), exit_code=result.returncode))
        (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        return result

    fixture = root / 'fixtures/m2_single_layer/layer.json'
    shutil.copyfile(fixture, output / 'input.json')
    generated = output / 'generated'
    result = invoke('generate', [sys.executable, '-m', 'moe_ub_cosim.generate', '--input', str(output / 'input.json'),
                                 '--output-dir', str(generated)])
    assert result.returncode == 0, result.stderr
    plan = read(generated / 'plan.json')
    graph_hashes = {f'layer.{r}.et': sha(generated / f'layer.{r}.et') for r in range(4)}
    baseline = read(root / 'fixtures/m2_delay_feedback/given-events.json')
    transfer_ids = {(t['kind'], t['src_rank'], t['dst_rank']): t['transfer_id'] for t in plan['transfers']}
    # One event changes per case. SEND and RECV completion remain independent.
    cases = [('given-base', None, None, None, 26000),
             ('given-last-input', ('dispatch', 1, 2), 'data_ready_ns', 30000, 30000),
             ('given-local-send', ('dispatch', 2, 3), 'transaction_done_ns', 34000, 34000),
             ('given-noncritical-input', ('dispatch', 0, 2), 'data_ready_ns', 24000, 26000),
             ('given-unrelated-input', ('dispatch', 3, 1), 'data_ready_ns', 54000, 26000)]
    summaries, reports = {}, {}
    libraries = {p.resolve() for p in (build / 'output/lib').glob('libns3*.so')}
    assert len(libraries) == 10

    def check_run(label):
        run = output / label
        report = read(run / 'result.json')
        assert graph_hashes == {name: sha(run / name) for name in graph_hashes}
        # C++ orders JSON object keys differently; the plan must be structurally
        # identical, while the binary Chakra graphs must be byte-identical.
        assert read(run / 'plan.json') == plan
        assert {Path(p).resolve() for p in report['loaded_libraries'] if Path(p).name.startswith('libns3')} == libraries
        summaries[label] = check_execution(plan, report, run)
        reports[label] = report
        return report

    for label, key, field, value, expected_start in cases:
        schedule = deepcopy(baseline)
        if key is not None:
            next(e for e in schedule['events'] if e['transfer_id'] == transfer_ids[key])[field] = value
            assert sum(a != b for a, b in zip(schedule['events'], baseline['events'])) == 1
        path = output / (label + '.json'); path.write_text(json.dumps(schedule, indent=2) + '\n')
        result = invoke(label, ['timeout', '--kill-after=5s', '30s', str(test_binary), str(generated),
                                str(system), str(path), str(output / label)])
        assert result.returncode == 0 and not result.stderr.strip(), (label, result.stdout, result.stderr)
        report = check_run(label)
        assert report['event_source'] == 'test_double' and report['network_nodes'] == 0
        rank2 = summaries[label]['ranks'][2]
        assert rank2['expert_start_ns'] == expected_start and rank2['expert_end_ns'] == expected_start + 12000
        assert rank2['local_merge_end_ns'] == expected_start + 13000
        starts = [t['send_start_ns'] for t in report['transfers'] if t['transfer_id'] in
                  {transfer_ids['combine', 2, 0], transfer_ids['combine', 2, 1]}]
        assert min(starts) == expected_start + 13000
        assert summaries[label]['global_dispatch_done_ns'] == 56000
        assert len(report['events']) == 56
        for event in schedule['events']:
            got = next(t for t in report['transfers'] if t['transfer_id'] == event['transfer_id'])
            for field in ('data_ready_ns', 'transaction_done_ns'):
                assert got[field] == event[field]
            recorded = {e['event']: e['time_ns'] for e in report['events'] if e['transfer_id'] == event['transfer_id']}
            assert recorded == dict(send_registered=got['send_start_ns'], receive_registered=got['receive_start_ns'],
                                    data_ready=got['data_ready_ns'], transaction_done=got['transaction_done_ns'])
    assert summaries['given-unrelated-input']['ranks'][1]['expert_start_ns'] == 54000
    assert summaries['given-base']['ranks'][1]['expert_start_ns'] == 50000
    assert summaries['given-last-input']['ranks'][2]['combine_finish_ns'] == summaries['given-base']['ranks'][2]['combine_finish_ns']
    print('PASS: 26/38 us, delayed 30/42 us; local SEND, noncritical and unrelated input controls; no global barrier')

    network = root / 'fixtures/m2_single_layer/network'
    for label, delay in [('ub-base', '20ns'), ('ub-link-delay', '2020ns')]:
        case = output / (label + '-network'); shutil.copytree(network, case)
        rows = list(csv.DictReader((case / 'topology.csv').open()))
        assert rows[1]['nodeId1'] == '1' and rows[1]['nodeId2'] == '4' and rows[1]['delay'] == '20ns'
        rows[1]['delay'] = delay
        with (case / 'topology.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0].keys(), lineterminator='\n')
            writer.writeheader(); writer.writerows(rows)
        result = invoke(label, ['timeout', '--kill-after=5s', '30s', str(real_binary), str(generated), str(system),
                                str(case), '-', str(output / label)])
        assert result.returncode == 0 and not result.stderr.strip(), (label, result.stdout, result.stderr)
        report = check_run(label)
        assert report['csv_completed_tasks'] == 0 and report['peak_wqes'] == [1] * 4 and report['active_wqes'] == [0] * 4
        assert len(report['target_writes']) == len(report['wqe_starts']) == len(report['wqe_completions']) == 14
        commits = {t['task']: t for t in report['target_writes']}
        ends = {t['task']: t for t in report['wqe_completions']}
        for t in report['transfers']:
            assert t['submitted_tasks'] == 1 and t['bytes'] == t['committed_bytes'] == commits[t['ub_task_id']]['bytes'] == 8192
            assert t['data_ready_ns'] == commits[t['ub_task_id']]['time_ns'] < t['transaction_done_ns'] == ends[t['ub_task_id']]['time_ns']
        assert all(s['finished'] and not s['remaining_nodes'] and
                   all(s[k] == 0 for k in ('cpu_slots', 'compute_slots', 'send_slots', 'pending_events', 'pending_tasks'))
                   for s in report['rank_resources'])
    a, b = summaries['ub-base']['ranks'][2], summaries['ub-link-delay']['ranks'][2]
    critical = next(t for t in reports['ub-link-delay']['transfers'] if t['transfer_id'] == transfer_ids['dispatch', 1, 2])
    assert b['expert_start_ns'] == critical['data_ready_ns'] > a['expert_start_ns']
    assert b['expert_end_ns'] - a['expert_end_ns'] == b['expert_start_ns'] - a['expert_start_ns']
    assert b['local_merge_end_ns'] - a['local_merge_end_ns'] == b['expert_start_ns'] - a['expert_start_ns']
    for path in network.iterdir():
        if path.name != 'topology.csv':
            assert sha(path) == sha(output / 'ub-base-network' / path.name) == sha(output / 'ub-link-delay-network' / path.name)
    base_rows = list(csv.DictReader((output / 'ub-base-network/topology.csv').open()))
    slow_rows = list(csv.DictReader((output / 'ub-link-delay-network/topology.csv').open()))
    assert [(i, key) for i in range(4) for key in base_rows[i] if base_rows[i][key] != slow_rows[i][key]] == [(1, 'delay')]
    print('PASS: real UB local conditions and network delay propagation on identical graphs, fourteen writes / 114688 bytes')

    bad_cases = [('missing', lambda s: s['events'].pop()),
                 ('duplicate', lambda s: s['events'].__setitem__(1, deepcopy(s['events'][0]))),
                 ('unknown', lambda s: s['events'][0].update(transfer_id=999)),
                 ('negative', lambda s: s['events'][0].update(data_ready_ns=-1)),
                 ('fraction', lambda s: s['events'][0].update(data_ready_ns=1.5)),
                 ('boolean', lambda s: s['events'][0].update(data_ready_ns=True)),
                 ('out-of-bound', lambda s: s['events'][0].update(data_ready_ns=1000000)),
                 ('extra-field', lambda s: s['events'][0].update(extra=0))]
    for label, mutate in bad_cases + [('before-send', lambda s: s['events'][0].update(data_ready_ns=0))]:
        bad = deepcopy(baseline); mutate(bad)
        path = output / ('bad-' + label + '.json'); path.write_text(json.dumps(bad) + '\n')
        run = output / ('bad-' + label)
        result = invoke('bad-' + label, ['timeout', '--kill-after=5s', '30s', str(test_binary), str(generated),
                                         str(system), str(path), str(run)])
        assert result.returncode == 1 and 'Delay test failure:' in result.stderr, (label, result.stderr)
        if label == 'before-send':
            assert 'given event precedes SEND issue' in result.stderr and not (run / 'result.json').exists()
        else:
            assert not run.exists()
    existing = output / 'given-base'
    before = {p: sha(p) for p in existing.rglob('*') if p.is_file()}
    result = invoke('overwrite', [str(test_binary), str(generated), str(system), str(output / 'given-base.json'), str(existing)])
    assert result.returncode == 1 and 'must be new' in result.stderr and all(sha(p) == h for p, h in before.items())

    # Audit the new target independently; check_layer.py audits the real target.
    recipe = invoke('test-commands', ['ninja', '-C', str(build), '-t', 'commands', 'cosim-layer-delay-test'])
    assert recipe.returncode == 0
    forbidden = ('network_frontend/', 'extern/network_backend/')
    assert not any(v in recipe.stdout for v in forbidden)
    sources = []
    for line in recipe.stdout.splitlines():
        words = shlex.split(line)
        if '-c' in words:
            p = Path(words[words.index('-c') + 1]).resolve()
            assert any(p.is_relative_to(root / area) for area in
                       ('backend', 'tests/integration', 'third_party/ns-3-ub', 'third_party/LLMServingSim/astra-sim'))
            sources.append(dict(path=str(p), sha256=sha(p), command=line))
    assert len(sources) == len({s['path'] for s in sources})
    link = build / 'cosim-m2/delay-test-link.map'
    link_text = link.read_text()
    assert not any(v in link_text for v in forbidden)
    members = re.findall(r'libAstraSim\.a\(([^)]+)\)\s+\(--whole-archive\)', link_text)
    assert len(members) == len(set(members)) == 52
    assert {(build / p).resolve() for p in re.findall(r'\bLOAD\s+(\S*libns3[^\s]*)', link_text)} == libraries
    (output / 'compiled-sources.json').write_text(json.dumps(sources, indent=2) + '\n')
    (output / 'summary.json').write_text(json.dumps(dict(passed=True, given_event_runs=5, real_ub_runs=2,
        rejected_inputs=10, graph_hashes=graph_hashes, cases=summaries,
        real_rank2_expert_shift_ns=b['expert_start_ns'] - a['expert_start_ns'],
        test_binary_sha256=sha(test_binary), real_binary_sha256=sha(real_binary),
        test_link_map_sha256=sha(link)), indent=2) + '\n')
    print('PASS: ten invalid/overwrite rejections; 52 ASTRA core objects and one set of ten UB ns-3 libraries')


if __name__ == '__main__':
    main()
