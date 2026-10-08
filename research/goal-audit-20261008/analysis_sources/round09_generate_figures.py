#!/usr/bin/env python3
"""Generate publication-style static figures for the round-09 report."""

from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "visualizations"
ROUND07 = ROOT.parent / "第07轮_Block24端到端collective_profile_20260926"

BLUE = "#2563EB"
ORANGE = "#EA580C"
GREEN = "#15803D"
PURPLE = "#7E22CE"
RED = "#DC2626"
GRAY = "#64748B"
LIGHT = "#E2E8F0"
DARK = "#0F172A"

mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["WenQuanYi Micro Hei", "DejaVu Sans"],
    "axes.unicode_minus": False,
    "svg.fonttype": "none",
    "font.size": 10,
    "axes.titlesize": 13,
    "axes.labelsize": 10,
    "figure.dpi": 150,
})


def finish(fig: plt.Figure, name: str, footer: str) -> None:
    fig.text(0.01, 0.008, footer, ha="left", va="bottom", fontsize=8, color=GRAY)
    output_path = OUT / name
    fig.savefig(output_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    if output_path.suffix == ".svg":
        svg = output_path.read_text(encoding="utf-8")
        output_path.write_text(
            "\n".join(line.rstrip() for line in svg.splitlines()) + "\n",
            encoding="utf-8",
        )


def request_size_figure() -> None:
    df = pd.read_csv(ROOT / "analysis/llmservingsim_input_sweep.csv")
    fig, axes = plt.subplots(1, 3, figsize=(12.8, 4.8))
    metrics = [
        ("ttft_ms", "TTFT", "ms"),
        ("tpot_ms", "TPOT", "ms/token"),
        ("request_latency_ms", "请求完成时间", "ms"),
    ]
    for panel, (ax, (column, title, unit)) in enumerate(zip(axes, metrics)):
        for output, color, marker in [(4, BLUE, "o"), (32, ORANGE, "s")]:
            part = df[df.output_tokens == output].sort_values("input_tokens")
            ax.plot(part.input_tokens, part[column], marker=marker, linewidth=2, color=color, label=f"输出 {output} tokens")
            if panel == 0 and output == 32:
                continue
            for x, y in zip(part.input_tokens, part[column]):
                offset = -15 if panel == 1 and output == 4 else 7
                suffix = "（两种输出相同）" if panel == 0 and x == 1024 else ""
                ax.annotate(f"{y:.1f}{suffix}", (x, y), xytext=(0, offset), textcoords="offset points", ha="center", fontsize=7.5, color=DARK)
        if panel == 0:
            ax.set_ylim(20, 220)
        elif panel == 1:
            ax.set_ylim(8.055, 8.37)
        else:
            ax.set_ylim(35, 490)
        ax.axvline(2048, color=GRAY, linestyle="--", linewidth=1)
        ymin, ymax = ax.get_ylim()
        ax.text(2048, ymin + (ymax - ymin) * 0.92, "2048-token\n调度预算", ha="center", va="top", fontsize=7.5, color=GRAY)
        ax.set_title(title)
        ax.set_xlabel("输入长度（tokens）")
        ax.set_ylabel(f"{title}（{unit}）")
        ax.grid(axis="y", color=LIGHT, linewidth=0.8)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].legend(frameon=False, loc="upper left")
    fig.suptitle("输入长度主要抬高 TTFT，输出长度主要累积请求时延", fontweight="bold")
    fig.subplots_adjust(left=0.07, right=0.985, bottom=0.22, top=0.82, wspace=0.34)
    finish(
        fig,
        "llmservingsim-input-output-sweep.svg",
        "来源: analysis/llmservingsim_input_sweep.csv | 物理量: token 数、TTFT/TPOT/请求时延(ms) | 证据: simulator-output + profile-derived + analytical",
    )


