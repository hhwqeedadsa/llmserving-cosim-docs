"""Ordinary seven-stage references, frozen without any future network input."""
import argparse
from collections import Counter
from pathlib import Path

from experiments.ecmp_baseline_audit.model import demand, project
from experiments.compact_network_choice.compact import summarize
from .data import CONDITIONS, load, save


def execute(observed, output):
    output.mkdir()
    for batch in (4,8):
        cfg,plan = load(observed/f'batch{batch}/input.json'),load(observed/f'batch{batch}/plan.json')
        declaration,local = demand(cfg,plan),summarize(cfg,plan)
        predictions = {}
        save(output/f'batch{batch}-declaration.json',declaration)
        for name in CONDITIONS:
            topology = load(observed/(name+'-topology/topology.json'))
            projection = project(topology,declaration,11)
            edges = {}
            for e in topology['links']:
                edges[e['src'],e['src_port']] = e
                edges[e['dst'],e['dst_port']] = e
            methods = {m:[] for m in ('equal_path_phase','hashed_link_phase','hashed_rtt_phase')}
            for row in projection['stages']:
                stage = row['stage']
                propagation = max(sum(edges[tuple(edge)]['delay_ns'] for edge in message['path'])
                    for message in projection['messages'] if message['stage'] == stage)
                self_local = max(r['self_ns']+r['local_ns'] for r in local['local'] if r['stage'] == stage)
                for method in methods:
                    service = row['equal_path_peak_service_ns'] if method == 'equal_path_phase' else row['deterministic_peak_service_ns']
                    factor = 2 if method == 'hashed_rtt_phase' and stage.endswith(('.entry','.exit')) else 1
                    methods[method].append(dict(stage=stage,service_ns=service,propagation_ns=factor*propagation,
                        local_ns=self_local,estimated_ns=max(service+factor*propagation,self_local)))
            methods = {m:dict(stages=rows,estimated_application_ns=local['expert_ns']+sum(r['estimated_ns'] for r in rows))
                for m,rows in methods.items()}
            cells = Counter()
            for row in declaration['messages']:
                c = declaration['connections'][row['connection']]
                a,b,an,bn = c['source_rank'],c['peer_rank'],c['source_nic'],c['destination_nic']
                if row['direction'] == 'reply': a,b,an,bn = b,a,bn,an
                cells[row['stage'],a,b,an,bn,row['direction']] += row['bytes']
            messages = [dict(stage=s,src=a,dst=b,source_nic=an,destination_nic=bn,direction=d,bytes=n)
                for (s,a,b,an,bn,d),n in sorted(cells.items())]
            loads = []
            for r in projection['deterministic_load']:
                assert r['bytes_denominator'] == 1
                loads.append(dict(stage=r['stage'],src=r['node'],src_port=r['port'],framed_bytes=r['bytes_numerator']))
            predictions[name] = dict(methods=methods,messages=messages,loads=loads,projection=projection)
        choices = {}
        for method in methods:
            values = {name:row['methods'][method]['estimated_application_ns'] for name,row in predictions.items()}
            tied = [name for name in CONDITIONS if values[name] == min(values.values())]
            choices[method] = dict(selected=tied[0],tied_best=tied)
        save(output/f'batch{batch}.json',dict(batch=batch,predictions=predictions,choices=choices,
            reads_truth=False,hardware_calibrated=False,strict_latency_bound=False))


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--observed',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();execute(a.observed.resolve(),a.output.resolve())
