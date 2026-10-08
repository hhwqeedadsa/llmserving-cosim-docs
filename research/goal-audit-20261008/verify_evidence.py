#!/usr/bin/env python3
"""Recheck archived evidence against original inputs/outputs, without running simulators.

Requires pandas, numpy, PyYAML and the dependencies of LLMServingSim config_builder.
This is an evidence audit, not a hardware-accuracy or 384-rank execution test.
"""

import argparse
import collections
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import sys

import pandas as pd
import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True, help="a100_ep_runtime root")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    here = Path(__file__).resolve().parent
    site = here.parents[1]
    llm = workspace / "第04轮_仿真平台验证_20260917/LLMServingSim/repo"
    ns3 = workspace / "第05轮_联合仿真M0_20260919/ns-3-ub/repo"
    round09 = workspace / "第09轮_模型硬件拓扑trace与网络竞争_20260929"
    round10 = workspace / "第10轮_长时间网络竞争流量_20260930"
    inputs = round09 / "experiments/llmservingsim_input_sweep"
    sources = {}
    findings = {}

    def record(path):
        path = Path(path).resolve()
        root = workspace if path.is_relative_to(workspace) else site
        key = ("workspace/" if root == workspace else "site/") + str(path.relative_to(root))
        sources[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        return path

    def frame(path):
        return pd.read_csv(record(path))

    def read(path):
        return record(path).read_text(encoding="utf-8")

    def near(a, b, tolerance=1e-6):
        assert math.isclose(float(a), float(b), rel_tol=0, abs_tol=tolerance), (a, b)

    model = json.loads(read(llm / "configs/model/Qwen/Qwen3-30B-A3B-Instruct-2507.json"))
    h, layers, experts = model["hidden_size"], model["num_hidden_layers"], model["num_experts"]
    q, kv = model["num_attention_heads"] * model["head_dim"], model["num_key_value_heads"] * model["head_dim"]
    one_expert = 3 * h * model["moe_intermediate_size"]
    shared = 2 * model["vocab_size"] * h + h + layers * (h * (q + 2 * kv) + q * h + 2 * h + 2 * model["head_dim"] + h * experts)
    total = shared + layers * experts * one_expert
    active_convention = shared + layers * model["num_experts_per_tok"] * one_expert
    kv_per_token = layers * 2 * kv * 2
    inventory = frame(site / "research/round09/model_inventory.csv").iloc[0]
    assert total == int(inventory.total_parameters)
    assert active_convention == int(inventory.active_parameters_per_token)
    assert kv_per_token == int(inventory.kv_bytes_per_token)
    findings["model"] = dict(total_parameters=total, active_parameters_shared_plus_topk=active_convention,
                             bf16_weight_gib=total * 2 / 2**30, kv_bytes_per_token=kv_per_token,
                             caveat="Full embedding/head tables counted in shared parameters; not actual per-token FLOPs or measured memory.")

    meta = yaml.safe_load(read(llm / "profiler/perf/RTXPRO6000/Qwen/Qwen3-30B-A3B-Instruct-2507/bf16/meta.yaml"))
    assert meta["engine_effective"]["tensor_parallel_size"] == 1
    assert meta["engine_effective"]["load_format"] == "dummy"
    assert meta["tp_degrees"] == [1, 2]
    findings["gpu_profile"] = dict(gpu=meta["gpu"], profiled_at=meta["profiled_at"],
                                   profiler_engine_tp=1, available_tp_profiles=[1, 2],
                                   evidence="bundled-profile metadata, not a new A100 measurement")

    sweep = frame(site / "research/round09/llmservingsim_input_sweep.csv")
    assert {(int(r.input_tokens), int(r.output_tokens)) for r in sweep.itertuples()} == {(i,o) for i in [128,1024,4096] for o in [4,32]}
    for row in sweep.itertuples():
        name = f"input{row.input_tokens}_output{row.output_tokens}"
        request = json.loads(read(inputs / (name + ".jsonl")).strip())
        assert request["input_toks"] == row.input_tokens and request["output_toks"] == row.output_tokens
        result = frame(inputs / "results" / (name + ".csv")).iloc[0]
        assert int(result["input"]) == row.input_tokens and int(result["output"]) == row.output_tokens
        for original, derived in [("TTFT", "ttft_ms"), ("TPOT", "tpot_ms"), ("latency", "request_latency_ms"), ("queuing_delay", "queuing_delay_ms")]:
            near(result[original] / 1e6, getattr(row, derived))
        run = inputs / "astra_inputs" / row.run_id
        text_traces = sorted((run / "trace").rglob("instance*_batch*.txt"))
        assert len(text_traces) == row.batch_trace_count
        counts = collections.Counter()
        sizes = collections.Counter()
        for path in text_traces:
            for line in read(path).splitlines()[3:]:
                cols = line.split()
                if not cols:
                    continue
                primitive, size = (cols[2], int(cols[3])) if cols[0] == "EXPERT" else (cols[8], int(cols[9]))
                primitive = primitive.split(":")[0]
                if primitive != "NONE":
                    counts[primitive] += 1
                    sizes[primitive] += size
        assert set(counts) == {"ALLREDUCE", "ALLGATHER", "REDUCESCATTER"}
        for primitive, count in counts.items():
            assert count == getattr(row, primitive.lower() + "_count")
            assert sizes[primitive] == getattr(row, primitive.lower() + "_logical_bytes")
        et_files = sorted(run.rglob("*.et"))
        assert len(et_files) >= 2 * len(text_traces)
        for path in et_files:
            assert record(path).stat().st_size > 0
        network = yaml.safe_load(read(run / "network/network.yml"))
        assert network == dict(topology=["FullyConnected"], npus_count=[2], bandwidth=[16.0], latency=[20000.0])
        system = json.loads(read(run / "system/system.json"))
        for key in ["all-reduce", "all-gather", "reduce-scatter", "all-to-all"]:
            assert system[key + "-implementation"] == ["ring"]
        assert system["collective-optimization"] == "localBWAware"
    findings["request_sweep"] = dict(cases=len(sweep), text_traces=int(sweep.batch_trace_count.sum()),
                                     result="All original request outputs, collective counts/bytes and saved configs agree with the published ledger")

    for relative in ["serving/core/config_builder.py", "serving/core/trace_generator.py", "serving/core/graph_generator.py",
                     "profiler/core/engine.py", "astra-sim/astra-sim/workload/Workload.cc",
                     "astra-sim/extern/graph_frontend/chakra/src/converter/llm_converter.py"]:
        record(llm / relative)
    sys.path.insert(0, str(llm))
    from serving.core.config_builder import _resolve_parallelism, _resolve_dp_groups, _compute_network_dims, _normalize_network_dim_values
    instances = [dict(tp_size=2, pp_size=3, ep_size=128, dp_group="candidate384") for _ in range(64)]
    for instance in instances:
        _resolve_parallelism(instance, model)
    _resolve_dp_groups(instances)
    dims = _compute_network_dims(instances)
    assert dims == [2, 3, 64] and sum(i["num_npus"] for i in instances) == 384
    assert instances[0]["ep_dim"] == [True, False, True]
    assert _normalize_network_dim_values([50, 12.5, 25], 3, "link_bw") == [50, 12.5, 25]
    assert _normalize_network_dim_values(16, 3, "link_bw") == [16, 16, 16]
    try:
        _normalize_network_dim_values([50, 25], 3, "link_bw")
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid dimension count should fail")
    rejected = []
    for tp, ep in [(1, 128), (2, 64)]:
        old = [dict(tp_size=tp, pp_size=1, ep_size=ep, dp_group="old") for _ in range(3)]
        try:
            for instance in old:
                _resolve_parallelism(instance, model)
            _resolve_dp_groups(old)
        except ValueError as exc:
            rejected.append(str(exc))
        else:
            raise AssertionError("Independent-axis shorthand is not a supported config")
    findings["mapping_and_bandwidth"] = dict(validated_config_dims=dims, logical_devices=384, ep_per_stage=128,
        status="configuration validation only; no 384-rank execution or NPU performance claim",
        old_direct_config_rejections=rejected,
        bandwidth_examples="[50,12.5,25] GB/s are arbitrary API test values, NOT Huawei specifications")

    package = ns3 / "scratch/20260929-block24-network-contention"
    matrix = yaml.safe_load(read(package / "matrix.yaml"))
    for path in ["experiment-plan.md", "run-ledger.md", "analysis_classification.csv"]:
        record(package / path)
    summary = frame(site / "research/round09/network_contention_summary.csv").set_index("case_id")
    assert len(summary) == len(matrix) == 11
    for case in matrix:
        case_id = case["case_id"]
        directory = package / "cases" / case_id
        read(directory / "experiment-spec.md")
        traffic = frame(directory / "traffic.csv")
        tasks = frame(directory / "output/task_statistics.csv")
        topology = frame(directory / "topology.csv")
        record(directory / "routing_table.csv")
        record(directory / "network_attribute.txt")
        row = summary.loc[case_id]
        assert len(tasks) == len(traffic) == row.completed_tasks == row.task_count
        assert int(tasks["dataSize(Byte)"].sum()) == int(traffic["dataSize(Byte)"].sum()) == row.payload_bytes
        assert (tasks["taskCompletesTime(us)"] > tasks["taskStartTime(us)"]).all()
        target = tasks[tasks.taskId == 0].iloc[0]
        near(target["lastPacketACKs(us)"] - target["firstPacketSends(us)"], row.target_packet_fct_us)
        near(tasks["lastPacketACKs(us)"].max() - tasks["firstPacketSends(us)"].min(), row.phase_packet_completion_us)
        expected_access, expected_core = ("100Gbps", "400Gbps") if case_id.startswith("c") else ("400Gbps", "100Gbps")
        assert list(topology.bandwidth) == [expected_access] * 8 + [expected_core]
        control = package / "cases" / ("c0-balanced" if case_id.startswith("c") else "a0-target-only")
        for name in ["node.csv", "topology.csv", "routing_table.csv", "network_attribute.txt"]:
            assert record(directory / name).read_bytes() == record(control / name).read_bytes(), (case_id, name)
        if case_id.startswith("c"):
            assert len(traffic) == 16
            assert traffic.groupby("sourceNode")["dataSize(Byte)"].sum().tolist() == [278528] * 4
        queue_max = 0
        for path in sorted((directory / "runlog").glob("QueueTrace_node_*_port_*.tr")):
            values = [int(value) for value in re.findall(r"totalBytes:\s*(\d+)", read(path))]
            queue_max = max(queue_max, max(values, default=0))
        assert queue_max == row.max_voq_plus_egress_bytes, (case_id, queue_max, row.max_voq_plus_egress_bytes)
    assert summary.loc["a1-ep-same", "target_packet_fct_us"] > summary.loc["a2-ep-reverse", "target_packet_fct_us"] > summary.loc["a0-target-only", "target_packet_fct_us"]
    monotone = summary.loc[["a0-target-only", "b1-bg25", "b2-bg50", "b3-bg75", "b4-bg100"], "target_packet_fct_us"]
    assert monotone.is_monotonic_increasing
    assert summary.loc[["c0-balanced", "c1-zipf", "c2-hot"], "phase_packet_completion_us"].is_monotonic_increasing
    assert summary.loc[["c0-balanced", "c1-zipf", "c2-hot"], "max_voq_plus_egress_bytes"].is_monotonic_increasing
    assert summary.loc["a3-ep-kv", "phase_packet_completion_us"] > summary.loc["a1-ep-same", "phase_packet_completion_us"]
    findings["contention"] = dict(cases=11, completed_tasks=int(summary.completed_tasks.sum()),
        prediction_status="All registered direction, byte-ratio and hotspot predictions matched",
        same_direction_fct_us=float(summary.loc["a1-ep-same", "target_packet_fct_us"]),
        hotspot_phase_ratio=float(summary.loc["c2-hot", "phase_packet_completion_us"] / summary.loc["c0-balanced", "phase_packet_completion_us"]))

    block = workspace / "第07轮_Block24端到端collective_profile_20260926"
    manifest = frame(block / "case_snapshot/flow_manifest.csv")
    packet_profile = frame(block / "analysis/block24_packet_profile.csv")
    block_summary = json.loads(read(block / "analysis/block24_summary.json"))
    assert len(manifest) == 8 and manifest.payload_bytes.sum() == 2129920
    assert len(packet_profile) == block_summary["selected_task"]["data_packets"] == 68
    findings["finest_granularity"] = dict(selected_peer_payload_bytes=278528, selected_peer_data_packets=68,
        selected_peer_wqe_segments=block_summary["selected_task"]["wqe_segments"],
        boundary="Packet/hop/queue events are modeled; CCL channel/chunk schedule is not hardware-measured")
    parser_source = workspace / "第06轮_SimAI与LLMServingSim_trace分析_20260924/tools/parse_llmservingsim_trace.py"
    read(parser_source)
    trace = inputs / "astra_inputs/round09-i128-o4/trace/RTXPRO6000/Qwen/Qwen3-30B-A3B-Instruct-2507/instance0_batch0.txt"
    lines = read(trace).splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("layernorm_289 "))
    assert sum(line.startswith("o_proj_") for line in lines[:index]) == 24
    assert (289 - 1) // 12 == 24
    findings["block_identity"] = dict(zero_based_block=24, ordinal_block=25, batch=0, request=0,
                                      first_operation="layernorm_289", compute_evidence="profile-derived")

    packets = frame(round10 / "analysis/long_window_packet_events.csv")
    bw = frame(round10 / "analysis/long_window_bandwidth_timeseries.csv")
    waves = frame(round10 / "analysis/long_window_wave_summary.csv")
    tasks = frame(round10 / "analysis/long_window_task_windows.csv")
    assert len(packets) == 1302 and len(tasks) == 20 and len(waves) == 10
    assert bw.bin_end_us.max() == 1100
    near(bw.wire_bytes_in_bin.sum(), packets.wire_bytes.sum(), 0.001)
    assert bw.groupby(["direction", "bin_start_us"]).bandwidth_gbps.sum().max() <= 100.000001
    for previous, following in zip(waves.itertuples(), list(waves.itertuples())[1:]):
        quiet = bw[(bw.bin_start_us >= previous.phase_last_ack_us) & (bw.bin_end_us <= following.release_us)]
        assert len(quiet) and quiet.bandwidth_gbps.max() == 0
    for row in tasks.itertuples():
        selected = packets[packets.task_id == row.task_id]
        assert selected.payload_bytes.sum() == row.payload_bytes
    zoom = frame(round10 / "analysis/zoom_w1_w2_bandwidth_timeseries.csv")
    assert zoom.bin_start_us.min() == 100 and zoom.bin_end_us.max() == 280
    assert zoom[zoom.bin_start_us >= 260].bandwidth_gbps.max() == 0
    findings["long_timeline"] = dict(window_us=1100, waves=10, inter_wave_idle_gaps=9, tasks=20, packets=1302,
        conserved_wire_bytes=int(packets.wire_bytes.sum()), zoom_export_window_us=[100, 280],
        evidence="synthetic release schedule + ns3 packet traces; gaps are not observed GPU compute")
    for filename in ["analyze_long_window.py", "test_long_window.py", "generate_figures.py"]:
        record(round10 / "analysis" / filename)
    raw_case = ns3 / "scratch/20260930-long-window-contention/case"
    record(raw_case / "output/task_statistics.csv")
    record(raw_case / "traffic.csv")
    for pattern in ["AllPacketTrace_PKT_node_*.tr", "PortTrace_node_*_port_4.tr", "QueueTrace_node_*_port_4.tr"]:
        for path in sorted((raw_case / "runlog").glob(pattern)):
            record(path)
    for number, filename in [("round10", "long-window-network-traffic-waves.svg"), ("round10", "zoomed-w1-w2-contention-peaks.svg"), ("round09", "huawei-384-logical-mapping.svg")]:
        record(site / "source/_static" / number / filename)
    for filename in ["long_window_packet_events.csv", "long_window_bandwidth_timeseries.csv", "long_window_queue_timeseries.csv",
                     "long_window_task_windows.csv", "long_window_wave_summary.csv", "zoom_w1_w2_bandwidth_timeseries.csv", "zoom_w1_w2_queue_timeseries.csv"]:
        assert record(round10 / "analysis" / filename).read_bytes() == record(site / "research/round10" / filename).read_bytes()

    output = dict(audit_date="2026-10-08", scope="Archived experiment evidence and current code; no new simulation or hardware run", findings=findings)
    (here / "audit.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    with (here / "sources.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["source_path", "sha256"])
        writer.writerows(sorted(sources.items()))
    print(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"PASS: {len(sources)} source artifacts fingerprinted")


if __name__ == "__main__":
    main()
