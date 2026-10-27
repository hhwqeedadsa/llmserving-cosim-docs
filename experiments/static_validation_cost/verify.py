"""Recompute from kernel receipts and raw network results, independent of analysis."""
import argparse
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

from .data import CASES, load, save


def kernel(record, probe):
    assert record['schema_version'] == 'complete-workflow-wait4/1'
    assert record['exit_code'] == record['raw_wait_status'] == 0
    assert record['elapsed_wall_ns'] == record['end_perf_ns']-record['start_perf_ns'] > 0
    usage = record['rusage']
    assert usage['ru_utime'] >= 0 and usage['ru_stime'] >= 0
    assert record['subtree_CPU_ns'] == round(10**9*(usage['ru_utime']+usage['ru_stime'])) > 0
    assert record['max_single_process_rss_kib'] == usage['ru_maxrss'] > 0
    assert record['affinity'] and record['includes_waited_descendants']
    assert probe['status'] == 'completed' and probe['child_exit_code'] == 0 and not probe['remaining_live_group_pids']
    assert probe['peak_sampled_group_rss_bytes'] <= probe['limits']['sampled_group_rss_bytes']
    assert probe['wall_seconds'] <= probe['limits']['wall_seconds']
    assert probe['peak_sampled_artifact_bytes'] <= probe['limits']['artifact_bytes']


def order(ledger):
    rows = [r for r in ledger['records'] if r['network']]
    assert ledger['closed'] and ledger['state'] == 'completed' and ledger['network_attempts'] == len(rows) == 28
    offset = 0
    for index, (batch, mode) in enumerate(CASES):
        for pair in range(3):
            expected = [False, True] if (index+pair)%2 == 0 else [True, False]
            for cached in expected:
                row = rows[offset]; case = row['case']; offset += 1
                assert row['exit_code'] == 0
                assert (case['batch'], case['mode'], case['pair'], case['cached'], case['timing']) == (batch, mode, pair, cached, False)
                assert row['label'] == case['label'] == f'b{batch}-{mode}-p{pair}-'+('cached' if cached else 'full')
    for batch, mode in CASES:
        row = rows[offset]; case = row['case']; offset += 1
        assert row['exit_code'] == 0 and (case['batch'], case['mode'], case['pair'], case['cached'], case['timing']) == (batch, mode, None, True, True)
    assert len({r['label'] for r in rows}) == 28
    return rows


def network(stage, row, overrides=None):
    case = row['case']; out = stage/'truth'/row['label']
    old = stage/'reference'/f"batch{case['batch']}"/case['mode']
    def read(name): return overrides[name] if overrides and name in overrides else load(out/name)
    value, reference = read('result.json'), load(old/'result.json')
    for result in (value, reference):
        native, system = [], []
        for path in result['loaded_libraries']:
            if Path(path).name.startswith('libns3.'): native.append(Path(path).name)
            else: system.append(path)
        assert len(native) == len(set(native)) == 8
        result['loaded_libraries'] = (sorted(native), system)
    assert value == reference
    assert (out/'control.jsonl').read_bytes() == (old/'control.jsonl').read_bytes()
    name = 'modern-execution.json' if case['mode'] == 'modern' else 'event-execution.json'
    assert read(name) == load(old/name)
    decisions = value['session']['decisions']
    if case['cached']:
        cache = read('static-validation.json')
        assert cache['schema_version'] == 'moe-static-stream-validation/1' and cache['host_only'] is True
        assert cache['calls'] == decisions+1 and cache['hits'] == decisions
        assert cache['validations'] == cache['entries'] == 1 and cache['unkeyable'] == 0
        assert set(cache) == {'schema_version', 'host_only', 'calls', 'hits', 'validations', 'entries', 'unkeyable'}
    else: assert not (out/'static-validation.json').exists()


def sweep(records):
    assert records[0]['type'] == 'header' and records[-1]['type'] == 'footer' and records[-1]['complete']
    starts, ends, children = {}, {}, defaultdict(list)
    for row in records[1:-1]:
        if row['type'] == 'begin':
            assert row['id'] not in starts; starts[row['id']] = row; children[row['parent']].append(row['id'])
        elif row['type'] == 'end':
            assert row['id'] not in ends; ends[row['id']] = row
        else: assert row['type'] == 'snapshot'
    assert starts.keys() == ends.keys() and len(starts) == records[-1]['spans']
    totals = {}
    for identity, start in starts.items():
        stop = ends[identity]; parent = start['parent']
        if parent: assert starts[parent]['wall_ns'] <= start['wall_ns'] <= stop['wall_ns'] <= ends[parent]['wall_ns']
        group = totals.setdefault(start['name'], dict(count=0, inclusive_wall_ns=0, inclusive_cpu_ns=0, exclusive_wall_ns=0, exclusive_cpu_ns=0))
        group['count'] += 1
        for clock in ('wall', 'cpu'):
            duration = stop[clock+'_ns']-start[clock+'_ns']
            nested = sum(ends[c][clock+'_ns']-starts[c][clock+'_ns'] for c in children[identity])
            assert duration >= nested >= 0
            group['inclusive_'+clock+'_ns'] += duration
            group['exclusive_'+clock+'_ns'] += duration-nested
    return totals


