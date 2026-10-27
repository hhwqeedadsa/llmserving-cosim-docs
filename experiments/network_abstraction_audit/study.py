"""Offline representation audit. Synthetic variants never execute a network."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

from moe_ub_cosim.modern_deepep_plan import compile_plan
from moe_ub_cosim.modern_deepep_runtime import ModernDeepEPRuntime
from moe_ub_cosim.route_evidence import validate_raw

NAMES = ('900', '901', '905', '1003', '1004', '1007', '1008', '1011')
ARCHIVE_SHA = 'd4a1ace33a5a5fdfa8ea73c71fb5bbee0e7755ee0b505abeff0e7c5a816e3e4e'


def load(path):
    return json.loads(path.read_bytes())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(repo, run):
    from experiments.compact_network_choice.checkpoint import admit
    observed = run/'observed'
    assert not observed.exists()
    archive = repo/'runs/compact_choice_20261027/checkpoint.tar.gz'
    receipt = load(archive.with_suffix('.receipt.json'))
    assert receipt['sha256'] == ARCHIVE_SHA
    blobs = admit(archive, ARCHIVE_SHA)
    selected = {}
    for name, raw in blobs.items():
        if name.startswith('evidence/observed/') and (
            name.endswith('/input.json') or name.endswith('/plan.json') or
            name.startswith('evidence/observed/routes/') or name.endswith('/source.json')
        ):
            relative = name.removeprefix('evidence/observed/')
            path = observed/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            selected[relative] = sha(path)
    assert all((observed/'routes'/(n+'.json')).exists() for n in NAMES)
    for name in ('python/moe_ub_cosim/modern_deepep_plan.py',
                 'python/moe_ub_cosim/modern_native.py',
                 'python/moe_ub_cosim/modern_native_layout.py',
                 'python/moe_ub_cosim/modern_deepep_runtime.py'):
        destination = run/'source'/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((repo/name).read_bytes())
    intake = repo/'experiments/route_coverage/results/local_additions_20261027/intake.json'
    save(observed/'intake.json', load(intake))
    model_reference = load(repo/'data/moe-trace-small/manifest.json')
    save(observed/'model-reference.json', {k: model_reference[k] for k in ('model', 'layers')})
    save(run/'inputs.json', dict(archive_sha256=receipt['sha256'], members=selected,
        intake_sha256=sha(intake), repository_head=subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip(),
        platform_sources={str(p.relative_to(run/'source')): sha(p)
            for p in (run/'source').rglob('*.py')},
        raw_directory_confirmed_by_user=True, remote_revision_content_verified=False,
        new_networks=0, new_builds=0, gpu_executed=False))


def variants(config):
    """Preserve every token's owner at every Top-K position, change only IDs."""
    result = {'original': deepcopy(config)}
    for mode in ('concentrated', 'spread'):
        cfg = deepcopy(config)
        for call in cfg['calls']:
            ordinal = 0
            for tokens in call['tokens']:
                for choices in tokens:
                    used = Counter()
                    for i, expert in enumerate(choices):
                        owner = expert//64
                        offset = 0 if mode == 'concentrated' else ordinal*8
                        choices[i] = owner*64+(offset+used[owner])%64
                        used[owner] += 1
                    ordinal += 1
        result[mode] = cfg
    return result


