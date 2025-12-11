import os
import re
import time
from collections import defaultdict
from itertools import groupby
from concurrent.futures import ProcessPoolExecutor, as_completed

import networkx as nx
from tqdm import tqdm
from typing import List


def _process_path_chunk(graph, path_finding_algo, node_pairs):
    # 子进程中串行处理一段节点对，避免 IO 干扰
    paths = []
    for src, dst in tqdm(node_pairs, desc="Processing paths", leave=False):
        try:
            for p in path_finding_algo(graph, src, dst):
                paths.append(p)
        except nx.exception.NetworkXNoPath:
            pass
    return paths

VALID_BANDWIDTH_UNIT = {"bps", "Kbps", "Mbps", "Gbps", "Tbps"}
VALID_TIME_UNIT = {"ns", "us", "ms", "s"}

def validate_with_unit(name, value, allowed_units, example):
        ok = isinstance(value, str) and re.match(r'^\s*\d+(\.\d+)?\s*[A-Za-z]+\s*$', value or "")
        if not ok:
            print(f"\033[91m[Error] add_netisim_edge: 参数 {name}='{value}' 缺少单位，示例: {example}\033[0m")
            raise ValueError(f"{name} must include unit, e.g. {example}")
        unit = re.findall(r'[A-Za-z]+', value.strip())[-1]
        if unit not in allowed_units:
            print(f"\033[91m[Error] add_netisim_edge: 参数 {name}='{value}' 单位不被支持，可用单位: {', '.join(allowed_units)}\033[0m")
            raise ValueError(f"{name} unit must be one of: {', '.join(allowed_units)}")

