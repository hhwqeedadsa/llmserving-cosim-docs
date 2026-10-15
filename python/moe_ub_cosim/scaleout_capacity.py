"""Bounded algebraic planning, without executing a network or predicting latency."""
from fractions import Fraction
import hashlib
import json
from pathlib import Path


def fields(value, names, label):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError('Invalid capacity ' + label + ' fields')


def integer(value, label, low=1, high=2**31 - 1):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Invalid capacity ' + label)


def estimate(c):
    fields(c, 'schema_version parallel model workload fabric payload_bytes', 'root')
    if c['schema_version'] != 'moe-scaleout-capacity/1':
        raise ValueError('Unsupported capacity schema')
    p, m, w, f = (c[k] for k in ('parallel', 'model', 'workload', 'fabric'))
    fields(p, 'world_gpus ep_size tp_size pp_size', 'parallel')
    fields(m, 'routed_experts top_k hidden_dim activation_bytes moe_layers', 'model')
    fields(w, 'phase sequences_per_rank sequence_length gradient_accumulation', 'workload')
    fields(f, 'gpus_per_server nics_per_gpu leaf_count spine_count leaf_downlinks leaf_uplinks '
           'leaf_port_capacity spine_port_capacity nic_rate_bps uplink_rate_bps include_nvswitch', 'fabric')
    for key, value in p.items():
        integer(value, key, high=4096)
    model_limits = dict(routed_experts=65536, top_k=256, hidden_dim=1048576,
                        activation_bytes=16, moe_layers=4096)
    for key, value in m.items():
        integer(value, key, high=model_limits[key])
    for key in ('sequences_per_rank', 'sequence_length', 'gradient_accumulation'):
        integer(w[key], key, high=1048576)
    for key in set(f) - {'include_nvswitch'}:
        integer(f[key], key, high=10**15 if key.endswith('_bps') else 4096)
    integer(c['payload_bytes'], 'payload_bytes', high=65536)
    n, ep, e, k = p['world_gpus'], p['ep_size'], m['routed_experts'], m['top_k']
    if p['tp_size'] != 1 or p['pp_size'] != 1:
        raise ValueError('Capacity v1 supports TP=PP=1 only')
    if n % ep or e % ep or ep > e or k > e:
        raise ValueError('Require EP dividing world and experts, EP<=experts, Top-K<=experts')
    if w['phase'] not in ('decode', 'prefill', 'train'):
        raise ValueError('Require phase decode, prefill or train')
    if w['phase'] != 'train' and w['gradient_accumulation'] != 1:
        raise ValueError('Gradient accumulation only applies to train')
    if w['phase'] == 'decode' and w['sequence_length'] != 1:
        raise ValueError('Decode sequence_length is query tokens per step and must be 1; not KV context')
    if type(f['include_nvswitch']) is not bool or f['nics_per_gpu'] != 1:
        raise ValueError('Capacity v1 requires boolean include_nvswitch and one NIC per GPU')
    if n % f['gpus_per_server'] or f['leaf_count'] * f['leaf_downlinks'] != n:
        raise ValueError('Require whole servers and exactly enough leaf NIC downlinks')
    if f['leaf_uplinks'] != f['spine_count']:
        raise ValueError('Capacity v1 uses one link per leaf-spine pair')
    if (f['leaf_downlinks'] + f['leaf_uplinks'] > f['leaf_port_capacity'] or
            f['leaf_count'] > f['spine_port_capacity']):
        raise ValueError('Clos switch port capacity exceeded')
    if max(f['leaf_port_capacity'], f['spine_port_capacity'], f['gpus_per_server']) > 1024:
        raise ValueError('Locked dense switch arrays allow at most 1024 device slots plus slot zero')

    tokens_per_rank = w['sequences_per_rank'] * w['sequence_length']
    total_tokens = n * tokens_per_rank
    vector_bytes = m['hidden_dim'] * m['activation_bytes']
    # Uniform Top-K without replacement, equal contiguous-sized expert partitions.
    miss = Fraction(1)
    outside = e - e // ep
    if k > outside:
        miss = Fraction(0)
    else:
        for i in range(k):
            miss *= Fraction(outside - i, e - i)
    destinations = (ep - 1) * (1 - miss)
    copies = Fraction(k * (ep - 1), ep)
    legs = 4 if w['phase'] == 'train' else 2
    per_layer_copy = total_tokens * vector_bytes * copies
    per_layer_rank = total_tokens * vector_bytes * destinations

    def volume(dispatch):
        micro = dispatch * legs * m['moe_layers']
        window = micro * w['gradient_accumulation']
        packets = window / c['payload_bytes']
        return dict(dispatch_bytes_per_layer_microbatch=float(dispatch),
                    combine_bytes_per_layer_microbatch=float(dispatch),
                    all_moe_legs_bytes_per_microbatch=float(micro),
                    all_moe_legs_bytes_per_window=float(window),
                    ideal_payload_packets_at_expected_volume=(packets.numerator + packets.denominator - 1) // packets.denominator)

    servers = n // f['gpus_per_server']
    switches = f['leaf_count'] + f['spine_count']
    nv = servers if f['include_nvswitch'] else 0
    fixed = 1025 * 1025 * 8 * 4
    # Illustrative array-only allocation: actual port IDs include unused slot zero.
    dynamic = 8 * 4 * (f['leaf_count'] * (f['leaf_downlinks'] + f['leaf_uplinks'] + 1)**2 +
                       f['spine_count'] * (f['leaf_count'] + 1)**2 +
                       nv * (f['gpus_per_server'] + 1)**2)
    return dict(schema_version='moe-scaleout-capacity-result/1', state='estimate_only',
        network_executed=False, hardware_fit_validated=False,
        parallel=dict(world_gpus=n, dense_attention_dp_ranks=n, ep_size=ep,
                      expert_replica_groups=n // ep, experts_per_rank=e // ep,
                      potential_directed_peer_pairs=n * (ep - 1)),
        workload=dict(phase=w['phase'], tokens_per_rank_microbatch=tokens_per_rank,
                      tokens_per_ep_group_microbatch=ep * tokens_per_rank,
                      global_sequences_per_microbatch=n * w['sequences_per_rank'],
                      sequences_per_window=n * w['sequences_per_rank'] * w['gradient_accumulation'],
                      window='optimizer step' if w['phase'] == 'train' else 'one model forward batch/step',
                      moe_legs=legs, expected_remote_expert_copies_per_token=float(copies),
                      expected_remote_destination_ranks_per_token=float(destinations)),
        logical_remote_rank_volume=dict(per_expert_copy=volume(per_layer_copy),
                                        per_destination_rank_dedup=volume(per_layer_rank)),
        fabric=dict(servers=servers, ethernet_switches=switches, modeled_nvswitches=nv,
                    nic_links=n, leaf_spine_links=f['leaf_count'] * f['spine_count'],
                    leaf_downstream_bps=f['leaf_downlinks'] * f['nic_rate_bps'],
                    leaf_upstream_bps=f['leaf_uplinks'] * f['uplink_rate_bps'],
                    down_to_up_capacity_ratio=f['leaf_downlinks'] * f['nic_rate_bps'] /
                                              (f['leaf_uplinks'] * f['uplink_rate_bps'])),
        memory=dict(scope='m_bytes arrays only; excludes MMU, routing, packets, QPs, graphs and logs',
                    locked_array_bytes_per_switch=fixed,
                    locked_ethernet_array_bytes=switches * fixed,
                    locked_nvswitch_array_bytes=nv * fixed,
                    locked_total_array_bytes=(switches + nv) * fixed,
                    dynamic_port_array_design_estimate_bytes=dynamic,
                    dynamic_port_allocation_implemented=False),
        assumptions=['Uniform independent Top-K selections without replacement; equal experts per rank; no capacity drops or padding.',
                     'Two alternative logical communication templates; neither is an actual trace replay.',
                     'Combine uses the same vector size; training assumes equal-size forward/backward activations.',
                     'Remote rank bytes can travel inside a server; they are not Ethernet wire bytes.',
                     'No placement, cross-server fraction, routing, ACK/ECN/PFC, headers, gradient DP, recompute or compute costs.',
                     'Packet figure rounds aggregate expected volume; excludes per-flow rounding, acknowledgments and retransmissions.',
                     'One NIC per GPU, one link per leaf-spine pair, optional one modeled NVSwitch per server; not a hardware specification.'])


def execute(config, output):
    config, output = Path(config).resolve(), Path(output).resolve()
    if config.stat().st_size > 65536:
        raise ValueError('Capacity config exceeds 64KiB')
    raw = config.read_bytes()
    if len(raw) > 65536:
        raise ValueError('Capacity config exceeds 64KiB')
    c = json.loads(raw)
    result = estimate(c)
    result['input_sha256'] = hashlib.sha256(raw).hexdigest()
    output.mkdir(parents=True, exist_ok=False)
    (output / 'config.json').write_bytes(raw)
    (output / 'capacity.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    return dict(passed=True, state=result['state'], network_executed=False, output=str(output))