def counts(config):
    histogram = [0]*256
    owners = [0]*4
    tokens = pairs = remote = 0
    for call in config['calls']:
        for src, rows in enumerate(call['tokens']):
            for choices in rows:
                tokens += 1
                destinations = set()
                for expert in choices:
                    histogram[expert] += 1
                    owners[expert//64] += 1
                    destinations.add(expert//64)
                pairs += len(destinations)
                remote += len(destinations-{src})
    return dict(tokens=tokens, selections=sum(histogram), expert_counts=histogram,
        active_experts=sum(n > 0 for n in histogram), hottest_expert_tokens=max(histogram),
        hottest_to_mean=max(histogram)/(sum(histogram)/256),
        owner_expert_selection_totals=owners, token_owner_pairs=pairs,
        remote_token_copies_each_phase=remote)


def projection(plan):
    """Remove just count-array CONTENT, preserving its transfer and all ordering."""
    output = deepcopy(plan)
    for op in output['operations']:
        if op['kind'] == 'expert_count_block':
            assert len(op['values']) == 64
            op['values'] = 'expert_count_payload_content_excluded'
    return output


def transcript(config):
    # Caller-supplied untimed schedule, never an external completion observation.
    runtime = ModernDeepEPRuntime(config)
    while runtime.ready():
        runtime.fire(runtime.ready()[0])
    result = runtime.snapshot()
    assert result['all_events_fired'] and result['all_application_complete']
    result.pop('plan_sha256')
    return result


def analyze(run, output):
    assert not output.exists()
    output.mkdir()
    observed = run/'observed'
    intake = load(observed/'intake.json')
    layers = load(observed/'model-reference.json')['layers']
    original_rows = {n: load(observed/'routes'/(n+'.json')) for n in NAMES}
    for rows in original_rows.values():
        validate_raw(rows, layers)
    expected_source = {r['name']: r for r in load(observed/'source.json')['selected']}
    for n in NAMES:
        path = observed/'routes'/(n+'.json')
        assert sha(path) == expected_source[n+'.json']['sha256']
        assert original_rows[n][1]['3'][0] == expected_source[n+'.json']['choices']
    reports = {}
    for batch in (4, 8):
        cfg = load(observed/f'batch{batch}/input.json')
        assert cfg['costs'] == dict(source_prepare_ns=100, expert_ns=1000,
            reduction_ns=100, local_copy_ns=100)
        for i, n in enumerate(NAMES[:batch]):
            assert cfg['modern']['calls'][0]['tokens'][i%4][i//4] == original_rows[n][1]['3'][0]
        rows = {}
        reference = compile_plan(cfg['modern'])
        assert reference == load(observed/f'batch{batch}/plan/plan.json')
        baseline_transcript = transcript(cfg['modern'])
        for name, config in variants(cfg['modern']).items():
            plan = compile_plan(config)
            # This strong equality also checks shape, bytes, target addressing,
            # Top-K position groups, bindings, event dependencies and slot groups.
            assert projection(plan) == projection(reference)
            same_schedule = transcript(config) == baseline_transcript
            assert same_schedule
            cell = output/f'batch{batch}'/name
            save(cell/'input.json', config)
            save(cell/'plan.json', plan)
            statistics = counts(config)
            rows[name] = dict(**statistics, operations=len(plan['operations']), events=len(plan['events']),
                slots=len(plan['slots']),
                network_shape_equal_after_only_count_values_removed=True,
                same_untimed_caller_schedule=True,
                projected_plan_sha256=hashlib.sha256(json.dumps(projection(plan), sort_keys=True).encode()).hexdigest(),
                declared_expert_stage_ns_per_rank=cfg['costs']['expert_ns'],
                data_operand_bytes=sum(o['operand_bytes'] for o in plan['operations']
                    if o['path']=='rdma' and o['kind']=='token_data'),
                control_operand_bytes=sum(o['operand_bytes'] for o in plan['operations']
                    if o['path']=='rdma' and o['kind']!='token_data'))
        assert len({r['hottest_expert_tokens'] for r in rows.values()}) > 1
        reports[str(batch)] = rows
    save(output/'summary.json', dict(schema_version='network-abstraction-audit/1', batches=reports,
        transformations='artificial_within_owner_position_preserving_not_extra_real_requests',
        proved='compiler_shape_and_one_untimed_schedule_invariance_only',
        count_payload_bytes_identical=False, network_timing_equivalence_executed=False,
        new_networks=0, gpu_executed=False, hardware_calibrated=False,
        missing_compute_model='expert_stage_done has a fixed per-rank duration independent of expert counts',
        raw_source_files=8, entry_index=1, layer=3, ep=4, experts=256, top_k=8))
    print('Audited 8 raw files, two batches and 6 offline plans; no network execution.')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('prepare', 'analyze'))
    p.add_argument('--repository', type=Path)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    if a.action == 'prepare':
        prepare(a.repository.resolve(), a.run.resolve())
    else:
        analyze(a.run.resolve(), a.output.resolve())
