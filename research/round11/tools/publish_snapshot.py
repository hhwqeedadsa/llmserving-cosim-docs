#!/usr/bin/env python3
"""Publish only this round's reproducibility snapshot; preserve all historical files."""
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]
REPO=Path("/home/lry/a100_ep_runtime/第05轮_联合仿真M0_20260919/ns-3-ub/repo")
PKG=REPO/"scratch/20261008-dependent-moe-kv-contention"
WEB=Path("/home/lry/llmserving-cosim-sphinx")
DEST=WEB/"research/round11"
STATIC=WEB/"source/_static/round11"

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def csv_write(path,rows):
    with path.open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]))
        w.writeheader();w.writerows(rows)

def main():
    DEST.mkdir(parents=True,exist_ok=True); STATIC.mkdir(parents=True,exist_ok=True)
    # Copy profile metadata as provenance, not as a new measurement.
    profile=REPO.parents[2]/"第04轮_仿真平台验证_20260917/LLMServingSim/repo/profiler/perf/RTXPRO6000/Qwen/Qwen3-30B-A3B-Instruct-2507/bf16/meta.yaml"
    shutil.copy2(profile,ROOT/"sources/profile-meta.yaml")
    for dirname in ["sources","tools","reports","analysis","visualizations"]:
        for file in (ROOT/dirname).rglob("*"):
            if not file.is_file() or "__pycache__" in file.parts:
                continue
            target=DEST/file.relative_to(ROOT); target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(file,target)
    for file in (ROOT/"visualizations").glob("*.svg"):
        shutil.copy2(file,STATIC/file.name)
    names=["experiment-plan.md","matrix.yaml","command-manifest.yaml","run-ledger.md","generation.json"]
    for name in names:
        target=DEST/"experiment"/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(PKG/name,target)
    for case in (PKG/"cases").iterdir():
        for file in case.iterdir():
            if file.is_file():
                target=DEST/"experiment/cases"/case.name/file.name
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,target)
        target=DEST/"experiment/cases"/case.name/"output"
        target.mkdir(exist_ok=True)
        shutil.copy2(case/"output/task_statistics.csv",target/"task_statistics.csv")
    # Inventory original evidence, including raw traces retained locally.
    originals=[]
    for file in sorted(PKG.rglob("*")):
        if file.is_file():
            originals.append(dict(path=str(file),sha256=sha(file),bytes=file.stat().st_size))
    originals.append(dict(path=str(profile),sha256=sha(profile),bytes=profile.stat().st_size))
    csv_write(ROOT/"analysis/original-artifacts.csv",originals)
    shutil.copy2(ROOT/"analysis/original-artifacts.csv",DEST/"analysis/original-artifacts.csv")
    provenance=dict(repo=str(REPO),commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=REPO,text=True).strip(),
        dirty_core_sha256={str(f.relative_to(REPO)):sha(f) for f in [
            REPO/"src/unified-bus/model/protocol/ub-transaction.cc",
            REPO/"src/unified-bus/model/protocol/ub-transaction.h"]},
        raw_trace_policy="Raw runlog retained locally; all original hashes inventoried. Published snapshot includes inputs, parser outputs and derived packet/queue evidence.",
        date_timezone="2026-10-08 Asia/Shanghai")
    (DEST/"provenance.json").write_text(json.dumps(provenance,indent=2)+"\n")
    # Normalize authored text only; CSV, raw parser output and run records keep original bytes.
    for file in DEST.rglob("*"):
        if file.is_file() and file.suffix in {".py",".md",".yaml",".svg"} and "sources" not in file.parts:
            file.write_text("\n".join(line.rstrip() for line in file.read_text().splitlines()).rstrip()+"\n")
    rows=[dict(path=str(f.relative_to(DEST)),sha256=sha(f),bytes=f.stat().st_size)
          for f in sorted(DEST.rglob("*")) if f.is_file() and f.name!="published-files.csv"]
    csv_write(DEST/"published-files.csv",rows)
    print("published files",len(rows),"original files",len(originals),
          "bytes",sum(r["bytes"] for r in rows))

if __name__=="__main__":
    main()
