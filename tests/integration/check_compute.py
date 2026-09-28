"""Execute the bounded M1.1 cases and audit the real compute executable."""
import argparse
import collections
import datetime
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess


def sha256(path):
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
                 (build / "cosim-m1/target-facts.txt").read_text().splitlines())
    binary = Path(facts["compute"])
    system = root / "fixtures/m1_compute/system.json"
    fixture = json.loads((root / "fixtures/m1_compute/comp_1000ns.json").read_text())
    records = []

    def invoke(label, argv):
        started = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result = subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False)
        (output / (label + ".stdout.log")).write_text(result.stdout)
        (output / (label + ".stderr.log")).write_text(result.stderr)
        records.append({"label": label, "argv": argv, "cwd": str(root),
                        "started_utc": started,
                        "finished_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "exit_code": result.returncode})
        (output / "commands.json").write_text(json.dumps(records, indent=2) + "\n")
        return result

    expected_modules = {"applications", "bridge", "config-store", "core", "internet",
                        "network", "point-to-point", "stats", "traffic-control", "unified-bus"}
    expected_libraries = {build / "output/lib" / ("libns3.44-" + name + ".so")
                          for name in expected_modules}
    case_results = {}
    # Explicit expected times are independent of the executable's report.
    for label, start, duration, finish in [
            ("comp-1000ns", 0, 1000, 1000),
            ("offset-1000ns", 37, 1000, 1037),
            ("comp-1ns", 0, 1, 1),
            ("zero-comp", 37, 0, 37)]:
        data = dict(fixture, start_ns=start, duration_ns=duration)
        source = output / (label + ".json")
        source.write_text(json.dumps(data, indent=2) + "\n")
        run_dir = output / label
        result = invoke(label, ["timeout", "--kill-after=5s", "30s", str(binary),
                                str(source), str(system), str(run_dir)])
        assert result.returncode == 0, (label, result.stderr)
        assert not result.stderr, (label, result.stderr)
        report = json.loads((run_dir / "result.json").read_text())
        ops = int(duration > 0)
        expected = {
            "schema_version": "moe-ub-compute-result/1",
            "compute_source": "synthetic_fixed_duration",
            "calibration_status": "unvalidated_reference",
            "start_ns": start, "astra_start_ns": start,
            "duration_ns": duration, "chakra_runtime_ns": duration,
            "finish_ns": finish, "astra_finish_ns": finish,
            "completion_times_ns": [finish], "finished": True,
            "initial_in_flight": 0, "in_flight_after_issue": ops,
            "pending_events_after_issue": ops, "final_in_flight": [0, 0, 0],
            "remaining_nodes": False, "pending_events": 0, "event_buckets": 0,
            "gpu_ops": ops, "gpu_time_ns": duration,
            "scheduled_callbacks": ops, "executed_callbacks": ops, "ub_type": "ns3::UbApp"}
        for field, value in expected.items():
            assert report[field] == value, (label, field, report[field], value)
        loaded = [Path(path).resolve() for path in report["loaded_libraries"]
                  if Path(path).name.startswith("libns3")]
        assert len(loaded) == 10 and set(loaded) == expected_libraries, (label, loaded)
        trace = (run_dir / "log/log.log").read_text()
        for event, time in [("issue", start), ("callback", finish)]:
            entries = re.findall(event + r",sys->id=0, tick=(\d+), node->id=1,", trace)
            assert entries == ([str(time)] if ops else []), (label, event, entries)
        assert not (run_dir / "log/err.log").read_text(), label
        case_results[label] = {"start_ns": start, "duration_ns": duration,
                               "finish_ns": report["finish_ns"],
                               "input_sha256": sha256(source),
                               "graph_sha256": sha256(run_dir / "compute.0.et")}
        print(f"PASS {label}: start={start}, duration={duration}, finish={finish}, resources released")

    for label, updates, error in [
            ("negative", {"duration_ns": -1}, "nonnegative integer"),
            ("fractional", {"duration_ns": 0.5}, "nonnegative integer"),
            ("unknown-schema", {"schema_version": "moe-ub-compute/2"}, "Unsupported"),
            ("overflow", {"duration_ns": 2**63}, "exceeds ns-3 time range"),
            ("sum-overflow", {"start_ns": 2**63 - 1, "duration_ns": 1},
             "completion exceeds ns-3 time range")]:
        source = output / (label + ".json")
        source.write_text(json.dumps(dict(fixture, **updates)) + "\n")
        run_dir = output / label
        result = invoke(label, ["timeout", "--kill-after=5s", "30s", str(binary),
                                str(source), str(system), str(run_dir)])
        assert result.returncode == 1 and error in result.stderr, (label, result.stderr)
        assert not run_dir.exists(), label
        print(f"PASS rejected {label}")
    previous = output / "comp-1000ns"
    hashes = {path: sha256(path) for path in previous.rglob("*") if path.is_file()}
    result = invoke("refuse-overwrite", ["timeout", "--kill-after=5s", "30s", str(binary),
                    str(output / "comp-1000ns.json"), str(system), str(previous)])
    assert result.returncode == 1 and "must be new" in result.stderr
    assert all(sha256(path) == digest for path, digest in hashes.items())

    recipe = invoke("compute-commands", ["ninja", "-C", str(build), "-t", "commands",
                                        "cosim-compute"])
    assert recipe.returncode == 0
    forbidden = ("network_frontend/", "extern/network_backend/")
    assert not any(part in recipe.stdout for part in forbidden)
    compiled = []
    astra = root / "third_party/LLMServingSim/astra-sim"
    ub = root / "third_party/ns-3-ub"
    for line in recipe.stdout.splitlines():
        words = shlex.split(line)
        if "-c" not in words:
            continue
        source = Path(words[words.index("-c") + 1]).resolve()
        assert any(source.is_relative_to(path) for path in (astra, ub, root / "backend")), source
        standard = [word for word in words if word.startswith("-std=")]
        assert len(standard) == 1
        astra_side = source.is_relative_to(astra) or source == root / "backend/astra_logging.cc"
        assert standard[0] in (["-std=c++17", "-std=gnu++17"] if astra_side
                               else ["-std=c++20"])
        compiled.append({"source": str(source), "standard": standard[0],
                         "sha256": sha256(source), "command": line})
    counts = collections.Counter(item["source"] for item in compiled)
    assert all(count == 1 for count in counts.values())
    assert counts[str(root / "backend/cosim_compute.cc")] == 1
    assert counts[str(root / "backend/ub_network_adapter.cc")] == 1
    assert counts[str(root / "backend/astra_logging.cc")] == 1
    members = invoke("astra-members", ["ar", "t", facts["astra_library"]])
    assert members.returncode == 0 and len(members.stdout.splitlines()) == 52
    link_map_path = build / "cosim-m1/link.map"
    link_map = link_map_path.read_text()
    whole_members = re.findall(r"libAstraSim\.a\(([^)]+)\)\s+\(--whole-archive\)", link_map)
    assert collections.Counter(whole_members) == collections.Counter(members.stdout.splitlines())
    assert not any(part in link_map for part in forbidden)
    linked_ns3 = set(re.findall(r"\bLOAD\s+(\S*libns3[^\s]*)", link_map))
    assert {(build / path).resolve() for path in linked_ns3} == expected_libraries
    elf = invoke("elf-dynamic", ["readelf", "-d", str(binary)])
    assert elf.returncode == 0
    assert "libns3.44-unified-bus.so" in elf.stdout and "libns3.44-core.so" in elf.stdout
    (output / "compiled-sources.json").write_text(json.dumps(compiled, indent=2) + "\n")
    summary = {"passed": True, "cases": case_results, "rejected_inputs": 6,
               "binary_sha256": sha256(binary), "system_sha256": sha256(system),
               "link_map_sha256": sha256(link_map_path), "compiled_sources": len(compiled),
               "loaded_ns3": [{"path": str(path), "sha256": sha256(path)}
                              for path in sorted(expected_libraries)]}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("PASS compute link boundary: all 52 ASTRA objects; exactly one set of 10 UB libraries")


if __name__ == "__main__":
    main()