def direction_figure() -> None:
    df = pd.read_csv(ROOT / "analysis/network_contention_summary.csv").set_index("case_id")
    ids = ["a0-target-only", "a1-ep-same", "a2-ep-reverse", "a3-ep-kv"]
    labels = ["仅目标流", "同向 EP", "反向 EP", "同向 KV"]
    x = np.arange(len(ids))
    target = df.loc[ids, "target_packet_fct_us"].to_numpy()
    phase = df.loc[ids, "phase_packet_completion_us"].to_numpy()
    queues = df.loc[ids, "max_voq_plus_egress_bytes"].to_numpy() / 1024
    fig, (ax, axq) = plt.subplots(1, 2, figsize=(10.8, 4.5), gridspec_kw={"width_ratios": [1.25, 1]})
    width = 0.36
    ax.bar(x - width / 2, target, width, color=BLUE, label="目标流 FCT")
    ax.bar(x + width / 2, phase, width, color=ORANGE, label="阶段完成时间")
    for i, (a, b) in enumerate(zip(target, phase)):
        ax.text(i - width / 2, a + 1.2, f"{a:.1f}", ha="center", fontsize=8)
        ax.text(i + width / 2, b + 1.2, f"{b:.1f}", ha="center", fontsize=8)
    ax.set_xticks(x, labels)
    ax.set_ylabel("时间（µs）")
    ax.set_title("竞争方向与流类型")
    ax.legend(frameon=False)
    ax.grid(axis="y", color=LIGHT)
    ax.spines[["top", "right"]].set_visible(False)
    bars = axq.bar(x, queues, color=[GRAY, BLUE, GREEN, ORANGE])
    axq.bar_label(bars, fmt="%.0f", padding=3, fontsize=8)
    axq.set_xticks(x, labels)
    axq.set_ylabel("最大队列（KiB）")
    axq.set_title("任一观测端口的最大队列")
    axq.grid(axis="y", color=LIGHT)
    axq.spines[["top", "right"]].set_visible(False)
    fig.suptitle("同向共享瓶颈接近翻倍；反向流影响很小", fontweight="bold")
    fig.tight_layout(rect=(0, 0.08, 1, 0.93))
    finish(
        fig,
        "block24-direction-contention.svg",
        "来源: analysis/network_contention_summary.csv (a0-a3) | 物理量: packet-envelope FCT/phase time(µs)、queue bytes(KiB) | 证据: ns3-simulated/trace-derived",
    )


def byte_ratio_figure() -> None:
    df = pd.read_csv(ROOT / "analysis/network_contention_summary.csv").set_index("case_id")
    ids = ["a0-target-only", "b1-bg25", "b2-bg50", "b3-bg75", "b4-bg100"]
    ratio = np.array([0, 25, 50, 75, 100])
    fct = df.loc[ids, "target_packet_fct_us"].to_numpy()
    queue = df.loc[ids, "max_voq_plus_egress_bytes"].to_numpy() / 1024
    slope, intercept = np.polyfit(ratio, fct, 1)
    fit = slope * ratio + intercept
    r2 = 1 - np.sum((fct - fit) ** 2) / np.sum((fct - fct.mean()) ** 2)
    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    ax.plot(ratio, fct, color=BLUE, marker="o", linewidth=2.5, label="目标流 FCT")
    ax.plot(ratio, fit, color=GRAY, linestyle="--", linewidth=1.5, label=f"线性拟合 R²={r2:.4f}")
    for x, y in zip(ratio, fct):
        ax.annotate(f"{y:.2f}", (x, y), xytext=(0, 7), textcoords="offset points", ha="center", fontsize=8)
    ax.set_xlabel("同向竞争字节 / 目标流字节（%）")
    ax.set_ylabel("目标流 packet-envelope FCT（µs）")
    ax.set_xticks(ratio)
    ax.grid(color=LIGHT)
    ax.spines[["top", "right"]].set_visible(False)
    ax2 = ax.twinx()
    ax2.plot(ratio, queue, color=ORANGE, marker="s", linewidth=1.8, label="最大队列")
    ax2.set_ylabel("最大队列（KiB）", color=ORANGE)
    ax2.tick_params(axis="y", colors=ORANGE)
    lines, labels = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines + lines2, labels + labels2, frameon=False, loc="upper left")
    ax.set_title("竞争字节增加，目标流 FCT 近似线性增长", fontweight="bold")
    fig.tight_layout(rect=(0, 0.09, 1, 1))
    finish(
        fig,
        "block24-background-byte-ratio.svg",
        "来源: analysis/network_contention_summary.csv (a0,b1-b4) | 物理量: 竞争字节比(%)、目标流 FCT(µs)、最大队列(KiB) | 证据: ns3-simulated/trace-derived",
    )


