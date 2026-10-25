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
    strong = load("experiments/nic_wakeup_effect/results/20261024/summary.json",
                  "较强基线：修复源NIC后同版本正常/故障任务结果")
    load("experiments/selective_externality/protocol.md", "通信窗口：参数与执行前设计", "text")
    load("experiments/causal_fault/README.md", "旧式任务图的适用范围", "text")
    load("docs/reviews/原生批次交付与四卡完整调用_20261025.md", "四卡完整调用的结果与捕获边界", "text")
    load("docs/reviews/源NIC修复后的完整应用与拥塞反馈_20261024.md", "较强基线的配置、负面结果与适用范围", "text")
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

    def room(name, label, healthy, fault, scope):
        assert fault >= healthy > 0
        return {"id": name, "label": label, "healthy_ns": healthy, "fault_ns": fault,
                "gap_ns": fault - healthy, "reference_gap_percent": (fault - healthy) / fault * 100,
                "scope": scope, "is_strict_optimization_bound": False,
                "new_method_gain_measured": False}

    room_cases = [room("window-ack", "通信窗口 · 确认（主指标）",
        window["cases"]["healthy-selective"]["all_work_ack_ns"],
        window["cases"]["failed-selective"]["all_work_ack_ns"], "实验A；全部0ns发布；不是完整任务"),
        room("window-receive", "通信窗口 · 接收",
        window["cases"]["healthy-selective"]["all_work_accepted_ns"],
        window["cases"]["failed-selective"]["all_work_accepted_ns"], "实验A；全部0ns发布；不是完整任务")]
    for name, label in [("delayed", "较强任务基线 · 延迟改路"), ("instant", "较强任务基线 · 立即改路")]:
        room_cases.append(room("strong-" + name, label,
            strong["cases"]["fixed-healthy"]["application_completion_ns"],
            strong["cases"]["fixed-live-" + name]["application_completion_ns"],
            "旧式两层两步参考；含已有恢复、端口过滤、16us乱序等待及源NIC修复；非现代完整推理"))

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
        "room_analysis": {"cases": room_cases,
                          "scenario_formula": "(affected_fraction * eliminated_fraction * gap_ns - overhead_ns_per_request) / (healthy_ns + affected_fraction * gap_ns) * 100",
                          "scenario_is_measurement": False,
                          "fault_rate_measured": False,
                          "new_method_gain_measured": False},
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
        "@@ROOM_ROWS@@": "\n".join(
            f'<tr><th scope="row">{html.escape(r["label"])}</th>'
            f'<td>{r["healthy_ns"] / 1000:.3f}</td><td>{r["fault_ns"] / 1000:.3f}</td>'
            f'<td>{r["gap_ns"] / 1000:.3f}</td><td>{r["reference_gap_percent"]:.2f}%</td></tr>'
            for r in room_cases),
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
