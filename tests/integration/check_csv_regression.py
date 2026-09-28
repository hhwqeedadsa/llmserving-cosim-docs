"""Compare patched and preserved native UB CSV runs, including raw traces."""
import argparse
import csv
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--reference-binary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--case-dir", type=Path)
    parser.add_argument("--dependency-visibility-delay")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    ub = root / "third_party/ns-3-ub"
    source = args.case_dir.resolve() if args.case_dir else ub / "scratch/2nodes_single-tp"
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    records = []

    def run(label, command):
        started = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result = subprocess.run(command, cwd=ub, capture_output=True, text=True, check=False)
        (output / (label + ".stdout.log")).write_text(result.stdout)
        (output / (label + ".stderr.log")).write_text(result.stderr)
        records.append({"label": label, "cwd": str(ub), "argv": command,
                        "started_utc": started, "exit_code": result.returncode,
                        "finished_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()})
        (output / "commands.json").write_text(json.dumps(records, indent=2) + "\n")
        assert result.returncode == 0 and not result.stderr.strip(), (label, result.stderr)

    inputs = {path.name: sha256(path) for path in source.iterdir() if path.is_file()}
    with (source / "traffic.csv").open() as stream:
        expected_tasks = list(csv.DictReader(stream))
    expected_bytes = sum(int(row["dataSize(Byte)"]) for row in expected_tasks)
    manifests = {}
    binary_hashes = {}
    for label, binary in [("reference", args.reference_binary.resolve()),
                          ("patched", args.binary.resolve())]:
        case = output / label
        case.mkdir()
        for name in inputs:
            shutil.copyfile(source / name, case / name)
        binary_hashes[label] = sha256(binary)
        command = ["timeout", "--kill-after=5s", "60s", str(binary), "--case-path=" + str(case)]
        if args.dependency_visibility_delay:
            command.append("--dependency-visibility-delay=" + args.dependency_visibility_delay)
        run(label, command)
        # The native parser does not propagate all child exit codes. Recheck both explicitly.
        for script in ("task_statistics.py", "cal_throughput.py"):
            run(label + "-" + script.removesuffix(".py"),
                ["python3", str(ub / "scratch/ns-3-ub-tools/trace_analysis" / script), str(case), "true"])
        for name, digest in inputs.items():
            assert sha256(case / name) == sha256(source / name) == digest
        for name in ("task_statistics.csv", "throughput.csv"):
            assert sha256(case / "test" / name) == sha256(case / "output" / name)
        with (case / "test/task_statistics.csv").open() as stream:
            tasks = list(csv.DictReader(stream))
        assert len(tasks) == len(expected_tasks)
        actual = {row["taskId"]: row for row in tasks}
        for expected in expected_tasks:
            assert all(actual[expected["taskId"]][key] == value for key, value in expected.items())
        assert sum(int(row["dataSize(Byte)"]) for row in tasks) == expected_bytes
        manifests[label] = {str(path.relative_to(case)): sha256(path)
                            for directory in ("runlog", "output", "test")
                            for path in (case / directory).rglob("*") if path.is_file()}
    differences = sorted(name for name in set(manifests["reference"]) | set(manifests["patched"])
                         if manifests["reference"].get(name) != manifests["patched"].get(name))
    (output / "comparison.json").write_text(json.dumps({"binary_sha256": binary_hashes,
        "inputs": inputs, "manifests": manifests, "differences": differences}, indent=2) + "\n")
    assert not differences, differences
    (output / "summary.json").write_text(json.dumps({"passed": True, "tasks": len(expected_tasks),
        "business_bytes": expected_bytes, "identical_result_files": len(manifests["reference"]),
        "binary_sha256": binary_hashes}, indent=2) + "\n")
    print(f"PASS: {len(expected_tasks)} CSV tasks / {expected_bytes} bytes, "
          f"{len(manifests['reference'])} identical trace/statistics files")


if __name__ == "__main__":
    main()
