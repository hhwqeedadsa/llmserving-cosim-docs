#!/usr/bin/env python3
"""Generate the long-window contention topology and traffic figures."""

from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "visualizations"

BLUE = "#2563EB"
ORANGE = "#EA580C"
GREEN = "#15803D"
PURPLE = "#7E22CE"
RED = "#DC2626"
TEAL = "#0F766E"
GRAY = "#64748B"
LIGHT = "#E2E8F0"
DARK = "#0F172A"

mpl.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["WenQuanYi Micro Hei", "DejaVu Sans"],
        "axes.unicode_minus": False,
        "svg.fonttype": "none",
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.labelsize": 10,
        "figure.dpi": 150,
    }
)


def finish(fig: plt.Figure, name: str, footer: str) -> None:
    fig.text(0.01, 0.008, footer, ha="left", va="bottom", fontsize=8, color=GRAY)
    output_path = OUT / name
    fig.savefig(output_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    svg = output_path.read_text(encoding="utf-8")
    output_path.write_text(
        "\n".join(line.rstrip() for line in svg.splitlines()) + "\n",
        encoding="utf-8",
    )


def competition_topology_figure() -> None:
    fig, ax = plt.subplots(figsize=(12.6, 5.7))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 7)
    ax.axis("off")

    left_hosts = [(1.0, 5.6, "GPU/NIC 0\n目标"), (1.0, 4.1, "GPU/NIC 1\nEP / KV"), (1.0, 2.6, "GPU/NIC 2"), (1.0, 1.1, "GPU/NIC 3")]
    right_hosts = [(12.0, 5.6, "GPU/NIC 4"), (12.0, 4.1, "GPU/NIC 5"), (12.0, 2.6, "GPU/NIC 6"), (12.0, 1.1, "GPU/NIC 7")]
    for x, y, label in left_hosts + right_hosts:
        ax.add_patch(
            FancyBboxPatch(
                (x, y),
                1.25,
                0.72,
                boxstyle="round,pad=0.04",
                facecolor="#F8FAFC",
                edgecolor=LIGHT,
                linewidth=1.2,
            )
        )
        ax.text(x + 0.625, y + 0.36, label, ha="center", va="center", fontsize=8.5, color=DARK)

    switches = [(4.25, 3.15, "switch 8"), (8.65, 3.15, "switch 9")]
    for x, y, label in switches:
        ax.add_patch(
            FancyBboxPatch(
                (x, y),
                1.25,
                1.05,
                boxstyle="round,pad=0.06",
                facecolor="#E0E7FF",
                edgecolor=BLUE,
                linewidth=1.4,
            )
        )
        ax.text(x + 0.625, y + 0.52, label, ha="center", va="center", fontsize=11, color=DARK)

    for _, y, _ in left_hosts:
        ax.plot([2.25, 4.25], [y + 0.36, 3.68], color=GRAY, linewidth=1.2, alpha=0.75)
    for _, y, _ in right_hosts:
        ax.plot([9.9, 12.0], [3.68, y + 0.36], color=GRAY, linewidth=1.2, alpha=0.75)
    ax.text(3.05, 5.55, "每条 access 400 Gbps", color=GRAY, fontsize=9, ha="center")
    ax.text(10.95, 5.55, "每条 access 400 Gbps", color=GRAY, fontsize=9, ha="center")

    # Highlight the three representative routes on top of the physical links.
    ax.add_patch(FancyArrowPatch((2.22, 5.93), (4.28, 3.83), arrowstyle="-|>", mutation_scale=12, linewidth=2.6, color=BLUE))
    ax.add_patch(FancyArrowPatch((2.22, 4.43), (4.28, 3.64), arrowstyle="-|>", mutation_scale=12, linewidth=2.6, color=RED))
    ax.add_patch(FancyArrowPatch((11.98, 4.43), (9.87, 3.48), arrowstyle="-|>", mutation_scale=12, linewidth=2.4, color=PURPLE))
    ax.plot([5.5, 8.65], [3.65, 3.65], color=LIGHT, linewidth=12, solid_capstyle="butt", zorder=0)
    ax.add_patch(
        FancyArrowPatch(
            (5.5, 3.82),
            (8.65, 3.82),
            arrowstyle="-|>",
            mutation_scale=13,
            linewidth=3.4,
            color=BLUE,
            alpha=0.9,
        )
    )
    ax.add_patch(
        FancyArrowPatch(
            (5.5, 3.60),
            (8.65, 3.60),
            arrowstyle="-|>",
            mutation_scale=13,
            linewidth=3.4,
            color=RED,
            alpha=0.88,
        )
    )
    ax.add_patch(
        FancyArrowPatch(
            (8.65, 3.36),
            (5.5, 3.45),
            arrowstyle="-|>",
            mutation_scale=13,
            linewidth=3,
            color=PURPLE,
            alpha=0.8,
        )
    )
    ax.text(7.08, 4.45, "共享核心：每个方向 100 Gbps", ha="center", fontsize=11, color=ORANGE, fontweight="bold")
    ax.text(7.08, 3.02, "反向流走独立的全双工方向", ha="center", fontsize=9, color=PURPLE)

    ax.add_patch(
        FancyBboxPatch(
            (4.0, 4.55),
            1.75,
            0.85,
            boxstyle="round,pad=0.05",
            facecolor="#FFF7ED",
            edgecolor=ORANGE,
            linewidth=1.2,
        )
    )
    ax.text(4.875, 5.12, "switch 8 / port 4", ha="center", fontsize=8.5, color=DARK)
    ax.text(4.875, 4.78, "VOQ + egress queue", ha="center", fontsize=9, color=ORANGE)
    ax.add_patch(FancyArrowPatch((4.88, 4.55), (4.88, 4.18), arrowstyle="-|>", mutation_scale=12, color=ORANGE))

    flow_lines = [
        (BLUE, "目标 AllGather 0→4"),
        (RED, "同向 EP / KV 1→5"),
        (PURPLE, "反向 EP 5→1"),
    ]
    for index, (color, label) in enumerate(flow_lines):
        y = 0.55 - index * 0.0
        x = 3.2 + index * 3.1
        ax.plot([x, x + 0.45], [y, y], color=color, linewidth=4)
        ax.text(x + 0.58, y, label, va="center", fontsize=9, color=DARK)

    ax.text(
        7.0,
        6.65,
        "竞争如何产生：多个 400-Gbps 入口同时把包送向同一个 100-Gbps 出端口",
        ha="center",
        fontsize=14,
        fontweight="bold",
        color=DARK,
    )
    ax.text(
        7.0,
        6.12,
        "同方向：共享 port 4 的发送时隙并形成队列；反方向：占用另一套发送时隙，通常不挤压目标方向",
        ha="center",
        fontsize=10,
        color=GRAY,
    )
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    finish(
        fig,
        "shared-bottleneck-contention-topology.svg",
        "来源: case/node.csv + topology.csv + routing_table.csv + traffic.csv | 物理量: access/core link capacity(Gbps)、flow direction、queue location | 证据: ns3-input-config/design schematic；不是实机拓扑测量",
    )


