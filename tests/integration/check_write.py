"""Run the bounded M1.3 write cases and verify actual WQE/ASTRA evidence."""
import argparse
import collections
import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    build = args.build_dir.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    facts = dict(line.split("=", 1) for line in
                 (build / "cosim-m1/write-target.txt").read_text().splitlines())
    binary = Path(facts["write"])
    fixture = json.loads((root / "fixtures/m1_transfer/single_write.json").read_text())
    system = root / "fixtures/m1_compute/system.json"
    case = root / "third_party/ns-3-ub/scratch/2nodes_single-tp"
    records = []

    def invoke(label, command):
        started = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
        (output / (label + ".stdout.log")).write_text(result.stdout)
        (output / (label + ".stderr.log")).write_text(result.stderr)
        records.append({"label": label, "cwd": str(root), "argv": command,
                        "started_utc": started, "exit_code": result.returncode,
                        "finished_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()})
        (output / "commands.json").write_text(json.dumps(records, indent=2) + "\n")
        return result

    libraries = {build / "output/lib" / ("libns3.44-" + name + ".so") for name in
                 ("applications", "bridge", "config-store", "core", "internet", "network",
                  "point-to-point", "stats", "traffic-control", "unified-bus")}
    results = {}
    for label, updates in [("receive-first", {}), ("send-first", {"receive_start_ns": 30}),
                           ("late-receive", {"receive_start_ns": 20000}),
                           ("late-reclaim", {"receive_start_ns": 20000, "reclaim_tps": True}),
                           ("one-byte", {"bytes": 1}), ("reclaim-tps", {"reclaim_tps": True})]:
        data = dict(fixture, **updates)
        source = output / (label + ".json")
        source.write_text(json.dumps(data, indent=2) + "\n")
        run_dir = output / label
        result = invoke(label, ["timeout", "--kill-after=5s", "30s", str(binary), str(source),
                               str(system), str(case), str(run_dir)])
        assert result.returncode == 0, (label, result.stdout, result.stderr)
        assert not result.stderr.strip(), (label, result.stderr)
        report = json.loads((run_dir / "result.json").read_text())
        expected = {"submitted_tasks": 1, "send_callbacks": 1, "receive_callbacks": 1,
                    "receiver_waiting": False, "target_graph_finished": True,
                    "remaining_receive_nodes": False, "final_receive_slots": 0,
                    "pending_target_events": 0, "context_active": False, "observing_target": False,
                    "committed_bytes": data["bytes"], "initial_send_slots": 0, "send_slots_after_issue": 1,
                    "submitted_before_return": 0, "source_graph_finished": True,
                    "remaining_send_nodes": False, "final_send_slots": 0,
                    "pending_astra_events": 0, "pending_executor_tasks": 0,
                    "later_event_executed": True,
                    "jetty_resources_released": True, "csv_completed_tasks": 0,
                    "sender_completion": "transaction_done", "transfer_id": data["transfer_id"],
                    "ub_task_id": data["task_id"], "bytes": data["bytes"], "tag": data["tag"],
                    "graph_id": "m1-write", "send_node_id": 1, "recv_node_id": 2,
                    "src_rank": 0, "dst_rank": 1, "priority": 7,
                    "calibration_status": "unvalidated_reference"}
        for key, value in expected.items():
            assert report[key] == value, (label, key, report[key], value)
        start, complete = report["wqe_starts"], report["wqe_completions"]
        assert len(start) == len(complete) == 1
        assert start[0] == dict(node=0, jetty=0, task=data["task_id"], time_ns=data["start_ns"])
        assert complete[0]["node"] == 0 and complete[0]["task"] == data["task_id"]
        finish = complete[0]["time_ns"]
        assert report["simulation_end_ns"] == report["test_stop_ns"] == data["start_ns"] + 1000000
        assert data["start_ns"] < finish <= report["simulation_end_ns"]
        assert report["transaction_done_ns"] == report["send_done_ns"] == finish
        assert report["source_graph_completions_ns"] == [finish]
        writes = report["target_writes"]
        # Each native target commit is a deduplicated, reassembled TA unit, up to 64 KiB.
        assert len(writes) == report["committed_segments"] == (data["bytes"] + 65535) // 65536
        assert sum(entry["bytes"] for entry in writes) == data["bytes"]
        assert len({(entry["jetty"], entry["sequence"]) for entry in writes}) == len(writes)
        assert all(entry["src"] == 0 and entry["dst"] == 1 and entry["task"] == data["task_id"]
                   and entry["jetty"] == start[0]["jetty"] and entry["bytes"] > 0 for entry in writes)
        ready = writes[-1]["time_ns"]
        receive_done = max(ready, data["receive_start_ns"])
        assert data["start_ns"] < ready < finish
        assert report["data_ready_ns"] == ready and report["receive_done_ns"] == receive_done
        assert report["target_graph_completions_ns"] == [receive_done + data["receive_compute_ns"]]
        if data["receive_start_ns"] > finish:
            assert report["context_active_at_source_finish"] is True
            assert report["receive_callbacks_at_source_finish"] == 0
        else:
            assert report["receive_callbacks_at_source_finish"] == 1
        trace = (run_dir / "network/runlog/TaskTrace_node_0.tr").read_text()
        task_events = re.findall(r"\[([\d.]+)us\] WQE (Starts|Completes), jettyNum: 0 taskId: (\d+)\n", trace)
        assert [(int(Decimal(t) * 1000), event, int(task)) for t, event, task in task_events] == [
            (data["start_ns"], "Starts", data["task_id"]), (finish, "Completes", data["task_id"])]
        astra = (run_dir / "log/log.log").read_text()
        for event, time in [("issue", data["start_ns"]), ("callback", finish)]:
            assert re.findall(event + r",sys->id=0, tick=(\d+), node->id=1,", astra) == [str(time)]
        for event, node, time in [("issue", 2, data["receive_start_ns"]),
                                  ("callback", 2, receive_done), ("issue", 3, receive_done),
                                  ("callback", 3, receive_done + data["receive_compute_ns"])]:
            assert re.findall(event + rf",sys->id=1, tick=(\d+), node->id={node},", astra) == [str(time)]
        assert not (run_dir / "log/err.log").read_text()
        assert not (run_dir / "network/traffic.csv").exists()
        loaded = [Path(path).resolve() for path in report["loaded_libraries"]
                  if Path(path).name.startswith("libns3")]
        assert len(loaded) == 10 and set(loaded) == libraries
        # The locked GetUselessTpns scans every local TP and collects all idle pairs,
        # including the unused priority-8 pair. Preserve that policy.
        assert report["initial_tp_counts"] == [2, 2]
        assert report["final_tp_counts"] == ([0, 0] if data["reclaim_tps"] else [2, 2])
        results[label] = {"data_ready_ns": ready, "receive_done_ns": receive_done,
                          "transaction_done_ns": finish, "bytes": data["bytes"],
                          "input_sha256": digest(source), "graph_sha256": [digest(run_dir / ("write." + str(rank) + ".et")) for rank in (0, 1)]}
        print(f"PASS {label}: one WQE, data_ready={ready}, RECV={receive_done}, SEND={finish} ns; both graphs completed, contexts released")
    assert results["receive-first"]["transaction_done_ns"] == results["send-first"]["transaction_done_ns"]
    assert results["receive-first"]["transaction_done_ns"] == results["reclaim-tps"]["transaction_done_ns"]
    assert all(results[label]["transaction_done_ns"] == results["receive-first"]["transaction_done_ns"]
               and results[label]["data_ready_ns"] == results["receive-first"]["data_ready_ns"]
               for label in ("send-first", "late-receive", "late-reclaim", "reclaim-tps"))
    assert results["one-byte"]["transaction_done_ns"] < results["receive-first"]["transaction_done_ns"]

    for label, changes in [("zero-bytes", {"bytes": 0}), ("byte-overflow", {"bytes": 2**32}),
                           ("id-overflow", {"task_id": 2**32}), ("negative", {"bytes": -1}),
                           ("fraction", {"bytes": 0.5}), ("tag-remapping", {"tag": 500000000}),
                           ("old-schema", {"schema_version": "moe-ub-write/1"}),
                           ("receive-outside-horizon", {"receive_start_ns": 1000010}),
                           ("compute-outside-horizon", {"receive_compute_ns": 1000010}),
                           ("zero-compute", {"receive_compute_ns": 0})]:
        source = output / (label + ".json")
        source.write_text(json.dumps(dict(fixture, **changes)) + "\n")
        run_dir = output / label
        result = invoke(label, [str(binary), str(source), str(system), str(case), str(run_dir)])
        assert result.returncode == 1 and not run_dir.exists(), (label, result.stderr)
    previous = output / "receive-first"
    hashes = {path: digest(path) for path in previous.rglob("*") if path.is_file()}
    result = invoke("overwrite", [str(binary), str(output / "receive-first.json"),
                                  str(system), str(case), str(previous)])
    assert result.returncode == 1 and "must be new" in result.stderr
    assert all(digest(path) == value for path, value in hashes.items())

    recipe = invoke("write-commands", ["ninja", "-C", str(build), "-t", "commands", "cosim-write"])
    assert recipe.returncode == 0
    forbidden = ("network_frontend/", "extern/network_backend/")
    assert not any(part in recipe.stdout for part in forbidden)
    sources = []
    for line in recipe.stdout.splitlines():
        words = shlex.split(line)
        if "-c" in words:
            path = Path(words[words.index("-c") + 1]).resolve()
            assert any(path.is_relative_to(root / area) for area in
                       ("backend", "third_party/ns-3-ub", "third_party/LLMServingSim/astra-sim"))
            sources.append({"path": str(path), "sha256": digest(path), "command": line})
    counts = collections.Counter(item["path"] for item in sources)
    assert all(n == 1 for n in counts.values())
    assert counts[str(root / "backend/cosim_write.cc")] == 1
    assert counts[str(root / "backend/ub_write_transfer.cc")] == 1
    link_map = (build / "cosim-m1/write-link.map").read_text()
    assert not any(part in link_map for part in forbidden)
    members = re.findall(r"libAstraSim\.a\(([^)]+)\)\s+\(--whole-archive\)", link_map)
    assert len(members) == len(set(members)) == 52
    assert {(build / path).resolve() for path in re.findall(r"\bLOAD\s+(\S*libns3[^\s]*)", link_map)} == libraries
    (output / "compiled-sources.json").write_text(json.dumps(sources, indent=2) + "\n")
    (output / "summary.json").write_text(json.dumps({"passed": True, "cases": results,
        "rejected_inputs": 11, "binary_sha256": digest(binary),
        "link_map_sha256": digest(build / "cosim-m1/write-link.map"),
        "loaded_libraries": {str(path): digest(path) for path in sorted(libraries)}}, indent=2) + "\n")
    print("PASS write boundary: UINT8 bounds, one actual UB network, all 52 ASTRA objects")


if __name__ == "__main__":
    main()
