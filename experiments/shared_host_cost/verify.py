"""Offline independent clock sweep, immutable-input checks and corruption probes."""
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
from experiments.shared_uplink_choice.prepare import load, save, sha
from experiments.shared_host_cost.compiler import inspect


def need(ok, message):
    if not ok: raise ValueError(message)


def sweep(rows):
    """Accumulate every clock increment to active intervals, unlike interval subtraction."""
    need(rows[0]['type'] == 'header' and rows[-1]['type'] == 'footer' and rows[-1]['complete'], 'trace boundaries')
    stack, seen, totals = [], set(), {}
    last_wall = last_cpu = 0
    for row in rows[1:-1]:
        wall, cpu = row['wall_ns'], row['cpu_ns']
        need(type(wall) is int and type(cpu) is int and wall >= last_wall and cpu >= last_cpu, 'clock regression')
        for item in stack:
            phase = totals[item['name']]
            phase['inclusive_wall_ns'] += wall-last_wall
            phase['inclusive_cpu_ns'] += cpu-last_cpu
        if stack:
            phase = totals[stack[-1]['name']]
            phase['exclusive_wall_ns'] += wall-last_wall
            phase['exclusive_cpu_ns'] += cpu-last_cpu
        last_wall, last_cpu = wall, cpu
        if row['type'] == 'begin':
            need(row['id'] not in seen and row['parent'] == (stack[-1]['id'] if stack else 0), 'invalid parent')
            seen.add(row['id']); stack.append(row)
            phase = totals.setdefault(row['name'], dict(count=0, inclusive_wall_ns=0, inclusive_cpu_ns=0,
                exclusive_wall_ns=0, exclusive_cpu_ns=0))
            phase['count'] += 1
        elif row['type'] == 'end':
            need(stack and stack[-1]['id'] == row['id'], 'unmatched end')
            stack.pop()
        else: need(row['type'] == 'snapshot', 'unknown record')
    need(not stack and len(seen) == rows[-1]['spans'], 'incomplete intervals')
    return totals


def project(result, accepted):
    """Historical path projection is for offline recovery, never a live library check.

    The original reference bytes are separately anchored by source.json; runtime
    members are hash checked here. Original live checks remain in analyze.compare.
    """
    need(set(accepted) == {Path(p).name for p in result['loaded_libraries'] if Path(p).name.startswith('libns3.')},
        'loaded library names differ')
    return dict(result, loaded_libraries=[Path(p).name if Path(p).name.startswith('libns3.') else p
        for p in result['loaded_libraries']])


def equivalent(old, new, accepted):
    need(project(old, accepted) == project(new, accepted), 'physical execution differs')


def same_application(old, new):
    need(old == new, 'application execution differs')


