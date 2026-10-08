#!/usr/bin/env python3
"""Copy the explicitly selected rebuilt research artifacts into the website repo."""

import argparse
from pathlib import Path
import shutil

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--workspace", type=Path, required=True)
args = parser.parse_args()
site = Path(__file__).resolve().parents[2]
snapshots = Path(__file__).resolve().parent / "analysis_sources"
snapshots.mkdir(exist_ok=True)
round09 = args.workspace / "第09轮_模型硬件拓扑trace与网络竞争_20260929"
round10 = args.workspace / "第10轮_长时间网络竞争流量_20260930"
for root, prefix, filenames in [(round09, "round09", ["build_ledgers.py", "generate_figures.py"]),
                                (round10, "round10", ["analyze_long_window.py", "test_long_window.py", "generate_figures.py"])]:
    for filename in filenames:
        shutil.copy2(root / "analysis" / filename, snapshots / (prefix + "_" + filename))
for name in ["long_window_packet_events.csv", "long_window_bandwidth_timeseries.csv", "long_window_queue_timeseries.csv",
             "long_window_task_windows.csv", "long_window_wave_summary.csv", "zoom_w1_w2_bandwidth_timeseries.csv", "zoom_w1_w2_queue_timeseries.csv"]:
    shutil.copy2(round10 / "analysis" / name, site / "research/round10" / name)
for name in ["long-window-network-traffic-waves.svg", "zoomed-w1-w2-contention-peaks.svg"]:
    shutil.copy2(round10 / "visualizations" / name, site / "source/_static/round10" / name)
shutil.copy2(round10 / "experiments/experiment-spec.md", site / "research/round10/experiments/experiment-spec.md")
shutil.copy2(round09 / "analysis/huawei_384_mapping.csv", site / "research/round09/huawei_384_mapping.csv")
shutil.copy2(round09 / "visualizations/huawei-384-logical-mapping.svg", site / "source/_static/round09/huawei-384-logical-mapping.svg")
for root, number, report in [(round09, "round09", "模型硬件拓扑trace与网络竞争报告.md"),
                             (round10, "round10", "长时间网络竞争与流量波形报告.md")]:
    content = (root / "reports" / report).read_text(encoding="utf-8")
    content = content.replace("../visualizations/", f"../../source/_static/{number}/")
    (site / "research" / number / "report.md").write_text(content, encoding="utf-8")
print("Selected rebuilt artifacts synchronized; no simulation inputs or logs modified.")