def long_window_traffic_figure() -> None:
    bandwidth = pd.read_csv(ROOT / "analysis/long_window_bandwidth_timeseries.csv")
    queue = pd.read_csv(ROOT / "analysis/long_window_queue_timeseries.csv")
    tasks = pd.read_csv(ROOT / "analysis/long_window_task_windows.csv")
    waves = pd.read_csv(ROOT / "analysis/long_window_wave_summary.csv")

    flow_order = ["target", "ep_competitor", "kv_competitor", "balanced_dispatch"]
    colors = {
        "target": BLUE,
        "ep_competitor": RED,
        "kv_competitor": ORANGE,
        "balanced_dispatch": GREEN,
        "reverse_ep": PURPLE,
    }
    labels = {
        "target": "目标 AllGather",
        "ep_competitor": "同向 EP",
        "kv_competitor": "同向 KV",
        "balanced_dispatch": "四 peer dispatch",
        "reverse_ep": "反向 EP",
    }

    x = np.sort(bandwidth.bin_center_us.unique())
    series = {
        kind: bandwidth[bandwidth.flow_kind == kind]
        .set_index("bin_center_us")
        .reindex(x, fill_value=0)
        .bandwidth_gbps.to_numpy()
        for kind in labels
    }
    l2r_total = np.sum([series[kind] for kind in flow_order], axis=0)
    rolling = pd.Series(l2r_total).rolling(window=5, center=True, min_periods=1).mean().to_numpy()

    fig, axes = plt.subplots(
        3,
        1,
        figsize=(14.2, 9.0),
        sharex=True,
        gridspec_kw={"height_ratios": [1.65, 1.0, 0.95]},
    )
    fig.suptitle(
        "1.1 ms 长窗口：10 轮 Block 24 / EP / KV 流量形成可见波峰与空闲波谷",
        fontsize=14,
        fontweight="bold",
        y=0.995,
    )

    ax = axes[0]
    base = np.zeros(len(x))
    for kind in flow_order:
        values = series[kind]
        ax.fill_between(
            x,
            base,
            base + values,
            step="mid",
            color=colors[kind],
            alpha=0.84,
            label=labels[kind],
        )
        base += values
    ax.fill_between(
        x,
        -series["reverse_ep"],
        0,
        step="mid",
        color=PURPLE,
        alpha=0.82,
        label="反向 EP（R→L）",
    )
    ax.plot(x, rolling, color=DARK, linewidth=1.7, label="L→R 25-µs 移动平均")
    ax.axhline(100, color=GRAY, linestyle="--", linewidth=0.9)
    ax.axhline(-100, color=GRAY, linestyle="--", linewidth=0.9)
    ax.axhline(0, color=DARK, linewidth=0.8)
    ax.set_ylim(-112, 122)
    ax.set_ylabel("核心链路数据带宽（Gbps）\n上：L→R；下：R→L")
    ax.set_title("① 核心端口带宽（5 µs 分箱）；黑线为显示用移动平均", loc="left", fontsize=10.5, pad=48)
    ax.grid(axis="x", color=LIGHT, linewidth=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, ncol=3, loc="lower right", fontsize=8)

    for row in waves.itertuples():
        ax.axvline(row.release_us, color=GRAY, linewidth=0.55, alpha=0.5)
        ax.text(
            row.release_us + 2,
            106,
            f"W{int(row.wave_id)} {row.wave_label}",
            rotation=36,
            ha="left",
            va="bottom",
            fontsize=7.5,
            color=DARK,
        )

    axq = axes[1]
    l2r_queue = queue[queue.direction == "L2R"].sort_values("bin_center_us")
    r2l_queue = queue[queue.direction == "R2L"].sort_values("bin_center_us")
    axq.fill_between(
        l2r_queue.bin_center_us,
        0,
        l2r_queue.queue_kib,
        step="mid",
        color=ORANGE,
        alpha=0.72,
        label="L→R：switch 8 / port 4",
    )
    axq.plot(
        r2l_queue.bin_center_us,
        r2l_queue.queue_kib,
        color=PURPLE,
        linewidth=1.4,
        label="R→L：switch 9 / port 4",
    )
    l2r_peak_time = float(l2r_queue.raw_max_time_us.iloc[0])
    l2r_peak = float(l2r_queue.raw_max_queue_kib.iloc[0])
    r2l_peak_time = float(r2l_queue.raw_max_time_us.iloc[0])
    r2l_peak = float(r2l_queue.raw_max_queue_kib.iloc[0])
    axq.plot(l2r_peak_time, l2r_peak, "o", color=RED, markersize=4)
    axq.annotate(
        f"L→R 回调记录峰值 {l2r_peak:.1f} KiB @ {l2r_peak_time:.2f} µs",
        (l2r_peak_time, l2r_peak),
        xytext=(8, -19),
        textcoords="offset points",
        fontsize=8.5,
        color=RED,
    )
    axq.plot(r2l_peak_time, r2l_peak, "s", color=PURPLE, markersize=4)
    axq.set_ylabel("核心队列（KiB）")
    axq.set_title("② 队列随每轮注入形成不同高度的波峰，并在空闲期回落到零", loc="left", fontsize=10.5)
    axq.grid(axis="x", color=LIGHT, linewidth=0.8)
    axq.spines[["top", "right"]].set_visible(False)
    axq.legend(frameon=False, ncol=2, loc="upper right", fontsize=8)

    axw = axes[2]
    lane_order = ["target", "ep_competitor", "kv_competitor", "balanced_dispatch", "reverse_ep"]
    lane_y = {kind: len(lane_order) - 1 - index for index, kind in enumerate(lane_order)}
    windows = (
        tasks.groupby(["wave_id", "wave_label", "flow_kind"], as_index=False)
        .agg(first_packet_us=("first_packet_us", "min"), last_ack_us=("last_ack_us", "max"))
    )
    for row in windows.itertuples():
        y = lane_y[row.flow_kind]
        duration = row.last_ack_us - row.first_packet_us
        axw.barh(
            y,
            duration,
            left=row.first_packet_us,
            height=0.52,
            color=colors[row.flow_kind],
            alpha=0.86,
        )
        if duration >= 20:
            axw.text(
                row.first_packet_us + duration / 2,
                y,
                f"W{int(row.wave_id)}",
                ha="center",
                va="center",
                fontsize=7.5,
                color="white",
                fontweight="bold",
            )
    axw.set_yticks(
        [lane_y[kind] for kind in lane_order],
        [labels[kind] for kind in lane_order],
    )
    axw.set_title("③ 每类 task 从 first-packet 到 last-ACK 的活动窗口", loc="left", fontsize=10.5)
    axw.set_xlabel("ns-3 仿真时间（µs）；总观察窗口 1.1 ms")
    axw.set_xlim(0, 1100)
    axw.set_xticks(np.arange(0, 1101, 100))
    axw.grid(axis="x", color=LIGHT, linewidth=0.8)
    axw.spines[["top", "right", "left"]].set_visible(False)

    fig.tight_layout(rect=(0, 0.05, 1, 0.975), h_pad=0.9)
    finish(
        fig,
        "long-window-network-traffic-waves.svg",
        "来源: ns-3 AllPacketTrace + PortTrace + QueueTrace + task_statistics.csv | 物理量: 5-µs data-packet wire bandwidth(Gbps)、25-µs moving average、1-µs queue occupancy(KiB)、task window(µs) | 证据: ns3-simulated/trace-derived；释放时间是受控合成 replay，不是生产流量测量",
    )


