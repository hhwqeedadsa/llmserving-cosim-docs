#!/usr/bin/env python3
"""Build a compact, offline visualization snapshot from a standard run dir."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def quantile(values: list[int], q: float) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    idx = round((len(ordered) - 1) * q)
    return ordered[idx]


def operation_kind(operation_id: str) -> str:
    if ".dispatch" in operation_id:
        return "dispatch"
    if ".combine" in operation_id:
        return "combine"
    if operation_id.startswith("KV.") or ".kv" in operation_id.lower():
        return "kv_transfer"
    return "other"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ports", type=int, default=6)
    parser.add_argument(
        "--source-label",
        help="Public provenance label; defaults to the run directory name",
    )
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    manifest = json.loads((run_dir / "manifest.json").read_text())
    if not manifest.get("clean_finish"):
        raise SystemExit(f"run is not clean: {run_dir}")

    metrics = json.loads((run_dir / "metrics/service_metrics.json").read_text())
    transfers = read_jsonl(run_dir / "truth/transfer_registry.jsonl")

    compact_transfers = []
    ready_values = []
    txn_values = []
    for item in transfers:
        ready = int(item["recv_done_ts"]) - int(item["submit_ts"])
        txn = int(item["transaction_done_ts"]) - int(item["submit_ts"])
        ready_values.append(ready)
        txn_values.append(txn)
        compact_transfers.append({
            "id": item["transfer_id"],
            "op": item["operation_id"],
            "kind": operation_kind(item["operation_id"]),
            "src": int(item["src_rank"]),
            "dst": int(item["dst_rank"]),
            "bytes": int(item["bytes"]),
            "submit": int(item["submit_ts"]),
            "ready": ready,
            "txn": txn,
            "ackTail": txn - ready,
        })

    port_rows: dict[str, list[dict]] = defaultdict(list)
    totals: dict[str, int] = defaultdict(int)
    with (run_dir / "observed/port_windows.csv").open(newline="") as handle:
        for row in csv.DictReader(handle):
            key = f"sw{row['switch_id']}:p{row['port_id']}:{row['direction']}"
            point = {
                "t": int(row["start_local_ns"]),
                "bytes": int(row["counted_packet_bytes"]),
                "packets": int(row["packet_count"]),
            }
            port_rows[key].append(point)
            totals[key] += point["bytes"]

    busiest = sorted(totals, key=totals.get, reverse=True)[: args.ports]
    sample = {
        "source": args.source_label or run_dir.name,
        "runId": manifest["run_id"],
        "summary": {
            "requests": int(metrics["requests_completed"]),
            "simEndNs": int(metrics["sim_end_ns"]),
            "transfers": len(compact_transfers),
            "businessBytes": sum(t["bytes"] for t in compact_transfers),
            "txnP50Ns": quantile(txn_values, 0.50),
            "txnP95Ns": quantile(txn_values, 0.95),
            "txnP99Ns": quantile(txn_values, 0.99),
            "txnMaxNs": max(txn_values),
            "readyP95Ns": quantile(ready_values, 0.95),
        },
        "requests": metrics["requests"],
        "transfers": compact_transfers,
        "ports": [{
            "id": key,
            "totalBytes": totals[key],
            "points": port_rows[key],
        } for key in busiest],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    body = "window.MOE_SAMPLE_RUN = " + json.dumps(
        sample, ensure_ascii=False, separators=(",", ":")
    ) + ";\n"
    args.output.write_text(body)


if __name__ == "__main__":
    main()
