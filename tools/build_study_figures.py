#!/usr/bin/env python3
"""Build deterministic SVG figures and compact evidence snapshots for the study.

The repository contains only the fields needed by the figures.  Use
``--analysis-dir`` to refresh those snapshots from the round-06 experiment;
without it, figures are rebuilt entirely from the committed snapshots.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
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


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def fmt(value: float, digits: int = 1) -> str:
    return f"{value:,.{digits}f}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


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
    (STATIC_DIR / name).write_text(
        svg_doc(width, height, title, desc, body), encoding="utf-8"
    )


def title_block(title: str, subtitle: str) -> str:
    return (
        f'<text x="60" y="42" class="title">{esc(title)}</text>'
        f'<text x="60" y="67" class="subtitle">{esc(subtitle)}</text>'
    )


def import_snapshots(analysis_dir: Path) -> None:
    analysis_dir = analysis_dir.resolve()
    sources = {
        "simai_summary": analysis_dir / "experiments/simai_analytical/results/round06-fullEndToEnd.csv",
        "simai_slice": analysis_dir / "analysis/simai_representative_slice.csv",
        "simccl_flows": analysis_dir / "experiments/simccl_alltoall_8rank/ncclFlowModel_detailed_flows.csv",
        "dense_requests": analysis_dir / "experiments/llmservingsim_dense/requests.csv",
        "moe_requests": analysis_dir / "experiments/llmservingsim_moe_a2a/requests.csv",
        "middle_block": analysis_dir / "analysis/llmservingsim_middle_block_events.csv",
        "ns3_flows": analysis_dir / "analysis/ns3_flow_events.csv",
        "ns3_nics": analysis_dir / "analysis/ns3_nic_summary.csv",
        "selected_flow": analysis_dir / "analysis/selected_flow_profile.csv",
        "evidence": analysis_dir / "analysis/evidence_ledger.csv",
        "ns3_summary": analysis_dir / "analysis/ns3_summary.json",
        "llm_summary": analysis_dir / "analysis/llmservingsim_summary.json",
    }
    missing = [str(path) for path in sources.values() if not path.exists()]
    if missing:
        raise SystemExit("missing study inputs:\n" + "\n".join(missing))

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    simai_first = sources["simai_summary"].read_text(encoding="utf-8").splitlines()[:2]
    simai = next(csv.DictReader(simai_first))
    dense = read_csv(sources["dense_requests"])[0]
    moe = read_csv(sources["moe_requests"])[0]
    ns3_summary = json.loads(sources["ns3_summary"].read_text(encoding="utf-8"))
    llm_summary = json.loads(sources["llm_summary"].read_text(encoding="utf-8"))

    def leading_int(value: str) -> int:
        return int(value.strip().split()[0])

    summary = {
        "study": "round06-simai-llmservingsim-ns3",
        "simai": {
            "gpus": 9216,
            "layers": 1789,
            "compute_us": leading_int(simai[" total comp"]),
            "exposed_comm_us": leading_int(simai[" total exposed comm"]),
            "bubble_us": leading_int(simai[" bubble time"]),
            "total_us": int(simai[" Total time"]),
            "comm_domains_us": {
                "DP": leading_int(simai[" Expose DP comm"]),
                "DP_EP": leading_int(simai[" Expose DP_EP comm"]),
                "TP": leading_int(simai[" Expose TP comm"]),
                "EP": leading_int(simai[" Expose_EP_comm"]),
            },
        },
        "llmservingsim": {
            "dense": {
                "model": dense["model"],
                "ttft_ms": int(dense["TTFT"]) / 1e6,
                "tpot_ms": int(dense["TPOT"]) / 1e6,
                "end_ms": int(dense["end_time"]) / 1e6,
            },
            "moe": {
                "model": moe["model"],
                "ttft_ms": int(moe["TTFT"]) / 1e6,
                "tpot_ms": int(moe["TPOT"]) / 1e6,
                "end_ms": int(moe["end_time"]) / 1e6,
                "compute_per_rank_ms": llm_summary["compute_ns_per_rank"][0] / 1e6,
                "communication_residual_ms": llm_summary["residual_comm_ns"] / 1e6,
            },
        },
        "ns3": ns3_summary,
        "input_hashes": {key: sha256(path) for key, path in sources.items()},
        "source_files": {
            key: str(path.relative_to(analysis_dir)) for key, path in sources.items()
        },
    }
    (DATA_DIR / "study_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    csv_specs = {
        "simai_slice.csv": ("simai_slice", ["order", "phase", "compute_us", "comm_op", "bytes", "analytical_comm_us", "group", "group_size", "evidence"]),
        "simccl_flows.csv": ("simccl_flows", ["collective", "op", "data_size", "algorithm", "protocol", "channel_id", "flow_id", "src", "dest", "flow_size", "chunk_id", "chunk_count", "conn_type", "parent_flow_ids", "prev_flow_ids"]),
        "moe_middle_block.csv": ("middle_block", ["event_id", "resource", "resource_id", "start_us", "end_us", "duration_us", "event_type", "op", "src", "dst", "bytes", "evidence", "notes"]),
        "ns3_flows.csv": ("ns3_flows", ["event_id", "resource_id", "start_us", "end_us", "duration_us", "op", "src", "dst", "bytes", "evidence"]),
        "ns3_nics.csv": ("ns3_nics", ["nodeId", "portId", "type", "startTimestamp (us)", "completesTimestamp (us)", "TotalBytes", "throughput(Gbps)"]),
        "selected_flow.csv": ("selected_flow", ["task_id", "packet", "psn", "stage", "timestamp_us", "src", "dst", "payload_bytes", "evidence"]),
        "evidence_ledger.csv": ("evidence", ["artifact", "source", "evidence_class", "can_conclude", "cannot_conclude"]),
    }
    for output_name, (source_key, fields) in csv_specs.items():
        source_rows = read_csv(sources[source_key])
        rows = [{field: row.get(field, "") for field in fields} for row in source_rows]
        write_csv(DATA_DIR / output_name, rows, fields)


def load_data() -> tuple[dict, dict[str, list[dict[str, str]]]]:
    summary_path = DATA_DIR / "study_summary.json"
    if not summary_path.exists():
        raise SystemExit("study snapshots are missing; pass --analysis-dir once")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    names = [
        "simai_slice",
        "simccl_flows",
        "moe_middle_block",
        "ns3_flows",
        "ns3_nics",
        "selected_flow",
        "evidence_ledger",
    ]
    return summary, {name: read_csv(DATA_DIR / f"{name}.csv") for name in names}


def validate_data(summary: dict, tables: dict[str, list[dict[str, str]]]) -> None:
    simccl = tables["simccl_flows"]
    ns3_flows = tables["ns3_flows"]
    block = tables["moe_middle_block"]
    device_nics = [row for row in tables["ns3_nics"] if int(row["nodeId"]) < 8]
    comm_ops = {
        row["op"] for row in block if row["event_type"] == "communication"
    }

    checks = [
        (len(simccl) == 56, "SimCCL snapshot must contain 56 flows"),
        (
            sum(int(row["flow_size"]) for row in simccl) == 29_360_128,
            "SimCCL payload conservation failed",
        ),
        (len(ns3_flows) == 56, "ns-3 snapshot must contain 56 flows"),
        (len(block) == 22, "middle block snapshot must contain 22 lane events"),
        (
            comm_ops == {"ALLREDUCE", "ALLGATHER", "REDUCESCATTER"},
            "middle block collective sequence changed",
        ),
        (len(device_nics) == 16, "expected Rx and Tx rows for eight device NICs"),
        (
            sum(summary["simai"]["comm_domains_us"].values())
            == summary["simai"]["exposed_comm_us"],
            "SimAI communication-domain times do not conserve the reported total",
        ),
    ]
    failures = [message for passed, message in checks if not passed]
    if failures:
        raise SystemExit("study data validation failed:\n" + "\n".join(failures))


def figure_evidence_pipeline() -> None:
    width, height = 1320, 470
    body = title_block(
        "从 workload 到逐包 trace：数据和证据不能混级",
        "箭头表示数据变换；标签表示该阶段新增的物理量或结构，不表示实机测量。",
    )
    nodes = [
        (55, 120, 190, 94, "Workload / request", "layer、bytes、依赖", LIGHT_GRAY, INK),
        (295, 120, 190, 94, "SimAI analytical", "compute / comm 时间", LIGHT_ORANGE, ORANGE),
        (535, 120, 190, 94, "SimCCL", "src、dst、payload", LIGHT_PURPLE, PURPLE),
        (775, 120, 190, 94, "ns-3-UB", "queue / packet / ACK", LIGHT_BLUE, BLUE),
        (1015, 120, 250, 94, "网络观测", "FCT、吞吐、逐跳时间", LIGHT_GREEN, GREEN),
        (295, 302, 190, 94, "LLMServingSim", "request / batch / TTFT", LIGHT_BLUE, BLUE),
        (535, 302, 190, 94, "Layer profile", "GPU layer latency", LIGHT_GREEN, GREEN),
        (775, 302, 190, 94, "统一事件流", "GPU / NIC 同轴切片", LIGHT_PURPLE, PURPLE),
    ]
    for x, y, w, h, name, detail, fill, accent in nodes:
        body += f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}"/>'
        body += f'<rect x="{x}" y="{y}" width="6" height="{h}" rx="3" fill="{accent}"/>'
        body += f'<text x="{x+20}" y="{y+37}" class="value">{esc(name)}</text>'
        body += f'<text x="{x+20}" y="{y+65}" class="label">{esc(detail)}</text>'
    arrows = [
        (245, 167, 295, 167), (485, 167, 535, 167), (725, 167, 775, 167), (965, 167, 1015, 167),
        (245, 188, 295, 327), (485, 349, 535, 349), (725, 349, 775, 349), (870, 302, 870, 214),
    ]
    body += '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#7b8794"/></marker></defs>'
    for x1, y1, x2, y2 in arrows:
        body += f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#7b8794" stroke-width="2" marker-end="url(#arrow)"/>'
    body += f'<text x="55" y="442" class="small">证据顺序：scenario input → analytical/profile-derived → structural → simulated；本轮无 measured 网络证据。</text>'
    write_svg("evidence-pipeline.svg", width, height, "仿真证据链", "SimAI、LLMServingSim、SimCCL 与 ns-3 之间的数据变换和证据类型。", body)


def figure_simai_breakdown(summary: dict) -> None:
    values = [
        ("Compute", summary["simai"]["compute_us"], BLUE),
        ("Exposed communication", summary["simai"]["exposed_comm_us"], ORANGE),
        ("Pipeline bubble", summary["simai"]["bubble_us"], PURPLE),
    ]
    total = summary["simai"]["total_us"]
    width, height = 1280, 390
    body = title_block("SimAI 9,216-GPU 全局时间构成", "总模拟时间 7,545,619 μs；堆叠段长度按占比编码。")
    x0, y, bar_w, bar_h = 70, 125, 1140, 72
    x = x0
    for label, value, color in values:
        w = bar_w * value / total
        body += f'<rect x="{x:.2f}" y="{y}" width="{w:.2f}" height="{bar_h}" fill="{color}"/>'
        if w >= 150:
            body += f'<text x="{x+w/2:.2f}" y="{y+31}" text-anchor="middle" fill="#ffffff" style="font-size:14px;font-weight:700">{esc(label)}</text>'
            body += f'<text x="{x+w/2:.2f}" y="{y+54}" text-anchor="middle" fill="#ffffff" style="font-size:13px">{value/total*100:.2f}%</text>'
        else:
            body += f'<line x1="{x+w/2:.2f}" y1="{y}" x2="{x+w/2:.2f}" y2="94" stroke="{color}" stroke-width="2"/>'
            body += f'<text x="{x+w/2-4:.2f}" y="88" text-anchor="end" class="value">{esc(label)} · {value/total*100:.2f}%</text>'
        x += w
    legend_y = 250
    for idx, (label, value, color) in enumerate(values):
        lx = 80 + idx * 395
        body += f'<circle cx="{lx}" cy="{legend_y}" r="7" fill="{color}"/>'
        body += f'<text x="{lx+16}" y="{legend_y+5}" class="label">{esc(label)}</text>'
        body += f'<text x="{lx+16}" y="{legend_y+31}" class="value">{value:,} μs</text>'
    body += f'<text x="70" y="345" class="small">分析模型输出；物理量为模拟关键路径时间，不是 9,216 台 GPU 的时间求和，也不是墙钟运行时间。</text>'
    write_svg("simai-time-breakdown.svg", width, height, "SimAI 全局时间构成", "计算、暴露通信和流水线气泡在总模拟时间中的占比。", body)


def figure_simai_comm(summary: dict) -> None:
    values = list(summary["simai"]["comm_domains_us"].items())
    total = summary["simai"]["exposed_comm_us"]
    max_v = max(value for _, value in values)
    width, height = 1180, 470
    body = title_block("SimAI 暴露通信时间按并行域分类", "条长为 exposed communication time（μs）；右侧百分比以全部暴露通信 2,656,839 μs 为分母。")
    plot_x, plot_w = 245, 790
    for idx, (label, value) in enumerate(values):
        y = 120 + idx * 72
        w = plot_w * value / max_v
        color = [BLUE, ORANGE, PURPLE, GREEN][idx]
        body += f'<text x="220" y="{y+27}" text-anchor="end" class="label">{esc(label)}</text>'
        body += f'<rect x="{plot_x}" y="{y}" width="{w:.2f}" height="38" rx="3" fill="{color}"/>'
        body += f'<text x="{plot_x+w+12:.2f}" y="{y+25}" class="value">{value:,} μs  ·  {value/total*100:.1f}%</text>'
    body += f'<line x1="{plot_x}" y1="98" x2="{plot_x}" y2="406" class="grid"/>'
    body += f'<text x="60" y="445" class="small">EP + DP_EP = {(summary["simai"]["comm_domains_us"]["EP"] + summary["simai"]["comm_domains_us"]["DP_EP"]):,} μs，占暴露通信 {((summary["simai"]["comm_domains_us"]["EP"] + summary["simai"]["comm_domains_us"]["DP_EP"])/total*100):.1f}%。</text>'
    write_svg("simai-communication-breakdown.svg", width, height, "SimAI 通信域分解", "DP、DP_EP、TP 和 EP 的暴露通信时间。", body)


def figure_simai_slice(rows: list[dict[str, str]]) -> None:
    labels = ["expert\ncompute", "TP\nA2A", "EP\nA2A", "input\nAG", "output\nRS", "EP\ncombine", "TP\ncombine"]
    durations = [float(row["compute_us"]) + float(row["analytical_comm_us"]) for row in rows]
    mib = [float(row["bytes"]) / 1048576 for row in rows]
    width, height = 1360, 610
    body = title_block("代表性 SimAI MoE layer slice", "上图：analytical phase time（μs，对数轴）；下图：声明的 collective payload（MiB，线性轴）。")
    x0, plot_w, step = 120, 1130, 1130 / len(rows)
    colors = [GREEN, BLUE, PURPLE, CYAN, ORANGE, PURPLE, BLUE]
    # Log-duration panel keeps the 53–7,956 μs phases simultaneously legible.
    import math
    top_y, top_h = 115, 180
    for tick in [1, 10, 100, 1000, 10000]:
        ty = top_y + top_h - (math.log10(tick) / 4) * top_h
        body += f'<line x1="{x0}" y1="{ty:.2f}" x2="{x0+plot_w}" y2="{ty:.2f}" class="grid"/>'
        body += f'<text x="{x0-12}" y="{ty+4:.2f}" text-anchor="end" class="axis">{tick:,}</text>'
    for idx, value in enumerate(durations):
        h = (math.log10(max(value, 1)) / 4) * top_h
        x = x0 + idx * step + 28
        body += f'<rect x="{x:.2f}" y="{top_y+top_h-h:.2f}" width="{step-56:.2f}" height="{h:.2f}" fill="{colors[idx]}"/>'
        body += f'<text x="{x+(step-56)/2:.2f}" y="{top_y+top_h-h-8:.2f}" text-anchor="middle" class="value">{value:,.0f}</text>'
    body += f'<text x="34" y="210" transform="rotate(-90 34 210)" class="axis">phase time (μs, log₁₀)</text>'
    lower_y, lower_h = 365, 130
    max_mib = max(mib)
    for tick in [0, 64, 128, 192]:
        ty = lower_y + lower_h - tick / max_mib * lower_h
        body += f'<line x1="{x0}" y1="{ty:.2f}" x2="{x0+plot_w}" y2="{ty:.2f}" class="grid"/>'
        body += f'<text x="{x0-12}" y="{ty+4:.2f}" text-anchor="end" class="axis">{tick}</text>'
    for idx, value in enumerate(mib):
        h = value / max_mib * lower_h if max_mib else 0
        x = x0 + idx * step + 28
        body += f'<rect x="{x:.2f}" y="{lower_y+lower_h-h:.2f}" width="{step-56:.2f}" height="{h:.2f}" fill="{colors[idx]}" opacity="0.78"/>'
        if value:
            body += f'<text x="{x+(step-56)/2:.2f}" y="{lower_y+lower_h-h-7:.2f}" text-anchor="middle" class="value">{value:,.0f}</text>'
        label_lines = labels[idx].split("\n")
        body += f'<text x="{x+(step-56)/2:.2f}" y="{lower_y+lower_h+24}" text-anchor="middle" class="axis">{label_lines[0]}</text>'
        body += f'<text x="{x+(step-56)/2:.2f}" y="{lower_y+lower_h+40}" text-anchor="middle" class="axis">{label_lines[1]}</text>'
    body += f'<text x="34" y="450" transform="rotate(-90 34 450)" class="axis">payload (MiB)</text>'
    body += f'<text x="120" y="585" class="small">时间来自 SimAI analytical backend；bytes 来自 workload 声明。两者都不是 packet-level FCT 或实机 NIC 计数。</text>'
    write_svg("simai-moe-slice.svg", width, height, "SimAI MoE 时间切片", "代表性 MoE 层中计算和集合通信阶段的分析时间与 payload 字节。", body)


def figure_request_latency(summary: dict) -> None:
    dense = summary["llmservingsim"]["dense"]
    moe = summary["llmservingsim"]["moe"]
    scenarios = [("Dense · 1 GPU", dense), ("MoE · TP2/EP2", moe)]
    width, height = 1080, 540
    body = title_block("LLMServingSim 请求级延迟", "同一请求规模：input 128 tokens、output 4 tokens；柱高为 simulator 输出的 TTFT / TPOT（ms）。")
    plot_x, plot_y, plot_w, plot_h = 105, 105, 870, 320
    max_v = 35
    for tick in range(0, 36, 5):
        y = plot_y + plot_h - tick / max_v * plot_h
        body += f'<line x1="{plot_x}" y1="{y:.2f}" x2="{plot_x+plot_w}" y2="{y:.2f}" class="grid"/>'
        body += f'<text x="{plot_x-12}" y="{y+4:.2f}" text-anchor="end" class="axis">{tick}</text>'
    group_centers = [310, 745]
    for idx, (label, values) in enumerate(scenarios):
        center = group_centers[idx]
        for offset, key, color, metric in [(-65, "ttft_ms", BLUE, "TTFT"), (15, "tpot_ms", ORANGE, "TPOT")]:
            value = values[key]
            h = value / max_v * plot_h
            body += f'<rect x="{center+offset}" y="{plot_y+plot_h-h:.2f}" width="58" height="{h:.2f}" fill="{color}"/>'
            body += f'<text x="{center+offset+29}" y="{plot_y+plot_h-h-9:.2f}" text-anchor="middle" class="value">{value:.3f}</text>'
            body += f'<text x="{center+offset+29}" y="{plot_y+plot_h+23}" text-anchor="middle" class="axis">{metric}</text>'
        body += f'<text x="{center-11}" y="{plot_y+plot_h+54}" text-anchor="middle" class="label">{esc(label)}</text>'
    body += f'<text x="28" y="285" transform="rotate(-90 28 285)" class="axis">request latency (ms)</text>'
    body += f'<circle cx="105" cy="500" r="6" fill="{BLUE}"/><text x="120" y="505" class="small">TTFT：到首 token</text>'
    body += f'<circle cx="285" cy="500" r="6" fill="{ORANGE}"/><text x="300" y="505" class="small">TPOT：后续 token 平均间隔</text>'
    body += f'<text x="615" y="505" class="small">两者语义不同，不相加。</text>'
    write_svg("llmservingsim-request-latency.svg", width, height, "LLMServingSim 请求级延迟", "Dense 与 MoE 场景的 TTFT 和 TPOT。", body)


def figure_middle_block(rows: list[dict[str, str]]) -> None:
    width, height = 1460, 600
    block_start = min(float(row["start_us"]) for row in rows)
    block_end = max(float(row["end_us"]) for row in rows)
    span = block_end - block_start
    plot_x, plot_y, plot_w = 155, 132, 1235
    lanes = ["GPU0", "NIC0", "GPU1", "NIC1"]
    lane_y = {lane: plot_y + idx * 86 for idx, lane in enumerate(lanes)}
    body = title_block("LLMServingSim 中间 MoE block：GPU 与 NIC 观测点", "Transformer block 24；横轴为相对 block 起点的时间（μs），四条 lane 共用同一时间轴。")
    for tick in range(0, 701, 100):
        if tick > span + 70:
            break
        x = plot_x + tick / span * plot_w
        body += f'<line x1="{x:.2f}" y1="{plot_y-22}" x2="{x:.2f}" y2="{plot_y+330}" class="grid"/>'
        body += f'<text x="{x:.2f}" y="{plot_y+353}" text-anchor="middle" class="axis">{tick}</text>'
    op_colors = {"compute": GREEN, "ALLREDUCE": BLUE, "ALLGATHER": PURPLE, "REDUCESCATTER": ORANGE}
    for lane in lanes:
        y = lane_y[lane]
        body += f'<text x="130" y="{y+24}" text-anchor="end" class="value">{lane}</text>'
        body += f'<rect x="{plot_x}" y="{y}" width="{plot_w}" height="38" fill="{LIGHT_GRAY}"/>'
    for row in rows:
        lane = row["resource_id"]
        start = float(row["start_us"]) - block_start
        end = float(row["end_us"]) - block_start
        x = plot_x + start / span * plot_w
        w = max(1.5, (end - start) / span * plot_w)
        op = row["op"]
        color = op_colors[op] if row["event_type"] == "communication" else op_colors["compute"]
        y = lane_y[lane]
        body += f'<rect x="{x:.2f}" y="{y}" width="{w:.2f}" height="38" fill="{color}" opacity="0.88"/>'
        if w > 115:
            short = "expert" if op.startswith("expert_") else op
            body += f'<text x="{x+w/2:.2f}" y="{y+24}" text-anchor="middle" fill="#ffffff" style="font-size:12px;font-weight:700">{esc(short)} · {float(row["duration_us"]):.1f} μs</text>'
        elif row["event_type"] == "communication" and lane in ("NIC0",):
            body += f'<text x="{x+w/2:.2f}" y="{y-7}" text-anchor="middle" class="small">{esc(op.replace("REDUCESCATTER", "RS").replace("ALLGATHER", "AG").replace("ALLREDUCE", "AR"))}</text>'
    body += f'<text x="{plot_x+plot_w/2}" y="{plot_y+382}" text-anchor="middle" class="axis">relative time from block 24 start (μs)</text>'
    legend = [(GREEN, "GPU compute · profile-derived"), (BLUE, "AR · reconstructed"), (PURPLE, "AG · reconstructed"), (ORANGE, "RS · reconstructed")]
    for idx, (color, label) in enumerate(legend):
        x = 155 + idx * 300
        body += f'<rect x="{x}" y="536" width="14" height="14" fill="{color}"/><text x="{x+22}" y="548" class="small">{esc(label)}</text>'
    write_svg("moe-middle-block-timeline.svg", width, height, "MoE 中间 block 时间线", "GPU0、NIC0、GPU1、NIC1 上的计算与通信事件。", body)


def figure_flow_matrix(rows: list[dict[str, str]]) -> None:
    width, height = 1000, 880
    cell, x0, y0 = 78, 220, 135
    lookup = {(int(row["src"]), int(row["dest"])): int(row["flow_size"]) for row in rows}
    body = title_block("SimCCL 4 MiB AllToAll 的 rank-pair 流量矩阵", "行是 source rank，列是 destination rank；单元格数值是 payload（KiB）。")
    for rank in range(8):
        body += f'<text x="{x0+rank*cell+cell/2}" y="{y0-20}" text-anchor="middle" class="value">dst {rank}</text>'
        body += f'<text x="{x0-22}" y="{y0+rank*cell+cell/2+5}" text-anchor="end" class="value">src {rank}</text>'
        for dst in range(8):
            x, y = x0 + dst * cell, y0 + rank * cell
            value = lookup.get((rank, dst), 0)
            fill = LIGHT_BLUE if value else LIGHT_GRAY
            body += f'<rect x="{x}" y="{y}" width="{cell-3}" height="{cell-3}" fill="{fill}" stroke="{GRID}"/>'
            text = f"{value/1024:.0f}" if value else "—"
            color = BLUE if value else MUTED
            body += f'<text x="{x+(cell-3)/2}" y="{y+(cell-3)/2+5}" text-anchor="middle" style="font-size:13px;font-weight:700;fill:{color}">{text}</text>'
    body += f'<text x="{x0+4*cell}" y="{y0+8*cell+42}" text-anchor="middle" class="axis">destination rank</text>'
    body += f'<text x="58" y="{y0+4*cell}" transform="rotate(-90 58 {y0+4*cell})" text-anchor="middle" class="axis">source rank</text>'
    body += f'<text x="220" y="825" class="small">56 个非对角 flow；每个 512 KiB；总 payload 28 MiB。矩阵只表达结构和字节，不表达完成时间。</text>'
    write_svg("simccl-flow-matrix.svg", width, height, "SimCCL AllToAll 流量矩阵", "8 个 rank 之间的 56 条 512 KiB 流。", body)


def figure_ns3_timeline(rows: list[dict[str, str]]) -> None:
    width, height = 1370, 690
    plot_x, plot_y, plot_w = 130, 115, 1160
    max_t = 78.0
    body = title_block("ns-3-UB：56 条 P2P flow 的仿真 envelope", "每条线从 first packet send 到 last packet ACK；按 source NIC 分 lane，单位 μs。")
    for tick in range(0, 81, 10):
        x = plot_x + tick / max_t * plot_w
        body += f'<line x1="{x:.2f}" y1="{plot_y-10}" x2="{x:.2f}" y2="{plot_y+455}" class="grid"/>'
        body += f'<text x="{x:.2f}" y="{plot_y+480}" text-anchor="middle" class="axis">{tick}</text>'
    for nic in range(8):
        center_y = plot_y + nic * 57 + 22
        body += f'<text x="{plot_x-18}" y="{center_y+4}" text-anchor="end" class="value">NIC{nic}</text>'
        body += f'<line x1="{plot_x}" y1="{center_y}" x2="{plot_x+plot_w}" y2="{center_y}" stroke="{GRID}"/>'
        nic_rows = [row for row in rows if int(row["src"]) == nic]
        for idx, row in enumerate(nic_rows):
            start, end = float(row["start_us"]), float(row["end_us"])
            y = center_y - 15 + idx * 5
            x1, x2 = plot_x + start / max_t * plot_w, plot_x + end / max_t * plot_w
            body += f'<line x1="{x1:.2f}" y1="{y:.2f}" x2="{x2:.2f}" y2="{y:.2f}" stroke="{BLUE}" stroke-width="3" opacity="0.72"/>'
            body += f'<circle cx="{x2:.2f}" cy="{y:.2f}" r="2.5" fill="{ORANGE}"/>'
    body += f'<text x="{plot_x+plot_w/2}" y="{plot_y+520}" text-anchor="middle" class="axis">ns-3 simulation time (μs)</text>'
    body += f'<line x1="890" y1="650" x2="930" y2="650" stroke="{BLUE}" stroke-width="3"/><text x="940" y="654" class="small">flow envelope</text>'
    body += f'<circle cx="1090" cy="650" r="3" fill="{ORANGE}"/><text x="1102" y="654" class="small">last-packet ACK</text>'
    write_svg("ns3-flow-timeline.svg", width, height, "ns-3 flow 时间线", "八个源 NIC 发出的 56 条流的包 envelope。", body)


def figure_nic_throughput(rows: list[dict[str, str]]) -> None:
    device_rows = [row for row in rows if int(row["nodeId"]) < 8]
    width, height = 1220, 560
    plot_x, plot_y, plot_w, plot_h = 100, 110, 1040, 330
    y_min, y_max = 394.0, 398.0
    body = title_block("ns-3 device NIC 的 active-window throughput", "每个点是该 NIC 从首个到最后一个 trace 字节之间的平均线速吞吐；纵轴截断到 394–398 Gbps。")
    for tick in [394, 395, 396, 397, 398]:
        y = plot_y + plot_h - (tick-y_min)/(y_max-y_min)*plot_h
        body += f'<line x1="{plot_x}" y1="{y:.2f}" x2="{plot_x+plot_w}" y2="{y:.2f}" class="grid"/>'
        body += f'<text x="{plot_x-12}" y="{y+4:.2f}" text-anchor="end" class="axis">{tick}</text>'
    by_nic = {(int(row["nodeId"]), row["type"]): float(row["throughput(Gbps)"]) for row in device_rows}
    for nic in range(8):
        x = plot_x + (nic + .5) / 8 * plot_w
        rx, tx = by_nic[(nic, "Rx")], by_nic[(nic, "Tx")]
        for offset, value, color, marker in [(-15, rx, BLUE, "circle"), (15, tx, ORANGE, "rect")]:
            y = plot_y + plot_h - (value-y_min)/(y_max-y_min)*plot_h
            if marker == "circle":
                body += f'<circle cx="{x+offset:.2f}" cy="{y:.2f}" r="7" fill="{color}"/>'
            else:
                body += f'<rect x="{x+offset-7:.2f}" y="{y-7:.2f}" width="14" height="14" fill="{color}"/>'
            body += f'<text x="{x+offset:.2f}" y="{y-12:.2f}" text-anchor="middle" class="small">{value:.2f}</text>'
        body += f'<text x="{x:.2f}" y="{plot_y+plot_h+28}" text-anchor="middle" class="axis">NIC{nic}</text>'
    body += f'<text x="28" y="280" transform="rotate(-90 28 280)" class="axis">active-window throughput (Gbps)</text>'
    body += f'<circle cx="100" cy="510" r="6" fill="{BLUE}"/><text x="114" y="515" class="small">Rx</text>'
    body += f'<rect x="175" y="504" width="12" height="12" fill="{ORANGE}"/><text x="196" y="515" class="small">Tx</text>'
    body += f'<text x="355" y="515" class="small">不是固定窗口平均，也不是 GPU 有效 payload goodput；包含 trace 统计口径下的协议字节。</text>'
    write_svg("ns3-nic-throughput.svg", width, height, "ns-3 NIC 吞吐", "八个 device NIC 的收发 active-window throughput。", body)


def figure_selected_flow(rows: list[dict[str, str]]) -> None:
    width, height = 1390, 690
    plot_y = 150
    stages = ["source_nic_tx", "switch_ingress_arrival", "switch_egress_tx", "destination_nic_rx"]
    stage_names = ["NIC0 Tx", "switch ingress", "switch egress", "NIC1 Rx"]
    ymap = {stage: plot_y + idx * 82 for idx, stage in enumerate(stages)}
    body = title_block("选定 flow 的逐包 / 逐跳 profile", "task 0：NIC0 → NIC1，512 KiB；拆分首包与尾部两个时间窗，避免 0.1 μs 级逐跳差异被 77 μs 全轴压扁。")
    left_x, left_w, left_min, left_max = 210, 450, 0.0, 0.35
    right_x, right_w, right_min, right_max = 800, 510, 74.5, 77.0
    for tick in [0.0, 0.1, 0.2, 0.3]:
        x = left_x + (tick-left_min)/(left_max-left_min)*left_w
        body += f'<line x1="{x:.2f}" y1="{plot_y-20}" x2="{x:.2f}" y2="{plot_y+270}" class="grid"/>'
        body += f'<text x="{x:.2f}" y="{plot_y+300}" text-anchor="middle" class="axis">{tick:.1f}</text>'
    for tick in [74.5, 75.0, 75.5, 76.0, 76.5, 77.0]:
        x = right_x + (tick-right_min)/(right_max-right_min)*right_w
        body += f'<line x1="{x:.2f}" y1="{plot_y-20}" x2="{x:.2f}" y2="{plot_y+270}" class="grid"/>'
        body += f'<text x="{x:.2f}" y="{plot_y+300}" text-anchor="middle" class="axis">{tick:.1f}</text>'
    body += f'<text x="{left_x+left_w/2}" y="112" text-anchor="middle" class="value">首包窗口 · 0–0.35 μs</text>'
    body += f'<text x="{right_x+right_w/2}" y="112" text-anchor="middle" class="value">末包与完成窗口 · 74.5–77.0 μs</text>'
    body += f'<text x="730" y="305" text-anchor="middle" style="font-size:28px;fill:{MUTED}">//</text>'
    for stage, name in zip(stages, stage_names):
        y = ymap[stage]
        body += f'<text x="{left_x-20}" y="{y+5}" text-anchor="end" class="value">{esc(name)}</text>'
        body += f'<line x1="{left_x}" y1="{y}" x2="{left_x+left_w}" y2="{y}" stroke="{GRID}"/>'
        body += f'<line x1="{right_x}" y1="{y}" x2="{right_x+right_w}" y2="{y}" stroke="{GRID}"/>'
    panels = [
        ("first_data_packet", BLUE, left_x, left_w, left_min, left_max),
        ("last_data_packet", PURPLE, right_x, right_w, right_min, right_max),
    ]
    for packet, color, panel_x, panel_w, panel_min, panel_max in panels:
        points = []
        packet_rows = [row for row in rows if row["packet"] == packet and row["stage"] in ymap]
        packet_rows.sort(key=lambda row: float(row["timestamp_us"]))
        for row in packet_rows:
            x = panel_x + (float(row["timestamp_us"])-panel_min)/(panel_max-panel_min)*panel_w
            y = ymap[row["stage"]]
            points.append(f"{x:.2f},{y:.2f}")
            body += f'<circle cx="{x:.2f}" cy="{y:.2f}" r="7" fill="{color}"/>'
            body += f'<text x="{x:.2f}" y="{y-13}" text-anchor="middle" class="small">{float(row["timestamp_us"]):.3f}</text>'
        body += f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="3"/>'
    completion_rows = [row for row in rows if row["packet"] == "flow"]
    for idx, row in enumerate(completion_rows):
        t = float(row["timestamp_us"])
        x = right_x + (t-right_min)/(right_max-right_min)*right_w
        color = GREEN if row["stage"] == "wqe_complete" else RED
        body += f'<line x1="{x:.2f}" y1="{plot_y-22}" x2="{x:.2f}" y2="{plot_y+285}" stroke="{color}" stroke-width="3" stroke-dasharray="6 5"/>'
        label = "WQE complete" if row["stage"] == "wqe_complete" else "ACK envelope"
        body += f'<text x="{x-8:.2f}" y="{plot_y+323+idx*22}" text-anchor="end" style="font-size:12px;font-weight:700;fill:{color}">{label} · {t:.5f} μs</text>'
    body += f'<text x="{(left_x+right_x+right_w)/2}" y="{plot_y+395}" text-anchor="middle" class="axis">ns-3 simulation time (μs, split axis)</text>'
    body += f'<circle cx="210" cy="615" r="6" fill="{BLUE}"/><text x="225" y="620" class="small">first 4 KiB packet</text>'
    body += f'<circle cx="390" cy="615" r="6" fill="{PURPLE}"/><text x="405" y="620" class="small">last 4 KiB data packet</text>'
    body += f'<text x="210" y="655" class="small">WQE completion 与 parser 的 last-packet-ACK envelope 是不同事件源和完成语义；图中分别保留。</text>'
    write_svg("selected-flow-packet-profile.svg", width, height, "选定流逐跳 profile", "task 0 首包、末包的逐跳时间和两种完成事件。", body)


def figure_evidence_classes(rows: list[dict[str, str]]) -> None:
    classes = ["scenario input", "profile-derived trace", "analytical model", "structural flow", "simulator output", "reconstructed timeline", "simulated flow timing", "simulated NIC timing", "simulated per-hop trace"]
    short_class = {
        "scenario input": "输入",
        "profile-derived trace": "Profile",
        "analytical model": "分析模型",
        "structural flow": "结构",
        "simulator output": "服务仿真",
        "reconstructed timeline": "重建",
        "simulated flow timing": "网络仿真",
        "simulated NIC timing": "网络仿真",
        "simulated per-hop trace": "网络仿真",
    }
    columns = ["输入", "Profile", "分析模型", "结构", "服务仿真", "重建", "网络仿真"]
    width, height = 1440, 730
    x0, y0, row_h = 410, 135, 57
    col_w = 135
    body = title_block("证据分类：每个 artifact 能证明到哪一层", "圆点是 artifact 的证据类型；类别描述来源，不代表由左到右的精度等级。")
    for idx, col in enumerate(columns):
        body += f'<text x="{x0+idx*col_w+col_w/2}" y="{y0-18}" text-anchor="middle" class="value">{esc(col)}</text>'
    for ridx, row in enumerate(rows):
        y = y0 + ridx * row_h
        artifact = Path(row["artifact"]).name
        if len(artifact) > 37:
            artifact = artifact[:34] + "…"
        body += f'<rect x="45" y="{y-20}" width="{width-90}" height="{row_h-4}" fill="{LIGHT_GRAY if ridx%2==0 else PAPER}"/>'
        body += f'<text x="60" y="{y+9}" class="label">{esc(artifact)}</text>'
        for cidx in range(len(columns)+1):
            x = x0 + cidx * col_w
            body += f'<line x1="{x}" y1="{y-20}" x2="{x}" y2="{y+row_h-24}" stroke="{GRID}"/>'
        col = columns.index(short_class[row["evidence_class"]])
        cx = x0 + col*col_w + col_w/2
        color = [MUTED, GREEN, ORANGE, PURPLE, BLUE, RED, CYAN][col]
        body += f'<circle cx="{cx}" cy="{y+5}" r="10" fill="{color}"/>'
    body += f'<text x="45" y="685" class="small">本轮没有 “measured / 实机测量” artifact。仿真、分析和重建结果均不得改写为 A100/NVLink 实测性能。</text>'
    write_svg("evidence-classification.svg", width, height, "证据分类矩阵", "九个实验 artifact 按输入、Profile、分析、结构、服务仿真、重建和网络仿真分类。", body)


def build_figures(summary: dict, tables: dict[str, list[dict[str, str]]]) -> None:
    figure_evidence_pipeline()
    figure_simai_breakdown(summary)
    figure_simai_comm(summary)
    figure_simai_slice(tables["simai_slice"])
    figure_request_latency(summary)
    figure_middle_block(tables["moe_middle_block"])
    figure_flow_matrix(tables["simccl_flows"])
    figure_ns3_timeline(tables["ns3_flows"])
    figure_nic_throughput(tables["ns3_nics"])
    figure_selected_flow(tables["selected_flow"])
    figure_evidence_classes(tables["evidence_ledger"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--analysis-dir",
        type=Path,
        help="Round-06 root containing analysis/ and experiments/; refresh snapshots first",
    )
    args = parser.parse_args()
    if args.analysis_dir:
        import_snapshots(args.analysis_dir)
    summary, tables = load_data()
    validate_data(summary, tables)
    build_figures(summary, tables)
    print(f"wrote study snapshots and SVG figures under {STATIC_DIR}")


if __name__ == "__main__":
    main()
