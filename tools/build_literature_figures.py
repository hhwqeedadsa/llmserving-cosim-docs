#!/usr/bin/env python3
"""Build deterministic literature-coverage snapshots and SVG figures."""

from __future__ import annotations

import csv
import html
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "research" / "literature_ledger.csv"
STATIC_DIR = ROOT / "source" / "_static" / "study"
DATA_DIR = STATIC_DIR / "data"

INK = "#18212f"
MUTED = "#5f6b7a"
GRID = "#d9e0e8"
PAPER = "#ffffff"
BLUE = "#2563eb"
PURPLE = "#7c3aed"
GREEN = "#059669"


VENUE_ORDER = [
    "SIGCOMM",
    "INFOCOM",
    "NSDI",
    "OSDI",
    "SOSP",
    "MLSys",
    "SC",
    "ISCA",
    "ISPASS",
    "IISWC",
    "FAST",
    "SIGMETRICS",
    "PACMI at SOSP",
    "Hot Interconnects",
    "SC Workshop",
    "SIGCOMM Poster",
]


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def family(category: str) -> str:
    if category.startswith("serving-") or category in {
        "inference-runtime",
        "moe-serving",
        "moe-serving-network",
        "serving-network",
    }:
        return "Serving/runtime"
    if category in {
        "simulation-modeling",
        "workload-measurement",
        "measurement-resilience",
    }:
        return "Measurement/simulation"
    return "Network/collective"


def read_ledger() -> list[dict[str, str]]:
    with LEDGER.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
    required = {
        "id",
        "title",
        "venue",
        "year",
        "doi_or_url",
        "category",
        "measured_object",
        "granularity",
        "evidence_type",
        "optimization_knob",
        "primary_metric",
        "reported_result_or_scope",
        "limitation",
        "relation_to_ours",
    }
    if not required.issubset(reader.fieldnames or []):
        raise SystemExit("literature ledger is missing required fields")
    if not rows or [row["id"] for row in rows] != [f"L{i:02d}" for i in range(1, len(rows) + 1)]:
        raise SystemExit("literature ids must be unique and sequential from L01")
    if any(not all(row[field].strip() for field in required) for row in rows):
        raise SystemExit("literature ledger contains an empty required value")
    if any(not row["doi_or_url"].startswith("https://") for row in rows):
        raise SystemExit("literature source URLs must use HTTPS")
    return rows


def write_summary(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    counts = Counter((row["venue"], family(row["category"])) for row in rows)
    summary = []
    for venue in VENUE_ORDER:
        values = {
            name: counts[(venue, name)]
            for name in ("Serving/runtime", "Measurement/simulation", "Network/collective")
        }
        total = sum(values.values())
        if total:
            summary.append({"venue": venue, **values, "total": total})
    if sum(int(row["total"]) for row in summary) != len(rows):
        raise SystemExit("venue order does not cover every ledger row")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "literature_coverage.csv"
    fields = ["venue", "Serving/runtime", "Measurement/simulation", "Network/collective", "total"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary)
    return summary


def svg_document(width: int, height: int, title: str, desc: str, body: str) -> str:
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


def build_venue_coverage(summary: list[dict[str, object]], paper_count: int) -> None:
    width = 1380
    row_height = 35
    height = 235 + row_height * len(summary)
    plot_x = 230
    plot_y = 105
    plot_w = 1050
    max_total = max(int(row["total"]) for row in summary)
    scale_max = ((max_total + 4) // 5) * 5
    colors = {
        "Serving/runtime": BLUE,
        "Measurement/simulation": PURPLE,
        "Network/collective": GREEN,
    }

    body = (
        '<text x="60" y="42" class="title">Top-venue coverage by research object</text>'
        f'<text x="60" y="67" class="subtitle">{paper_count} included publications as of 2026-09-26. Bar length is ledger coverage, not venue quality or total field output.</text>'
    )
    for tick in range(0, scale_max + 1, 5):
        x = plot_x + tick / scale_max * plot_w
        body += f'<line x1="{x:.2f}" y1="{plot_y-12}" x2="{x:.2f}" y2="{plot_y+row_height*len(summary)}" class="grid"/>'
        body += f'<text x="{x:.2f}" y="{plot_y-22}" text-anchor="middle" class="axis">{tick}</text>'

    for index, row in enumerate(summary):
        y = plot_y + index * row_height
        venue = str(row["venue"])
        body += f'<text x="{plot_x-14}" y="{y+21}" text-anchor="end" class="label">{esc(venue)}</text>'
        x = plot_x
        for name in ("Serving/runtime", "Measurement/simulation", "Network/collective"):
            value = int(row[name])
            bar_w = value / scale_max * plot_w
            if value:
                body += f'<rect x="{x:.2f}" y="{y+4}" width="{bar_w:.2f}" height="22" fill="{colors[name]}"/>'
                if bar_w >= 28:
                    body += f'<text x="{x+bar_w/2:.2f}" y="{y+20}" text-anchor="middle" fill="#ffffff" style="font-size:11px;font-weight:700">{value}</text>'
            x += bar_w
        body += f'<text x="{x+9:.2f}" y="{y+21}" class="value">{row["total"]}</text>'

    legend_y = plot_y + row_height * len(summary) + 35
    legend_x = plot_x
    for index, name in enumerate(("Serving/runtime", "Measurement/simulation", "Network/collective")):
        x = legend_x + index * 245
        body += f'<rect x="{x}" y="{legend_y}" width="15" height="15" fill="{colors[name]}"/>'
        body += f'<text x="{x+24}" y="{legend_y+13}" class="small">{esc(name)}</text>'
    body += f'<text x="{plot_x}" y="{legend_y+47}" class="small">Source: research/literature_ledger.csv; family is derived from category. SC Workshop and SIGCOMM Poster remain separate from main tracks.</text>'

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    output = STATIC_DIR / "literature-venue-coverage.svg"
    output.write_text(
        svg_document(
            width,
            height,
            "Literature coverage by venue and research object",
            "The number of included publications for each venue, split into serving/runtime, measurement/simulation, and network/collective families.",
            body,
        ),
        encoding="utf-8",
    )


def main() -> None:
    rows = read_ledger()
    summary = write_summary(rows)
    build_venue_coverage(summary, len(rows))
    print(f"built literature coverage for {len(rows)} publications")


if __name__ == "__main__":
    main()
