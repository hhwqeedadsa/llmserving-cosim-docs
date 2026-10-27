"""Offline behavior equivalence and independent CPU arithmetic, with corruption checks."""
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
from experiments.shared_uplink_choice.prepare import load, save, sha
from experiments.shared_host_cost.verify import sweep


def need(value, message):
    if not value: raise ValueError(message)


def equal(a, b):
    a, b = deepcopy(a), deepcopy(b)
    def pop(result):
        return Counter(Path(p).name if Path(p).name.startswith('libns3.') else p for p in result.pop('loaded_libraries'))
    need(pop(a) == pop(b) and a == b, 'native state differs')


def verify(stage):
    summary = load(stage/'report/summary.json')
    need(summary['passed'] and len(summary['cases']) == 4, 'missing CPU summary')
    for n, d in load(stage/'source.json')['copied'].items(): need(sha(stage/n) == d, 'source/reference changed')
    for n, d in load(stage/'runtime-identity.json')['files'].items(): need(sha(stage/'runtime'/n) == d, 'runtime changed')
    results = {}
    for batch in (4, 8):
        for mode in ('modern', 'generic'):
            name = f'batch{batch}-{mode}'; old = stage/f'reference/batch{batch}/{mode}'; new = stage/f'truth/batch{batch}/{mode}'
            equal(load(old/'result.json'), load(new/'result.json'))
            snapshot = 'modern-execution.json' if mode == 'modern' else 'event-execution.json'
            need(load(old/snapshot) == load(new/snapshot), 'application differs')
            need((old/'control.jsonl').read_bytes() == (new/'control.jsonl').read_bytes(), 'control order differs')
            cases = summary['cases'][name]; trace_hashes = {}
            for directory, key in ((old, 'old_O0'), (new, 'optimized_O3')):
                metric = cases[key]; total = 0
                for writer, root in (('worker', mode+'_worker'), ('native', 'native_total')):
                    path = directory/(writer+'_host.jsonl')
                    phases = sweep([json.loads(line) for line in path.read_text().splitlines()])
                    need(phases == metric[writer+'_phases'], 'phase sweep differs')
                    cpu = phases[root]['inclusive_cpu_ns']; total += cpu
                    need(sum(p['exclusive_cpu_ns'] for p in phases.values()) == cpu, 'phase sum differs')
                    trace_hashes[key+'/'+writer] = sha(path)
                need(total == metric['observed_CPU_ns'], 'total CPU differs')
                native = metric['native_phases']['native_total']['inclusive_cpu_ns']
                need(abs(native/total-metric['all_native_CPU_fraction']) < 1e-12, 'native fraction differs')
                need(abs(total/(total-native)-metric['zero_native_CPU_work_factor']) < 1e-12, 'work bound differs')
            results[name] = dict(complete_state_control_application_identical=True, phase_sweep_identical=True,
                source_packets=cases['optimized_O3']['source_packets'], physical_TX=cases['optimized_O3']['physical_TX'],
                trace_sha256=trace_hashes)
    base = stage/'truth/batch4/modern'; rejected = []
    def reject(label, action):
        try: action()
        except (ValueError, AssertionError, IndexError, KeyError): rejected.append(label)
        else: raise ValueError('corruption accepted: '+label)
    rows = [json.loads(line) for line in (base/'native_host.jsonl').read_text().splitlines()]
    bad = deepcopy(rows); bad[2]['parent'] = 0
    reject('parent nesting', lambda: sweep(bad))
    bad = deepcopy(rows); bad[3]['cpu_ns'] = bad[2]['cpu_ns']-1
    reject('CPU clock regression', lambda: sweep(bad))
    bad_result = load(base/'result.json'); bad_result['packet_capture']['events'][0][0] += 1
    reject('physical packet time', lambda: equal(load(stage/'reference/batch4/modern/result.json'), bad_result))
    bad_app = load(base/'modern-execution.json'); bad_app['events'][-1]['time_ns'] += 1
    reject('application event', lambda: need(load(stage/'reference/batch4/modern/modern-execution.json') == bad_app, 'event differs'))
    return dict(passed=True, observations=results, rejected_corruptions=rejected,
        effective_optimization='-O3', loader_metadata_normalization_preregistered=True,
        stable_wall_speedup_proven=False, novel_algorithm_speedup_proven=False)


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--stage', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); need(not a.output.exists(), 'preserve prior verification'); save(a.output, verify(a.stage.resolve()))