def zoomed_peak_comparison_figure() -> None:
    bandwidth = pd.read_csv(ROOT / "analysis/zoom_w1_w2_bandwidth_timeseries.csv")
    queue = pd.read_csv(ROOT / "analysis/zoom_w1_w2_queue_timeseries.csv")
    packets = pd.read_csv(ROOT / "analysis/long_window_packet_events.csv")
    tasks = pd.read_csv(ROOT / "analysis/long_window_task_windows.csv")

    cases = [
        {
            "wave_id": 1,
            "release_us": 110.0,
            "title": "W1 同向 EP（全局 110–165 µs）",
            "subtitle": "目标与 EP 共同分摊 L→R 100 Gbps",
            "competitor": "ep_competitor",
            "competitor_label": "同向 EP 1→5",
            "competitor_color": RED,
            "direction": "same",
        },
        {
            "wave_id": 2,
            "release_us": 220.0,
            "title": "W2 反向 EP（全局 220–275 µs）",
            "subtitle": "目标和 EP 分别占用 L→R / R→L",
            "competitor": "reverse_ep",
            "competitor_label": "反向 EP 5→1",
            "competitor_color": PURPLE,
            "direction": "reverse",
        },
    ]

    fig, axes = plt.subplots(
        3,
        2,
        figsize=(14.2, 9.4),
        sharex="col",
        gridspec_kw={"height_ratios": [1.45, 1.0, 0.75]},
    )
    fig.suptitle(
        "两个代表性波峰的微秒级放大：等字节、只改变竞争方向",
        fontsize=14,
        fontweight="bold",
        y=0.995,
    )

    for column, config in enumerate(cases):
        release = config["release_us"]
        start = release - 2.0
        end = release + 55.0
        task_part = tasks[tasks.wave_id == config["wave_id"]]
        packet_part = packets[packets.wave_id == config["wave_id"]]

        ax = axes[0, column]
        target_frame = bandwidth[
            (bandwidth.flow_kind == "target")
            & (bandwidth.bin_center_us >= start)
            & (bandwidth.bin_center_us <= end)
        ].sort_values("bin_center_us")
        competitor_frame = bandwidth[
            (bandwidth.flow_kind == config["competitor"])
            & (bandwidth.bin_center_us >= start)
            & (bandwidth.bin_center_us <= end)
        ].sort_values("bin_center_us")
        relative_x = target_frame.bin_center_us.to_numpy() - release
        target_values = target_frame.bandwidth_gbps.to_numpy()
        competitor_values = competitor_frame.bandwidth_gbps.to_numpy()
        ax.fill_between(
            relative_x,
            0,
            target_values,
            step="mid",
            color=BLUE,
            alpha=0.88,
            label="目标 AllGather 0→4",
        )
        if config["direction"] == "same":
            ax.fill_between(
                relative_x,
                target_values,
                target_values + competitor_values,
                step="mid",
                color=config["competitor_color"],
                alpha=0.84,
                label=config["competitor_label"],
            )
            ax.set_ylim(0, 112)
        else:
            ax.fill_between(
                relative_x,
                -competitor_values,
                0,
                step="mid",
                color=config["competitor_color"],
                alpha=0.84,
                label=config["competitor_label"],
            )
            ax.axhline(-100, color=GRAY, linestyle="--", linewidth=0.8)
            ax.set_ylim(-112, 112)
        ax.axhline(100, color=GRAY, linestyle="--", linewidth=0.8)
        ax.axhline(0, color=DARK, linewidth=0.8)
        ax.set_title(f"{config['title']}\n{config['subtitle']}", loc="left", fontsize=11)
        ax.set_ylabel("data wire bandwidth（Gbps）")
        ax.grid(axis="x", color=LIGHT, linewidth=0.8)
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(frameon=False, loc="lower right", fontsize=8)

        target_task = task_part[task_part.flow_kind == "target"].iloc[0]
        target_ack_rel = float(target_task.last_ack_us - release)
        competitor_task = task_part[task_part.flow_kind == config["competitor"]].iloc[0]
        competitor_ack_rel = float(competitor_task.last_ack_us - release)
        for row in range(3):
            axes[row, column].axvline(target_ack_rel, color=BLUE, linestyle="--", linewidth=1)
            if abs(competitor_ack_rel - target_ack_rel) > 0.1:
                axes[row, column].axvline(
                    competitor_ack_rel,
                    color=config["competitor_color"],
                    linestyle=":",
                    linewidth=1,
                )
        ax.text(
            target_ack_rel + 0.65,
            71,
            f"目标 ACK {target_ack_rel:.3f} µs",
            ha="left",
            color=BLUE,
            fontsize=8.5,
        )

        axq = axes[1, column]
        l2r = queue[
            (queue.direction == "L2R")
            & (queue.bin_center_us >= start)
            & (queue.bin_center_us <= end)
        ].sort_values("bin_center_us")
        r2l = queue[
            (queue.direction == "R2L")
            & (queue.bin_center_us >= start)
            & (queue.bin_center_us <= end)
        ].sort_values("bin_center_us")
        qx = l2r.bin_center_us.to_numpy() - release
        axq.fill_between(
            qx,
            0,
            l2r.queue_kib,
            step="mid",
            color=ORANGE,
            alpha=0.72,
            label="L→R queue",
        )
        if config["direction"] == "reverse":
            axq.plot(
                r2l.bin_center_us.to_numpy() - release,
                r2l.queue_kib,
                color=PURPLE,
                linewidth=1.6,
                label="R→L queue",
            )
        peak_row = l2r.loc[l2r.queue_kib.idxmax()]
        peak_x = float(peak_row.bin_center_us - release)
        peak_y = float(peak_row.queue_kib)
        axq.plot(peak_x, peak_y, "o", color=RED, markersize=4)
        axq.annotate(
            f"L→R {peak_y:.1f} KiB @ +{peak_x:.3f} µs",
            (peak_x, peak_y),
            xytext=(7, -17),
            textcoords="offset points",
            color=RED,
            fontsize=8.2,
        )
        axq.set_ylim(0, 520)
        axq.set_ylabel("queue occupancy（KiB）")
        axq.grid(axis="x", color=LIGHT, linewidth=0.8)
        axq.spines[["top", "right"]].set_visible(False)
        axq.legend(frameon=False, loc="upper right", fontsize=8)

        axp = axes[2, column]
        packet_lanes = [
            ("target", 1, BLUE, "目标 packets"),
            (config["competitor"], 0, config["competitor_color"], "EP packets"),
        ]
        for flow_kind, y, color, label in packet_lanes:
            frame = packet_part[packet_part.flow_kind == flow_kind]
            intervals = list(
                zip(
                    frame.packet_start_us - release,
                    frame.serialization_us,
                )
            )
            axp.broken_barh(
                intervals,
                (y - 0.31, 0.62),
                facecolors=color,
                edgecolors="none",
                alpha=0.9,
            )
        axp.set_yticks([1, 0], ["目标 packets", "EP packets"])
        axp.set_ylim(-0.65, 1.65)
        axp.set_xlim(-2, 55)
        axp.set_xticks(np.arange(0, 56, 5))
        axp.set_xlabel("相对 wave release 的仿真时间（µs）；带宽/队列分箱 0.25 µs")
        axp.grid(axis="x", color=LIGHT, linewidth=0.8)
        axp.spines[["top", "right", "left"]].set_visible(False)

    axes[0, 0].text(
        1.0,
        104,
        "同一方向只能合计到 100 Gbps",
        color=DARK,
        fontsize=8.5,
    )
    axes[0, 1].text(
        1.0,
        101,
        "两个方向可同时接近 100 Gbps",
        color=DARK,
        fontsize=8.5,
    )
    fig.tight_layout(rect=(0, 0.055, 1, 0.97), h_pad=0.85, w_pad=1.6)
    finish(
        fig,
        "zoomed-w1-w2-contention-peaks.svg",
        "来源: ns-3 AllPacketTrace + PortTrace + QueueTrace + task_statistics.csv | 物理量: 0.25-µs data wire bandwidth(Gbps)、0.25-µs queue occupancy(KiB)、333.92-ns packet serialization、relative time(µs) | 证据: ns3-simulated/trace-derived；W1/W2 payload相同，仅方向不同",
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    competition_topology_figure()
    long_window_traffic_figure()
    zoomed_peak_comparison_figure()
    print("\n".join(str(path) for path in sorted(OUT.glob("*.svg"))))


if __name__ == "__main__":
    main()
