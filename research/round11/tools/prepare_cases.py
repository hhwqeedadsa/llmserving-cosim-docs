#!/usr/bin/env python3
"""Generate only the pre-registered dependent-replay cases; never execute ns-3."""
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO = Path("/home/lry/a100_ep_runtime/第05轮_联合仿真M0_20260919/ns-3-ub/repo")
PKG = REPO / "scratch/20261008-dependent-moe-kv-contention"
WORK = REPO.parents[2]
sys.path.insert(0, str(REPO / ".codex/skills/openusim-run-experiment/scripts"))
from openusim_run_experiment.network_attribute_writer import write_network_attributes, observability_preset
from openusim_run_experiment.case_checker import check_case_files

def write_csv(path, rows):
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

def main():
    matrix = yaml.safe_load((PKG / "matrix.yaml").read_text())
    for item in matrix:
        case = PKG / item["case_dir"]
        assert (case / "experiment-spec.md").is_file()
        if (case / "traffic.csv").exists():
            raise FileExistsError(f"Refuse to overwrite {case}")
    sources = [
        REPO / "scratch/20260926-block24-collective-profile/flow_manifest.csv",
        WORK / "第06轮_SimAI与LLMServingSim_trace分析_20260924/analysis/llmservingsim_middle_block_events.csv",
    ]
    (ROOT / "sources").mkdir(parents=True, exist_ok=True)
    source_rows = []
    for source in sources:
        dest = ROOT / "sources" / source.name
        shutil.copy2(source, dest)
        source_rows.append(dict(path=str(source), snapshot=str(dest.relative_to(ROOT)),
                                sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
    write_csv(ROOT / "sources/source_manifest.csv", source_rows)
    with sources[0].open() as f:
        template = list(csv.DictReader(f))
    assert len(template) == 8
    with sources[1].open() as f:
        events = list(csv.DictReader(f))
    compute = [e for e in events if e["event_type"] == "compute" and e["resource_id"] == "GPU0"]
    assert abs(sum(float(e["duration_us"]) for e in compute[:6]) - 36.330) < 1e-9
    assert [int(template[i]["launch_delay_ns"]) for i in [0,2,4,6]] == [36330,0,2549,456737]
    for item in matrix:
        case = PKG / item["case_dir"]
        topo = """import sys
from pathlib import Path
sys.path.insert(0, REPO_TOOLS)
import net_sim_builder as netsim
import networkx as nx

def bounded_paths(G, source, target):
    try:
        return nx.all_shortest_paths(G, source, target)
    except nx.NetworkXNoPath:
        return []

if __name__ == "__main__":
    graph = netsim.NetworkSimulationGraph()
    graph.output_dir = str(Path(__file__).parent) + "/"
    for host in range(8):
        graph.add_netisim_host(host, forward_delay="10ns")
    for switch in [8, 9]:
        graph.add_netisim_node(switch, forward_delay="10ns")
    for host in range(8):
        graph.add_netisim_edge(host, 8 if host < 4 else 9, bandwidth="400Gbps", delay="20ns")
    graph.add_netisim_edge(8, 9, bandwidth="CORE_RATEGbps", delay="50ns")
    graph.build_graph_config()
    graph.gen_compressed_route_table(path_finding_algo=bounded_paths, multiple_workers=1)
    graph.write_config(include_transport=False)
"""
        topo = topo.replace("REPO_TOOLS", repr(str(REPO / "scratch/ns-3-ub-tools")))
        topo = topo.replace("CORE_RATE", str(item["core_gbps"]))
        (case / "generate_topology.py").write_text(topo)
        subprocess.run([sys.executable, str(case / "generate_topology.py")], check=True, cwd=REPO)
        traffic, manifest = [], []
        for group in range(3):
            nodes = [group, group + 4]
            for rep in range(10):
                for row in template:
                    old_phase = int(row["phase_id"])
                    phase = group*100 + rep*4 + old_phase
                    dependency = "" if rep == 0 and old_phase == 0 else str(phase-1)
                    delay = int(row["launch_delay_ns"])
                    if rep == 0 and old_phase == 0:
                        delay += group*40000
                    task = len(traffic)
                    src, dst = nodes[int(row["src_rank"])], nodes[int(row["dst_rank"])]
                    traffic.append(dict(taskId=task, sourceNode=src, destNode=dst,
                        **{"dataSize(Byte)":int(row["payload_bytes"])}, opType="URMA_WRITE",
                        priority=7, delay=f"{delay}ns", phaseId=phase, dependOnPhases=dependency))
                    manifest.append(dict(task_id=task, group=f"G{group}", repetition=rep,
                        source_block=24, collective=row["collective_op"],
                        algorithm_stage=row["algorithm_stage"], phase_id=phase,
                        depends_on_phase=dependency, src=src, dst=dst,
                        payload_bytes=int(row["payload_bytes"]), delay_ns=delay,
                        source_task_id=row["task_id"],
                        evidence="profile-derived-compute/trace-bytes/ring-projection/synthetic-replica-replay"))
        if item["kv_enabled"]:
            for cycle in range(10):
                for offset in [100,140,180,500,520]:
                    task = len(traffic)
                    phase = 1000 + task - 240
                    delay = (cycle*800+offset)*1000
                    traffic.append(dict(taskId=task, sourceNode=3, destNode=7,
                        **{"dataSize(Byte)":524288}, opType="URMA_WRITE", priority=7,
                        delay=f"{delay}ns", phaseId=phase, dependOnPhases=""))
                    manifest.append(dict(task_id=task, group="KV", repetition=cycle,
                        source_block="", collective="KV-proxy", algorithm_stage="p2p",
                        phase_id=phase, depends_on_phase="", src=3, dst=7, payload_bytes=524288,
                        delay_ns=delay, source_task_id="", evidence="synthetic-background-KV-proxy"))
        write_csv(case / "traffic.csv", traffic)
        write_csv(case / "flow_manifest.csv", manifest)
        resolution = write_network_attributes(case,
            explicit_overrides={
                "ns3::UbSwitch::FlowControl":"CBFC",
                "ns3::UbRoutingProcess::RoutingAlgorithm":"HASH",
                "ns3::UbSwitchAllocator::AllocationTime":"1ns",
                "ns3::UbPort::UbInterframeGap":"0ns",
                "ns3::UbTransportChannel::EnableRetrans":"false",
                "ns3::UbTransportChannel::EnableFastRetrans":"false",
                "UB_CC_ENABLED":"false",
            }, observability_overrides=observability_preset("detailed"))
        (case / "parameter-resolution.json").write_text(json.dumps(resolution, indent=2)+"\n")
        check = check_case_files(case, "on-demand")
        assert check["status"] == "ok", check
        print(item["case_id"], len(traffic), "tasks", check)
    (PKG / "generation.json").write_text(json.dumps({"status":"generated","cases":matrix},indent=2)+"\n")

if __name__ == "__main__":
    main()
