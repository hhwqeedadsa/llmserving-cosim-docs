#!/usr/bin/env python3
"""Build the offline weekly report from existing, versioned evidence only."""
import hashlib
import html
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def main():
    sources = []

    def load(path, label, kind="json"):
        raw = (ROOT / path).read_bytes()
        sources.append({"path": path, "label": label,
                        "sha256": hashlib.sha256(raw).hexdigest()})
        return json.loads(raw) if kind == "json" else raw.decode("utf-8")

    window = load("experiments/selective_externality/results/20261024/summary.json",
                  "通信窗口：四条件结果与逐流分析")
    graph = load("experiments/causal_fault/results/20261024/summary.json",
                 "保留任务依赖：四条件参考结果")
    native = load("fixtures/native_callback_batches/verification.json",
                  "四卡完整功能调用：独立核验")
    load("experiments/selective_externality/protocol.md", "通信窗口：参数与执行前设计", "text")
    load("experiments/causal_fault/README.md", "旧式任务图的适用范围", "text")
    load("docs/reviews/原生批次交付与四卡完整调用_20261025.md", "四卡完整调用的结果与捕获边界", "text")
    assert window["passed"] and native["verified"]
    assert native["original_four_rank_call_complete"]
    assert not window["complete_application_executed"]
    four = native["cases"]["four-routes"]
    assert four["application"]["passed"] and not four["capture_complete"]
    interference = next(x for x in window["all_stream_comparisons"]
                        if x["timer"] == "selective" and x["stream"] == 8)
    assert interference["witness_same_offset"]
    assert interference["accepted_delta_ns"] == sum(interference[k] for k in
        ("witness_source_tx_delta_ns", "witness_queue_delta_ns", "witness_nonqueue_delta_ns"))

    def cases(data, fields):
        return {name: {k: row[k] for k in fields} for name, row in data["cases"].items()}

    data = {
        "schema": "moe-weekly-network-report/1", "as_of": "2026-10-25",
        "window": {"platform_commit": window["platform_commit"],
                   "cases": cases(window, ["all_work_accepted_ns", "all_work_ack_ns", "tx_payload_bytes"]),
                   "fault_effect": window["fault_effect"]},
        "graph": {"cases": cases(graph, ["group_completion_ns", "tx_payload_bytes"]),
                  "contrasts": graph["contrasts"], "scope": graph["scope"]},
        "four_rank": {"application": four["application"], "delivery": four["delivery"],
                      "original_decisions": four["original_decisions"],
                      "capture_complete": four["capture_complete"],
                      "omitted_capture_events": four["omitted_capture_events"]},
        "interference": interference,
        "stream8": {case: {
            **{key: row[key] for key in ("accepted_ns", "acked_ns", "tx_payload_bytes")},
            "witness": {key: row["witness"][key] for key in
                        ("source_tx_ns", "destination_rx_ns", "queue_wait_ns", "nonqueue_transit_ns")},
            "queues": [{key: q[key] for key in ("node", "peer", "wait_ns")}
                       for q in row["witness"]["queues"]]}
            for case, row in window["preregistered_stream8"].items()},
        "sources": sources,
    }
    labels = {"healthy-gbn": "正常 / 回退重传", "failed-gbn": "故障 / 回退重传",
              "healthy-selective": "正常 / 选择性重传", "failed-selective": "故障 / 选择性重传"}
    def raw_table(rows, fields):
        return "\n".join("<tr><th scope=\"row\">" + labels[name] + "</th>" +
            "".join(f"<td>{row[key] / 1000:.3f}</td>" if key.endswith("_ns") else
                    f"<td>{row[key]:,}</td>" for key in fields) + "</tr>"
            for name, row in rows.items())
    substitutions = {
        "@@REPORT_DATA@@": json.dumps(data, ensure_ascii=False, indent=2).replace("<", "\\u003c"),
        "@@WINDOW_ROWS@@": raw_table(data["window"]["cases"], ["all_work_accepted_ns", "all_work_ack_ns", "tx_payload_bytes"]),
        "@@GRAPH_ROWS@@": raw_table(data["graph"]["cases"], ["group_completion_ns", "tx_payload_bytes"]),
        "@@SOURCE_ROWS@@": "\n".join(
            f'<tr><td><a href="../../../{html.escape(s["path"], quote=True)}">{html.escape(s["label"])}</a>'
            f'<small>{html.escape(s["path"])}</small></td><td><code>{s["sha256"]}</code></td></tr>' for s in sources),
    }
    report = (HERE / "report.template.html").read_text()
    for marker, value in substitutions.items():
        assert marker in report, marker
        report = report.replace(marker, value)
    assert "@@" not in report
    (HERE / "report-data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    (HERE / "report.html").write_text(report)
    print(json.dumps({"report": str(HERE / "report.html"), "sources": len(sources),
                      "report_bytes": len(report.encode()), "new_network_executions": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
