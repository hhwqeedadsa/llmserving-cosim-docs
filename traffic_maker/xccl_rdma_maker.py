import math
import os
import argparse
import datetime
import shutil
import csv


def generate_all_rank_table(total_host, n_comm_size, type="distributed"):
    """
    根据通信域数量和大小生成所有排名表。

    参数:
    - n_comm_num: 通信域数量
    - n_comm_size: 每个通信域的大小
    - type: 分布类型，默认为 "distributed"，否则为集中排布

    返回:
    - all_rank_table: 每个通信域的排名表列表
    """
    if total_host % n_comm_size != 0:
        raise ValueError("total_host % n_comm_size != 0")
    n_comm_num = total_host // n_comm_size
    if type == "distributed":
        print("ranktable均匀分布")
    else:
        print("ranktable集中排布")

    all_rank_table = []
    for comm_id in range(n_comm_num):
        if type == "distributed":
            rt = [i * n_comm_num + comm_id for i in range(n_comm_size)]
        else:
            rt = [i + comm_id * n_comm_num for i in range(n_comm_size)]
        all_rank_table.append(rt)
    return all_rank_table

def generate_ar_ring_logic_comm_pairs(n_comm_size, n_byte):
    """
    生成Ring AllReduce通信对。

    参数:
    - n_comm_size: 每个通信域的大小
    - n_byte: 要传输的字节数

    返回:
    - ring_logic_comm_pairs: 每个阶段的通信对列表， ring_logic_comm_pairs[phase] = (sender, recver, trans_byte)
    """
    logic_rank_table = [i for i in range(n_comm_size)]
    ring_logic_comm_pairs = []
    for phase_id in range(n_comm_size - 1):
        phase_comm_pair = []
        phase_byte = n_byte / n_comm_size
        for r in logic_rank_table:
            send_pair = (r, (r + 1) % len(logic_rank_table), phase_byte)
            phase_comm_pair.append(send_pair)
        ring_logic_comm_pairs.append(phase_comm_pair)
    return ring_logic_comm_pairs


def generate_ar_RHD_logic_comm_pairs(n_comm_size, n_byte):
    n_total_phase = math.ceil(math.log2(n_comm_size))
    logic_rank_table = [i for i in range(n_comm_size)]
    nhr2_logic_comm_pairs = []

    if 2**n_total_phase > n_comm_size:
        phase_comm_pair = []
        recursive_list = [x for x in range(2**(n_total_phase-1),n_comm_size)]
        for idx,rec in enumerate(recursive_list):
            send_pair = (idx, rec, n_byte)
            phase_comm_pair.append(send_pair)
        nhr2_logic_comm_pairs.append(phase_comm_pair)
        n_total_phase -= 1
        logic_rank_table = [i for i in range(2**n_total_phase)]
    # ReduceScatter
    for phase_id in range(n_total_phase):
        phase_comm_pair = []
        phase_byte = round(n_byte / (2 ** (phase_id + 1)))
        send_gap = 2 ** phase_id
        for r in logic_rank_table:
            send_pair = (r, (r - send_gap) % len(logic_rank_table), phase_byte)
            phase_comm_pair.append(send_pair)
        nhr2_logic_comm_pairs.append(phase_comm_pair)
    # AllGather
    # for phase_id in range(n_total_phase - 1, -1, -1):
    #     phase_comm_pair = []
    #     phase_byte = round(n_byte / (2 ** (phase_id + 1)))
    #     send_gap = 2 ** phase_id
    #     for r in logic_rank_table:
    #         send_pair = (r, (r - send_gap) % len(logic_rank_table), phase_byte)
    #         phase_comm_pair.append(send_pair)
    #     nhr2_logic_comm_pairs.append(phase_comm_pair)
    nhr2_logic_comm_pairs += nhr2_logic_comm_pairs[::-1]

    return nhr2_logic_comm_pairs


def generate_ar_NHR2_logic_comm_pairs(n_comm_size, n_byte):
    """
    生成NHR2.0 AllReduce通信对。

    参数:
    - n_comm_size: 每个通信域的大小
    - n_byte: 要传输的字节数

    返回:
    - nhr2_logic_comm_pairs: 每个阶段的通信对列表， nhr2_logic_comm_pairs[phase] = (sender, recver, trans_byte)
    """
    n_total_phase = math.ceil(math.log2(n_comm_size))
    logic_rank_table = [i for i in range(n_comm_size)]
    nhr2_logic_comm_pairs = []
    # ReduceScatter 
    for phase_id in range(n_total_phase):
        phase_comm_pair = []
        phase_byte = round(n_byte / (2 ** (phase_id + 1)))
        send_gap = 2 ** phase_id
        for r in logic_rank_table:
            send_pair = (r, (r - send_gap) % len(logic_rank_table), phase_byte)
            phase_comm_pair.append(send_pair)
        nhr2_logic_comm_pairs.append(phase_comm_pair)
    # AllGather 
    # for phase_id in range(n_total_phase - 1, -1, -1):
    #     phase_comm_pair = []
    #     phase_byte = round(n_byte / (2 ** (phase_id + 1)))
    #     send_gap = 2 ** phase_id
    #     for r in logic_rank_table:
    #         send_pair = (r, (r - send_gap) % len(logic_rank_table), phase_byte)
    #         phase_comm_pair.append(send_pair)
    #     nhr2_logic_comm_pairs.append(phase_comm_pair)
    nhr2_logic_comm_pairs += nhr2_logic_comm_pairs[::-1]

    return nhr2_logic_comm_pairs