def skew_figure() -> None:
    df = pd.read_csv(ROOT / "analysis/network_contention_summary.csv").set_index("case_id")
    ids = ["c0-balanced", "c1-zipf", "c2-hot"]
    labels = ["均衡\n25/25/25/25", "Zipf\n48/24/16/12", "热点\n97/1/1/1"]
    phase = df.loc[ids, "phase_packet_completion_us"].to_numpy()
    p95 = df.loc[ids, "task_fct_p95_us"].to_numpy()
    queue = df.loc[ids, "max_voq_plus_egress_bytes"].to_numpy() / 1024
    x = np.arange(3)
    fig, (ax, axq) = plt.subplots(1, 2, figsize=(9.8, 4.5))
    width = 0.36
    ax.bar(x - width / 2, phase, width, color=BLUE, label="阶段完成")
    ax.bar(x + width / 2, p95, width, color=PURPLE, label="任务 FCT P95")
    ax.set_xticks(x, labels)
    ax.set_ylabel("时间（µs）")
    ax.set_title("总字节和 task 数固定")
    ax.legend(frameon=False)
    ax.grid(axis="y", color=LIGHT)
    ax.spines[["top", "right"]].set_visible(False)
    bars = axq.bar(x, queue, color=[GREEN, ORANGE, RED])
    axq.bar_label(bars, fmt="%.0f", padding=3, fontsize=9)
    axq.set_xticks(x, labels)
    axq.set_ylabel("最大队列（KiB）")
    axq.set_title("流量越集中，目的端排队越高")
    axq.grid(axis="y", color=LIGHT)
    axq.spines[["top", "right"]].set_visible(False)
    fig.suptitle("目的端倾斜把 phase 从 23.99 µs 拉长到 88.94 µs", fontweight="bold")
    fig.tight_layout(rect=(0, 0.08, 1, 0.93))
    finish(
        fig,
        "block24-destination-skew.svg",
        "来源: analysis/network_contention_summary.csv (c0-c2) | 物理量: phase/P95 FCT(µs)、最大队列(KiB) | 证据: ns3-simulated/trace-derived",
    )


def timeline_figure() -> None:
    df = pd.read_csv(ROUND07 / "analysis/block24_unified_events.csv")
    lanes = ["GPU0", "NIC0", "GPU1", "NIC1"]
    ypos = {lane: 3 - i for i, lane in enumerate(lanes)}
    fig, (overview, zoom) = plt.subplots(2, 1, figsize=(12.5, 6.8), gridspec_kw={"height_ratios": [1, 1.2]})
    colors = {"compute": BLUE, "flow": ORANGE}
    for ax, xmin, xmax, title in [(overview, 0, 525, "完整 Block 24"), (zoom, 0, 66, "前 66 µs 放大：dense compute → AllReduce → AllGather")]:
        for _, row in df.iterrows():
            if row.resource_id not in ypos:
                continue
            left = float(row.start_us)
            width = float(row.duration_us)
            if left > xmax or left + width < xmin:
                continue
            ax.barh(ypos[row.resource_id], width, left=left, height=0.58, color=colors.get(row.event_type, GRAY), alpha=0.9)
            visible = min(left + width, xmax) - max(left, xmin)
            if visible > (25 if xmax > 100 else 3.8):
                label = row.op.replace("layernorm_", "LN").replace("attention_", "Attn ").replace("expert_", "Expert ")
                if row.event_type == "flow":
                    label = f"{row.op}\n{int(row.bytes/1024)} KiB {int(row.src_rank)}→{int(row.dst_rank)}"
                ax.text(max(left, xmin) + visible / 2, ypos[row.resource_id], label, ha="center", va="center", fontsize=7.5, color="white")
        ax.set_xlim(xmin, xmax)
        ax.set_yticks([ypos[l] for l in lanes], lanes)
        ax.set_xlabel("相对 Block 24 起点的组合时间（µs）")
        ax.set_title(title, loc="left")
        ax.grid(axis="x", color=LIGHT)
        ax.spines[["top", "right", "left"]].set_visible(False)
    overview.text(58, 3.38, "Expert compute 456.737 µs（GPU0/GPU1 并行）", color=BLUE, fontsize=9)
    zoom.axvline(36.33, color=GRAY, linestyle="--", linewidth=1)
    zoom.axvline(51.203, color=GRAY, linestyle="--", linewidth=1)
    handles = [plt.Rectangle((0, 0), 1, 1, color=BLUE), plt.Rectangle((0, 0), 1, 1, color=ORANGE)]
    overview.legend(handles, ["GPU compute（profile-derived）", "NIC flow（ns3-simulated）"], frameon=False, ncol=2, loc="upper right")
    fig.suptitle("Block 24：GPU 计算与 NIC 通信的统一事件时间线", fontweight="bold")
    fig.tight_layout(rect=(0, 0.055, 1, 0.95))
    finish(
        fig,
        "block24-gpu-nic-timeline.svg",
        "来源: round07/analysis/block24_unified_events.csv | 物理量: GPU operation / NIC task start-end(µs)、flow bytes(KiB) | 证据: profile-derived + algorithm-projected + ns3-simulated",
    )


