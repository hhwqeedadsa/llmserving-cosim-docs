#!/usr/bin/env python3
"""Build deterministic Block 24 end-to-end profile snapshots and SVG figures."""

from __future__ import annotations

import argparse
import csv
import html
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "source" / "_static" / "study"
DATA_DIR = STATIC_DIR / "data"

INK = "#18212f"
MUTED = "#5f6b7a"
GRID = "#d9e0e8"
PAPER = "#ffffff"
BLUE = "#2563eb"
GREEN = "#059669"
ORANGE = "#d97706"
PURPLE = "#7c3aed"
RED = "#dc2626"
CYAN = "#0891b2"
LIGHT_BLUE = "#dbeafe"
LIGHT_GREEN = "#d1fae5"
LIGHT_ORANGE = "#ffedd5"
LIGHT_PURPLE = "#ede9fe"
LIGHT_GRAY = "#f1f5f9"


SNAPSHOTS = [
    "block24_summary.json",
    "block24_flow_manifest.csv",
    "block24_task_profile.csv",
    "block24_phase_profile.csv",
    "block24_duration_comparison.csv",
    "block24_packet_profile.csv",
    "block24_port_profile.csv",
    "block24_unified_events.csv",
]


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def svg_doc(width: int, height: int, title: str, desc: str, body: str) -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
  <title id="title">{esc(title)}</title>
  <desc id="desc">{esc(desc)}</desc>
  <rect width="{width}" height="{height}" fill="{PAPER}"/>
  <style>
    text {{ font-family: Inter, "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; fill: {INK}; }}
    .title {{ font-size: 24px; font-weight: 700; }}
    .subtitle {{ font-size: 14px; fill: {MUTED}; }}
    .axis {{ font-size: 12px; fill: {MUTED}; }}
    .label {{ font-size: 13px; }}
    .value {{ font-size: 13px; font-weight: 700; }}
    .small {{ font-size: 11px; fill: {MUTED}; }}
    .grid {{ stroke: {GRID}; stroke-width: 1; }}
  </style>
  {body}
</svg>
'''


def write_svg(name: str, width: int, height: int, title: str, desc: str, body: str) -> None:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    (STATIC_DIR / name).write_text(svg_doc(width, height, title, desc, body), encoding="utf-8")


def title_block(title: str, subtitle: str) -> str:
    return (
        f'<text x="60" y="42" class="title">{esc(title)}</text>'
        f'<text x="60" y="67" class="subtitle">{esc(subtitle)}</text>'
    )


def import_snapshots(analysis_dir: Path) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    missing = [name for name in SNAPSHOTS if not (analysis_dir / name).is_file()]
    if missing:
        raise SystemExit("missing Block 24 snapshots: " + ", ".join(missing))
    for name in SNAPSHOTS:
        shutil.copyfile(analysis_dir / name, DATA_DIR / name)


def load_data() -> tuple[dict, dict[str, list[dict[str, str]]]]:
    summary_path = DATA_DIR / "block24_summary.json"
    if not summary_path.is_file():
        raise SystemExit("Block 24 snapshots missing; pass --analysis-dir once")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    tables = {
        name: read_csv(DATA_DIR / f"block24_{name}.csv")
        for name in ("task_profile", "phase_profile", "duration_comparison", "packet_profile", "port_profile", "unified_events")
    }
    return summary, tables


def validate(summary: dict, tables: dict[str, list[dict[str, str]]]) -> None:
    tasks = tables["task_profile"]
    packets = tables["packet_profile"]
    phases = tables["phase_profile"]
    checks = [
        (len(tasks) == 8, "expected 8 tasks"),
        (len(phases) == 4, "expected 4 phases"),
        (len(packets) == 68, "expected 68 selected-task data packets"),
        (sum(int(row["payload_bytes"]) for row in tasks) == 2_129_920, "task payload mismatch"),
        (sum(int(row["payload_bytes"]) for row in packets) == 278_528, "packet payload mismatch"),
        (summary["invariants"]["tasks_completed"] == 8, "summary task mismatch"),
    ]
    failures = [message for passed, message in checks if not passed]
    if failures:
        raise SystemExit("Block 24 validation failed:\n" + "\n".join(failures))


def figure_lineage() -> None:
    width, height = 1460, 560
    body = title_block(
        "Block 24 trace 怎样变成 ns-3 的真实场景流",
        "每一步保留稳定 ID 和 evidence label；蓝色字段是语义，紫色字段是结构展开，绿色字段是 ns-3 时间证据。",
    )
    nodes = [
        (55, 130, 205, 105, "LLMServingSim trace", "op · bytes · profile latency", LIGHT_BLUE, BLUE),
        (305, 130, 205, 105, "Block 24 slice", "request · batch · block", LIGHT_BLUE, BLUE),
        (555, 130, 205, 105, "2-rank ring projection", "stage · src · dst · bytes", LIGHT_PURPLE, PURPLE),
        (805, 130, 205, 105, "ns-3 traffic.csv", "phase · dependency · delay", LIGHT_PURPLE, PURPLE),
        (1055, 130, 205, 105, "Task trace", "start · complete · FCT", LIGHT_GREEN, GREEN),
        (1055, 345, 205, 105, "Packet / queue / port", "hop time · bytes · occupancy", LIGHT_GREEN, GREEN),
    ]
    for x, y, w, h, label, detail, fill, accent in nodes:
        body += f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}"/>'
        body += f'<rect x="{x}" y="{y}" width="6" height="{h}" rx="3" fill="{accent}"/>'
        body += f'<text x="{x+20}" y="{y+42}" class="value">{esc(label)}</text>'
        body += f'<text x="{x+20}" y="{y+72}" class="label">{esc(detail)}</text>'
    body += '<defs><marker id="arr" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#7b8794"/></marker></defs>'
    arrows = [(260, 182, 305, 182), (510, 182, 555, 182), (760, 182, 805, 182), (1010, 182, 1055, 182), (1158, 235, 1158, 345)]
    for x1, y1, x2, y2 in arrows:
        body += f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#7b8794" stroke-width="2" marker-end="url(#arr)"/>'
    keys = [
        (80, 400, "collective_id", "req0.batch0.block24.moe_allgather", BLUE),
        (510, 170, "flow_id", "flow4", PURPLE),
        (710, 150, "task_id", "4", GREEN),
        (890, 365, "packet key", "task 4 / PSN 136…204", GREEN),
    ]
    for x, key_width, key, value, color in keys:
        body += f'<text x="{x}" y="305" class="small">{esc(key)}</text>'
        body += f'<rect x="{x}" y="320" width="{key_width}" height="42" rx="5" fill="{LIGHT_GRAY}"/>'
        body += f'<text x="{x+12}" y="346" style="font-size:12px;font-weight:700;fill:{color}">{esc(value)}</text>'
    body += f'<text x="55" y="505" class="small">边界：源 trace 没有 NCCL backend flow；src/dst 是显式的 2-rank ring projection。ns-3 时间只属于本图配置的 400 Gbps 单交换机。</text>'
    write_svg("block24-trace-lineage.svg", width, height, "Block 24 trace lineage", "从 LLMServingSim trace 到 ns-3 task、packet、queue 和 port 证据的数据血缘。", body)


def figure_unified_timeline(events: list[dict[str, str]], summary: dict) -> None:
    width, height = 1500, 640
    plot_x, plot_y, plot_w = 165, 125, 1255
    x_max = 525.0
    lanes = ["GPU0", "NIC0", "GPU1", "NIC1"]
    lane_y = {lane: plot_y + index * 92 for index, lane in enumerate(lanes)}
    body = title_block(
        "同一个 Block 24：GPU 计算与 NIC collective 的 ns-3 组合时间线",
        "横轴从 block 起点归零；GPU bar 用 profile duration，NIC bar 用本次 ns-3 WQE start→complete。",
    )
    for tick in range(0, 526, 50):
        x = plot_x + tick / x_max * plot_w
        body += f'<line x1="{x:.2f}" y1="{plot_y-22}" x2="{x:.2f}" y2="{plot_y+330}" class="grid"/>'
        body += f'<text x="{x:.2f}" y="{plot_y+360}" text-anchor="middle" class="axis">{tick}</text>'
    for lane in lanes:
        y = lane_y[lane]
        body += f'<text x="{plot_x-20}" y="{y+25}" text-anchor="end" class="value">{lane}</text>'
        body += f'<rect x="{plot_x}" y="{y}" width="{plot_w}" height="42" fill="{LIGHT_GRAY}"/>'
    colors = {"compute": GREEN, "AllReduce": BLUE, "AllGather": PURPLE, "ReduceScatter": ORANGE}
    for row in events:
        lane = row["resource_id"]
        if lane not in lane_y:
            continue
        start, end = float(row["start_us"]), float(row["end_us"])
        x = plot_x + start / x_max * plot_w
        w = max(1.5, (end - start) / x_max * plot_w)
        color = colors["compute"] if row["event_type"] == "compute" else colors[row["op"]]
        y = lane_y[lane]
        body += f'<rect x="{x:.2f}" y="{y}" width="{w:.2f}" height="42" fill="{color}" opacity="0.9"/>'
        if w > 125:
            label = "expert" if row["op"].startswith("expert_") else row["op"]
            body += f'<text x="{x+w/2:.2f}" y="{y+26}" text-anchor="middle" fill="#ffffff" style="font-size:12px;font-weight:700">{esc(label)} · {float(row["duration_us"]):.3f} μs</text>'
    body += f'<text x="{plot_x+215}" y="103" class="small">P0 AR reduce-scatter 36.330 μs · P1 AR allgather 42.493 μs · P2 MoE AG 51.203 μs · P3 MoE RS 514.487 μs</text>'
    body += f'<text x="{plot_x+plot_w/2}" y="{plot_y+397}" text-anchor="middle" class="axis">relative Block 24 time (μs)</text>'
    legend = [(GREEN, "GPU compute · profile-derived"), (BLUE, "AllReduce · ns-3"), (PURPLE, "AllGather · ns-3"), (ORANGE, "ReduceScatter · ns-3")]
    for index, (color, label) in enumerate(legend):
        x = 165 + index * 310
        body += f'<rect x="{x}" y="548" width="15" height="15" fill="{color}"/><text x="{x+24}" y="561" class="small">{esc(label)}</text>'
    block_end = summary["block_timing_us"]["ns3_composed_task_complete"]
    body += f'<text x="165" y="602" class="small">最终 task completion：{block_end:.6f} μs；20 ns dependency-visibility gap 在本图尺度小于 1 px，数值保留在 phase CSV。</text>'
    write_svg("block24-unified-timeline.svg", width, height, "Block 24 unified timeline", "GPU0、NIC0、GPU1、NIC1 的计算与通信事件共轴时间线。", body)


def figure_duration_comparison(rows: list[dict[str, str]], summary: dict) -> None:
    values = [
        ("AllReduce", float(rows[0]["llmservingsim_reconstructed_us"]), float(rows[0]["ns3_simulated_us"])),
        ("AllGather", float(rows[1]["llmservingsim_reconstructed_us"]), float(rows[1]["ns3_simulated_us"])),
        ("ReduceScatter", float(rows[2]["llmservingsim_reconstructed_us"]), float(rows[2]["ns3_simulated_us"])),
        ("Block 24", summary["block_timing_us"]["llmservingsim_reconstructed"], summary["block_timing_us"]["ns3_composed_task_complete"]),
    ]
    width, height = 1280, 610
    plot_x, plot_y, plot_w, plot_h = 105, 110, 1090, 365
    max_value = 700.0
    body = title_block(
        "重建时间与 ns-3 组合时间：同一阶段，不同模型口径",
        "橙色为 TTFT residual + 16 GB/s/20 μs ring 重建；蓝色为 400 Gbps ns-3 task completion（Block 还组合了相同 GPU profile）。",
    )
    for tick in range(0, 701, 100):
        y = plot_y + plot_h - tick / max_value * plot_h
        body += f'<line x1="{plot_x}" y1="{y:.2f}" x2="{plot_x+plot_w}" y2="{y:.2f}" class="grid"/>'
        body += f'<text x="{plot_x-12}" y="{y+4:.2f}" text-anchor="end" class="axis">{tick}</text>'
    group_w = plot_w / len(values)
    for index, (label, reconstructed, ns3_value) in enumerate(values):
        center = plot_x + (index + 0.5) * group_w
        for offset, value, color in [(-52, reconstructed, ORANGE), (12, ns3_value, BLUE)]:
            h = value / max_value * plot_h
            body += f'<rect x="{center+offset:.2f}" y="{plot_y+plot_h-h:.2f}" width="48" height="{h:.2f}" fill="{color}"/>'
            body += f'<text x="{center+offset+24:.2f}" y="{plot_y+plot_h-h-8:.2f}" text-anchor="middle" class="value">{value:.3f}</text>'
        body += f'<text x="{center:.2f}" y="{plot_y+plot_h+30}" text-anchor="middle" class="label">{esc(label)}</text>'
    body += f'<text x="30" y="300" transform="rotate(-90 30 300)" class="axis">duration (μs)</text>'
    body += f'<rect x="105" y="545" width="14" height="14" fill="{ORANGE}"/><text x="128" y="557" class="small">LLMServingSim reconstructed</text>'
    body += f'<rect x="355" y="545" width="14" height="14" fill="{BLUE}"/><text x="378" y="557" class="small">ns-3 simulated / composed</text>'
    body += f'<text x="705" y="557" class="small">差值来自带宽/latency/transport 模型选择，不是硬件误差。</text>'
    write_svg("block24-duration-comparison.svg", width, height, "Block 24 duration comparison", "三类 collective 和整个 Block 24 的重建时间与 ns-3 组合时间比较。", body)


def figure_flow_profile(tasks: list[dict[str, str]]) -> None:
    width, height = 1420, 650
    plot_x, plot_y, plot_w, plot_h = 100, 115, 1240, 360
    y_max = 7.0
    body = title_block(
        "Block 24 的 8 条 rank-pair flow：谁到谁、多少字节、耗时多少",
        "柱高为 WQE task FCT；圆点为 first-packet-send→last-packet-ACK envelope；每个 phase 的两个方向并行。",
    )
    for tick in range(0, 8):
        y = plot_y + plot_h - tick / y_max * plot_h
        body += f'<line x1="{plot_x}" y1="{y:.2f}" x2="{plot_x+plot_w}" y2="{y:.2f}" class="grid"/>'
        body += f'<text x="{plot_x-12}" y="{y+4:.2f}" text-anchor="end" class="axis">{tick}</text>'
    step = plot_w / len(tasks)
    stage_labels = []
    for index, row in enumerate(tasks):
        x = plot_x + index * step + 31
        bar_w = step - 62
        fct = float(row["task_fct_us"])
        envelope = float(row["packet_envelope_us"])
        color = BLUE if row["src_rank"] == "0" else ORANGE
        h = fct / y_max * plot_h
        cy = plot_y + plot_h - envelope / y_max * plot_h
        body += f'<rect x="{x:.2f}" y="{plot_y+plot_h-h:.2f}" width="{bar_w:.2f}" height="{h:.2f}" fill="{color}" opacity="0.86"/>'
        body += f'<circle cx="{x+bar_w/2:.2f}" cy="{cy:.2f}" r="6" fill="{RED}"/>'
        body += f'<text x="{x+bar_w/2:.2f}" y="{plot_y+plot_h-h-9:.2f}" text-anchor="middle" class="value">{fct:.3f}</text>'
        body += f'<text x="{x+bar_w/2:.2f}" y="{plot_y+plot_h+28}" text-anchor="middle" class="axis">task {row["task_id"]}</text>'
        body += f'<text x="{x+bar_w/2:.2f}" y="{plot_y+plot_h+47}" text-anchor="middle" class="small">{row["src_rank"]}→{row["dst_rank"]}</text>'
        body += f'<text x="{x+bar_w/2:.2f}" y="{plot_y+plot_h+64}" text-anchor="middle" class="small">{int(row["payload_bytes"])/1024:.0f} KiB</text>'
        if index % 2 == 0:
            stage_labels.append((x + step - 31, row["collective_op"], row["algorithm_stage"]))
    for center, op, stage in stage_labels:
        short_stage = stage.replace("reduce_scatter", "reduce-scatter").replace("ring_exchange", "exchange")
        body += f'<text x="{center:.2f}" y="{plot_y+plot_h+102}" text-anchor="middle" class="label">{esc(op)} · {esc(short_stage)}</text>'
    body += f'<text x="28" y="300" transform="rotate(-90 28 300)" class="axis">flow duration (μs)</text>'
    body += f'<rect x="100" y="602" width="14" height="14" fill="{BLUE}"/><text x="123" y="614" class="small">rank 0→1 WQE FCT</text>'
    body += f'<rect x="285" y="602" width="14" height="14" fill="{ORANGE}"/><text x="308" y="614" class="small">rank 1→0 WQE FCT</text>'
    body += f'<circle cx="505" cy="609" r="6" fill="{RED}"/><text x="520" y="614" class="small">packet/ACK envelope</text>'
    write_svg("block24-flow-profile.svg", width, height, "Block 24 flow profile", "Block 24 八条双向流的 WQE FCT、packet envelope、方向和 payload。", body)


def figure_packet_hops(packets: list[dict[str, str]], summary: dict) -> None:
    width, height = 1480, 760
    plot_x, plot_y, plot_w, plot_h = 100, 120, 1300, 375
    y_max = 800.0
    body = title_block(
        "选定 task 4：68 个数据包的逐跳延迟分解",
        "rank 0→1 AllGather，278,528 B；每根柱是一个 4 KiB 数据包，分解为源链路、交换机驻留和目的链路。",
    )
    for tick in range(0, 801, 100):
        y = plot_y + plot_h - tick / y_max * plot_h
        body += f'<line x1="{plot_x}" y1="{y:.2f}" x2="{plot_x+plot_w}" y2="{y:.2f}" class="grid"/>'
        body += f'<text x="{plot_x-12}" y="{y+4:.2f}" text-anchor="end" class="axis">{tick}</text>'
    bar_step = plot_w / len(packets)
    for index, row in enumerate(packets):
        x = plot_x + index * bar_step + 1
        base = plot_y + plot_h
        values = [
            (float(row["source_to_switch_ns"]), BLUE),
            (float(row["switch_dwell_ns"]), PURPLE),
            (float(row["switch_to_dest_ns"]), ORANGE),
        ]
        for value, color in values:
            h = value / y_max * plot_h
            body += f'<rect x="{x:.2f}" y="{base-h:.2f}" width="{max(2.0, bar_step-2):.2f}" height="{h:.2f}" fill="{color}"/>'
            base -= h
    for tick in [0, 15, 31, 47, 63, 67]:
        x = plot_x + (tick + 0.5) * bar_step
        body += f'<text x="{x:.2f}" y="{plot_y+plot_h+25}" text-anchor="middle" class="axis">{tick}</text>'
    body += f'<text x="28" y="315" transform="rotate(-90 28 315)" class="axis">forward packet latency (ns)</text>'
    body += f'<text x="{plot_x+plot_w/2}" y="{plot_y+plot_h+52}" text-anchor="middle" class="axis">data packet index (0…67)</text>'
    selected = summary["selected_task"]
    milestones = [
        ("task start · 0.000 μs", 0.0, BLUE, -28, "start"),
        ("first packet · 0.001 μs", 0.001, GREEN, 40, "start"),
        (f"WQE complete · {selected['task_fct_us']:.3f} μs", selected["task_fct_us"], PURPLE, -28, "end"),
        (f"last ACK · {selected['last_packet_ack_us'] - selected['task_start_us']:.3f} μs", selected["last_packet_ack_us"] - selected["task_start_us"], RED, 40, "end"),
    ]
    line_x, line_y, line_w = 165, 625, 1145
    max_us = 6.7
    body += f'<line x1="{line_x}" y1="{line_y}" x2="{line_x+line_w}" y2="{line_y}" stroke="{GRID}" stroke-width="5"/>'
    for label, value, color, label_offset, anchor in milestones:
        x = line_x + value / max_us * line_w
        body += f'<line x1="{x:.2f}" y1="{line_y-18}" x2="{x:.2f}" y2="{line_y+18}" stroke="{color}" stroke-width="3"/>'
        body += f'<text x="{x:.2f}" y="{line_y+label_offset}" text-anchor="{anchor}" class="small">{esc(label)}</text>'
    body += f'<text x="{line_x+line_w/2}" y="{line_y+72}" text-anchor="middle" class="axis">time relative to task 4 start (μs)</text>'
    legend = [(BLUE, "source→switch 103–104 ns"), (PURPLE, "switch dwell 10–518 ns"), (ORANGE, "switch→destination 103–104 ns")]
    for index, (color, label) in enumerate(legend):
        x = 100 + index * 355
        body += f'<rect x="{x}" y="714" width="14" height="14" fill="{color}"/><text x="{x+23}" y="726" class="small">{esc(label)}</text>'
    body += f'<text x="1120" y="726" class="small">max switch queue: {selected["max_switch_queue_bytes"]:,} B</text>'
    write_svg("block24-task4-packet-hops.svg", width, height, "Task 4 packet hop profile", "68 个数据包的源链路、交换机驻留、目的链路延迟和任务完成里程碑。", body)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis-dir", type=Path)
    args = parser.parse_args()
    if args.analysis_dir:
        import_snapshots(args.analysis_dir.resolve())
    summary, tables = load_data()
    validate(summary, tables)
    figure_lineage()
    figure_unified_timeline(tables["unified_events"], summary)
    figure_duration_comparison(tables["duration_comparison"], summary)
    figure_flow_profile(tables["task_profile"])
    figure_packet_hops(tables["packet_profile"], summary)
    print(f"wrote Block 24 snapshots and figures under {STATIC_DIR}")


if __name__ == "__main__":
    main()