def generate_a2a_pairwise_logic_comm_pairs(n_comm_size, n_byte):
    """
    生成pairwise All2All通信对。

    参数:
    - n_comm_size: 每个通信域的大小
    - n_byte: 要传输的字节数

    返回:
    - pw_logic_comm_pairs: 每个阶段的通信对列表， pw_logic_comm_pairs[phase] = (sender, recver, trans_byte)
    """
    n_total_phase = n_comm_size - 1
    logic_rank_table = [i for i in range(n_comm_size)]
    pw_logic_comm_pairs = []
    for phase_id in range(n_total_phase):
        phase_comm_pair = []
        phase_byte = n_byte / n_comm_size
        send_gap = phase_id + 1
        for r in logic_rank_table:
            send_pair = (r, (r + send_gap) % len(logic_rank_table), phase_byte)
            phase_comm_pair.append(send_pair)
        pw_logic_comm_pairs.append(phase_comm_pair)
    return pw_logic_comm_pairs


def generate_a2a_scatter_logic_comm_pairs(n_comm_size, n_byte, k):
    n_total_phase = n_comm_size - 1
    logic_rank_table = [i for i in range(n_comm_size)]
    pw_logic_comm_pairs = []
    for phase_id in range(n_total_phase):
        phase_comm_pair = []
        phase_byte = n_byte / n_comm_size
        send_gap = phase_id + 1
        for r in logic_rank_table:
            send_pair = (r, (r + send_gap) % len(logic_rank_table), phase_byte)
            phase_comm_pair.append(send_pair)
        pw_logic_comm_pairs.append(phase_comm_pair)
    n_phases = len(pw_logic_comm_pairs)
    out = []
    for i in range(0, n_phases, k):
        # 当前组取 [i: i+k] 所有阶段，展平成一个大列表
        merged = []
        for stage in pw_logic_comm_pairs[i:i + k]:
            merged.extend(stage)
        out.append(merged)
    return out


