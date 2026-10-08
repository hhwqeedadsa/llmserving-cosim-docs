#!/usr/bin/env python3
"""Execute the registered manifest once, preserving failures and raw outputs."""
from datetime import datetime
from zoneinfo import ZoneInfo
import json
from pathlib import Path
import subprocess
import time
import yaml

REPO = Path("/home/lry/a100_ep_runtime/第05轮_联合仿真M0_20260919/ns-3-ub/repo")
PKG = REPO / "scratch/20261008-dependent-moe-kv-contention"

def now():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()

def main():
    commands = yaml.safe_load((PKG / "command-manifest.yaml").read_text())["commands"]
    for step in commands:
        if step["phase"] != "run":
            continue
        case = Path(step["output_dir"])
        if (case / "runlog").exists() or (case / "execution.json").exists():
            raise FileExistsError(f"Existing run: {case}")
        record = dict(case_id=step["case_id"], command=step["command"],
                      start=now(), status="running", checkpoint_policy="continue_full_matrix")
        (case / "execution.json").write_text(json.dumps(record,indent=2)+"\n")
        print("RUN", step["case_id"], flush=True)
        tick = time.monotonic()
        with (case / "run-output.txt").open("w") as log:
            result = subprocess.run(step["command"], shell=True, cwd=REPO, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=120)
        record.update(end=now(), wall_seconds=time.monotonic()-tick, returncode=result.returncode)
        required = [case / name for name in ["canonical.rank0.txt","output/task_statistics.csv","runlog"]]
        record["artifacts"] = {str(f.relative_to(case)):f.exists() for f in required}
        record["status"] = "success" if result.returncode == 0 and all(record["artifacts"].values()) else "failed"
        record["failure_category"] = None if record["status"] == "success" else "execution_or_missing_artifacts"
        record["retryable"] = record["status"] != "success"
        (case / "execution.json").write_text(json.dumps(record,indent=2)+"\n")
        with (PKG / "run-ledger.md").open("a") as ledger:
            ledger.write(f"\n### {record['case_id']}\nStatus: {record['status']}; return code: {result.returncode}; start {record['start']}; end {record['end']}; wall {record['wall_seconds']:.3f} s.\nCommand: `{record['command']}`\nArtifacts: {record['artifacts']}\nFailure: {record['failure_category']}; retryable: {record['retryable']}.\nCheckpoint: {'continue to next pre-registered case' if record['status']=='success' else 'safety stop'}.\n")
        print(json.dumps(record, ensure_ascii=False), flush=True)
        if record["status"] != "success":
            raise RuntimeError(record)

if __name__ == "__main__":
    main()
