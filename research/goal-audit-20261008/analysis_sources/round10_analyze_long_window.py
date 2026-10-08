#!/usr/bin/env python3
"""Reconstruct a 1.1-ms long-window traffic profile from ns-3 traces."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CASE = (
    ROOT.parent
    / "第05轮_联合仿真M0_20260919/ns-3-ub/repo/scratch/20260930-long-window-contention/case"
)

BIN_US = 5.0
QUEUE_BIN_US = 1.0

HORIZON_US = 1100.0
CORE_CAPACITY_GBPS = 100.0

WIRE_OVERHEAD_BYTES = 78
HEADER_RE = re.compile(
    r"Uid:(?P<uid>\d+)\s+Psn:(?P<psn>\d+)\s+Src:(?P<src>\d+)\s+Dst:(?P<dst>\d+)"
    r".*?Type:PKT\s+Size:(?P<size>\d+)\s+TaskId:(?P<task>\d+)"
)

BRACKET_RE = re.compile(r"\[\s*(\d+)\s*\]")
PORT_RE = re.compile(
    r"\[(?P<time>[0-9.]+)us\]\s+Port\s+(?P<direction>Tx|Rx).*?PacketSize:\s*(?P<size>\d+)"
)
QUEUE_RE = re.compile(
    r"\[(?P<time>[0-9.]+)us\].*?totalBytes:\s*(?P<bytes>\d+)"
)

FLOW_KIND = {
    0: "target",
    1: "target",
    2: "ep_competitor",
    3: "target",
    4: "reverse_ep",
    5: "target",
    6: "kv_competitor",
    7: "balanced_dispatch",
    8: "balanced_dispatch",
    9: "balanced_dispatch",
    10: "balanced_dispatch",
    11: "target",
    12: "ep_competitor",
    13: "target",
    14: "kv_competitor",
    15: "reverse_ep",
    16: "target",
    17: "target",
    18: "ep_competitor",
    19: "kv_competitor",
}

WAVE_LABEL = {
    0: "目标基线",
    1: "同向 EP",
    2: "反向 EP",
    3: "同向 KV",
    4: "四 peer dispatch",
    5: "50% EP",
    6: "KV + 反向 EP",
    7: "目标基线",
    8: "同向 EP",
    9: "KV drain",
}


def parse_packet_file(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    packets = []
    for index, line in enumerate(lines):
        match = HEADER_RE.search(line)
        if not match or index + 2 >= len(lines):
            continue
        payload_bytes = int(match.group("size"))
        if payload_bytes < 1024:
            continue
        timestamps_ns = [int(value) for value in BRACKET_RE.findall(lines[index + 2])]
        if len(timestamps_ns) < 6:
            raise ValueError(f"Expected six hop timestamps in {path}: {lines[index:index + 3]}")
        src = int(match.group("src"))
        dst = int(match.group("dst"))
        packets.append(
            {
                "uid": int(match.group("uid")),
                "psn": int(match.group("psn")),
                "src": src,
                "dst": dst,
                "task_id": int(match.group("task")),
                "payload_bytes": payload_bytes,
                "wire_bytes": payload_bytes + WIRE_OVERHEAD_BYTES,
                "direction": "L2R" if src < 4 and dst >= 4 else "R2L",
                "core_egress_rounded_us": timestamps_ns[2] / 1000.0,
            }
        )
    return packets


def port_data_events(path: Path) -> list[tuple[float, int]]:
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = PORT_RE.search(line)
        if match and match.group("direction") == "Tx" and int(match.group("size")) >= 1024:
            events.append((float(match.group("time")), int(match.group("size"))))
    return events


def assign_precise_core_egress(packets: pd.DataFrame) -> None:
    packets["packet_start_us"] = packets.core_egress_rounded_us
    for direction, node in [("L2R", 8), ("R2L", 9)]:
        selected = packets[packets.direction == direction]
        packet_events = sorted(
            (float(row.core_egress_rounded_us), int(row.wire_bytes))
            for row in selected.itertuples()
        )
        port_events = sorted(
            port_data_events(CASE / "runlog" / f"PortTrace_node_{node}_port_4.tr")
        )
        matched = len(packet_events) == len(port_events) and all(
            abs(packet_time - port_time) <= 0.001 and packet_size == port_size
            for (packet_time, packet_size), (port_time, port_size) in zip(
                packet_events, port_events
            )
        )
        if not matched:
            raise AssertionError(
                f"{direction}: AllPacketTrace and core PortTrace mismatch "
                f"({len(packet_events)} vs {len(port_events)})"
            )
        indices = selected.sort_values(
            ["core_egress_rounded_us", "task_id", "psn"]
        ).index
        packets.loc[indices, "packet_start_us"] = [time for time, _ in port_events]


def time_edges(bin_us: float, start_us: float, end_us: float) -> np.ndarray:
    if not all(np.isfinite([bin_us, start_us, end_us])) or bin_us <= 0 or end_us <= start_us:
        raise ValueError("Expected finite, positive bin width and increasing time bounds")
    edges = np.arange(start_us, end_us, bin_us)
    return np.append(edges, end_us)


def integrate_bandwidth(
    packets: pd.DataFrame,
    bin_us: float = BIN_US,
    start_us: float = 0.0,
    end_us: float = HORIZON_US,
) -> pd.DataFrame:
    edges = time_edges(bin_us, start_us, end_us)
    rows = []
    groups = ["target", "ep_competitor", "kv_competitor", "balanced_dispatch", "reverse_ep"]
    directions = {"target": "L2R", "ep_competitor": "L2R", "kv_competitor": "L2R", "balanced_dispatch": "L2R", "reverse_ep": "R2L"}
    for flow_kind in groups:
        group = packets[packets.flow_kind == flow_kind]
        wire_bytes = np.zeros(len(edges) - 1, dtype=float)
        for packet in group.itertuples():
            start = float(packet.packet_start_us)
            end = float(packet.packet_end_us)
            if end <= start_us or start >= end_us:
                continue
            left = max(0, int(np.floor((start - start_us) / bin_us)))
            right = min(
                len(wire_bytes) - 1,
                int(np.floor((end - start_us - 1e-12) / bin_us)),
            )
            for bin_index in range(left, right + 1):
                overlap = max(
                    0.0,
                    min(end, edges[bin_index + 1]) - max(start, edges[bin_index]),
                )
                wire_bytes[bin_index] += (
                    overlap * CORE_CAPACITY_GBPS * 1000.0 / 8.0
                )
        for bin_index, byte_count in enumerate(wire_bytes):
            width = edges[bin_index + 1] - edges[bin_index]
            bandwidth = byte_count * 8.0 / (width * 1000.0)
            rows.append(
                {
                    "flow_kind": flow_kind,
                    "direction": directions[flow_kind],
                    "bin_start_us": edges[bin_index],
                    "bin_end_us": edges[bin_index + 1],
                    "bin_center_us": (edges[bin_index] + edges[bin_index + 1]) / 2.0,
                    "wire_bytes_in_bin": byte_count,
                    "bandwidth_gbps": bandwidth,
                    "utilization_pct": bandwidth / CORE_CAPACITY_GBPS * 100.0,
                    "link_capacity_gbps": CORE_CAPACITY_GBPS,
                    "bin_width_us": width,
                    "evidence": "ns3-simulated/trace-derived",
                }
            )
    return pd.DataFrame(rows)


def sample_queue(
    direction: str,
    node: int,
    bin_us: float = QUEUE_BIN_US,
    start_us: float = 0.0,
    end_us: float = HORIZON_US,
) -> pd.DataFrame:
    path = CASE / "runlog" / f"QueueTrace_node_{node}_port_4.tr"
    updates = []
    for order, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        match = QUEUE_RE.search(line)
        if match:
            updates.append((float(match.group("time")), order, int(match.group("bytes"))))
    frame = pd.DataFrame(updates, columns=["time_us", "order", "queue_bytes"])
    if frame.empty:
        raise ValueError(f"No queue events in {path}")
    raw_window = frame[(frame.time_us >= start_us) & (frame.time_us < end_us)]
    frame = frame.sort_values(["time_us", "order"]).groupby("time_us", as_index=False).tail(1)
    edges = time_edges(bin_us, start_us, end_us)
    centers = (edges[:-1] + edges[1:]) / 2.0
    times = frame.time_us.to_numpy()
    values = frame.queue_bytes.to_numpy()
    indices = np.searchsorted(times, centers, side="right") - 1
    sampled = np.where(indices >= 0, values[np.maximum(indices, 0)], 0)
    initial_index = np.searchsorted(times, start_us, side="left") - 1
    initial_value = int(values[initial_index]) if initial_index >= 0 else 0
    candidates = [(start_us, initial_value)] + list(
        raw_window[["time_us", "queue_bytes"]].itertuples(index=False, name=None)
    )
    peak_time, peak_bytes = max(candidates, key=lambda item: item[1])
    return pd.DataFrame(
        {
            "direction": direction,
            "bin_center_us": centers,
            "queue_bytes": sampled.astype(int),
            "queue_kib": sampled / 1024.0,
            "raw_max_queue_bytes": int(peak_bytes),
            "raw_max_queue_kib": float(peak_bytes) / 1024.0,
            "raw_max_time_us": float(peak_time),
            "raw_scope_start_us": start_us,
            "raw_scope_end_us": end_us,
            "node_id": node,
            "port_id": 4,
            "bin_width_us": np.diff(edges),
            "evidence": "ns3-simulated/trace-derived",
        }
    )


def task_windows() -> pd.DataFrame:
    tasks = pd.read_csv(CASE / "output/task_statistics.csv")
    tasks = tasks.rename(
        columns={
            "taskId": "task_id",
            "sourceNode": "src",
            "destNode": "dst",
            "dataSize(Byte)": "payload_bytes",
            "phaseId": "wave_id",
            "taskStartTime(us)": "task_start_us",
            "taskCompletesTime(us)": "task_complete_us",
            "firstPacketSends(us)": "first_packet_us",
            "lastPacketACKs(us)": "last_ack_us",
            "taskThroughput(Gbps)": "task_throughput_gbps",
        }
    )
    tasks["flow_kind"] = tasks.task_id.map(FLOW_KIND)
    tasks["wave_label"] = tasks.wave_id.map(WAVE_LABEL)
    tasks["direction"] = np.where((tasks.src < 4) & (tasks.dst >= 4), "L2R", "R2L")
    tasks["packet_fct_us"] = tasks.last_ack_us - tasks.first_packet_us
    tasks["evidence"] = "ns3-simulated/trace-derived"
    return tasks[
        [
            "wave_id",
            "wave_label",
            "task_id",
            "flow_kind",
            "direction",
            "src",
            "dst",
            "payload_bytes",
            "task_start_us",
            "task_complete_us",
            "first_packet_us",
            "last_ack_us",
            "packet_fct_us",
            "task_throughput_gbps",
            "evidence",
        ]
    ]


def wave_summary(tasks: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for wave_id, group in tasks.groupby("wave_id", sort=True):
        targets = group[group.flow_kind == "target"]
        rows.append(
            {
                "wave_id": int(wave_id),
                "wave_label": WAVE_LABEL[int(wave_id)],
                "release_us": float(group.task_start_us.min()),
                "task_count": len(group),
                "l2r_payload_bytes": int(group[group.direction == "L2R"].payload_bytes.sum()),
                "r2l_payload_bytes": int(group[group.direction == "R2L"].payload_bytes.sum()),
                "phase_last_ack_us": float(group.last_ack_us.max()),
                "wave_duration_us": float(group.last_ack_us.max() - group.first_packet_us.min()),
                "target_fct_us": float(targets.packet_fct_us.iloc[0]) if len(targets) else np.nan,
                "evidence": "ns3-simulated/trace-derived",
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    tasks = task_windows()
    packet_rows = []
    for source in sorted(tasks.src.unique()):
        packet_rows.extend(
            parse_packet_file(CASE / "runlog" / f"AllPacketTrace_PKT_node_{source}.tr")
        )
    packets = pd.DataFrame(packet_rows)
    packets["flow_kind"] = packets.task_id.map(FLOW_KIND)
    packets["wave_id"] = packets.task_id.map(tasks.set_index("task_id").wave_id)
    packets["wave_label"] = packets.wave_id.map(WAVE_LABEL)
    assign_precise_core_egress(packets)
    packets["serialization_us"] = (
        packets.wire_bytes * 8.0 / (CORE_CAPACITY_GBPS * 1000.0)
    )
    packets["packet_end_us"] = packets.packet_start_us + packets.serialization_us
    packets["link_capacity_gbps"] = CORE_CAPACITY_GBPS
    packets["evidence"] = "ns3-simulated/trace-derived"

    bandwidth = integrate_bandwidth(packets)
    queues = pd.concat(
        [sample_queue("L2R", 8), sample_queue("R2L", 9)],
        ignore_index=True,
    )
    summary = wave_summary(tasks)
    zoom_bandwidth = integrate_bandwidth(
        packets,
        bin_us=0.25,
        start_us=100.0,
        end_us=280.0,
    )
    zoom_queues = pd.concat(
        [
            sample_queue("L2R", 8, bin_us=0.25, start_us=100.0, end_us=280.0),
            sample_queue("R2L", 9, bin_us=0.25, start_us=100.0, end_us=280.0),
        ],
        ignore_index=True,
    )

    packets.to_csv(ROOT / "analysis/long_window_packet_events.csv", index=False, float_format="%.6f")
    bandwidth.to_csv(ROOT / "analysis/long_window_bandwidth_timeseries.csv", index=False, float_format="%.6f")
    queues.to_csv(ROOT / "analysis/long_window_queue_timeseries.csv", index=False, float_format="%.6f")
    tasks.to_csv(ROOT / "analysis/long_window_task_windows.csv", index=False, float_format="%.6f")
    summary.to_csv(ROOT / "analysis/long_window_wave_summary.csv", index=False, float_format="%.6f")
    zoom_bandwidth.to_csv(ROOT / "analysis/zoom_w1_w2_bandwidth_timeseries.csv", index=False, float_format="%.6f")
    zoom_queues.to_csv(ROOT / "analysis/zoom_w1_w2_queue_timeseries.csv", index=False, float_format="%.6f")

    direction_max = (
        bandwidth.groupby(["direction", "bin_center_us"], as_index=False).bandwidth_gbps.sum()
        .groupby("direction").bandwidth_gbps.max().round(6).to_dict()
    )
    queue_max = queues.groupby("direction").raw_max_queue_kib.first().round(3).to_dict()
    print(
        f"tasks={len(tasks)} packets={len(packets)} waves={len(summary)} "
        f"horizon_us={HORIZON_US} bandwidth_bin_us={BIN_US}"
    )
    print("max_bandwidth_gbps=", direction_max)
    print("raw_max_queue_kib=", queue_max)


if __name__ == "__main__":
    main()
