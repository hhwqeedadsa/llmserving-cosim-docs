"""Conditional Combine operand projection; declared, untimed public executions.

Preparation alone reads original routes. Analysis never reads truth. The public
runtime is used to construct legal transcripts, not as an expected-value oracle.
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
from itertools import permutations
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
REV = 'f6aa38fe06cd4a0489e7730365d4e0114970a2f3'


def load(p): return json.loads(p.read_text())
def save(p, v): p.write_text(json.dumps(v, ensure_ascii=False, indent=2)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def prepare(a):
    assert not a.output.exists()
    a.output.mkdir(); obs = a.output/'observed'; obs.mkdir(); truth = a.output/'truth'; truth.mkdir()
    manifest = load(a.manifest)
    assert manifest['source']['dataset_revision'] == '27febb7b2d24169560a9f6f83d38eb53f94916cf'
    rows = []
    for item in manifest['files']:
        p = a.source_root/item['file']['path']
        assert p.stat().st_size < 4*2**20 and sha(p) == item['file']['sha256']
        rows.append(load(p)[0]['4'][:16])
        shutil.copyfile(p, truth/p.name)
    assert [len(r) for r in rows] == [16, 16, 10, 6]
    tree = load(a.source_root/'data/research/deepep_modern_20261022/tree.json')
    assert tree['sha'] == REV and not tree['truncated']
    shutil.copyfile(a.source_root/'data/research/deepep_modern_20261022/tree.json', truth/'tree.json')
    sources = []
    selections = [('deepep_modern_20261022', n) for n in (
        'deep_ep/include/deep_ep/impls/ep/dispatch.cuh', 'deep_ep/include/deep_ep/impls/ep/combine.cuh',
        'deep_ep/include/deep_ep/comm/barrier.cuh', 'deep_ep/include/deep_ep/layout/ep/token.cuh')]
    selections += [('deepep_layout_20261022', 'deep_ep/include/deep_ep/impls/ep/dispatch_copy_epilogue.cuh'),
                   ('deepep_layout_20261022', 'deep_ep/include/deep_ep/common/compiled.cuh')]
    for cache, name in selections:
        p = a.source_root/'data/research'/cache/'sources'/name
        raw = p.read_bytes(); entry = next(r for r in tree['tree'] if r['path'] == name)
        blob = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        assert blob == entry['sha'] and len(raw) == entry['size']
        dst = obs/'sources'/name; dst.parent.mkdir(parents=True, exist_ok=True); dst.write_bytes(raw)
        sources.append(dict(path=name, commit=REV, sha256=sha(p), git_blob_sha=blob, bytes=len(raw),
                            public_url=f'https://github.com/deepseek-ai/DeepEP/blob/{REV}/{name}'))
    save(obs/'sources.json', sources); save(truth/'manifest.json', manifest)
    base = dict(schema_version='moe-modern-deepep-input/1',
        model=dict(experts=8, top_k=2, hidden_size=256, precision='bf16'),
        execution=dict(ranks=4, max_tokens_per_rank=4, mode='direct_rdma_only_fresh',
            stream='compute_wait_no_deferred_epilogue', expanded_layout=True, allow_multiple_reduction=True,
            expert_alignment=1, num_sms=2, dispatch_warps=2, combine_warps=2, call_contexts=2,
            requested_contexts=17, connections=2, local_devices=[[0, 1] for _ in range(4)]),
        calls=[dict(name='toy', tokens=[[[4, 5] for _ in range(4)], [], [], []])],
        limits=dict(max_events=8192, max_operations=2048, max_input_bytes=2**20, max_output_bytes=16*2**20))
    real = deepcopy(base)
    real['model'].update(experts=256, top_k=8, hidden_size=7168)
    real['execution'].update(max_tokens_per_rank=16, num_sms=8, dispatch_warps=4, combine_warps=4, call_contexts=8)
    real['calls'] = [dict(name='seen-prefill-prefix', tokens=rows)]
    for name, cfg in [('toy', base), ('real', real)]: save(obs/(name+'.json'), cfg)
    save(obs/'cases.json', [dict(name='toy-'+''.join(map(str, p)), input='toy', permutation=list(p)) for p in permutations(range(4))]
         + [dict(name='real-'+p, input='real', policy=p) for p in ('forward', 'reverse', 'sha256')])
    save(obs/'scope.json', dict(network_executed=False, GPU_executed=False, callbacks='declared untimed transcripts',
        actual_gpu_configuration_known=False, independent_requests=False, timing_equivalence_claimed=False))
    shutil.copyfile(Path(__file__).with_name('slot_projection_protocol.md'), a.output/'protocol.md')
    print(json.dumps(dict(prepared=True, sources=len(sources), cases=27)))


def execute(a):
    from moe_ub_cosim.modern_deepep_runtime import ModernDeepEPRuntime  # public functional API
    assert not a.output.exists(); a.output.mkdir(); commands = []; started = time.monotonic()
    env = dict(os.environ, PYTHONPATH=str(ROOT/'python'), PYTHONDONTWRITEBYTECODE='1')
    def cli(name, args):
        command = [sys.executable, '-m', 'moe_ub_cosim', 'scaleout', *map(str, args)]
        before = time.monotonic(); assert before-started < 55
        with (a.output/(name+'.stdout')).open('w') as so, (a.output/(name+'.stderr')).open('w') as se:
            run = subprocess.run(command, cwd=a.output, env=env, stdout=so, stderr=se, timeout=min(30, 58-(before-started)))
        commands.append(dict(argv=command, cwd=str(a.output), exitcode=run.returncode, wall_seconds=time.monotonic()-before))
        save(a.output/'commands.json', commands); assert run.returncode == 0, name
    plans = {}
    for name in ('toy', 'real'):
        cli(name+'-plan', ['modern-deepep-plan', '--config', a.observed/(name+'.json'), '--output', a.output/(name+'-plan')])
        plans[name] = load(a.output/(name+'-plan')/'plan.json')
    for case in load(a.observed/'cases.json'):
        cfg = load(a.observed/(case['input']+'.json')); runtime = ModernDeepEPRuntime(cfg)
        slot_names = {s['id'] for s in plans[case['input']]['slots']}; actions = []
        wanted = [f'm0.slot.s0.d2.t{t}' for t in case.get('permutation', [])]
        while runtime.ready():
            ready = runtime.ready(); slots = [e for e in ready if e in slot_names]
            if wanted:
                if wanted[0] in slots: selected = wanted.pop(0)
                else:
                    non_slots = [e for e in ready if e not in slot_names]
                    assert non_slots, 'permutation is not executable'; selected = min(non_slots)
            elif slots:
                policy = case.get('policy', 'forward')
                selected = max(slots) if policy == 'reverse' else min(slots, key=lambda e: hashlib.sha256(e.encode()).hexdigest()) if policy == 'sha256' else min(slots)
            else: selected = min(ready)
            runtime.fire(selected); actions.append(selected)
        assert not wanted and runtime.snapshot()['all_events_fired'], case['name']
        save(a.output/(case['name']+'-actions.json'), dict(schema_version='moe-modern-deepep-actions/1', events=actions))
        cli(case['name'], ['modern-deepep-replay', '--config', a.observed/(case['input']+'.json'),
            '--actions', a.output/(case['name']+'-actions.json'), '--output', a.output/(case['name']+'-result.json')])
    print(json.dumps(dict(executed_declared_transcripts=27, public_commands=len(commands), network_executed=False)))


def projection(plan, cfg):
    """Interval intersection, without assigning any token to an actual slot."""
    x = cfg['execution']; totals = Counter(); n = Counter()
    for s in plan['slots']: n[s['call'], s['src'], s['dst']] += 1
    for (ci, origin, owner), count in sorted(n.items()):
        N = sum(n[ci, s, owner] for s in range(x['ranks']))
        prefix = sum(n[ci, s, owner] for s in range(origin))
        per = (N+x['num_sms']*x['combine_warps']-1)//(x['num_sms']*x['combine_warps'])
        for channel in range(x['num_sms']*x['combine_warps']):
            overlap = max(0, min(prefix+count, (channel+1)*per)-max(prefix, channel*per))
            if not overlap: continue
            sm, warp = channel%x['num_sms'], channel//x['num_sms']; q = x['call_contexts']
            if q == 1: context = 0
            elif x['num_sms'] <= q:
                local = q//x['num_sms']+int(sm < q%x['num_sms']); context = sm+(warp%local)*x['num_sms']
            else: context = (sm*x['combine_warps']+warp)%q
            connection = context%x['connections']; local_context = context//x['connections']
            devices = x['local_devices'][owner]; device = devices[connection%len(devices)]
            key = (ci, owner, origin, context, connection, local_context, device)
            totals[key] += overlap
    return totals


def analyze(a):
    assert not a.output.exists(); a.output.mkdir(); cases = load(a.observed/'cases.json'); summaries = {}
    for name in ('toy', 'real'):
        cfg = load(a.observed/(name+'.json')); plan = load(a.run/(name+'-plan')/'plan.json')
        expected = projection(plan, cfg); profiles = []; identities = []; allocations = []
        combine_ops = {op['id']: op for op in plan['operations'] if op['phase']=='combine' and op['kind']=='token_data'}
        for case in [c for c in cases if c['input']==name]:
            result = load(a.run/(case['name']+'-result.json')); assert result['all_events_fired']
            allocations.append(json.dumps(result['allocated_slots'], sort_keys=True))
            observed = Counter(); mapped = {}
            for row in result['log']:
                if row['kind'] != 'issued' or row['operation'] not in combine_ops: continue
                op = combine_ops[row['operation']]; b = row['binding']
                key = (op['call'], op['src'], op['dst'], b['context_index'], b['connection'], b['local_context'], b['local_device'])
                observed[key] += 1; mapped[op['id']] = dict(context=b['context_index'], connection=b['connection'], receive_index=row['receive_index'])
            assert observed == expected, case['name']
            profiles.append(case['name']); identities.append(mapped)
        rows = [dict(call=k[0], owner=k[1], origin=k[2], context=k[3], connection=k[4], local_context=k[5], local_device=k[6],
                     path='local' if k[1]==k[2] else 'rdma', token_puts=count,
                     operand_bytes=count*plan['layout']['combine_put_bytes']) for k, count in sorted(expected.items())]
        save(a.output/(name+'-profile.json'), rows)
        witnesses = [dict(operation=op, assignments=[dict(case=profiles[i], **d[op]) for i,d in enumerate(identities)])
                     for op in identities[0] if combine_ops[op]['path']=='rdma' and len({d[op]['connection'] for d in identities})>1]
        if name == 'toy': assert witnesses, 'toy lacks the predeclared connection-changing identity witness'
        save(a.output/(name+'-witnesses.json'), witnesses)
        summaries[name] = dict(cases=len(profiles), token_puts=sum(expected.values()),
            remote_token_puts=sum(r['token_puts'] for r in rows if r['path']=='rdma'),
            remote_operand_bytes=sum(r['operand_bytes'] for r in rows if r['path']=='rdma'),
            nonzero_profile_rows=len(rows), connection_changing_tokens=len(witnesses), profiles_identical=True,
            distinct_slot_allocations=len(set(allocations)),
            discriminating_slot_order_control=len(set(allocations))>1)
    save(a.output/'summary.json', dict(passed=True, cases=summaries, projected_quantity='Combine token put operand counts/bytes by declared mapping',
        network_executed=False, hardware_calibrated=False, timing_equivalence_proved=False, main_paper_contribution_established=False))
    print(json.dumps(summaries))


def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='stage', required=True)
    q = sub.add_parser('prepare'); q.add_argument('--manifest', type=Path, required=True); q.add_argument('--source-root', type=Path, default=ROOT); q.add_argument('--output', type=Path, required=True)
    q = sub.add_parser('execute'); q.add_argument('--observed', type=Path, required=True); q.add_argument('--output', type=Path, required=True)
    q = sub.add_parser('analyze')
    for k in ('observed', 'run', 'output'): q.add_argument('--'+k, type=Path, required=True)
    a = p.parse_args()
    for k, v in vars(a).items():
        if isinstance(v, Path): setattr(a, k, v.resolve())
    assert shutil.disk_usage(a.output.parent).free >= 8*2**30+32*2**20
    globals()[a.stage](a)


if __name__ == '__main__': main()