def verify(stage):
    import json
    rows = order(load(stage/'execution.json')); summary = load(stage/'report/summary.json')
    metrics = {}
    for row in rows:
        label, case = row['label'], row['case']
        metric = load(stage/'measurements'/(label+'.json')); probe = load(stage/'monitor'/label/'probe.json')
        kernel(metric, probe); network(stage, row)
        flags = metric['command']
        assert ('--host-timing' in flags) == case['timing']
        assert (('--reuse-static-validation' if case['mode'] == 'modern' else '--cached') in flags) == case['cached']
        metrics[label] = metric
        if case['timing']:
            data = summary['diagnostics'][f"batch{case['batch']}-{case['mode']}"]
            for writer in ('worker', 'native'):
                records = [json.loads(line) for line in (stage/'truth'/label/(writer+'_host.jsonl')).read_text().splitlines()]
                assert sweep(records) == data[writer+'_phases']
            worker = data['worker_phases']['modern_worker' if case['mode'] == 'modern' else 'generic_worker']['inclusive_cpu_ns']
            native = data['native_phases']['native_total']['inclusive_cpu_ns']
            assert data['observed_root_CPU_ns'] == worker+native
            assert data['all_native_free_CPU_work_factor'] == (worker+native)/worker
            assert data['native_CPU_fraction'] == native/(worker+native)
            assert data['run_exclusive_fraction'] == data['native_phases']['simulator_run']['exclusive_cpu_ns']/(worker+native)
        else:
            assert not (stage/'truth'/label/'worker_host.jsonl').exists()
            assert not (stage/'truth'/label/'native_host.jsonl').exists()
    for batch, mode in CASES:
        case = summary['cases'][f'batch{batch}-{mode}']; cpus, walls = [], []
        for pair in range(3):
            a, b = [metrics[f'b{batch}-{mode}-p{pair}-'+suffix] for suffix in ('full', 'cached')]
            cpu, wall = a['subtree_CPU_ns']/b['subtree_CPU_ns'], a['elapsed_wall_ns']/b['elapsed_wall_ns']
            found = case['pairs'][pair]; cpus.append(cpu); walls.append(wall)
            assert found['CPU_speedup'] == cpu and found['wall_speedup'] == wall
            assert found['first'] == ('cached' if (CASES.index((batch, mode))+pair)%2 else 'full')
            for suffix, metric in (('full', a), ('cached', b)):
                assert found[suffix]['subtree_CPU_ns'] == metric['subtree_CPU_ns']
                assert found[suffix]['elapsed_wall_ns'] == metric['elapsed_wall_ns']
                label = f'b{batch}-{mode}-p{pair}-'+suffix
                assert found[suffix]['sampled_group_RSS_bytes'] == load(stage/'monitor'/label/'probe.json')['peak_sampled_group_rss_bytes']
        assert case['median_CPU_speedup'] == sorted(cpus)[1] and case['median_wall_speedup'] == sorted(walls)[1]
        assert case['CPU_speedup_range'] == [min(cpus), max(cpus)] and case['wall_speedup_range'] == [min(walls), max(walls)]
        assert case['engineering_threshold_met'] == (min(cpus)>1.05 and sorted(cpus)[1]>=1.10)
    rejected = []
    def reject(name, fn):
        try: fn()
        except (AssertionError, ValueError, KeyError): rejected.append(name); return
        raise AssertionError('accepted corruption '+name)
    first = rows[0]; metric = deepcopy(metrics[first['label']]); metric['subtree_CPU_ns'] += 1
    reject('kernel_CPU_arithmetic', lambda: kernel(metric, load(stage/'monitor'/first['label']/'probe.json')))
    ledger = load(stage/'execution.json'); next(r for r in ledger['records'] if r['network'])['case']['pair'] = 99
    reject('predeclared_pair_order', lambda: order(ledger))
    cached = next(r for r in rows if r['case']['cached']); directory = stage/'truth'/cached['label']
    result = load(directory/'result.json'); result['transactions']['transport']['source_packets'][0][6] += 1
    reject('actual_packet', lambda: network(stage, cached, {'result.json': result}))
    name = 'modern-execution.json'; state = load(directory/name); state['events'][0]['time_ns'] += 1
    reject('application_completion', lambda: network(stage, cached, {name: state}))
    cache = load(directory/'static-validation.json'); cache['hits'] += 1
    reject('static_reuse_count', lambda: network(stage, cached, {'static-validation.json': cache}))
    return dict(passed=True, kernel_receipts=28, complete_networks_and_snapshots=28, pairs_checked=12,
        diagnostic_clock_sweeps=8, corruptions_rejected=rejected, new_networks=0,
        statistical_significance_claimed=False, research_innovation_proven=False)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); assert not a.output.exists(); save(a.output, verify(a.stage.resolve()))