def verify(stage):
    ledger = load(stage/'execution.json')
    need(ledger['state'] == 'completed' and ledger['closed'] and ledger['network_reruns'] == 0, 'incomplete ledger')
    need(sum(r['network'] for r in ledger['records']) == 4 and all(r['exit_code'] == 0 for r in ledger['records']), 'observations differ')
    copied = load(stage/'source.json')['copied']
    for name, digest in copied.items(): need(sha(stage/name) == digest, 'archived reference/input changed: '+name)
    identity = load(stage/'runtime-identity.json')
    for name, digest in identity['files'].items(): need(sha(stage/'runtime'/name) == digest, 'runtime bytes differ')
    accepted = {Path(n).name: d for n, d in identity['files'].items() if n.startswith('lib/')}
    compiler = stage/'compiler-evidence'
    evidence = load(compiler/'identity.json')
    for name, digest in evidence['files'].items(): need(sha(compiler/name) == digest, 'compiler evidence changed')
    need(all(identity['files'][n] == d for n, d in evidence['runtime_build_bytes'].items()), 'build/runtime mismatch')
    need(load(compiler/'provenance.json')['sources'] == identity['sources'], 'source identity differs')
    compiler_summary = inspect(compiler)
    need(compiler_summary == load(compiler/'summary.json'), 'compiler summary differs')
    summary = load(stage/'report/summary.json')
    measurements = {}
    for batch in (4, 8):
        for mode in ('modern', 'generic'):
            name = f'batch{batch}-{mode}'
            old, new = stage/f'reference/batch{batch}/{mode}', stage/f'truth/batch{batch}/{mode}'
            equivalent(load(old/'result.json'), load(new/'result.json'), accepted)
            filename = 'modern-execution.json' if mode == 'modern' else 'event-execution.json'
            same_application(load(old/filename), load(new/filename))
            need((old/'control.jsonl').read_bytes() == (new/'control.jsonl').read_bytes(), 'control bytes differ')
            need(load(new/'equivalence.json')['passed'], 'live equivalence not accepted')
            row = summary['measurements'][name]
            phases = {}
            for writer in ('worker', 'native'):
                rows = [json.loads(line) for line in (new/(writer+'_host.jsonl')).read_text().splitlines()]
                phases[writer] = sweep(rows)
                need(row[writer+'_phases'] == phases[writer], 'phase CPU decomposition differs')
                need(row['trace_records'][writer] == len(rows), 'record count differs')
                root = 'native_total' if writer == 'native' else mode+'_worker'
                need(sum(p['exclusive_cpu_ns'] for p in phases[writer].values()) == phases[writer][root]['inclusive_cpu_ns'],
                    'exclusive phase sum does not equal root')
            python_cpu = phases['worker'][mode+'_worker']['inclusive_cpu_ns']
            native_cpu = phases['native']['native_total']['inclusive_cpu_ns']
            total = python_cpu+native_cpu
            need(row['observed_CPU_ns'] == total and abs(row['all_native_CPU_fraction']-native_cpu/total) < 1e-12,
                'native fraction incorrect')
            need(abs(row['zero_native_CPU_work_factor']-total/python_cpu) < 1e-12, 'CPU work bound incorrect')
            need(abs(row['run_exclusive_CPU_fraction']-phases['native']['simulator_run']['exclusive_cpu_ns']/total) < 1e-12,
                'Run fraction incorrect')
            result = load(new/'result.json')
            need(row['source_packets'] == len(result['transactions']['transport']['source_packets']) and
                row['physical_TX'] == sum(r[3] == 0 for r in result['packet_capture']['events']), 'packet counts differ')
            measurements[name] = dict(equivalent=True, phase_sweep_identical=True, complete_result_and_control=True,
                records=row['trace_records'], physical_TX=row['physical_TX'], CPU_ns=total)
    corruptions = []
    def reject(label, function):
        try: function()
        except (ValueError, AssertionError, KeyError, IndexError): corruptions.append(label)
        else: raise ValueError('corruption accepted: '+label)
    base = stage/'truth/batch4/modern'
    rows = [json.loads(line) for line in (base/'native_host.jsonl').read_text().splitlines()]
    bad = deepcopy(rows); bad[2]['parent'] = 0
    reject('parent nesting', lambda: sweep(bad))
    bad = deepcopy(rows); bad[3]['cpu_ns'] = bad[2]['cpu_ns']-1
    reject('CPU timestamp regression', lambda: sweep(bad))
    reference = load(stage/'reference/batch4/modern/result.json')
    bad_result = load(base/'result.json'); bad_result['packet_capture']['events'][0][0] += 1
    reject('physical packet time', lambda: equivalent(reference, bad_result, accepted))
    reference = load(stage/'reference/batch4/modern/modern-execution.json')
    bad_app = load(base/'modern-execution.json')
    next(e for e in bad_app['events'] if e['kind'] == 'application_stage_complete')['time_ns'] += 1
    reject('application completion', lambda: same_application(reference, bad_app))
    return dict(passed=True, observations=measurements, rejected_corruptions=corruptions,
        immutable_copied_files=len(copied), runtime_members=len(identity['files']),
        compiler_units=compiler_summary['translation_units'], effective_optimization='-O0',
        original_live_library_hash_checks_preserved=True, offline_projection_requires_anchored_references=True,
        no_new_networks=True, optimized_backend_performance_measured=False,
        new_simulator_speedup_proven=False)


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--stage', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); need(not a.output.exists(), 'preserve existing verification')
    save(a.output, verify(a.stage.resolve()))
