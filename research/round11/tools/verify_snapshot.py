#!/usr/bin/env python3
"""Portable validation of the published round11 snapshot (not hardware accuracy)."""
import csv
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd

def main(root):
    files=pd.read_csv(root/"published-files.csv")
    for r in files.itertuples():
        p=root/r.path
        assert p.is_file(), p
        assert hashlib.sha256(p.read_bytes()).hexdigest()==r.sha256, p
    summary=pd.read_csv(root/"analysis/summary.csv").set_index("case_id")
    assert set(summary.index)=={"no_kv_100","kv_100","kv_200"}
    assert int(summary.tasks.sum())==820 and int(summary.packets.sum())==59600
    cases=root/"experiment/cases"
    for name,row in summary.iterrows():
        case=cases/name; d=root/"analysis"/name
        raw=pd.read_csv(case/"output/task_statistics.csv")
        flow=pd.read_csv(case/"flow_manifest.csv")
        traffic=pd.read_csv(case/"traffic.csv")
        t=pd.read_csv(d/"tasks.csv"); p=pd.read_csv(d/"packets.csv")
        ph=pd.read_csv(d/"phases.csv")
        assert len(raw)==len(flow)==len(traffic)==len(t)==int(row.tasks)
        assert len(p)==int(row.packets) and not p.uid.duplicated().any()
        pd.testing.assert_series_equal(p.groupby("task_id").payload_bytes.sum().sort_index(),
             t.set_index("task_id").payload_bytes.sort_index(),check_names=False)
        for original,derived in [("taskStartTime(us)","start_us"),
            ("taskCompletesTime(us)","complete_us"),("lastPacketACKs(us)","last_ack_us")]:
            np.testing.assert_allclose(raw.set_index("taskId")[original].sort_index(),
                t.set_index("task_id")[derived].sort_index(),atol=1e-8,rtol=0)
        infer=ph[ph.group!="KV"]
        assert abs(infer.complete_us.max()-row.inference_end_us)<1e-7
        assert abs(infer.duration_us.quantile(.95)-row.phase_p95_us)<1e-7
        index=ph.set_index("phase_id")
        for phase in ph.itertuples():
            expect=phase.delay_us if pd.isna(phase.depends_on_phase) else (
                index.loc[int(phase.depends_on_phase),"complete_us"]+.02+phase.delay_us)
            assert abs(phase.start_us-expect)<1e-6
        assert abs((p.end_us-p.start_us-p.wire_bytes*8/(row.core_gbps*1000)).abs().max())<2e-8
        b=pd.read_csv(d/"bandwidth_5us.csv")
        assert abs(b.wire_bytes.sum()-p.wire_bytes.sum())<.02
        assert b.groupby(["direction","center_us"]).gbps.sum().max()<=row.core_gbps+1e-5
        assert t.last_ack_us.max()<8400
        record=json.loads((case/"execution.json").read_text())
        assert record["returncode"]==0 and record["status"]=="success"
    a=cases/"no_kv_100"; b=cases/"kv_100"; c=cases/"kv_200"
    for name in ["node.csv","routing_table.csv","network_attribute.txt"]:
        assert (a/name).read_bytes()==(b/name).read_bytes()==(c/name).read_bytes()
    assert (a/"topology.csv").read_bytes()==(b/"topology.csv").read_bytes()
    tb=pd.read_csv(b/"topology.csv"); tc=pd.read_csv(c/"topology.csv")
    assert int((tb!=tc).sum().sum())==1
    assert tb.loc[8,"bandwidth"]=="100Gbps" and tc.loc[8,"bandwidth"]=="200Gbps"
    assert (b/"traffic.csv").read_bytes()==(c/"traffic.csv").read_bytes()
    pd.testing.assert_frame_equal(pd.read_csv(a/"traffic.csv"),pd.read_csv(b/"traffic.csv").iloc[:240])
    for name in ["dependent-long-window","dependent-peak-zoom","packet-slot-zoom","controlled-comparison"]:
        assert (root/f"visualizations/{name}.png").stat().st_size>10000
    print(json.dumps({"status":"passed","published_files":len(files),
        "tasks":int(summary.tasks.sum()),"packets":int(summary.packets.sum()),
        "scope":"snapshot hashes, original-task joins, payloads, dependencies, serialization, bandwidth integrals, fixed controls; not hardware accuracy"}))

if __name__=="__main__":
    main(Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parents[1])
