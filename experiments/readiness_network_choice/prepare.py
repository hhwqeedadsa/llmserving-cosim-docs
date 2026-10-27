"""Prepare source-bound declarations; reuse only the admitted O3 runtime."""
import argparse
from collections import Counter
from copy import deepcopy
import shutil
from pathlib import Path
import subprocess

from experiments.shared_uplink_choice.prepare import NAMES, CONDITIONS, load, save, sha

PROFILES = ('balanced1', 'routed1', 'balanced10', 'routed10')
IDENTITY = 'e0a6015eb54f6cd9f5ca77cc9c2ff22c9fe1389d52e58fc55ca5b58627d43ac6'


def prepare(repo, stage):
    observed = stage/'observed'
    observed.mkdir()
    intake_path = repo/'experiments/route_coverage/results/local_additions_20261027/intake.json'
    intake = load(intake_path)
    save(observed/'intake.json', intake)
    rows = {r['name']: r for r in intake['files']}
    originals = observed/'routes'
    originals.mkdir()
    selected, counts = [], Counter()
    tokens = [[] for _ in range(4)]
    for index, name in enumerate(NAMES):
        row = rows[name+'.json']
        path = repo/'data/moe-trace-small'/row['name']
        assert row['eligible_offline'] and row['origin_directory_user_confirmed'] and not row['duplicate_of']
        assert sha(path) == row['sha256']
        shutil.copyfile(path, originals/path.name)
        choices, = load(path)[1]['3']
        assert len(choices) == len(set(choices)) == 8 and all(0 <= e < 256 for e in choices)
        tokens[index % 4].append(choices)
        counts.update(e//64 for e in choices)
        selected.append(dict(name=path.name, sha256=row['sha256'], entry_index=1, layer=3, choices=choices))
    assert [counts[r] for r in range(4)] == [28, 10, 10, 16]
    assert len({r['sha256'] for r in selected}) == 8
    template = load(repo/'examples/modern_deepep_native/input-selective.json')
    template['schema_version'] = 'moe-modern-native-input/2'
    del template['costs']['expert_ns']
    template['modern']['execution'].update(ranks=4, max_tokens_per_rank=2, num_sms=2,
        dispatch_warps=2, combine_warps=2, call_contexts=5, requested_contexts=5,
        local_devices=[[0, 1] for _ in range(4)])
    template['modern']['model'].update(experts=256, top_k=8, hidden_size=7168)
    template['modern']['calls'] = [dict(name='entry1.layer3', tokens=tokens)]
    devices = [dict(device=0, nic=1, plane=0), dict(device=1, nic=2, plane=0)]
    template['deployment'] = [dict(rank=r, node=n, devices=deepcopy(devices)) for r, n in enumerate((0, 1, 4, 5))]
    save(observed/'modern-input.json', template['modern'])
    for profile in PROFILES:
        cfg = deepcopy(template)
        coefficient = 10000 if profile.endswith('10') else 1000
        vector = [16*coefficient]*4 if profile.startswith('balanced') else [counts[r]*coefficient for r in range(4)]
        cfg['expert_stage'] = dict(mode='per_call_rank_declared_ns/1', durations_ns=[vector],
            source=f'Declared sensitivity: {profile}, coefficient={coefficient} ns per expert contribution; '
                   'balanced uses the same sum. Not a measured GPU profile or grouped-GEMM model.')
        save(observed/(profile+'.json'), cfg)
    for name in CONDITIONS:
        topology = load(repo/'examples/scaleout_multirail_native/topology.json')
        protocol = load(repo/'examples/modern_deepep_native/rdma.json')
        protocol['payload_bytes'] = 2000
        access, core = 200 if name == 'access' else 100, 50 if name == 'core' else 25
        for tier in ('access', 'tor_agg', 'tor_core'):
            topology['links'][tier]['rate_bps'] = access*10**9
        topology['links']['agg_core']['rate_bps'] = core*10**9
        topology['nic']['total_rate_bps'] = 2*access*10**9
        ecn = next(iter(protocol['ecn_by_rate'].values()))
        protocol['ecn_by_rate'] = {str(rate*10**9): deepcopy(ecn) for rate in (access, core)}
        save(observed/(name+'-topology-config.json'), topology)
        save(observed/(name+'-protocol.json'), protocol)
    accepted = repo/'runs/optimized_host_cost_20261027/stage'
    identity_path = accepted/'runtime-identity.json'
    assert sha(identity_path) == IDENTITY
    identity = load(identity_path)
    assert len(identity['sources']) == 45 and len(identity['files']) == 10
    assert all(sha(repo/name) == digest for name, digest in identity['sources'].items())
    assert all(sha(accepted/'runtime'/name) == digest for name, digest in identity['files'].items())
    shutil.copytree(accepted/'runtime', stage/'runtime')
    shutil.copyfile(identity_path, stage/'runtime-identity.json')
    save(observed/'source.json', dict(repository_head=subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip(), selected=selected, source=intake['source'],
        intake_sha256=sha(intake_path), runtime_identity_sha256=IDENTITY,
        expert_contribution_counts=[counts[r] for r in range(4)], remote_identity_verified=False,
        hardware_calibrated=False, independent_holdout=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.repository.resolve(), args.stage.resolve())
