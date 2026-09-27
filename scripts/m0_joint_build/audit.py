"""Audit the M0 link probe; never run a simulation or modify upstream files."""
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
    parser.add_argument("--run-log", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    build = args.build_dir.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    commands = []

    def run(label, argv):
        result = subprocess.run(argv, cwd=root, capture_output=True, text=True,
                                timeout=60, check=False)
        (output / (label + ".stdout.log")).write_text(result.stdout)
        (output / (label + ".stderr.log")).write_text(result.stderr)
        commands.append({"label": label, "cwd": str(root), "argv": argv,
                         "exit_code": result.returncode,
                         "finished_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()})
        (output / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
        if result.returncode:
            raise RuntimeError(f"{label} exited {result.returncode}")
        return result.stdout

    facts = dict(line.split("=", 1) for line in
                 (build / "cosim-m0/target-facts.txt").read_text().splitlines() if line)
    probe = Path(facts["probe"])
    archive = Path(facts["astra_library"])
    sources = [Path(line).resolve() for line in
               (build / "cosim-m0/astra-sources.txt").read_text().splitlines()]
    astra = root / "third_party/LLMServingSim/astra-sim"
    ub = root / "third_party/ns-3-ub"
    expected_libdir = build / "output/lib"
    forbidden = ("network_frontend/", "extern/network_backend/")
    assert facts["astra_standard"] == "17" and facts["probe_standard"] == "20"
    assert len(sources) == len(set(sources)) > 0
    assert all(path.is_relative_to(astra) for path in sources)
    assert all(not any(part in str(path) for part in forbidden) for path in sources)
    for suffix in ("astra-sim/system/Sys.cc", "astra-sim/workload/Workload.cc",
                   "astra-sim/workload/HardwareResource.cc",
                   "src/feeder/et_feeder.cpp", "src/feeder/et_feeder_node.cpp",
                   "schema/protobuf/et_def.pb.cc", "src/third_party/utils/protoio.cc"):
        assert sum(str(path).endswith(suffix) for path in sources) == 1, suffix

    recipe = run("ninja-commands", ["ninja", "-C", str(build), "-t", "commands",
                                   "m0-joint-link-probe"])
    assert not any(part in recipe for part in forbidden)
    compiled = []
    for line in recipe.splitlines():
        words = shlex.split(line)
        if "-c" not in words:
            continue
        source = Path(words[words.index("-c") + 1])
        source = (source if source.is_absolute() else build / source).resolve()
        standard = [word for word in words if word.startswith("-std=")]
        assert len(standard) == 1, str(source)
        assert not any(word.startswith("-D_GLIBCXX_USE_CXX11_ABI=") for word in words)
        compiled.append({"source": str(source), "standard": standard[0],
                         "command": line, "sha256": sha256(source)})
        if source in sources:
            assert standard[0] in ("-std=c++17", "-std=gnu++17")
        elif source.is_relative_to(ub) or source.name == "link_probe.cc":
            assert standard[0] in ("-std=c++20", "-std=gnu++20")
        else:
            assert source.is_relative_to(astra / "extern/helper"), str(source)
    counts = collections.Counter(item["source"] for item in compiled)
    assert all(counts[str(source)] == 1 for source in sources)
    assert counts[str(root / "scripts/m0_joint_build/link_probe.cc")] == 1
    (output / "compiled-sources.json").write_text(json.dumps(compiled, indent=2) + "\n")
    members = run("archive-members", ["ar", "t", str(archive)]).splitlines()
    assert len(members) == len(sources)
    link_map = (build / "cosim-m0/link.map").read_text()
    assert not any(part in link_map for part in forbidden)
    for member in members:
        assert f"libAstraSim.a({member})" in link_map, member
    # CMake's GNU link feature restores the previous state with --pop-state.
    probe_link = next(line for line in recipe.splitlines()
                      if "-o " + str(probe.relative_to(build)) + " " in line)
    link_words = shlex.split(probe_link)
    archive_word = str(archive.relative_to(build))
    assert link_words.count(archive_word) == 1
    archive_index = link_words.index(archive_word)
    assert link_words[archive_index - 1] == "-Wl,--push-state,--whole-archive"
    assert link_words[archive_index + 1] == "-Wl,--pop-state"
    whole_members = re.findall(r"libAstraSim\.a\(([^)]+)\)\s+\(--whole-archive\)", link_map)
    assert collections.Counter(whole_members) == collections.Counter(members)
    symbols = run("probe-symbols", ["nm", "-C", "--defined-only", str(probe)])
    for symbol in ("AstraSim::Sys::boostedTick()", "AstraSim::Workload::issue_comp(",
                   "Chakra::ETFeeder::readGlobalMetadata()"):
        assert symbol in symbols, symbol
    core_symbols = run("archive-symbols", ["nm", "-C", "--defined-only", str(archive)])
    assert "ns3::" not in core_symbols

    elf = run("elf-dynamic", ["readelf", "-d", str(probe)])
    needed = re.findall(r"\(NEEDED\).*?\[(.*?)\]", elf)
    assert "libns3.44-unified-bus.so" in needed and "libns3.44-core.so" in needed
    run_text = args.run_log.read_text()
    for line in ("chakra_nodes_read=1", "astra_resource_npus=1", "ub_type=ns3::UbApp",
                 "ns3_now_ns=0", "simulation_events_executed=0",
                 "PASS: link and reader probe only"):
        assert run_text.splitlines().count(line) == 1, line
    libraries = [Path(line[len("library="):]).resolve() for line in run_text.splitlines()
                 if line.startswith("library=/")]
    ns3_libraries = [path for path in libraries if path.name.startswith("libns3")]
    expected_modules = {"applications", "bridge", "config-store", "core", "internet",
                        "network", "point-to-point", "stats", "traffic-control", "unified-bus"}
    assert {path.name for path in ns3_libraries} == {
        "libns3.44-" + name + ".so" for name in expected_modules}
    assert len(ns3_libraries) == len(expected_modules)
    assert all(path.parent == expected_libdir for path in ns3_libraries)
    for name in ("libns3.44-core.so", "libns3.44-unified-bus.so"):
        definitions = run(name + "-symbols", ["nm", "-D", "-C", "--defined-only",
                                              str(expected_libdir / name)])
        expected = "ns3::Simulator::Now()" if name.endswith("-core.so") else "ns3::UbApp::GetTypeId()"
        assert expected in definitions
    linked_ns3 = set(re.findall(r"\bLOAD\s+(\S*libns3[^\s]*)", link_map))
    assert linked_ns3, "No ns-3 LOAD entries in link map"
    assert all((build / path).resolve().parent == expected_libdir for path in linked_ns3)
    assert not any("libns3" in line and str(ub / "build") in line for line in recipe.splitlines())
    versions = {tool: run(tool + "-version", [tool, "--version"]).splitlines()[0]
                for tool in ("c++", "cmake", "ninja", "protoc")}
    report = {
        "passed": True, "scope": "Build/link/reader only; no simulation events",
        "versions": versions, "target_facts": facts,
        "astra_source_count": len(sources), "archive_members": len(members),
        "compiled_source_count": len(compiled),
        "standards": dict(collections.Counter(item["standard"] for item in compiled)),
        "probe_sha256": sha256(probe), "archive_sha256": sha256(archive),
        "elf_needed": needed,
        "loaded_libraries": [{"path": str(path), "sha256": sha256(path)} for path in libraries],
        "ns3_library_count": len(ns3_libraries),
        "link_map_sha256": sha256(build / "cosim-m0/link.map"),
        "astra_sources": [{"path": str(path), "sha256": sha256(path)} for path in sources],
    }
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ("astra_sources", "loaded_libraries")}, indent=2))


if __name__ == "__main__":
    main()
