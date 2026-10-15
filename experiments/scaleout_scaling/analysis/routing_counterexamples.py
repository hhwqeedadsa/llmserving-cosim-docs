"""Exact synthetic accounting of routing/placement counterexamples; no simulator or traffic timing."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys


def require(condition, message):
    if not condition:
        raise ValueError(message)


def analyze(c):
    require(c.get('schema_version') == 'routing-counterexamples/1' and
            c.get('source_kind') == 'synthetic_mechanism_examples', 'Synthetic examples only')
    size, per_server, window = (c[k] for k in ('vector_bytes', 'ranks_per_server', 'token_window_size'))
    require(all(type(v) is int and 0 < v <= 65536 for v in (size, per_server, window)), 'Invalid scalar bound')
    require(isinstance(c['cases'], list) and 0 < len(c['cases']) <= 64, 'Invalid case count')
    result, output_cells = {}, 0
    for case in c['cases']:
        name, placement, src, tokens = (case[k] for k in ('name', 'expert_to_rank', 'source_rank', 'expert_choices'))
        require(isinstance(name, str) and name and name not in result, 'Duplicate/invalid case name')
        require(isinstance(placement, list) and 0 < len(placement) <= 4096 and
                all(type(r) is int and 0 <= r < 4096 for r in placement), 'Invalid placement')
        ranks = max(placement) + 1
        require(set(placement) == set(range(ranks)) and ranks % per_server == 0 and
                type(src) is int and 0 <= src < ranks, 'Require complete ranks and servers')
        require(isinstance(tokens, list) and 0 < len(tokens) <= 4096, 'Invalid token bound')
        output_cells += len(placement) + ranks * (2 + (len(tokens) + window - 1) // window)
        require(output_cells <= 262144, 'Dense summary exceeds 262144-cell budget; use fewer cases/windows/ranks')
        expert_counts, rank_expert_inputs, received, window_received = Counter(), Counter(), Counter(), {}
        cross_server_direct = cross_server_coalesced = 0
        for i, choices in enumerate(tokens):
            require(isinstance(choices, list) and choices and
                    all(type(e) is int and 0 <= e < len(placement) for e in choices) and
                    len(choices) == len(set(choices)), 'Invalid token expert choices')
            expert_counts.update(choices)
            rank_expert_inputs.update(placement[e] for e in choices)
            destinations = {placement[e] for e in choices} - {src}
            bins = window_received.setdefault(i // window, Counter())
            for dst in destinations:
                received[dst] += size
                bins[dst] += size
            cross_server_direct += sum(size for dst in destinations if dst // per_server != src // per_server)
            cross_server_coalesced += size * len({dst // per_server for dst in destinations} - {src // per_server})
        # Include idle experts/ranks so that a hotspot ratio cannot silently omit zeroes.
        result[name] = dict(expert_counts=[expert_counts[e] for e in range(len(placement))],
                           rank_expert_inputs=[rank_expert_inputs[r] for r in range(ranks)],
                           remote_rank_dispatch_bytes=sum(received.values()),
                           remote_rank_receive_bytes=[received[r] for r in range(ranks)],
                           cross_server_dispatch_bytes_direct_rank=cross_server_direct,
                           cross_server_dispatch_bytes_node_coalesced=cross_server_coalesced,
                           token_window_rank_receive_bytes=[[bins[r] for r in range(ranks)] for bins in window_received.values()],
                           max_rank_receive_bytes_per_token_window=max(max(bins.values(), default=0) for bins in window_received.values()))
    return dict(source_kind=c['source_kind'], network_executed=False, real_training_data=False,
                scope='Dispatch vector payload only; node coalescing and direct-rank transfer are alternative templates',
                window_semantics='Fixed number of tokens, not physical time or measured microbatch boundaries',
                limits='No packet headers, QP, compute, padding/drop, intra-server forwarding cost, wire bytes or completion time',
                cases=result)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True, help='New output directory')
    args = p.parse_args()
    require(args.config.stat().st_size <= 1048576, 'Config larger than 1MiB')
    raw = args.config.read_bytes()
    result = analyze(json.loads(raw))
    result['input_sha256'] = hashlib.sha256(raw).hexdigest()
    result['analysis_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'config.json').write_bytes(raw)
    (args.output / 'counterexamples.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
