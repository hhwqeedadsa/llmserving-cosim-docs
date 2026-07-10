#!/usr/bin/env python3
"""Generate the public MTP task-start jitter demo case with ns-3-ub-tools."""

import argparse
import csv
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "scratch" / "ns-3-ub-tools"
CASE_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(TOOLS_DIR))
import net_sim_builder as netsim  # noqa: E402


NETWORK_ATTRIBUTES = """\
default ns3::UbApp::EnableMultiPath "false"
default ns3::UbApp::UseShortestPaths "true"
default ns3::UbLink::Delay "+0ns"
default ns3::UbPort::UbDataRate "400Gbps"
default ns3::UbPort::UbInterframeGap "+0ns"
default ns3::UbPort::CbfcFlitLenByte "20"
default ns3::UbPort::CbfcFlitsPerCell "8"
default ns3::UbPort::CbfcRetCellGrainDataPacket "4"
default ns3::UbPort::CbfcRetCellGrainControlPacket "32"
default ns3::UbPort::CbfcCtrlCrdRtrThldCell "1024"
default ns3::UbPort::CbfcInitCreditCell "6553"
default ns3::UbPort::PfcUpThld "819200"
default ns3::UbPort::PfcLowThld "655360"
default ns3::UbSwitch::FlowControl "CBFC"
default ns3::UbJetty::JettyOooAckThreshold "2048"
default ns3::UbJetty::UbJettyInflightMax "10000"
default ns3::UbTransportChannel::EnableRetrans "false"
default ns3::UbTransportChannel::InitialRTO "+25600ns"
default ns3::UbTransportChannel::MaxRetransAttempts "7"
default ns3::UbTransportChannel::RetransExponentFactor "1"
default ns3::UbTransportChannel::DefaultMaxWqeSegNum "1000"
default ns3::UbTransportChannel::DefaultMaxInflightPacketSize "1000"
default ns3::UbTransportChannel::TpOooThreshold "2048"
default ns3::UbTransportChannel::UsePacketSpray "false"
default ns3::UbTransportChannel::UseShortestPaths "true"
default ns3::UbSwitchAllocator::AllocationTime "+1ns"
default ns3::UbLdstInstance::ThreadNum "10"
default ns3::UbLdstInstance::QueuePriority "1"
default ns3::UbLdstThread::LoadResponseSize "512"
default ns3::UbLdstThread::StoreRequestSize "512"
default ns3::UbLdstThread::LoadRequestSize "64"
default ns3::UbLdstThread::StoreOutstanding "64"
default ns3::UbLdstThread::LoadOutstanding "64"
default ns3::UbLdstApi::UsePacketSpray "true"
default ns3::UbLdstApi::UseShortestPaths "true"
default ns3::UbCaqm::UbCaqmAlpha "0.5"
default ns3::UbCaqm::UbCaqmBeta "0.5"
default ns3::UbCaqm::UbCaqmGamma "0.5"
default ns3::UbCaqm::UbCaqmLambda "0.5"
default ns3::UbCaqm::UbCaqmTheta "10"
default ns3::UbCaqm::UbCaqmQt "40960"
default ns3::UbCaqm::UbCaqmCcUint "32"
default ns3::UbCaqm::UbMarkProbability "0.1"
default ns3::UbHostCaqm::UbCaqmCwnd "40960"
default ns3::UbSwitchCaqm::UbCcUpdatePeriod "+500ns"
default ns3::UbQueueManager::ReservePerQueueBytes "1048576"
default ns3::UbFault::UbFaultUsePacketSpray "true"

global UB_FAULT_ENABLE "false"
global UB_PRIORITY_NUM "16"
global UB_VL_NUM "16"
global UB_CC_ALGO "CAQM"
global UB_CC_ENABLED "false"

global UB_TRACE_ENABLE "true"
global UB_TASK_TRACE_ENABLE "true"
global UB_PACKET_TRACE_ENABLE "false"
global UB_PORT_TRACE_ENABLE "false"
global UB_QUEUE_TRACE_ENABLE "false"
global UB_FLOW_CONTROL_TRACE_ENABLE "false"
global UB_CONGESTION_CONTROL_TRACE_ENABLE "false"
global UB_RECORD_PKT_TRACE "false"

global UB_PARSE_TRACE_ENABLE "false"
global UB_PYTHON_SCRIPT_PATH "scratch/ns-3-ub-tools/trace_analysis/parse_trace.py"
"""


