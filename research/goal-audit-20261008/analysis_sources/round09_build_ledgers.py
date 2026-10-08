#!/usr/bin/env python3
"""Build the round-09 model, hardware, trace, CCL, mapping, and evidence ledgers."""

from __future__ import annotations

import csv
from pathlib import Path


ROUND_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROUND_ROOT / "analysis"
LLMSIM = Path("/home/lry/a100_ep_runtime/第04轮_仿真平台验证_20260917/LLMServingSim/repo")
NS3_CASES = Path(
    "/home/lry/a100_ep_runtime/第05轮_联合仿真M0_20260919/ns-3-ub/repo/"
    "scratch/20260929-block24-network-contention/cases"
)


def write_csv(name: str, fields: list[str], rows: list[dict]) -> None:
    with (ANALYSIS / name).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    write_csv(
        "model_inventory.csv",
        [
            "model", "architecture", "layers", "hidden_size", "attention_heads",
            "kv_heads", "head_dim", "experts", "top_k", "moe_intermediate_size",
            "max_position_tokens", "dtype", "total_parameters", "active_parameters_per_token",
            "bf16_weight_bytes", "bf16_weight_gib", "kv_bytes_per_token",
            "kv_128_mib", "kv_1024_mib", "kv_4096_mib", "source", "evidence", "boundary",
        ],
        [{
            "model": "Qwen/Qwen3-30B-A3B-Instruct-2507",
            "architecture": "Qwen3MoeForCausalLM",
            "layers": 48,
            "hidden_size": 2048,
            "attention_heads": 32,
            "kv_heads": 4,
            "head_dim": 128,
            "experts": 128,
            "top_k": 8,
            "moe_intermediate_size": 768,
            "max_position_tokens": 262144,
            "dtype": "bfloat16",
            "total_parameters": 30532122624,
            "active_parameters_per_token": 3353032704,
            "bf16_weight_bytes": 61064245248,
            "bf16_weight_gib": f"{61064245248 / 2**30:.6f}",
            "kv_bytes_per_token": 98304,
            "kv_128_mib": 12,
            "kv_1024_mib": 96,
            "kv_4096_mib": 384,
            "source": str(LLMSIM / "configs/model/Qwen/Qwen3-30B-A3B-Instruct-2507.json"),
            "evidence": "trace-derived/config-derived",
            "boundary": "Parameter and KV figures are derived from config dimensions; they are not device-memory measurements.",
        }],
    )

    write_csv(
        "hardware_profile_inventory.csv",
        [
            "asset", "scope", "location", "compute_or_link", "bandwidth_value",
            "bandwidth_unit", "latency_value", "latency_unit", "source", "evidence",
            "adjustable_field", "can_support", "cannot_support",
        ],
        [
            {
                "asset": "RTX PRO 6000 Blackwell Server Edition profile",
                "scope": "LLMServingSim GPU operator latency",
                "location": "GPU kernel/profile database",
                "compute_or_link": "compute",
                "bandwidth_value": "",
                "bandwidth_unit": "",
                "latency_value": "per operator lookup",
                "latency_unit": "ns",
                "source": str(LLMSIM / "profiler/perf/RTXPRO6000/Qwen/Qwen3-30B-A3B-Instruct-2507/bf16/meta.yaml"),
                "evidence": "profile-derived",
                "adjustable_field": "hardware profile directory and model/shape",
                "can_support": "Relative operator/request studies under this profile",
                "cannot_support": "A100 or Huawei NPU latency claims",
            },
            {
                "asset": "ASTRA analytical network",
                "scope": "LLMServingSim two-rank collective timing",
                "location": "fully connected logical fabric",
                "compute_or_link": "network",
                "bandwidth_value": 16,
                "bandwidth_unit": "GB/s",
                "latency_value": 20,
                "latency_unit": "us",
                "source": str(ROUND_ROOT / "experiments/llmservingsim_input_sweep/astra_inputs/round09-i128-o4/network/network.yml"),
                "evidence": "analytical/input-config",
                "adjustable_field": "network.yml bandwidth/latency/topology",
                "can_support": "Sensitivity to an abstract two-rank link",
                "cannot_support": "Packet queues, routing contention, or measured NIC behavior",
            },
            {
                "asset": "OpenUSim direction/background topology",
                "scope": "cases a0-a3 and b1-b4",
                "location": "host access link",
                "compute_or_link": "network",
                "bandwidth_value": 400,
                "bandwidth_unit": "Gbps",
                "latency_value": 20,
                "latency_unit": "ns propagation",
                "source": str(NS3_CASES / "a0-target-only/topology.csv"),
                "evidence": "ns3-input-config",
                "adjustable_field": "topology.csv data rate and delay",
                "can_support": "Core-bottleneck direction/type experiments",
                "cannot_support": "A100/NVLink or production-fabric calibration",
            },
            {
                "asset": "OpenUSim direction/background topology",
                "scope": "cases a0-a3 and b1-b4",
                "location": "inter-switch core link",
                "compute_or_link": "network",
                "bandwidth_value": 100,
                "bandwidth_unit": "Gbps",
                "latency_value": 50,
                "latency_unit": "ns propagation",
                "source": str(NS3_CASES / "a0-target-only/topology.csv"),
                "evidence": "ns3-input-config",
                "adjustable_field": "topology.csv data rate and delay",
                "can_support": "Shared-core contention and full-duplex direction isolation",
                "cannot_support": "Real switch oversubscription or routing policy",
            },
            {
                "asset": "OpenUSim destination-skew topology",
                "scope": "cases c0-c2",
                "location": "destination host access link",
                "compute_or_link": "network",
                "bandwidth_value": 100,
                "bandwidth_unit": "Gbps",
                "latency_value": 20,
                "latency_unit": "ns propagation",
                "source": str(NS3_CASES / "c0-balanced/topology.csv"),
                "evidence": "ns3-input-config",
                "adjustable_field": "topology.csv data rate and delay",
                "can_support": "Destination hotspot sensitivity",
                "cannot_support": "A specific deployed endpoint bandwidth",
            },
            {
                "asset": "OpenUSim destination-skew topology",
                "scope": "cases c0-c2",
                "location": "inter-switch core link",
                "compute_or_link": "network",
                "bandwidth_value": 400,
                "bandwidth_unit": "Gbps",
                "latency_value": 50,
                "latency_unit": "ns propagation",
                "source": str(NS3_CASES / "c0-balanced/topology.csv"),
                "evidence": "ns3-input-config",
                "adjustable_field": "topology.csv data rate and delay",
                "can_support": "Isolation of the destination access bottleneck",
                "cannot_support": "Huawei 384-supernode fabric bandwidth",
            },
            {
                "asset": "A100",
                "scope": "requested target for future calibration",
                "location": "GPU and NIC",
                "compute_or_link": "compute/network",
                "bandwidth_value": "unmeasured in round 09",
                "bandwidth_unit": "",
                "latency_value": "unmeasured in round 09",
                "latency_unit": "",
                "source": "No round-09 A100 artifact",
                "evidence": "not-measured",
                "adjustable_field": "replace GPU profile and calibrate link/collective parameters",
                "can_support": "Defines the next calibration target",
                "cannot_support": "Any round-09 A100 performance conclusion",
            },
            {
                "asset": "Huawei 384-supernode target",
                "scope": "candidate logical mapping only",
                "location": "intra-node / intra-supernode / external fabric",
                "compute_or_link": "compute/network",
                "bandwidth_value": "TBD by official specification or measurement",
                "bandwidth_unit": "",
                "latency_value": "TBD by official specification or measurement",
                "latency_unit": "",
                "source": str(ANALYSIS / "huawei_384_mapping.csv"),
                "evidence": "design-hypothesis",
                "adjustable_field": "parallel dimensions, placement, tier bandwidth/latency",
                "can_support": "Explicit parameter slots and mapping experiments",
                "cannot_support": "A claim that current 100/400-Gbps cases reproduce Huawei hardware",
            },
        ],
    )

    write_csv(
        "trace_lineage.csv",
        [
            "level", "producer", "record", "identity_key", "direct_fields", "time_semantics",
            "evidence", "feeds_ns3", "missing_at_this_level",
        ],
        [
            {"level": 1, "producer": "dataset + router", "record": "request", "identity_key": "instance_id/request_id", "direct_fields": "arrival,input_tokens,output_tokens,model", "time_semantics": "arrival and request completion", "evidence": "scenario-input/simulator-output", "feeds_ns3": "no", "missing_at_this_level": "operator and peer traffic"},
            {"level": 2, "producer": "LLMServingSim scheduler", "record": "batch", "identity_key": "instance_id/batch_id", "direct_fields": "prefill chunk,decode tokens,request membership", "time_semantics": "scheduler iteration", "evidence": "trace-derived", "feeds_ns3": "indirect", "missing_at_this_level": "rank-pair schedule"},
            {"level": 3, "producer": "trace_generator", "record": "transformer block", "identity_key": "batch_id/block_id", "direct_fields": "operator order and dependencies", "time_semantics": "profile duration per operator", "evidence": "profile-derived/trace-derived", "feeds_ns3": "indirect", "missing_at_this_level": "collective timestamps"},
            {"level": 4, "producer": "trace_generator + Chakra", "record": "operator/collective", "identity_key": "ET node id", "direct_fields": "comm_type,logical_bytes,involved_dim,dependency", "time_semantics": "compute duration; collective completion returned by ASTRA", "evidence": "trace-derived/analytical", "feeds_ns3": "collective semantic input", "missing_at_this_level": "real NCCL/HCCL peer/channel/chunk"},
            {"level": 5, "producer": "ASTRA collective algorithm or explicit projection", "record": "collective phase", "identity_key": "collective_id/phase_id", "direct_fields": "algorithm stage and dependency", "time_semantics": "algorithm schedule", "evidence": "algorithm-projected", "feeds_ns3": "yes", "missing_at_this_level": "runtime-selected algorithm and channel"},
            {"level": 6, "producer": "collective-to-flow adapter", "record": "peer flow", "identity_key": "flow_id", "direct_fields": "src,dst,bytes,release,dependency", "time_semantics": "flow release/dependency", "evidence": "algorithm-projected", "feeds_ns3": "yes: traffic.csv", "missing_at_this_level": "packet queue outcome"},
            {"level": 7, "producer": "OpenUSim/ns-3-UB", "record": "UB task/WQE", "identity_key": "task_id/WQE id", "direct_fields": "opcode,priority,phase,start,complete", "time_semantics": "discrete-event simulated time", "evidence": "ns3-simulated", "feeds_ns3": "native", "missing_at_this_level": "real NIC firmware behavior"},
            {"level": 8, "producer": "OpenUSim/ns-3-UB", "record": "WQE segment", "identity_key": "task_id/segment", "direct_fields": "segment bytes and issue order", "time_semantics": "simulated issue/complete", "evidence": "ns3-simulated", "feeds_ns3": "native", "missing_at_this_level": "measured NIC descriptor timing"},
            {"level": 9, "producer": "OpenUSim/ns-3-UB", "record": "4-KiB packet", "identity_key": "task_id/PSN", "direct_fields": "packet bytes,send,ACK", "time_semantics": "packet timestamps", "evidence": "ns3-simulated", "feeds_ns3": "native", "missing_at_this_level": "hardware packet capture"},
            {"level": 10, "producer": "OpenUSim/ns-3-UB", "record": "hop/queue/port", "identity_key": "node_id/port_id/PSN", "direct_fields": "ingress,egress,occupancy,throughput", "time_semantics": "per-hop/queue update time", "evidence": "ns3-simulated/trace-derived", "feeds_ns3": "output", "missing_at_this_level": "real switch counters and clock alignment"},
        ],
    )

    write_csv(
        "ccl_implementation_ledger.csv",
        ["stage", "primitive", "implementation", "input_bytes_semantics", "source", "evidence", "boundary"],
        [
            {"stage": "model trace", "primitive": "TP AllReduce", "implementation": "emitted after o_proj and dense down_proj when TP>1", "input_bytes_semantics": "logical tensor bytes", "source": str(LLMSIM / "serving/core/trace_generator.py"), "evidence": "code-inspected/trace-derived", "boundary": "not an NCCL/HCCL call trace"},
            {"stage": "model trace", "primitive": "MoE dispatch AllGather", "implementation": "default vLLM allgather_reducescatter backend; hidden plus router-logit bytes per EP rank", "input_bytes_semantics": "per-rank local chunk", "source": str(LLMSIM / "serving/core/trace_generator.py") + ":1129", "evidence": "code-inspected/trace-derived", "boundary": "docstring still says AllToAll; emitted trace is authoritative for this run"},
            {"stage": "model trace", "primitive": "MoE combine ReduceScatter", "implementation": "default vLLM allgather_reducescatter backend; hidden bytes", "input_bytes_semantics": "pre-scatter total buffer", "source": str(LLMSIM / "serving/core/trace_generator.py") + ":1129", "evidence": "code-inspected/trace-derived", "boundary": "does not expose peer/channel/chunk"},
            {"stage": "execution graph", "primitive": "collective node", "implementation": "Chakra COMM_COLL_NODE with comm_type,comm_size,involved_dim and dependency", "input_bytes_semantics": "logical collective size", "source": "generated per-rank .et files", "evidence": "trace-derived", "boundary": "binary ET stores graph semantics, not packet traces"},
            {"stage": "ASTRA execution", "primitive": "AR/AG/RS/A2A", "implementation": "Workload::issue_comm dispatches to Sys::generate_*", "input_bytes_semantics": "ET comm_size", "source": str(LLMSIM / "astra-sim/astra-sim/workload/Workload.cc") + ":272", "evidence": "code-inspected", "boundary": "ASTRA schedule is a simulator model"},
            {"stage": "collective algorithm", "primitive": "AR/AG/RS/A2A", "implementation": "ring for all four primitives; localBWAware optimization", "input_bytes_semantics": "algorithm-dependent chunks", "source": str(ROUND_ROOT / "experiments/llmservingsim_input_sweep/astra_inputs/round09-i128-o4/system/system.json"), "evidence": "input-config/algorithm-projected", "boundary": "not a runtime-selected NCCL/HCCL algorithm"},
            {"stage": "packet backend", "primitive": "selected peer flow", "implementation": "explicit src,dst,bytes projected to URMA_WRITE task in ns-3-UB", "input_bytes_semantics": "peer payload bytes", "source": str(NS3_CASES / "a0-target-only/traffic.csv"), "evidence": "algorithm-projected + ns3-simulated", "boundary": "URMA_WRITE is a transport projection, not a claim about CUDA CCL transport"},
        ],
    )

    write_csv(
        "huawei_384_mapping.csv",
        [
            "candidate", "dp", "tp", "ep", "logical_rank_product", "model_experts",
            "experts_per_ep_rank", "intended_question", "astra_mapping", "ns3_mapping",
            "intra_device_or_node_bw", "intra_supernode_bw", "external_fabric_bw",
            "evidence", "boundary", "pp", "config_status",
        ],
        [
            {"candidate": "DP3-EP128-archived", "dp": 3, "tp": 1, "pp": 1, "ep": 128, "logical_rank_product": "", "model_experts": 128, "experts_per_ep_rank": 1, "intended_question": "archived independent-axis sketch, not a supported config", "astra_mapping": "REJECTED: EP128 not divisible by DP group size 3", "ns3_mapping": "requires a separately defined placement", "intra_device_or_node_bw": "TBD", "intra_supernode_bw": "TBD", "external_fabric_bw": "TBD", "evidence": "config-validator-only", "config_status": "rejected-as-direct-config", "boundary": "Do not multiply EP into physical device count"},
            {"candidate": "DP3-TP2-EP64-archived", "dp": 3, "tp": 2, "pp": 1, "ep": 64, "logical_rank_product": "", "model_experts": 128, "experts_per_ep_rank": 2, "intended_question": "archived independent-axis sketch, not a supported config", "astra_mapping": "REJECTED: EP64 not divisible by DP group size 3", "ns3_mapping": "requires a separately defined placement", "intra_device_or_node_bw": "TBD", "intra_supernode_bw": "TBD", "external_fabric_bw": "TBD", "evidence": "config-validator-only", "config_status": "rejected-as-direct-config", "boundary": "TP and EP share devices in the current frontend"},
            {"candidate": "DP64-TP2-PP3-EP128", "dp": 64, "tp": 2, "pp": 3, "ep": 128, "logical_rank_product": 384, "model_experts": 128, "experts_per_ep_rank": 1, "intended_question": "configuration-valid 384-device candidate with pipeline stages", "astra_mapping": "[2,3,64]; EP involved_dim [true,false,true]", "ns3_mapping": "map rank placement to calibrated physical tiers before generating packet cases", "intra_device_or_node_bw": "TBD", "intra_supernode_bw": "TBD", "external_fabric_bw": "TBD", "evidence": "config-validator-only/design-hypothesis", "config_status": "validated-not-executed", "boundary": "No 384-rank simulation or Huawei performance claim"},
        ],
    )

    write_csv(
        "evidence_ledger.csv",
        ["artifact", "question", "physical_quantity", "unit", "source", "evidence", "supports", "does_not_support"],
        [
            {"artifact": "llmservingsim_input_sweep.csv", "question": "How do input/output tokens affect serving latency?", "physical_quantity": "TTFT, TPOT, request latency, queue delay, collective count/bytes", "unit": "ms, count, B", "source": str(ANALYSIS / "llmservingsim_input_sweep.csv"), "evidence": "simulator-output/profile-derived/analytical/trace-derived", "supports": "Controlled request-size comparison", "does_not_support": "A100 or production SLO measurement"},
            {"artifact": "network_contention_summary.csv", "question": "How do direction, competing bytes, and destination skew affect Block-24 traffic?", "physical_quantity": "flow FCT, phase completion, throughput, queue occupancy", "unit": "us, Gbps, B", "source": str(ANALYSIS / "network_contention_summary.csv"), "evidence": "ns3-simulated/trace-derived", "supports": "Causal comparisons inside registered topologies", "does_not_support": "Unmodeled production routing or hardware latency"},
            {"artifact": "network_core_bandwidth_timeseries.csv", "question": "Who occupies each core-link direction at each microsecond?", "physical_quantity": "per-flow data-packet wire bandwidth and utilization in 0.25-us bins", "unit": "Gbps, percent, us", "source": str(ANALYSIS / "network_core_bandwidth_timeseries.csv"), "evidence": "ns3-simulated/trace-derived", "supports": "Time-resolved per-flow sharing and full-duplex direction isolation", "does_not_support": "Hardware counter bandwidth or ACK/control overhead"},
            {"artifact": "network_core_packet_events.csv", "question": "Which exact packet occupies the core link at each instant?", "physical_quantity": "per-packet core-egress start/end, serialization time, payload and wire bytes", "unit": "ns, us, B", "source": str(ANALYSIS / "network_core_packet_events.csv"), "evidence": "ns3-simulated/trace-derived", "supports": "Packet-level alternation and cumulative wire-byte reconstruction", "does_not_support": "Hardware packet capture or real NIC scheduling"},
            {"artifact": "network_core_queue_timeseries.csv", "question": "How does the core queue build and drain over time?", "physical_quantity": "VOQ plus egress queue occupancy sampled every 0.25 us and raw-trace peak", "unit": "B, KiB, us", "source": str(ANALYSIS / "network_core_queue_timeseries.csv"), "evidence": "ns3-simulated/trace-derived", "supports": "Alignment of queue growth with simulated link occupancy", "does_not_support": "Physical switch buffer counters"},
            {"artifact": "destination_access_bandwidth_timeseries.csv", "question": "How does destination skew change parallel egress bandwidth over time?", "physical_quantity": "per-destination and aggregate data-packet wire bandwidth in 0.25-us bins", "unit": "Gbps, us", "source": str(ANALYSIS / "destination_access_bandwidth_timeseries.csv"), "evidence": "ns3-simulated/trace-derived", "supports": "Time-resolved loss of destination-port parallelism", "does_not_support": "Measured MoE router distribution or one physical 400-Gbps link"},
            {"artifact": "model_inventory.csv", "question": "What model and state sizes are being simulated?", "physical_quantity": "parameter count, weight bytes, KV bytes", "unit": "count, B, GiB, MiB", "source": str(ANALYSIS / "model_inventory.csv"), "evidence": "config-derived", "supports": "Memory and traffic sizing", "does_not_support": "Observed memory residency or bandwidth"},
            {"artifact": "hardware_profile_inventory.csv", "question": "Which compute/link parameter applies at each location?", "physical_quantity": "operator duration, link bandwidth, propagation/base latency", "unit": "ns, Gbps/GB/s, ns/us", "source": str(ANALYSIS / "hardware_profile_inventory.csv"), "evidence": "profile-derived/input-config", "supports": "Parameter provenance and adjustability", "does_not_support": "Cross-platform equivalence"},
            {"artifact": "round-07 Block-24 profile", "question": "What is the deepest completed end-to-end slice?", "physical_quantity": "GPU operator time, peer-flow/task/packet/hop/queue timing", "unit": "us, ns, B", "source": "/home/lry/a100_ep_runtime/第07轮_Block24端到端collective_profile_20260926/analysis", "evidence": "profile-derived/algorithm-projected/ns3-simulated", "supports": "request-to-packet lineage at one representative middle block", "does_not_support": "real CCL channel schedule or A100 measurement"},
            {"artifact": "huawei_384_mapping.csv", "question": "How can the workload be mapped onto 384 logical NPUs?", "physical_quantity": "DP/TP/EP degree and expert ownership", "unit": "rank/expert count", "source": str(ANALYSIS / "huawei_384_mapping.csv"), "evidence": "design-hypothesis", "supports": "Candidate experiment designs", "does_not_support": "Huawei hardware performance"},
        ],
    )


if __name__ == "__main__":
    main()
