import sys
from pathlib import Path
sys.path.insert(0, '/home/lry/a100_ep_runtime/第05轮_联合仿真M0_20260919/ns-3-ub/repo/scratch/ns-3-ub-tools')
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
    graph.add_netisim_edge(8, 9, bandwidth="200Gbps", delay="50ns")
    graph.build_graph_config()
    graph.gen_compressed_route_table(path_finding_algo=bounded_paths, multiple_workers=1)
    graph.write_config(include_transport=False)