def granularity_figure() -> None:
    items = [
        ("请求", "input/output/arrival", "simulator-output"),
        ("批次", "prefill chunk / decode", "trace-derived"),
        ("Block", "48 层中的 Block 24", "trace-derived"),
        ("算子/集合通信", "op latency / type / bytes", "profile + trace"),
        ("算法 phase", "ring stage / dependency", "algorithm-projected"),
        ("peer flow", "src → dst / payload", "algorithm-projected"),
        ("UB task / WQE", "opcode / start / complete", "ns3-simulated"),
        ("4 KiB packet", "PSN / send / ACK", "ns3-simulated"),
        ("hop / queue / port", "dwell / occupancy / throughput", "ns3-simulated"),
    ]
    fig, ax = plt.subplots(figsize=(12.6, 6.2))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 6.2)
    ax.axis("off")
    palette = [GRAY, GRAY, BLUE, BLUE, PURPLE, PURPLE, ORANGE, ORANGE, RED]
    positions = [(0.2, 3.35), (2.55, 3.35), (4.9, 3.35), (7.25, 3.35), (9.6, 3.35),
                 (9.6, 0.8), (7.25, 0.8), (4.9, 0.8), (2.55, 0.8)]
    for i, (((title, fields, evidence), color), (x, y)) in enumerate(zip(zip(items, palette), positions)):
        box = FancyBboxPatch((x, y), 2.0, 1.55, boxstyle="round,pad=0.04,rounding_size=0.08", facecolor=color, edgecolor="none", alpha=0.92)
        ax.add_patch(box)
        ax.text(x + 1.0, y + 1.19, f"{i + 1}. {title}", ha="center", va="center", color="white", fontsize=9, fontweight="bold")
        ax.text(x + 1.0, y + 0.78, fields.replace(" / ", "\n"), ha="center", va="center", color="white", fontsize=7.5)
        ax.text(x + 1.0, y + 0.22, evidence, ha="center", va="center", color="white", fontsize=7)
        if i < len(items) - 1:
            nx, ny = positions[i + 1]
            if i == 4:
                start, end = (x + 1.0, y - 0.03), (nx + 1.0, ny + 1.58)
            elif i < 4:
                start, end = (x + 2.02, y + 0.78), (nx - 0.03, ny + 0.78)
            else:
                start, end = (x - 0.03, y + 0.78), (nx + 2.02, ny + 0.78)
            ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=13, linewidth=1.2, color=DARK))
    ax.text(0.2, 5.25, "服务与模型语义", fontsize=10, color=GRAY)
    ax.text(7.25, 5.25, "collective 语义与算法投影", fontsize=10, color=PURPLE)
    ax.text(2.55, 0.38, "离散事件网络细节（读取顺序：右 → 左）", fontsize=10, color=ORANGE)
    ax.set_title("当前可下探的最细粒度：逐 packet、逐 hop、逐 queue/port", fontsize=14, fontweight="bold", pad=10)
    fig.tight_layout(rect=(0, 0.09, 1, 1))
    finish(
        fig,
        "trace-granularity-ladder.svg",
        "来源: analysis/trace_lineage.csv | 物理量: 数据粒度与关联键（方法图，无数值单位） | 证据: trace-derived + algorithm-projected + ns3-simulated",
    )