def build_topology(case_dir: Path, host_count: int, leaf_count: int) -> None:
    if host_count % leaf_count != 0:
        raise ValueError("host-count must be divisible by leaf-count")

    graph = netsim.NetworkSimulationGraph()
    graph.output_dir = str(case_dir)

    spine_count = host_count // leaf_count
    leaf_base = host_count
    spine_base = host_count + leaf_count

    leaf_ids = []
    spine_ids = []
    for host_id in range(host_count):
        graph.add_netisim_host(host_id, forward_delay="1ns")

    for leaf_index in range(leaf_count):
        leaf_id = leaf_base + leaf_index
        graph.add_netisim_node(leaf_id, forward_delay="1ns")
        leaf_ids.append(leaf_id)

    for spine_index in range(spine_count):
        spine_id = spine_base + spine_index
        graph.add_netisim_node(spine_id, forward_delay="1ns")
        spine_ids.append(spine_id)

    hosts_per_leaf = host_count // leaf_count
    for host_id in range(host_count):
        leaf_id = leaf_ids[host_id // hosts_per_leaf]
        graph.add_netisim_edge(host_id, leaf_id, bandwidth="400Gbps", delay="20ns", edge_count=1)

    for leaf_id in leaf_ids:
        for spine_id in spine_ids:
            graph.add_netisim_edge(leaf_id, spine_id, bandwidth="400Gbps", delay="20ns", edge_count=1)

    graph.build_graph_config()
    graph.gen_2layer_clos_compressed_route_table(host_count, leaf_count)
    graph.write_config(include_transport=False)


def build_traffic(args: argparse.Namespace, case_dir: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="mtp-task-jitter-traffic-") as tmp:
        cmd = [
            sys.executable,
            str(TOOLS_DIR / "traffic_maker" / "build_traffic.py"),
            "--host-count",
            str(args.host_count),
            "--comm-domain-size",
            str(args.comm_domain_size),
            "--data-size",
            args.data_size,
            "--algo",
            args.algo,
            "--phase-delay",
            str(args.phase_delay_ns),
            "--rank-mapping",
            args.rank_mapping,
            "--output-dir",
            tmp,
        ]
        if args.algo == "a2a_scatter":
            cmd.extend(["--scatter-k", str(args.scatter_k)])
        subprocess.run(cmd, check=True)
        generated = sorted(Path(tmp).glob("*/traffic.csv"))
        if len(generated) != 1:
            raise RuntimeError(f"expected one generated traffic.csv, found {len(generated)}")
        with generated[0].open(newline="", encoding="utf-8") as source:
            rows = list(csv.reader(source))
        with (case_dir / "traffic.csv").open("w", newline="", encoding="utf-8") as destination:
            csv.writer(destination, lineterminator="\n").writerows(rows)


def write_network_attributes(case_dir: Path) -> None:
    (case_dir / "network_attribute.txt").write_text(
        textwrap.dedent(NETWORK_ATTRIBUTES),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the public MTP task-start jitter demo case."
    )
    parser.add_argument("--host-count", type=int, default=8)
    parser.add_argument("--leaf-count", type=int, default=4)
    parser.add_argument("--comm-domain-size", type=int, default=4)
    parser.add_argument("--data-size", default="1MB")
    parser.add_argument(
        "--algo",
        choices=["ar_ring", "ar_nhr", "ar_rhd", "a2a_pairwise", "a2a_scatter"],
        default="a2a_scatter",
    )
    parser.add_argument("--scatter-k", type=int, default=2)
    parser.add_argument("--phase-delay-ns", type=int, default=10)
    parser.add_argument("--rank-mapping", choices=["linear", "round-robin"], default="linear")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.host_count % args.comm_domain_size != 0:
        raise ValueError("host-count must be divisible by comm-domain-size")

    for filename in [
        "node.csv",
        "topology.csv",
        "routing_table.csv",
        "transport_channel.csv",
        "traffic.csv",
        "network_attribute.txt",
    ]:
        path = CASE_DIR / filename
        if path.exists():
            path.unlink()

    build_topology(CASE_DIR, args.host_count, args.leaf_count)
    build_traffic(args, CASE_DIR)
    write_network_attributes(CASE_DIR)
    print(f"case written to {CASE_DIR}")


if __name__ == "__main__":
    main()