def write_rdma_operations(output_dir, all_rank_table, logic_comm_pairs, phase_delay):
    """
    将 RDMA 操作写入文件。

    参数:
    - output_dir: 保存输出文件的目录
    - all_rank_table: 每个通信域的rank table
    - logic_comm_pairs: 每个阶段的通信对列表
    - phase_delay: 每个阶段间的间隔(us)
    """
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)  # 递归删除整个目录
    os.makedirs(output_dir)  # 重新创建空目录
    table = {'taskId':[],
             'sourceNode':[],
             'destNode':[],
             'dataSize':[],
             'opType':[],
             'priority':[],
             'delay':[],
             'phaseId':[],
             'dependOnPhases':[]}
    taskId = -1
    phaseId = -1
    for rt_id in range(len(all_rank_table)):
        rt = all_rank_table[rt_id]
        for phase_id_in_one_operate in range(len(logic_comm_pairs)):
            phaseId += 1
            for pair in logic_comm_pairs[phase_id_in_one_operate]:
                taskId += 1
                send_rank = int(rt[pair[0]])
                recv_rank = int(rt[pair[1]])
                trans_byte = pair[2]
                table['taskId'].append(taskId)
                table['sourceNode'].append(send_rank)
                table['destNode'].append(recv_rank)
                table['dataSize'].append(int(trans_byte))
                table['opType'].append('URMA_WRITE')
                table['priority'].append(7)
                table['delay'].append(str(phase_delay)+'ns')
                table['phaseId'].append(phaseId)
                table['dependOnPhases'].append(phaseId - 1 if phase_id_in_one_operate!=0 else -1)
    with open(output_dir+'/traffic.csv', 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        # 写表头
        header = ['taskId', 'sourceNodeId', 'destNodeId', 'dataSize(Byte)',
                  'opType', 'priority', 'delay', 'phaseId', 'dependOnPhases']
        writer.writerow(header)

        # 写数据
        rows = zip(table['taskId'],
                   table['sourceNode'],
                   table['destNode'],
                   table['dataSize'],
                   table['opType'],
                   table['priority'],
                   table['delay'],
                   table['phaseId'],
                   table['dependOnPhases'])

        for row in rows:
            # dependonPhases 如果是 -1 就留空，否则把 phaseId-1 转成字符串
            dep = '' if row[-1] == -1 else str(row[-1])
            writer.writerow((*row[:-1], dep))


def parse_size(size_str):
    """
    解析带有单位的字节数字符串。

    参数:
    - size_str: 字节数字符串，可带有单位（如B、KB、MB、GB）

    返回:
    - size: 以字节为单位的整数

    抛出:
    - ValueError: 如果输入格式不正确
    """
    size_str = size_str.upper()
    if size_str.endswith('GB'):
        size = int(size_str[:-2]) * 1024 * 1024 * 1024
    elif size_str.endswith('MB'):
        size = int(size_str[:-2]) * 1024 * 1024
    elif size_str.endswith('KB'):
        size = int(size_str[:-2]) * 1024
    elif size_str.endswith('B'):
        size = int(size_str[:-1])
    elif size_str.isdigit():
        size = int(size_str)
    else:
        raise ValueError("无效的字节数格式，请使用 B、KB、MB 或 GB 作为单位")
    return size

def parse_args():
    """
    解析命令行参数。
    """
    parser = argparse.ArgumentParser(description="XCCL RDMA Maker")
    parser.add_argument('-t', '--total_host', type=int, required=False, help='主机总数')
    parser.add_argument('-c', '--comm_size', type=int, required=False, help='每个通信域的大小')
    parser.add_argument('-b', '--comm_byte', type=str, required=False, help='要传输的字节数（支持 KB、MB、GB 单位）')
    available_algo = ['ar_ring', 'ar_nhr2', 'ar_rhd', 'a2a_pairwise', 'a2a_scatter']
    parser.add_argument('-a', '--algo', type=str, required=False, choices=available_algo, help='算法类型')
    parser.add_argument('-r', '--rank_distribution', type=str, required=False, choices=['distributed', '集中'], help='排名分布类型')
    parser.add_argument('-d', '--phase_delay', type=int, required=False, help='phase间间隔(us)', default=0)
    parser.add_argument('-k', '--scatter_K', type=int, required=False, help='a2a_scatter模式下的参数,代表每轮单个rank发出的消息数目，取值范围 [1, comm_size）', default=0)
    args = parser.parse_args()

    # 生成默认输出目录名称
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    default_output_dir = f"{timestamp}_host{args.total_host}_{args.algo}-{args.comm_size}_{args.comm_byte}"
    parser.add_argument('-o', '--output_dir', type=str, default=default_output_dir, help='输出目录')

    return parser.parse_args()

if __name__ == "__main__":
    """
    执行脚本的主函数。
    """
    args = parse_args()

    args.total_host = 10
    args.comm_size = 10
    args.comm_byte = '64MB'
    args.phase_delay = 10
    args.algo = 'a2a_scatter'
    args.rank_distribution = 'distributed'
    args.k = 3
    args.output_dir = './output/'

    total_host = args.total_host
    comm_size = args.comm_size
    comm_byte = parse_size(args.comm_byte)  # 要传输的字节数
    phase_delay = args.phase_delay  # 输出目录
    algo = args.algo  # 算法类型
    rank_distribution = args.rank_distribution # 输出目录
    folder_name = f"{total_host}_{comm_size}_{comm_byte}_{phase_delay}_{algo}_{rank_distribution}"
    output_dir = args.output_dir + folder_name
    # 生成所有排名表
    all_rank_table = generate_all_rank_table(total_host, comm_size, type=rank_distribution)
    print(all_rank_table)

    # 生成逻辑通信对
    if algo == 'ar_ring':
        logic_comm_pairs = generate_ar_ring_logic_comm_pairs(comm_size, comm_byte)
    if algo == 'a2a_pairwise':
        logic_comm_pairs = generate_a2a_pairwise_logic_comm_pairs(comm_size, comm_byte)
    if algo == 'a2a_scatter':
        logic_comm_pairs = generate_a2a_scatter_logic_comm_pairs(comm_size, comm_byte, args.k)
    if algo == 'ar_nhr2':
        logic_comm_pairs = generate_ar_NHR2_logic_comm_pairs(comm_size, comm_byte)
    if algo == 'ar_rhd':
        logic_comm_pairs = generate_ar_RHD_logic_comm_pairs(comm_size, comm_byte)

    # RDMA 操作文件的输出目录
    write_rdma_operations(output_dir, all_rank_table, logic_comm_pairs, phase_delay)