def mapping_figure() -> None:
    fig, ax = plt.subplots(figsize=(11.5, 5.2))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 6)
    ax.axis("off")
    ax.text(6, 5.55, "384 设备候选：DP64 × TP2 × PP3；EP128 与 DP/TP 共享设备", ha="center", fontsize=13, fontweight="bold")
    ax.text(6, 5.08, "当前 config_builder 校验通过；尚未执行 384-rank 仿真，也不是华为硬件配置", ha="center", fontsize=10, color=RED)
    for stage in range(3):
        x = 0.4 + stage * 3.9
        ax.add_patch(FancyBboxPatch((x, 2.95), 3.45, 1.65, boxstyle="round,pad=0.06", facecolor="#EEF2FF", edgecolor=BLUE))
        ax.text(x + 1.725, 4.21, f"PP stage {stage}：16 个模型 blocks", ha="center", fontsize=11, color=DARK)
        ax.text(x + 1.725, 3.74, "64 个 instance × 2 个 TP ranks", ha="center", fontsize=10, color=DARK)
        ax.text(x + 1.725, 3.26, "128 台逻辑设备；同一 EP128 group", ha="center", fontsize=9, color=BLUE)
        if stage < 2:
            ax.add_patch(FancyArrowPatch((x + 3.48, 3.65), (x + 3.84, 3.65), arrowstyle="-|>", mutation_scale=13, color=GRAY))
    ax.text(6, 2.47, "ASTRA dimensions = [TP2, PP3, DP64]；EP involved_dim = [true, false, true]", ha="center", fontsize=10)
    for j, label in enumerate(["设备 / 节点内", "超节点内", "超节点外"]):
        x = 0.4 + j * 3.9
        ax.add_patch(FancyBboxPatch((x, 0.85), 3.45, 1.1, boxstyle="round,pad=0.04", facecolor="white", edgecolor=LIGHT))
        ax.text(x + 1.725, 1.58, label, ha="center", fontsize=10, color=GRAY)
        ax.text(x + 1.725, 1.11, "placement、带宽、时延：待校准", ha="center", fontsize=9, color=RED)
    ax.text(6, 0.3, "原 DP3×EP128 / DP3×TP2×EP64 不可直接映射为当前配置；EP 不是额外相乘的设备维度。", ha="center", fontsize=9, color=RED)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    finish(
        fig,
        "huawei-384-logical-mapping.svg",
        "来源: LLMServingSim config_builder + 2026-10-08 verify_evidence.py | 物理量: device/rank/block count | 证据: config-validator-only / design-hypothesis；非硬件测量",
    )


def core_bandwidth_timeseries_figure() -> None:
    bandwidth = pd.read_csv(ROOT / "analysis/network_core_bandwidth_timeseries.csv")
    tasks = pd.read_csv(ROOT / "analysis/network_temporal_task_windows.csv")
    cases = [
        ("a0-target-only", "仅目标流"),
        ("a1-ep-same", "同向 EP 竞争"),
        ("a2-ep-reverse", "反向 EP（全双工）"),
        ("a3-ep-kv", "同向 KV 竞争"),
    ]
    flow_colors = {
        "target": BLUE,
        "ep_competitor": ORANGE,
        "reverse_ep": PURPLE,
        "kv_competitor": RED,
    }
    flow_labels = {
        "target": "目标 AllGather 0→4",
        "ep_competitor": "EP 1→5",
        "reverse_ep": "反向 EP 5→1",
        "kv_competitor": "KV proxy 1→5",
    }
    fig, axes = plt.subplots(4, 1, figsize=(12.4, 8.6), sharex=True, sharey=True)
    for ax, (case_id, title) in zip(axes, cases):
        part = bandwidth[bandwidth.case_id == case_id]
        x = np.sort(part.bin_center_us.unique())
        positive_base = np.zeros(len(x))
        for flow_kind in part.flow_kind.drop_duplicates():
            flow = part[part.flow_kind == flow_kind].set_index("bin_center_us").reindex(x, fill_value=0)
            values = flow.bandwidth_gbps.to_numpy()
            direction = flow.direction.iloc[0]
            color = flow_colors[flow_kind]
            if direction == "L2R":
                ax.fill_between(x, positive_base, positive_base + values, step="mid", color=color, alpha=0.88, label=flow_labels[flow_kind])
                positive_base += values
            else:
                ax.fill_between(x, -values, 0, step="mid", color=color, alpha=0.88, label=flow_labels[flow_kind])
        task_part = tasks[tasks.case_id == case_id]
        target_ack = float(task_part[task_part.flow_kind == "target"].last_ack_us.iloc[0])
        phase_ack = float(task_part.last_ack_us.max())
        ax.axvline(target_ack, color=BLUE, linestyle="--", linewidth=1.2)
        ax.text(target_ack + 0.5, 72, f"目标 ACK {target_ack:.2f} µs", color=BLUE, fontsize=8, va="center")
        if phase_ack - target_ack > 2:
            ax.axvline(phase_ack, color=RED, linestyle=":", linewidth=1.2)
            ax.text(phase_ack + 0.5, 43, f"phase ACK {phase_ack:.2f} µs", color=RED, fontsize=8, va="center")
        ax.axhline(100, color=GRAY, linestyle="--", linewidth=0.8)
        ax.axhline(-100, color=GRAY, linestyle="--", linewidth=0.8)
        ax.axhline(0, color=DARK, linewidth=0.8)
        ax.set_ylim(-110, 110)
        ax.set_xlim(0, 70)
        ax.set_title(title, loc="left", fontsize=11)
        ax.grid(axis="x", color=LIGHT, linewidth=0.7)
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(frameon=False, loc="upper right", ncol=2, fontsize=8)
    axes[1].set_ylabel("核心链路数据带宽（Gbps）\n上：L→R；下：R→L")
    axes[-1].set_xlabel("仿真时间（µs）；时间分箱 0.25 µs")
    fig.suptitle("Block 24 竞争：100 Gbps 核心链路的微秒级占用", fontweight="bold")
    fig.tight_layout(rect=(0, 0.065, 1, 0.955))
    finish(
        fig,
        "block24-core-bandwidth-timeseries.svg",
        "来源: ns-3 AllPacketTrace + PortTrace，analysis/network_core_bandwidth_timeseries.csv | 物理量: data-packet wire bandwidth(Gbps), time(µs), 0.25-µs bins | 证据: ns3-simulated/trace-derived；不含 ACK/control 带宽",
    )


