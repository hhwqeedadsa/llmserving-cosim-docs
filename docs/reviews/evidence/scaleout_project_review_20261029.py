"""Read saved SimAI waves; audit contribution copies versus token/rank copies.

This does not execute a model or network and does not estimate DeepEP latency.
Only activation payload is compared; modern metadata/control is excluded.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def read(path, maximum=16 * 2**20):
    raw = path.read_bytes()
    if len(raw) > maximum:
        raise ValueError(f"oversized input: {path}")
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def audit(run):
    run = Path(run)
    config, config_hash = read(run / 'input.json', 2**20)
    if config['schema_version'] != 'moe-simai-service/1':
        raise ValueError('this audit requires the current sparse SimAI service')
    service = config['service']
    model = service['model']
    payload = model['hidden_size'] * model['activation_bytes']
    rows, total = [], Counter()
    for index, group in enumerate(service['parallel']['groups']):
        local_experts = model['experts'] // len(group)
        for step in range(service['workload']['decode_steps']):
            graph = f'g{index}.w{step}'
            wave, wave_hash = read(run / 'graphs' / graph / 'wave.json')
            if wave['graph_id'] != graph or wave['rank_ids'] != group:
                raise ValueError('wave group/identity mismatch')
            counts, expected = Counter(), Counter()
            for layer in wave['routes']:
                for token in layer['tokens']:
                    destinations = [group[e // local_experts] for e in token['experts']]
                    remote = [dst for dst in destinations if dst != token['rank']]
                    counts['token_layer_records'] += 1
                    counts['remote_contribution_copies'] += len(remote)
                    counts['remote_token_rank_copies'] += len(set(remote))
                    for dst in remote:
                        expected[layer['layer'], 'dispatch', token['rank'], dst] += payload
                        expected[layer['layer'], 'combine', dst, token['rank']] += payload
            actual = Counter()
            for message in wave['messages']:
                actual[message['layer'], message['phase'], message['src'], message['dst']] += message['bytes']
            if actual != expected:
                raise ValueError('saved message matrix differs from independent contribution expansion')
            counts['recorded_dispatch_payload_bytes'] = sum(
                size for (_, phase, _, _), size in actual.items() if phase == 'dispatch')
            counts['token_rank_dispatch_activation_bytes'] = counts['remote_token_rank_copies'] * payload
            counts['activation_copy_difference_bytes'] = (
                counts['recorded_dispatch_payload_bytes'] - counts['token_rank_dispatch_activation_bytes'])
            total.update(counts)
            rows.append(dict(graph_id=graph, ep=len(group), wave_sha256=wave_hash, **counts))
    return dict(
        schema_version='moe-project-review-copy-audit/1', input_sha256=config_hash,
        activation_payload_bytes_per_copy=payload, graphs=rows, totals=dict(total),
        scope='offline saved-wave conservation; static contiguous expert ownership; '
              'token/rank activation comparison only; excludes modern metadata, controls, '
              'Combine changes, packet overhead and timing; no network rerun or measured benefit')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='New JSON file; existing files are refused')
    args = parser.parse_args()
    result = audit(args.run)
    with args.output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