class NetworkSimulationGraph(nx.Graph):
    node_type = ["host", "node"]

    def __init__(self):
        super().__init__()
        self.link_port = {}            # (u, v, u_port) -> [v_port]
        self.port_list = []            # [(is_switch, port_num)] 按节点顺序
        self.link_infos = []           # [(u,u_port,v,v_port,bw,delay)]，bw/delay 为带单位字符串
        self.route_dict4tp = defaultdict(list)  # (host_a,host_b) -> [(src_port,metric,dst_port),...]
        self.output_dir = "./output/" + time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime()) + "/"

        self.host_ids = []
        self.node_ids = []

        # 下一跳端口表: next_hop_ports[this_node][neighbor] = [port1, port2, ...]
        self.next_hop_ports = []

        # 路由表: route_table_2port[node_id][dst_id] = set((out_port, metric, dst_port))
        self.route_table_2port = None

    # 基础节点/边 API（forward_delay 等属性原样保留，不做单位转换）
    def add_netisim_host(self, node_id, forward_delay="1ns", **attr):
        self.host_ids.append(node_id)
        validate_with_unit("forward_delay", forward_delay, VALID_TIME_UNIT, "1ns")
        return super().add_node(node_id, type="host", forward_delay=forward_delay, **attr)

    def add_netisim_node(self, node_id, forward_delay="1ns", **attr):
        self.node_ids.append(node_id)
        validate_with_unit("forward_delay", forward_delay, VALID_TIME_UNIT, "1ns")
        return super().add_node(node_id, type="node", forward_delay=forward_delay, **attr)

    def add_netisim_edge(self, u, v, bandwidth="200Gbps", delay="20ns", edge_count=1, **attr):
        # 注意：bandwidth、delay 以字符串形式存储，原样写入输出文件
        validate_with_unit("bandwidth", bandwidth, VALID_BANDWIDTH_UNIT, "400Gbps")
        validate_with_unit("delay", delay, VALID_TIME_UNIT, "10ns")
        return super().add_edge(
            u, v,
            bandwidth=bandwidth,
            delay=delay,
            edge_count=int(edge_count),
            **attr
        )

    # 统计/校验
    def check_graph_index_valid(self):
        for i in range(len(self.nodes)):
            assert self.has_node(i), f"编号{i}:必须从0开始顺序编号!"
            assert self.nodes[i]["type"] in self.node_type, f"编号{i}类型非法!"
        assert len(self.host_ids) + len(self.node_ids) == len(self.nodes), "host+node数应等于总节点数!"
        return True

    def get_host_num(self):
        return len(self.host_ids)

    def get_node_num(self):
        return len(self.node_ids)

    def get_total_num(self):
        return len(self.host_ids) + len(self.node_ids)

    def node_is_switch(self, node_id):
        return self.nodes[node_id]["type"] == "node"

    def node_is_host(self, node_id):
        return self.nodes[node_id]["type"] == "host"

    # 拓扑到端口映射（供后续路由/导出使用）
    def build_graph_config(self):
        assert self.check_graph_index_valid()

        total_node_num = self.get_total_num()

        # 确保节点编号连续且与插入顺序一致
        x = list(self.nodes)
        if x != list(range(len(x))):
            raise ValueError(f"节点编号应为 0..{len(x)-1} 的连续整数，实际得到 {x}")

        port_conn_count = [0] * total_node_num
        self.port_list.clear()
        self.link_port.clear()
        self.link_infos.clear()

        # 统计每个节点的端口总数（包含 edge_count）
        for node_id in self.nodes:
            t_port_num = 0
            for edge in self.edges(node_id):
                t_port_num += int(self.get_edge_data(edge[0], edge[1])["edge_count"])
            # is_switch: host=0, switch=1
            self.port_list.append((1 - self.node_is_host(node_id), t_port_num))

        # 链路展开为端口映射与 link_infos（带单位字符串原样携带）
        topo_edges = []
        for u, v in self.edges:
            edge_count = int(self.edges[u, v]["edge_count"])
            bw = self.edges[u, v]["bandwidth"]  # 字符串，如 '400Gbps'
            dl = self.edges[u, v]["delay"]      # 字符串，如 '20ns'
            for _ in range(edge_count):
                u_port = port_conn_count[u]
                v_port = port_conn_count[v]
                topo_edges.append((u, u_port, v, v_port))

                self.link_port[(u, v, u_port)] = [v_port]
                self.link_port[(v, u, v_port)] = [u_port]

                port_conn_count[u] += 1
                port_conn_count[v] += 1
                self.link_infos.append((u, u_port, v, v_port, bw, dl))

        # 构建邻居->本端口 映射
        self.next_hop_ports = [[[] for _ in range(total_node_num)] for _ in range(total_node_num)]
        for sn, sp, dn, dp in topo_edges:
            self.next_hop_ports[sn][dn].append(sp)
            self.next_hop_ports[dn][sn].append(dp)

        os.makedirs(self.output_dir, exist_ok=True)

    # 路由表基础结构
    def init_routing_tables(self):
        total_node_num = self.get_total_num()
        self.route_table_2port = [[set() for _ in range(total_node_num)] for _ in range(total_node_num)]

    # 查询端口
    def get_link_ports(self, this_node, next_hop):
        return self.next_hop_ports[this_node][next_hop]

    # 写入一条路由表项（带 ECMP 与最短度量合并）
    def set_route_table(self, curr_node, next_hop_node, node_before_dst, dst, metric):
        """
        手动添加一条路由表项到指定节点的路由表中
        
        此方法允许用户手动配置单条路由表项，支持ECMP(等价多路径)负载均衡。
        当存在相同出端口和目标端口的路由时，会自动保留更优(更小)的metric值。
        
        Parameters
        ----------
        curr_node : int
            当前节点ID，即要添加路由表项的节点
            
        next_hop_node : int
            下一跳节点ID，数据包从curr_node发出后的下一个节点
            
        node_before_dst : int
            目标节点的前一跳节点ID，用于确定数据包到达目标节点时的入端口
            当next_hop_node等于dst时，node_before_dst用于计算dst的入端口
            
        dst : int
            目标节点ID，数据包的最终目的地
            
        metric : int
            路由度量值，数值越小表示路径越优
            用于路径选择和ECMP负载均衡
            
        Notes
        -----
        - 路由表项格式为: (out_port, metric, dst_port)
        - 支持ECMP: 相同metric的多条路径会被同时保留
        - 自动去重: 相同出端口和目标端口的路由只保留最优metric
        - 端口计算: 基于build_graph_config()生成的端口映射关系
        
        Examples
        --------
        >>> # 添加从节点0到节点3的路由，下一跳为节点1
        >>> graph.set_route_table(curr_node=0, next_hop_node=1, 
        ...                       node_before_dst=2, dst=3, metric=2)
        
        >>> # 在路径 0->1->2->3 中，为节点1添加到节点3的路由
        >>> graph.set_route_table(curr_node=1, next_hop_node=2, 
        ...                       node_before_dst=2, dst=3, metric=1)
        
        See Also
        --------
        gen_route_table : 自动生成全网路由表
        get_link_ports : 获取节点间的端口连接信息
        """
        # node_before_dst 是 dst 的前一跳节点，用于确定 dst 侧的入端口号
        dst_ports = self.get_link_ports(dst, node_before_dst)
        for dst_port in dst_ports:
            next_hop_ports = (
                self.link_port[(dst, node_before_dst, dst_port)]
                if next_hop_node == dst
                else self.get_link_ports(curr_node, next_hop_node)
            )
            
            route_set = self.route_table_2port[curr_node][dst]
            
            for out_port in next_hop_ports:
                route_key = (out_port, dst_port)
                
                # 查找是否存在相同(out_port, dst_port)的路由
                conflicting_routes = [r for r in route_set if (r[0], r[-1]) == route_key]
                
                if not conflicting_routes:
                    # 无冲突，直接添加
                    route_set.add((out_port, metric, dst_port))
                else:
                    # 处理冲突路由（通常只有一个）
                    existing_route = conflicting_routes[0]
                    if existing_route[1] > metric:
                        route_set.remove(existing_route)
                        route_set.add((out_port, metric, dst_port))

    # 生成全网路由（按 host-host 最短路径，支持并行）
    def gen_route_table(self, write_file=True, host_router=True, path_finding_algo=nx.all_shortest_paths, multiple_workers=1):
        """
        使用寻路算法生成全网路由表
        
        此方法会自动寻找每一对host节点间的可用路径，并基于这些路径生成完整的路由表。
        如果你需要手动添加特定的路由表项，请使用 `set_route_table` 方法。
        
        Parameters
        ----------
        write_file : bool, optional
            是否将生成的路由表写入CSV文件，默认为True
            
        host_router : bool, optional
            是否为host节点生成路由文件，默认为True
            当设置为False时，仅为交换机节点生成路由表
            
        path_finding_algo : callable, optional
            自定义寻路算法函数，默认为 nx.all_shortest_paths
            函数签名必须为: path_finding_algo(graph, src, dst)
            - graph: NetworkSimulationGraph 实例
            - src: 源host节点ID (int)
            - dst: 目标host节点ID (int)
            返回值: 从src到dst的所有路径的迭代器，每条路径为节点ID的有序列表
  
        multiple_workers : int, optional
            寻路计算使用的并行进程数，默认为1
            设置大于1时启用多进程加速，适用于大型网络拓扑
            
        Notes
        -----
        - 方法会自动调用 build_graph_config() 来构建图配置
        - 生成的路由表支持ECMP(等价多路径)负载均衡
        - 路由表文件将输出到 self.output_dir 目录下的 route_table.csv
        
        Examples
        --------
        >>> # 使用默认最短路径算法
        >>> graph.gen_route_table()
        
        >>> # 使用自定义寻路算法和多进程加速
        >>> def custom_path_finder(graph, src, dst):
        ...     return nx.all_simple_paths(graph, src, dst, cutoff=5)
        >>> graph.gen_route_table(path_finding_algo=custom_path_finder, multiple_workers=4)
        """
        if self.next_hop_ports == []:
            self.build_graph_config()

        self.init_routing_tables()

        nodes = list(self.host_ids)
        node_pairs = [(nodes[i], nodes[j]) for i in range(len(nodes)) for j in range(i + 1, len(nodes))]

        paths = []
        if multiple_workers and multiple_workers > 1 and len(node_pairs) > 0:
            print(f"使用多进程加速，worker数量 {multiple_workers}:")
            chunk_size = max(1, len(node_pairs) // multiple_workers + 1)
            with ProcessPoolExecutor(max_workers=multiple_workers) as executor:
                futures = []
                # 传递一个浅拷贝的无属性 Graph 更轻量
                gcopy = nx.Graph(self)
                for i in range(0, len(node_pairs), chunk_size):
                    chunk = node_pairs[i:i + chunk_size]
                    futures.append(executor.submit(_process_path_chunk, gcopy, path_finding_algo, chunk))
                
                # 添加进度条显示多进程完成情况
                for fut in tqdm(as_completed(futures), total=len(futures), desc="计算路径中"):
                    try:
                        paths.extend(fut.result())
                    except Exception as e:
                        print(f"Error in path finding: {e}")
        else:
            # 串行回退
            for src, dst in tqdm(node_pairs, desc="计算路径中"):
                try:
                    for p in path_finding_algo(self, src, dst):
                        paths.append(p)
                except nx.exception.NetworkXNoPath:
                    pass

        # 将路径写入路由表（双向写入，覆盖所有中间节点到两端 host 的路由）
        for path in tqdm(paths, desc="写入路由表中"):
            if not path:
                continue
            src, dst = path[0], path[-1]
            for i in range(len(path)):
                node_id = path[i]
                # 指向 src 的反向路由
                if i >= 1:
                    metric = i
                    self.set_route_table(node_id, path[i - 1], path[1], src, metric)
                # 指向 dst 的正向路由
                if i < len(path) - 1:
                    metric = len(path) - i - 1
                    self.set_route_table(node_id, path[i + 1], path[-2], dst, metric)

        if write_file:
            self.write_route_table_to_csv(host_router=host_router)

    # 实际写出 CSV，并填充 route_dict4tp 供 ns3_config 使用
    def write_route_table_to_csv(self, host_router=False):
        route_table = self.route_table_2port
        route_dict = defaultdict(list)  # (node_id, dst_id, dst_port) -> [(out_port, metric), ...]

        # 添加进度条显示路由表处理进度
        for node_id in tqdm(self.nodes, desc="处理路由表中"):
            if self.node_is_host(node_id) and not host_router:
                continue
            for dst_id in range(len(route_table[node_id])):
                next_ports_metric_sets = sorted(route_table[node_id][dst_id])
                if not next_ports_metric_sets:
                    continue
                for p_m in next_ports_metric_sets:
                    # 写 CSV 需要聚合到 (node_id,dst_id,dst_port)
                    route_dict[(node_id, dst_id, p_m[-1])].append((p_m[0], p_m[1]))
                    # 同时保留给 ns3 tpn 映射
                    self.route_dict4tp[(node_id, dst_id)].append(p_m)

        os.makedirs(self.output_dir, exist_ok=True)
        filename = os.path.join(self.output_dir, "routing_table.csv")
        with open(filename, "w", encoding="utf-8") as f:
            f.write("nodeId,dstNodeId,dstPortId,outPorts,metrics\n")
            for key in tqdm(route_dict, desc="写入路由CSV"):
                outports = " ".join(str(x[0]) for x in route_dict[key])
                metrics = " ".join(str(x[1]) for x in route_dict[key])
                f.write(",".join(map(str, list(key) + [outports, metrics])) + "\n")
                
        print(f"路由表文件: {os.path.abspath(filename)}")
        
        check_route_table_connectivity(os.path.abspath(filename))

    def write_config(self):
        print("开始生成配置文件...")
        os.makedirs(self.output_dir, exist_ok=True)
        
        # 一次性构建所有文件路径
        files = {
            'node': os.path.join(self.output_dir, "node.csv"),
            'transport': os.path.join(self.output_dir, "transport_channel.csv"),
            'topology': os.path.join(self.output_dir, "topology.csv")
        }

        # 生成 transport_channel.csv
        print("生成TP Channel配置...")
        self._write_transport_channel_csv(files['transport'])
        
        # 生成 node.csv - 优化范围合并逻辑
        print("生成节点配置...")
        self._write_node_csv(files['node'])
        
        # 生成 topology.csv
        print("生成拓扑配置...")
        self._write_topology_csv(files['topology'])
        
        print("\n配置文件生成完成!")
        print(f"输出目录: {os.path.abspath(self.output_dir)}")

    def config_transport_channel(self, priority_list: List[int]):
        self.priority_list = priority_list
    
    def _write_transport_channel_csv(self, filepath):
        """
        生成TP Channel配置文件
    
        此方法为每对host节点之间的每条路由和每个优先级组合生成transport channel配置。
        每个transport channel代表一条具体的通信路径，包含源端口、目标端口、优先级和度量信息。
    
        Parameters
        ----------
        filepath : str
            输出CSV文件的完整路径
            生成的文件包含transport channel的详细配置信息
        
        Notes
        -----
        - 文件格式: nodeId1,portId1,tpn1,nodeId2,portId2,tpn2,priority,metric
        - 只为host节点之间生成transport channel（不包括交换机节点）
        - 每对host节点的每条路由会为每个配置的优先级生成一个channel
        - TPN(Transport Point Number)会为每个节点自动递增分配
        - 使用 `self.route_dict4tp` 中存储的路由信息
        - 优先级列表通过 `config_transport_channel()` 方法配置，默认为[7, 8]
    
        数据来源
        --------
        - route_dict4tp : 包含 (host_a, host_b) -> [(src_port, metric, dst_port)] 映射
        - port_list : 用于识别host节点数量
        - priority_list : 配置的优先级列表
    
        输出文件结构
        -----------
        - nodeId1: 源host节点ID
        - portId1: 源节点的出端口ID
        - tpn1: 源节点的transport point number
        - nodeId2: 目标host节点ID  
        - portId2: 目标节点的入端口ID
        - tpn2: 目标节点的transport point number
        - priority: 传输优先级
        - metric: 路由度量值
    
        Examples
        --------
        >>> # 配置优先级并生成transport channel文件
        >>> graph.config_transport_channel([7, 8, 9])
        >>> graph._write_transport_channel_csv("./output/transport_channel.csv")
        写入TP Channel: 100%|██████████| 12/12 [00:00<00:00, 1234.56it/s]
        TP Channel: /path/to/output/transport_channel.csv
    
        See Also
        --------
        config_transport_channel : 配置优先级列表
        gen_route_table : 生成路由表数据
        ns3_config : 完整的ns-3配置文件生成流程
        """
        host_num = len([x for x in self.port_list if x[0] == 0])
        tpn_counters = defaultdict(int)
        # 使用配置的优先级列表，如果没有配置则使用默认值
        priority_list = getattr(self, 'priority_list', [7, 8])
        
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            f.write("nodeId1,portId1,tpn1,nodeId2,portId2,tpn2,priority,metric\n")
            
            # 计算总的写入行数用于进度条
            total_combinations = (host_num * (host_num - 1) // 2) * len(priority_list)
            with tqdm(total=total_combinations, desc="写入TP Channel") as pbar:
                for host_a in range(host_num - 1):
                    for host_b in range(host_a + 1, host_num):
                        for priority in priority_list:
                            for src_port, metric, dst_port in self.route_dict4tp[(host_a, host_b)]:
                                row = (host_a, src_port, tpn_counters[host_a],
                                       host_b, dst_port, tpn_counters[host_b],
                                       priority, metric)
                                f.write(",".join(map(str, row)) + "\n")
                                tpn_counters[host_a] += 1
                                tpn_counters[host_b] += 1
                            pbar.update(1)
        print(f"TP Channel: {os.path.abspath(filepath)}")

    def _write_node_csv(self, filepath):
        """生成节点配置文件，优化连续范围合并"""
        map_dict = {0: "DEVICE", 1: "SWITCH"}
        
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            f.write("nodeId,nodeType,portNum,forwardDelay\n")
            
            # 按 (nodeType, portNum) 分组并合并连续节点
            for key, group_iter in groupby(enumerate(self.port_list), key=lambda x: x[1]):
                node_indices = [idx for idx, _ in group_iter]
                node_type, port_num = key
                
                # 将连续的节点ID合并为范围
                ranges = self._get_consecutive_ranges(node_indices)
                
                for range_start, range_end in ranges:
                    # 获取范围内第一个节点的 forward_delay（假设范围内相同）
                    forward_delay = self.nodes[range_start].get('forward_delay', '0ns')
                    
                    if range_start == range_end:
                        node_id_str = str(range_start)
                    else:
                        node_id_str = f"{range_start}..{range_end}"
                    
                    f.write(f"{node_id_str},{map_dict[node_type]},{port_num},{forward_delay}\n")
        print(f"节点配置: {os.path.abspath(filepath)}")
        
    def _get_consecutive_ranges(self, indices):
        """将连续的索引列表转换为范围元组列表"""
        if not indices:
            return []
        
        ranges = []
        start = indices[0]
        prev = indices[0]
        
        for curr in indices[1:] + [None]:  # 添加None作为结束标志
            if curr is None or curr != prev + 1:
                ranges.append((start, prev))
                if curr is not None:
                    start = curr
            if curr is not None:
                prev = curr
        
        return ranges

    def _write_topology_csv(self, filepath):
        """生成拓扑连接配置文件"""
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            f.write("nodeId1,portId1,nodeId2,portId2,bandwidth,delay\n")
            for u, u_port, v, v_port, bandwidth, delay in tqdm(self.link_infos, desc="写入拓扑连接"):
                f.write(f"{u},{u_port},{v},{v_port},{bandwidth},{delay}\n")
                
        print(f"拓扑配置: {os.path.abspath(filepath)}")

def check_route_table_connectivity(route_table_csv_path):
    """
    检查路由表文件中每个节点是否都包含到所有host节点的路由
    
    此函数读取路由表CSV文件，验证每个节点（包括host和switch）是否都有
    到达所有host节点的路由表项。确保网络连通性。
    
    Parameters
    ----------
    route_table_csv_path : str
        路由表CSV文件路径
        文件格式应为: nodeId,dstNodeId,dstPortId,outPorts,metrics
        
    Returns
    -------
    bool
        如果所有节点都有完整的host路由则返回True，否则返回False
        
    Raises
    ------
    FileNotFoundError
        当指定的路由表文件不存在时
    ValueError
        当CSV文件格式不正确时
        
    Notes
    -----
    - 函数会打印详细的检查结果和统计信息
    - 对于缺失路由的情况，会输出具体的缺失信息
    - host节点不需要到自己的路由（自环路由）
    
    Examples
    --------
    >>> check_all_hosts_routes("/path/to/route_table.csv")
    开始检查路由表完整性...
    路由表检查完成: ✓ 所有节点都有完整的host路由
    True
    """
    import csv
    from collections import defaultdict
    
    if not os.path.exists(route_table_csv_path):
        raise FileNotFoundError(f"路由表文件不存在: {route_table_csv_path}")
    
    print(f"开始检查路由表完整性...")
    print(f"路由表文件: {route_table_csv_path}")
    
    # 读取路由表并统计
    node_routes = defaultdict(set)  # node_id -> set of dst_host_ids
    all_nodes = set()
    all_hosts = set()
    
    try:
        with open(route_table_csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            expected_headers = {'nodeId', 'dstNodeId', 'dstPortId', 'outPorts', 'metrics'}
            
            if not expected_headers.issubset(set(reader.fieldnames)):
                raise ValueError(f"CSV文件缺少必要的列: {expected_headers - set(reader.fieldnames)}")
            
            for row_num, row in enumerate(reader, start=2):  # 从第2行开始（第1行是header）
                try:
                    node_id = int(row['nodeId'])
                    dst_node_id = int(row['dstNodeId'])
                    
                    all_nodes.add(node_id)
                    all_hosts.add(dst_node_id)  # 路由表中的目标都是host
                    node_routes[node_id].add(dst_node_id)
                    
                except ValueError as e:
                    print(f"警告: 第{row_num}行数据格式错误: {e}")
                    continue
                    
    except Exception as e:
        raise ValueError(f"读取CSV文件时发生错误: {e}")
    
    # 统计信息
    total_nodes = len(all_nodes)
    total_hosts = len(all_hosts)
    print(f"发现 {total_nodes} 个节点, {total_hosts} 个host节点")
    
    # 检查每个节点的路由完整性
    missing_routes = []
    complete_nodes = 0
    
    for node_id in sorted(all_nodes):
        node_host_routes = node_routes[node_id]
        # host节点不需要到自己的路由
        expected_hosts = all_hosts - {node_id} if node_id in all_hosts else all_hosts
        missing_hosts = expected_hosts - node_host_routes
        
        if not missing_hosts:
            complete_nodes += 1
        else:
            missing_routes.append((node_id, missing_hosts))
    
    # 输出检查结果
    print(f"\n路由完整性检查结果:")
    print(f"- 路由完整的节点: {complete_nodes}/{total_nodes}")
    print(f"- 路由不完整的节点: {len(missing_routes)}")
    
    if missing_routes:
        print(f"\n缺失路由详情:")
        for node_id, missing_hosts in missing_routes[:10]:  # 最多显示10个
            missing_list = sorted(list(missing_hosts))
            if len(missing_list) > 5:
                missing_str = f"{missing_list[:5]}... (共{len(missing_list)}个)"
            else:
                missing_str = str(missing_list)
            print(f"  节点 {node_id}: 缺失到host {missing_str} 的路由")
        
        if len(missing_routes) > 10:
            print(f"  ... 还有 {len(missing_routes) - 10} 个节点存在路由缺失")
        
        print(f"\n路由表检查失败: {len(missing_routes)} 个节点缺失路由")
        return False
    else:
        print(f"\n路由表检查完成: 所有节点都有完整的host路由")
        return True