def ep_kv_microsecond_profile_figure() -> None:
    bandwidth = pd.read_csv(ROOT / "analysis/network_core_bandwidth_timeseries.csv")
    queue = pd.read_csv(ROOT / "analysis/network_core_queue_timeseries.csv")
    tasks = pd.read_csv(ROOT / "analysis/network_temporal_task_windows.csv")
    packets = pd.read_csv(ROOT / "analysis/network_core_packet_events.csv")
    part = bandwidth[bandwidth.case_id == "a3-ep-kv"]
    x = np.sort(part.bin_center_us.unique())
    target = part[part.flow_kind == "target"].set_index("bin_center_us").reindex(x, fill_value=0).bandwidth_gbps.to_numpy()
    kv = part[part.flow_kind == "kv_competitor"].set_index("bin_center_us").reindex(x, fill_value=0).bandwidth_gbps.to_numpy()
    q = queue[queue.case_id == "a3-ep-kv"].sort_values("bin_center_us")
    task_part = tasks[tasks.case_id == "a3-ep-kv"].set_index("flow_kind")
    packet_part = packets[packets.case_id == "a3-ep-kv"].sort_values("packet_start_us")
    target_packets = packet_part[packet_part.flow_kind == "target"]
    kv_packets = packet_part[packet_part.flow_kind == "kv_competitor"]
    target_ack = float(task_part.loc["target", "last_ack_us"])
    kv_ack = float(task_part.loc["kv_competitor", "last_ack_us"])
    target_last_egress = float(target_packets.packet_end_us.max())
    kv_last_egress = float(kv_packets.packet_end_us.max())
    max_time = float(q.raw_trace_max_time_us.iloc[0])
    max_queue = float(q.raw_trace_max_queue_kib.iloc[0])

    fig, axes = plt.subplots(
        5,
        1,
        figsize=(13.2, 11.2),
        sharex=True,
        gridspec_kw={"height_ratios": [0.9, 1.35, 1.05, 1.0, 0.82]},
    )
    fig.suptitle(
        "Block 24 同向 EP + KV：从逐包调度到阶段完成的微秒级事件显微镜",
        fontsize=14,
        fontweight="bold",
        y=0.995,
    )

    # Panel 1: exact per-packet serialization intervals on the 100-Gbps core link.
    axp = axes[0]
    for packet_frame, y, color, label in [
        (target_packets, 1, BLUE, "目标 AllGather 0→4"),
        (kv_packets, 0, RED, "KV proxy 1→5"),
    ]:
        intervals = list(zip(packet_frame.packet_start_us, packet_frame.serialization_us))
        axp.broken_barh(intervals, (y - 0.31, 0.62), facecolors=color, edgecolors="none", alpha=0.9, label=label)
    axp.set_yticks([1, 0], ["目标包", "KV 包"])
    axp.set_ylim(-0.65, 1.65)
    axp.set_title("① 核心端口逐包序列化（每个满包 4,174 B，占用 333.92 ns）", loc="left", fontsize=10.5)
    axp.text(
        target_last_egress / 2,
        1.48,
        f"共享阶段：两条流逐包交替，目标共 {len(target_packets)} 包",
        ha="center",
        color=DARK,
        fontsize=8.5,
    )
    axp.text(
        (target_last_egress + kv_last_egress) / 2,
        0.48,
        f"目标退出后 KV 单独排空，KV 共 {len(kv_packets)} 包",
        ha="center",
        color=RED,
        fontsize=8.5,
    )
    axp.legend(frameon=False, ncol=2, loc="lower right", fontsize=8)
    axp.spines[["top", "right", "left"]].set_visible(False)

    # Panel 2: 0.25-us binned data bandwidth by flow.
    ax = axes[1]
    ax.fill_between(x, 0, target, step="mid", color=BLUE, alpha=0.9, label="目标 AllGather 0→4，272 KiB")
    ax.fill_between(x, target, target + kv, step="mid", color=RED, alpha=0.82, label="KV proxy 1→5，512 KiB")
    ax.axhline(100, color=GRAY, linestyle="--", linewidth=1, label="核心容量 100 Gbps")
    ax.set_ylabel("数据带宽（Gbps）")
    ax.set_ylim(0, 110)
    ax.legend(frameon=False, ncol=3, loc="upper right")
    ax.set_title("② 0.25-µs 时间片内的 wire bandwidth", loc="left", fontsize=10.5)
    ax.grid(axis="x", color=LIGHT)
    ax.spines[["top", "right"]].set_visible(False)

    # Panel 3: queue occupancy from the exact queue trace.
    axq = axes[2]
    axq.fill_between(q.bin_center_us, 0, q.queue_kib, step="mid", color=ORANGE, alpha=0.72)
    axq.plot(max_time, max_queue, "o", color=RED)
    axq.annotate(
        f"原始 trace 峰值 {max_queue:.1f} KiB @ {max_time:.4f} µs",
        (max_time, max_queue),
        xytext=(9, -18),
        textcoords="offset points",
        color=RED,
        fontsize=8.5,
    )
    axq.set_ylabel("核心端口队列（KiB）")
    axq.set_title("③ switch 8 / port 4：VOQ + egress 队列形成与排空", loc="left", fontsize=10.5)
    axq.grid(color=LIGHT)
    axq.spines[["top", "right"]].set_visible(False)

    # Panel 4: cumulative on-wire bytes, reconstructed from packet completions.
    axc = axes[3]
    for packet_frame, color, label in [
        (target_packets, BLUE, "目标累计 wire bytes"),
        (kv_packets, RED, "KV 累计 wire bytes"),
    ]:
        cumulative = packet_frame.wire_bytes.cumsum().to_numpy() / 1024.0
        step_x = np.r_[0.0, packet_frame.packet_end_us.to_numpy()]
        step_y = np.r_[0.0, cumulative]
        axc.step(step_x, step_y, where="post", color=color, linewidth=1.8, label=label)
        axc.text(
            float(packet_frame.packet_end_us.max()) + 0.45,
            float(cumulative[-1]),
            f"{cumulative[-1]:.1f} KiB wire",
            va="center",
            color=color,
            fontsize=8.5,
        )
    axc.set_ylabel("累计发送（KiB）")
    axc.set_title("④ 每条流通过核心链路的累计 wire bytes", loc="left", fontsize=10.5)
    axc.legend(frameon=False, ncol=2, loc="upper left", fontsize=8)
    axc.grid(axis="x", color=LIGHT)
    axc.spines[["top", "right"]].set_visible(False)

    # Panel 5: end-to-end task window, split into data completion and ACK tail.
    axw = axes[4]
    windows = [("目标 AllGather", "target", BLUE, 1), ("KV proxy", "kv_competitor", RED, 0)]
    for label, flow, color, y in windows:
        row = task_part.loc[flow]
        axw.barh(
            y,
            row.task_complete_us - row.first_packet_us,
            left=row.first_packet_us,
            height=0.46,
            color=color,
            alpha=0.88,
        )
        axw.barh(
            y,
            row.last_ack_us - row.task_complete_us,
            left=row.task_complete_us,
            height=0.46,
            facecolor="white",
            edgecolor=color,
            hatch="////",
            linewidth=0.9,
        )
        axw.text(
            row.last_ack_us + 0.5,
            y,
            f"complete {row.task_complete_us:.2f} / ACK {row.last_ack_us:.2f} µs",
            va="center",
            color=color,
            fontsize=8.5,
        )
    axw.set_yticks([1, 0], ["目标 AllGather", "KV proxy"])
    axw.set_title("⑤ task 生命周期：实色为数据完成，斜线为最后 ACK 返回", loc="left", fontsize=10.5)
    axw.set_xlabel("仿真时间（µs）；带宽与队列显示分箱 0.25 µs，逐包事件保留 ns 时间戳")
    axw.set_xlim(0, 70)
    axw.grid(axis="x", color=LIGHT)
    axw.spines[["top", "right", "left"]].set_visible(False)

    for axis in axes:
        axis.axvspan(target_last_egress, kv_last_egress, color=RED, alpha=0.035, zorder=0)
        axis.axvline(target_ack, color=BLUE, linestyle="--", linewidth=1)
        axis.axvline(kv_ack, color=RED, linestyle=":", linewidth=1)
    for axis in axes[:3]:
        axis.axvline(max_time, color=ORANGE, linestyle="-.", linewidth=0.9)
    axes[1].text(target_ack + 0.35, 17, f"目标 ACK\n{target_ack:.2f} µs", color=BLUE, fontsize=8)
    axes[1].text(kv_ack - 0.35, 17, f"phase ACK\n{kv_ack:.2f} µs", color=RED, fontsize=8, ha="right")
    fig.tight_layout(rect=(0, 0.045, 1, 0.978), h_pad=0.72)
    finish(
        fig,
        "block24-ep-kv-microsecond-profile.svg",
        "来源: ns-3 AllPacketTrace/PortTrace/QueueTrace/task_statistics，analysis/network_core_packet_events.csv 与 network_*_timeseries.csv | 物理量: packet serialization(ns), bandwidth(Gbps), queue/cumulative bytes(KiB), task/ACK time(µs) | 证据: ns3-simulated/trace-derived；100 Gbps代表性拓扑",
    )


