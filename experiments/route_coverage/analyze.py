"""Audit real routing coverage and declared four-rank traffic demand, offline.

Batch means distinct requests per EP group, not requests per rank. This does
not execute LLMServingSim, native RDMA or a GPU and reports no simulated time.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from moe_ub_cosim.data_inputs import ROOT
from moe_ub_cosim.route_evidence import iter_sources, unique_object, validate_manifest


def require(ok, message):
    if not ok:
        raise ValueError(message)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def distribution(values):
    values = sorted(values)
    return dict(count=len(values), minimum=values[0], median=values[(len(values)-1)//2],
                p95_nearest_rank=values[(95*len(values)+99)//100-1], maximum=values[-1])


def histogram_metrics(counts):
    total = sum(counts)
    return dict(selections=total, peak_to_mean=max(counts)*len(counts)/total,
                inactive_experts=counts.count(0),
                top8_selection_share=sum(sorted(counts, reverse=True)[:8])/total)


def audit(path, *, max_files=64, max_input_bytes=128*2**20):
    require(path.stat().st_size <= 2*2**20, 'Manifest exceeds 2 MiB')
    raw = path.read_bytes()
    manifest = json.loads(raw, object_pairs_hook=unique_object)
    validate_manifest(manifest)
    require(0 < len(manifest['files']) <= max_files <= 64, 'File budget exceeded')
    sizes = [(ROOT/f['file']['path']).stat().st_size for f in manifest['files']]
    require(all(s <= 8*2**20 for s in sizes) and sum(sizes) <= max_input_bytes <= 128*2**20,
            'Input byte budget exceeded')
    require(len({f['file']['sha256'] for f in manifest['files']}) == len(manifest['files']),
            'Duplicate contents cannot establish distinct request files')
    layers = [l for l in manifest['layers'] if l['kind'] == 'moe']
    require(0 < len(layers) <= 128 and all(l['num_experts'] <= 1024 and l['top_k'] <= 16 for l in layers),
            'Analysis dimensions exceeded')
    aggregate = {l['layer_id']: {p: [0]*l['num_experts'] for p in ('prefill', 'decode')} for l in layers}
    requests, decode = [], []
    for file_id, source in iter_sources(manifest):
        entries = source['raw']
        prompt, steps = source['prompt_tokens'], source['available_decode_records']
        rows = (prompt+steps)*len(layers)
        choices = (prompt+steps)*sum(l['top_k'] for l in layers)
        requests.append(dict(file_id=file_id, original_path=source['original_path'], file=source['file'],
                             prompt_tokens=prompt, decode_entries=steps, token_layer_rows=rows,
                             expert_choices=choices))
        decode.append({l['layer_id']: [entry[str(l['layer_id'])][0] for entry in entries[1:]] for l in layers})
        for layer in layers:
            lid = layer['layer_id']
            for phase, selected in (('prefill', entries[:1]), ('decode', entries[1:])):
                counts = aggregate[lid][phase]
                for entry in selected:
                    for token in entry[str(lid)]:
                        for expert in token:
                            counts[expert] += 1
    prefill = sum(r['prompt_tokens'] for r in requests)
    steps = sum(r['decode_entries'] for r in requests)
    report = dict(schema_version='moe-route-coverage/1', source=manifest['source'], model=manifest['model'],
                  manifest_sha256=hashlib.sha256(raw).hexdigest(), input_bytes=sum(sizes),
                  request_files=len(requests), content_duplicates=0,
                  independent_capture_runs_verified=False, moe_layers=len(layers),
                  prefill_tokens=prefill, decode_token_steps=steps,
                  prefill_token_layer_rows=prefill*len(layers), decode_token_layer_rows=steps*len(layers),
                  total_token_layer_rows=sum(r['token_layer_rows'] for r in requests),
                  total_expert_choices=sum(r['expert_choices'] for r in requests),
                  requests=requests, layers=[], network_simulations=0,
                  infer_eos_from_eof=False, heldout_validation=False)
    for layer in layers:
        lid = layer['layer_id']
        report['layers'].append(dict(layer_id=lid, num_experts=layer['num_experts'], top_k=layer['top_k'],
                                     **{phase: histogram_metrics(aggregate[lid][phase]) for phase in ('prefill', 'decode')}))
    # All groups use four potential destination ranks. Request i is declared at
    # source i % 4. Increasing B also increases active sources: explicit below.
    traffic = dict(schema_version='moe-route-traffic-demand/1', ranks=4,
                   expert_placement='contiguous_equal_capacity_per_layer',
                   source_assignment='manifest_request_index_modulo_4',
                   batch_definition='distinct_requests_per_EP_group',
                   source_count_changes_with_batch=True, activation_bytes=2,
                   activation_precision='declared_BF16_not_collection_evidence',
                   hidden_size=manifest['model']['hidden_dim'],
                   payload_unit='one_hidden_vector_per_token_per_remote_destination_rank',
                   excludes=['metadata', 'control', 'ACK_NACK_CNP', 'headers', 'combine',
                             'expert_compute', 'real_arrivals', 'queues', 'latency'],
                   dataset_coverage_sha256=hashlib.sha256(raw).hexdigest(), batches=[])
    for batch in (1, 2, 4, 8, 16, 32, 64):
        if len(requests) < batch:
            traffic['batches'].append(dict(batch=batch, state='unavailable_distinct_requests',
                                           available_request_files=len(requests)))
            continue
        require(all(l['num_experts'] % 4 == 0 for l in layers), 'Expert count not divisible by four')
        # Only full disjoint groups. Unused tails are reported, never duplicated.
        groups = [list(range(i, i+batch)) for i in range(0, len(requests)-batch+1, batch)]
        payloads, peak_means, remote_copies, lengths = [], [], [], []
        for group in groups:
            common_steps = min(requests[i]['decode_entries'] for i in group)
            lengths.append(common_steps)
            for layer in layers:
                lid = layer['layer_id']
                for step in range(common_steps):
                    dest_load = [0]*4
                    for index in group:
                        owners = {e//(layer['num_experts']//4) for e in decode[index][lid][step]}
                        for dest in owners - {index % 4}:
                            dest_load[dest] += 1
                    copies = sum(dest_load)
                    remote_copies.append(copies)
                    payloads.append(copies*manifest['model']['hidden_dim']*2)
                    if copies:
                        peak_means.append(max(dest_load)*4/copies)
        require(payloads, 'No common Decode entry in groups')
        traffic['batches'].append(dict(batch=batch, state='offline_demand_only', groups=groups,
                                       unused_request_indices=list(range(len(groups)*batch, len(requests))),
                                       common_decode_steps_per_group=lengths,
                                       application_waves=len(payloads),
                                       remote_vector_copies=distribution(remote_copies),
                                       dispatch_payload_bytes=distribution(payloads),
                                       positive_demand_destination_peak_to_mean=(distribution(peak_means) if peak_means else None),
                                       unused_decode_tokens_due_to_common_prefix=sum(requests[i]['decode_entries']-n
                                           for group, n in zip(groups, lengths) for i in group)))
    return report, traffic


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--max-files', type=int, default=64)
    p.add_argument('--max-input-bytes', type=int, default=128*2**20)
    a = p.parse_args()
    require(not a.output_dir.exists(), 'Output directory already exists')
    report, traffic = audit(a.manifest, max_files=a.max_files, max_input_bytes=a.max_input_bytes)
    a.output_dir.mkdir(parents=True)
    save(a.output_dir/'coverage.json', report)
    save(a.output_dir/'traffic-demand.json', traffic)
    print(json.dumps({k: report[k] for k in ('request_files', 'moe_layers', 'prefill_tokens',
        'decode_token_steps', 'total_token_layer_rows', 'total_expert_choices', 'input_bytes')}))


if __name__ == '__main__':
    main()
