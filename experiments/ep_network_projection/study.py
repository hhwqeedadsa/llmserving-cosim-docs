"""Real expert choices projected to rank demand; no network execution."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from math import comb
from pathlib import Path

from moe_ub_cosim.route_evidence import validate_raw
from moe_ub_cosim.modern_deepep_plan import compile_plan

EPS = (4, 8, 16, 32, 64, 128, 256)


def load(path): return json.loads(path.read_bytes())
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def prepare(repo, run):
    out = run/'observed'; assert not out.exists(); out.mkdir()
    intake = load(repo/'experiments/route_coverage/results/local_additions_20261027/intake.json')
    reference = load(repo/'data/moe-trace-small/manifest.json')
    files = sorted((r for r in intake['files'] if r['eligible_offline']), key=lambda r:int(r['name'][:-5]))
    assert len(files) == 15 and {'899.json', '1479.json'}.isdisjoint(r['name'] for r in files)
    rows = []; digests = set()
    for row in files:
        path = repo/'data/moe-trace-small'/row['name']
        digest = sha(path); assert digest == row['sha256'] and digest not in digests
        digests.add(digest)
        raw = path.read_bytes(); assert len(raw) <= 8*2**20
        validate_raw(json.loads(raw), reference['layers'])
        dest = out/'routes'/row['name']; dest.parent.mkdir(exist_ok=True); dest.write_bytes(raw)
        rows.append(dict(name=row['name'], sha256=digest, bytes=len(raw),
            original_path=row['original_path'], group=row['group']))
    assert sum(r['bytes'] for r in rows) <= 64*2**20
    save(out/'source.json', dict(source=intake['source'], model=reference['model'], layers=reference['layers'],
        files=rows, excluded_files=['899.json', '1479.json'],
        formal_route_manifest_created=False, remote_revision_content_verified=False,
        source_policy='user_confirmed_directory_and_local_content_hashes'))
    save(out/'control-input.json', load(repo/'examples/modern_deepep_native/input-selective.json')['modern'])
    source_names = ('python/moe_ub_cosim/modern_deepep_plan.py',
        'python/moe_ub_cosim/modern_native_layout.py',
        'experiments/static_dag_transfer/compile_event_graph.py',
        'experiments/shared_uplink_choice/generic.py')
    hashes = {}
    for name in source_names:
        dest = run/'source'/name; dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes((repo/name).read_bytes()); hashes[name] = sha(dest)
    save(run/'inputs.json', dict(source_sha256=sha(out/'source.json'), sources=hashes,
        input_bytes=sum(r['bytes'] for r in rows), new_networks=0, new_downloads=0))


def hit_probability(ep):
    return 1-comb(256-256//ep, 8)/comb(256, 8)


def destinations(choices, ep):
    return {e//(256//ep) for e in choices}


def describe_hist(hist):
    total = sum(hist.values()); assert total
    def order(k):
        so_far=0
        for v,n in sorted(hist.items()):
            so_far+=n
            if so_far>=k:return v
        raise AssertionError('missing order statistic')
    return dict(mean=sum(v*n for v,n in hist.items())/total,
        median_lower=order((total+1)//2), p95_nearest_rank=order((95*total+99)//100),
        minimum=min(hist), maximum=max(hist))


def control_check(observed):
    template = load(observed/'control-input.json')
    actual = {}
    for ranks in (4, 8):
        cfg=deepcopy(template)
        cfg['execution'].update(ranks=ranks, local_devices=[[0,1] for _ in range(ranks)])
        cfg['model'].update(experts=256,top_k=8,hidden_size=7168)
        cfg['calls']=[dict(name='empty-data-control-check',tokens=[[] for _ in range(ranks)])]
        plan=compile_plan(cfg)
        counts=Counter(o['kind'] for o in plan['operations'])
        assert counts == dict(barrier_signal=4*ranks*ranks, rank_count=ranks*ranks, expert_count_block=ranks*ranks)
        remote=[o for o in plan['operations'] if o['src']!=o['dst']]
        assert len(remote)==6*ranks*(ranks-1)
        assert sum(o['operand_bytes'] for o in remote)==ranks*(ranks-1)*(40+8*(256//ranks))
        actual[str(ranks)]=dict(control_operations=len(plan['operations']),remote_control_operations=len(remote),
            remote_control_operand_bytes=sum(o['operand_bytes'] for o in remote),network_executed=False)
    return dict(compiled_checks=actual, conditional_template_counts=[dict(ep=r,
        control_operations=6*r*r, remote_control_operations=6*r*(r-1),
        remote_control_operand_bytes=r*(r-1)*(40+8*(256//r)),
        compiler_executed=r in (4,8), within_declared_rank_support=r<=32,
        within_max_operation_budget=6*r*r<=16384,
        template_extrapolation_not_production_library=r not in (4,8)) for r in EPS])


def analyze(run, output):
    assert not output.exists(); output.mkdir()
    observed=run/'observed'; source=load(observed/'source.json')
    inputs=load(run/'inputs.json'); assert sha(observed/'source.json')==inputs['source_sha256']
    originals=[]
    for row in source['files']:
        path=observed/'routes'/row['name']; assert sha(path)==row['sha256']
        raw=load(path); prompt,decode=validate_raw(raw,source['layers'])
        originals.append(dict(name=row['name'],raw=raw,prompt_tokens=prompt,decode=decode))
    coverage=[]
    for ep in EPS:
        for phase in ('prefill','decode'):
            hist=Counter(); shapes=Counter(); layer_rows=[]
            for layer in range(3,61):
                local=Counter()
                for request in originals:
                    entries=request['raw'][:1] if phase=='prefill' else request['raw'][1:]
                    for entry in entries:
                        for choices in entry[str(layer)]:
                            ds=destinations(choices,ep)
                            local[len(ds)]+=1
                            shapes[sum(1<<d for d in ds)]+=1
                hist.update(local)
                layer_rows.append(dict(layer=layer,tokens=sum(local.values()),
                    mean_destinations=describe_hist(local)['mean'],all_ranks_tokens=local[ep]))
            count=sum(hist.values())
            coverage.append(dict(ep=ep,phase=phase,tokens=count,
                unique_destination_count=describe_hist(hist), destination_count_histogram=dict(sorted(hist.items())),
                mean_rank_fraction=describe_hist(hist)['mean']/ep,
                all_ranks_fraction=hist[ep]/count, distinct_rank_sets=len(shapes),
                uniform_expected_destinations=ep*hit_probability(ep), layers=layer_rows))
    grouped=[]
    for batch in (4,8):
        n_groups=len(originals)//batch
        groups=[originals[i*batch:(i+1)*batch] for i in range(n_groups)]
        lengths=[min(r['decode'] for r in group) for group in groups]
        unused=sum(r['decode']-steps for group,steps in zip(groups,lengths) for r in group)
        source_counts=Counter(j%4 for j in range(batch))
        for ep in EPS:
            hit=hit_probability(ep)
            expected_remote=batch*(ep-1)*hit
            expected_collision=sum(comb(batch-source_counts[r],2) for r in range(ep))*hit*hit
            remote_hist=Counter(); collision_hist=Counter(); peak_hist=Counter(); layer_rows=[]
            for layer in range(3,61):
                layer_remote=layer_collision=waves=0
                for group,steps in zip(groups,lengths):
                    for step in range(1,steps+1):
                        loads=Counter()
                        for j,request in enumerate(group):
                            choices=request['raw'][step][str(layer)][0]
                            loads.update(destinations(choices,ep)-{j%4})
                        remote=sum(loads.values())
                        collisions=sum(n*(n-1)//2 for n in loads.values())
                        peak=(max(loads.values())*ep/remote) if remote else None
                        remote_hist[remote]+=1; collision_hist[collisions]+=1
                        if peak is not None:peak_hist[peak]+=1
                        layer_remote+=remote;layer_collision+=collisions;waves+=1
                layer_rows.append(dict(layer=layer,waves=waves,remote_vectors=layer_remote,
                    pair_collisions=layer_collision,collision_to_uniform_expectation=layer_collision/(waves*expected_collision)))
            waves=sum(remote_hist.values()); remote_sum=sum(k*n for k,n in remote_hist.items())
            collision_sum=sum(k*n for k,n in collision_hist.items())
            assert waves==sum(lengths)*58
            grouped.append(dict(batch=batch,ep=ep,waves=waves,
                groups=[[r['name'] for r in g] for g in groups],common_decode_steps=lengths,
                unused_in_group_decode_steps=unused,unused_requests=[r['name'] for r in originals[n_groups*batch:]],
                fixed_source_ranks=4,source_tokens=dict(source_counts),
                remote_vector_count=describe_hist(remote_hist),remote_vectors_sum=remote_sum,
                reference_activation_payload_bytes=14336*remote_sum,
                pair_collisions=describe_hist(collision_hist),pair_collisions_sum=collision_sum,
                peak_to_mean_including_zero_ranks=describe_hist(peak_hist),
                uniform_expected_remote_vectors_per_wave=expected_remote,
                uniform_expected_pair_collisions_per_wave=expected_collision,
                observed_collision_to_uniform_expectation=collision_sum/(waves*expected_collision),
                layers=layer_rows))
    frozen=(run/'source/experiments/static_dag_transfer/compile_event_graph.py').read_text()
    assert "native = load(reference/'modern-execution.json')" in frozen
    assert "reference_timestamps_embedded=False" in frozen
    save(output/'summary.json',dict(schema_version='ep-network-projection/1',coverage=coverage,grouped=grouped,
        control=control_check(observed),valid_requests=len(originals),
        prefill_tokens=sum(r['prompt_tokens'] for r in originals),decode_records=sum(r['decode'] for r in originals),
        uniform_reference='independent_tokens_top8_without_replacement_from_256_experts',
        frozen_graph_source='baseline_modern_execution_is_used_to_extract_bindings_but_no_timestamps',
        input_only_graph_impossibility_proven=False,network_executions=0,gpu_executions=0,
        large_ep_packet_execution_proven=False,latency_or_speedup_estimated=False,
        novelty_proven=False,remote_source_revision_verified=False))
    print('Projected 15 full traces, 7 EP sizes and 2 batch groupings; zero network execution.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('prepare','analyze'))
    p.add_argument('--repository',type=Path);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path);a=p.parse_args()
    if a.action=='prepare':prepare(a.repository.resolve(),a.run.resolve())
    else:analyze(a.run.resolve(),a.output.resolve())