def destination_bandwidth_timeseries_figure() -> None:
    bandwidth = pd.read_csv(ROOT / "analysis/destination_access_bandwidth_timeseries.csv")
    summary = pd.read_csv(ROOT / "analysis/network_contention_summary.csv").set_index("case_id")
    cases = [
        ("c0-balanced", "均衡 25/25/25/25"),
        ("c1-zipf", "Zipf 48/24/16/12"),
        ("c2-hot", "热点 97/1/1/1"),
    ]
    destinations = ["dst4", "dst5", "dst6", "dst7"]
    colors = [BLUE, ORANGE, GREEN, PURPLE]
    fig, axes = plt.subplots(3, 1, figsize=(12.4, 7.9), sharex=True, sharey=True)
    for ax, (case_id, title) in zip(axes, cases):
        part = bandwidth[bandwidth.case_id == case_id]
        x = np.sort(part.bin_center_us.unique())
        values = [
            part[part.destination == destination].set_index("bin_center_us").reindex(x, fill_value=0).bandwidth_gbps.to_numpy()
            for destination in destinations
        ]
        ax.stackplot(x, values, colors=colors, alpha=0.82, labels=destinations, step="mid")
        total = np.sum(values, axis=0)
        ax.plot(x, total, color=DARK, linewidth=1.25, label="四端口总带宽")
        phase = float(summary.loc[case_id, "phase_packet_completion_us"])
        active = x <= phase
        mean_total = float(np.mean(total[active]))
        ax.axvline(phase, color=RED, linestyle="--", linewidth=1)
        ax.text(phase + 1.0, 333, f"phase {phase:.2f} µs", color=RED, fontsize=8)
        ax.text(61, 175, f"phase 内平均总带宽 {mean_total:.1f} Gbps\n≈ {mean_total/100:.2f} 个并行目的端口", color=DARK, fontsize=9)
        ax.set_title(title, loc="left", fontsize=11)
        ax.set_ylim(0, 420)
        ax.set_xlim(0, 95)
        ax.grid(axis="x", color=LIGHT)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].legend(frameon=False, ncol=5, loc="upper right", fontsize=8)
    axes[1].set_ylabel("四个 100 Gbps 目的端 access ports 的数据带宽（Gbps）")
    axes[-1].set_xlabel("仿真时间（µs）；时间分箱 0.25 µs")
    fig.suptitle("相同总字节：目的端越集中，可并行利用的出口带宽越少", fontweight="bold")
    fig.tight_layout(rect=(0, 0.065, 1, 0.955))
    finish(
        fig,
        "destination-access-bandwidth-timeseries.svg",
        "来源: ns-3 destination PortTrace + packet task mapping，analysis/destination_access_bandwidth_timeseries.csv | 物理量: per-destination/aggregate bandwidth(Gbps), time(µs), 0.25-µs bins | 证据: ns3-simulated/trace-derived",
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    request_size_figure()
    direction_figure()
    byte_ratio_figure()
    skew_figure()
    timeline_figure()
    granularity_figure()
    mapping_figure()
    core_bandwidth_timeseries_figure()
    ep_kv_microsecond_profile_figure()
    destination_bandwidth_timeseries_figure()
    print("\n".join(str(path) for path in sorted(OUT.glob("*.svg"))))


if __name__ == "__main__":
    main()
