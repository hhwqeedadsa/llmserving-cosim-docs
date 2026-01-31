# -*- coding: utf-8 -*-
import os
import csv
from glob import glob
import re
import pandas as pd
import sys

# 定义字段提取函数
def extract_task_fields(line):
    match = re.match(r'\[(.*?)\] (.*?),.*?taskId: (\d+)', line)
    if match:
        timestamp = match.group(1)[:-2]
        event = match.group(2)
        task_id = match.group(3)
        if "WQE Starts" in event:
            return {'task_id': task_id, 'timestamp': timestamp, 'event': 'Starts'}
        elif "WQE Completes" in event:
            return {'task_id': task_id, 'timestamp': timestamp, 'event': 'Completes'}
        elif "MEM Task Starts" in event:
            return {'task_id': task_id, 'timestamp': timestamp, 'event': 'Starts'}
        elif "MEM Task Completes" in event:
            return {'task_id': task_id, 'timestamp': timestamp, 'event': 'Completes'}
    return None

def extract_packet_fields(line):
    match = re.match(r'\[(.*?)\] (.*?),.*?taskId: (\d+)', line)
    if match:
        timestamp = match.group(1)[:-2]
        event = match.group(2)
        task_id = match.group(3)
        if "First Packet Sends" in event or "Last Packet ACKs" in event:
            return {'task_id': task_id, 'timestamp': timestamp, 'event': event}
    return None

def filterRedundantData(data):
    # 初始化结果字典
    result = {}
    # 遍历每个任务ID及其对应的事件列表
    for task_id, events in data.items():
        first_packet_sends = None
        last_packet_acks = None

        # 遍历每个事件
        for event in events:
            if event['event'] == 'First Packet Sends':
                if first_packet_sends is None:
                    first_packet_sends = float(event['timestamp'])
            elif event['event'] == 'Last Packet ACKs':
                last_packet_acks = float(event['timestamp'])

        # 将结果存储在结果字典中
        result[task_id] = {
            'First Packet Sends': first_packet_sends,
            'Last Packet ACKs': last_packet_acks
        }
    return result

def process_files_to_csv(input_folder,csv_file, output_csv, pattern="*.tr"):
    # 获取所有匹配的文件路径
    file_paths = glob(os.path.join(input_folder, pattern))
    task_data = {}
    packet_data = {}
    # 遍历处理每个文件
    for file_path in file_paths:
        try:
            filename = os.path.basename(file_path)

            if "PacketTrace_node_" in filename:
                # 读取PacketTrace文件
                with open(file_path, 'r') as f:
                    for line in f:
                        data = extract_packet_fields(line)
                        if data:
                            task_id = data['task_id']
                            if task_id not in packet_data:
                                packet_data[task_id] = []
                            packet_data[task_id].append(data)

            elif "TaskTrace_node_" in filename:
                # 读取TaskTrace文件           
                with open(file_path, 'r') as f:
                    for line in f:
                        data = extract_task_fields(line)
                        if data:
                            task_id = data['task_id']
                            if task_id not in task_data:
                                task_data[task_id] = []
                            task_data[task_id].append(data)
    
            #else :
                #print("不解析该文件：",filename)

        except Exception as e:
            print(f"处理文件 {file_path} 时出错: {e}")

    #print("packet_data:",packet_data)

    filterData = filterRedundantData(packet_data)

    # # 匹配数据并生成CSV
    merged_data = []
    for task_id in task_data:
        task_Data = task_data[task_id]
        packe_Data= filterData.get(task_id, {'First Packet Sends': None, 'Last Packet ACKs': None})
        # 使用字典推导式获取特定事件的timestamp
        timestamps = {
            item['event']: item['timestamp']
            for item in task_Data
            if item['event'] in ['Starts', 'Completes']
        }

        merged_entry = {
            'Task ID': task_id,
            'Task Start Time': timestamps.get('Starts', None),
            'Task End Time': timestamps.get('Completes', None),
            'First Packet Sends':packe_Data['First Packet Sends'],
            'Last Packet ACKs':packe_Data['Last Packet ACKs']
        }
        merged_data.append(merged_entry)

    # 读取CSV文件
    #csv_file = 'file.csv'  # 替换为traffic-config.csv
    df = pd.read_csv(csv_file, delimiter=',')

    # 确保DataFrame中存在新列
    df['taskStartTime(us)'] = None
    df['taskCompletesTime(us)'] = None
    df['firstPacketSends(us)'] = None
    df['lastPacketACKs(us)'] = None
    df['taskThroughput(Gbps)'] = None

    # 遍历任务详细信息列表
    missing_packet_info_count = 0
    total_tasks = len(merged_data)
    
    for task in merged_data:
        task_id = int(task['Task ID'])
        task_start_time = float(task['Task Start Time'])
        task_end_time = float(task['Task End Time'])
        
        if task['First Packet Sends'] is None:
            first_packet_sends = 0.0
            missing_packet_info_count += 1
        else:
            first_packet_sends = float(task['First Packet Sends'])

        if task['Last Packet ACKs'] is None:
            last_packet_ACKs = 0.0
        else:
            last_packet_ACKs = float(task['Last Packet ACKs'])
            
        task_throughput = round((((df['dataSize(Byte)']*8)/ (1000 ** 3)) / ((task_end_time - task_start_time)/1000000)), 4)

        # 将新数据添加到DataFrame中
        df.loc[df['taskId'] == task_id, 'taskStartTime(us)'] = task_start_time
        df.loc[df['taskId'] == task_id, 'taskCompletesTime(us)'] = task_end_time
        df.loc[df['taskId'] == task_id, 'firstPacketSends(us)'] = first_packet_sends
        df.loc[df['taskId'] == task_id, 'lastPacketACKs(us)'] = last_packet_ACKs
        df.loc[df['taskId'] == task_id, 'taskThroughput(Gbps)'] = task_throughput
    
    # 保存修改后的DataFrame到新的CSV文件
    df.to_csv(output_csv, index=False)
    
    print(f"Stats: Processed {total_tasks} tasks.")
    if missing_packet_info_count > 0:
        print(f"Note: {missing_packet_info_count}/{total_tasks} tasks missing packet timestamps (First Packet Sends / Last Packet ACKs).")
        print("      This is expected if UB_PACKET_TRACE_ENABLE is set to 'false'.")
    else:
        print("Stats: All tasks have complete packet timestamp info.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("python3 task_statistics.py traffic.csv")
        sys.exit(1)

    input_dir = sys.argv[1] +"runlog"  # 输入文件夹路径
    if len(sys.argv[2]) > 0 and sys.argv[2].lower() == 'true' :
        output_file = sys.argv[1] + "test/task_statistics.csv"  # 输出CSV路径
    else:
        output_file = sys.argv[1] + "output/task_statistics.csv"  # 输出CSV路径
    traffic_file = sys.argv[1] + "/traffic.csv"
    # 创建输入文件夹（如果不存在）
    os.makedirs(input_dir, exist_ok=True)
    if len(sys.argv[2]) > 0 and sys.argv[2].lower() == 'true' :
        output_dir = sys.argv[1] +"test"  # 输入文件夹路径
    else:
        output_dir = sys.argv[1] +"output"  # 输入文件夹路径
    os.makedirs(output_dir, exist_ok=True)
    
    process_files_to_csv(input_dir, traffic_file,output_file)
    print(f"Saved traffic tasks throughput results to {output_file}")
