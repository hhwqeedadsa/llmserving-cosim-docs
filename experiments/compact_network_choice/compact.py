"""Ordinary phase/load estimators. Reads only an observed input root, never truth."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from experiments.compact_network_choice.prepare import CONDITIONS, load, save

STAGES = ('dispatch.entry', 'dispatch.counts', 'dispatch.data', 'dispatch.exit',
          'combine.entry', 'combine.data', 'combine.exit')


def stage_of(op):
    if op['kind'] == 'barrier_signal':
        part = 'entry' if '.entry.' in op['id'] else 'exit' if '.exit.' in op['id'] else None
        assert part is not None
    else: part = 'data' if op['kind'] == 'token_data' else 'counts'
    stage = op['phase']+'.'+part; assert stage in STAGES
    return stage


def summarize(cfg, plan):
    cells = defaultdict(lambda: Counter(messages=0, operand_bytes=0, framed_bytes=0, reply_bytes=0))
    self_ns, local_ns = Counter(), Counter()
    paths = Counter()
    for op in plan['operations']:
        stage = stage_of(op); src, dst = op['src'], op['dst']; paths[op['path']] += 1
        if op['path'] == 'gin_self': self_ns[stage, src] += cfg['provider']['self_service']['complete_ns']
        elif op['path'] == 'local': local_ns[stage, src] += cfg['costs']['local_copy_ns']
        else:
            assert op['path'] == 'rdma' and src != dst
            cell = cells[stage, src, dst]; cell['messages'] += 1
            cell['operand_bytes'] += op['operand_bytes']
            cell['framed_bytes'] += 32+op['operand_bytes']
            cell['reply_bytes'] += 40 if op['kind'] == 'barrier_signal' else 0
        if op['kind'] == 'token_data' and op['phase'] == 'combine':
            local_ns['combine.data', src] += cfg['costs']['reduction_ns']
    for rank, tokens in enumerate(cfg['modern']['calls'][0]['tokens']):
        local_ns['dispatch.data', rank] += len(tokens)*cfg['costs']['source_prepare_ns']
        local_ns['dispatch.counts', rank] += cfg['costs']['source_prepare_ns']
    volume = Counter()
    for op in plan['operations']:
        if op['kind'] == 'token_data' and op['path'] == 'rdma':
            volume[op['phase'], 'src', op['src']] += op['operand_bytes']
            volume[op['phase'], 'dst', op['dst']] += op['operand_bytes']
    return dict(schema_version='compact-phase-load/1',
        cells=[dict(stage=s, src=a, dst=b, **v) for (s,a,b),v in sorted(cells.items())],
        local=[dict(stage=s, rank=r, self_ns=self_ns[s,r], local_ns=local_ns[s,r])
               for s in STAGES for r in range(4)],
        volume=[dict(phase=p, endpoint=d, rank=r, bytes=n) for (p,d,r),n in sorted(volume.items())],
        operations=len(plan['operations']), event_count=len(plan['events']), paths=dict(paths),
        expert_ns=cfg['costs']['expert_ns'], hardware_calibrated=False)


def predict(summary, topology, cfg, framed):
    nodes = {n['id']: n for n in topology['nodes']}
    deployment = {r['rank']: r['node'] for r in cfg['deployment']}
    def propagation(src, dst):
        # This estimator is deliberately restricted to this symmetric reference topology.
        a, b = nodes[deployment[src]], nodes[deployment[dst]]
        rates = {link['rate_bps'] for link in topology['links']}
        delays = {link['delay_ns'] for link in topology['links']}
        assert len(rates) == len(delays) == 1
        assert a['segment'] == b['segment'] and a['gpu_slot'] in (0, 1) and b['gpu_slot'] in (0, 1)
        assert a['server'] == b['server'] or a['pod'] != b['pod']
        return (2 if a['pod'] == b['pod'] else 6)*next(iter(delays))
    # Two NICs per active endpoint; no claim that the detailed controller balances them.
    rate = sum(port['rate_bps'] for port in topology['nic_ports'] if port['gpu'] == deployment[0])
    stages = []
    for stage in STAGES:
        sends, receives = Counter(), Counter(); prop = 0
        for row in summary['cells']:
            if row['stage'] != stage: continue
            src, dst = row['src'], row['dst']
            n = row['framed_bytes'] if framed else row['operand_bytes']
            sends[src] += n; receives[dst] += n
            if framed:
                sends[dst] += row['reply_bytes']; receives[src] += row['reply_bytes']
            prop = max(prop, propagation(src, dst))
        service = max([0, *sends.values(), *receives.values()])*8e9/rate
        local = max(r['self_ns']+r['local_ns'] for r in summary['local'] if r['stage'] == stage)
        estimate = max(prop+service, local)
        stages.append(dict(stage=stage, propagation_ns=prop, endpoint_service_ns=service,
                           local_service_ns=local, estimated_ns=estimate))
    return dict(estimated_application_ns=summary['expert_ns']+sum(s['estimated_ns'] for s in stages), stages=stages)


def choose(values):
    best = min(values.values())
    ties = [name for name in CONDITIONS if values[name] == best]
    return dict(selected=ties[0], tied_best=ties)


def execute(observed, output):
    assert not output.exists(); output.mkdir()
    for batch in (4, 8):
        cfg = load(observed/f'batch{batch}/input.json')
        plan = load(observed/f'batch{batch}/plan/plan.json')
        summary = summarize(cfg, plan)
        methods = {name: {} for name in ('volume', 'phase_payload', 'phase_framed')}
        for name in CONDITIONS:
            topology = load(observed/(name+'-topology')/'topology.json')
            rate = sum(p['rate_bps'] for p in topology['nic_ports'] if p['gpu'] == cfg['deployment'][0]['node'])
            volume = sum(max(r['bytes'] for r in summary['volume'] if r['phase'] == phase)*8e9/rate
                         for phase in ('dispatch', 'combine'))
            methods['volume'][name] = dict(estimated_application_ns=volume)
            for method, framed in (('phase_payload', False), ('phase_framed', True)):
                methods[method][name] = predict(summary, topology, cfg, framed)
        save(output/f'batch{batch}.json', dict(batch=batch, summary=summary, methods=methods,
            choices={m: choose({k:v['estimated_application_ns'] for k,v in rows.items()}) for m,rows in methods.items()},
            representation_compact_bytes=len(json.dumps(summary, sort_keys=True, separators=(',', ':')).encode()),
            plan_compact_bytes=len(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()),
            future_results_read=False, no_fitted_constants=True))


def main():
    p = argparse.ArgumentParser()
    for name in ('observed', 'output'): p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args(); execute(a.observed.resolve(), a.output.resolve())


if __name__ == '__main__': main()
