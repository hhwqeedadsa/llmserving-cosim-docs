# 本次实验条件与结果

- 硬件口径：模拟H100配置，320卡/40服务器/5Pod；没有执行真实GPU或校准硬件时延。
- 部署：组0: EP256，每rank每层1专家，12请求/2源rank；组1: EP64，每rank每层4专家，3请求/1源rank。
- 网络：1plane，440条边，交换机{'tor': 20, 'agg': 20, 'core': 8}；access: 400Gb/s；tor_agg: 25Gb/s；agg_core: 25Gb/s。
- 工作量：15原件请求，batch上限6/rank，实际最大6；原层[3, 4]，2步Decode，初始KV已就绪。
- 计算：declared_reference，不是完整R1数值执行或计算性能预测。
- 恢复：{"mode": "go_back_n", "initial_rto_ns": 40000, "max_rto_ns": 160000, "max_retries": 4, "max_events": 100000}。
- 周期性人工丢包：{"kind": "periodic_destination_data/1", "numerator": 1, "denominator": 1, "offset": 0, "start_ns": 0, "end_ns": 5000000, "nodes": []}。
- 执行结果：{"state": "failed", "native_network_executed": true, "finish_ns": 603090, "submitted_messages": 92, "logical_submitted_bytes": 1705984, "actual_data_ranks": 81, "complete_packet_capture": true, "packet_capture_events": 20268, "fault_drops": 1170, "data_loss": {"configuration": {"denominator": 1, "end_ns": 5000000, "kind": "periodic_destination_data/1", "nodes": [], "numerator": 1, "offset": 0, "start_ns": 0}, "dropped_packets": 1170, "eligible_destination_data_packets": 1170, "scope": "periodic artificial destination data loss; ACK/NACK/PFC unaffected; half-open time window; ordinal counts actual eligible arrivals including retransmissions", "window_packets": 1170}, "failure": {"message_id": 261121, "reason": "recovery_retry_exhausted", "scope": "whole shared simulation stopped; no successful callbacks or credits synthesized; live resources retained for diagnosis until teardown", "time_ns": 603090}}。

完整机器可读字段见scenario.json。窗口丢包率与全任务丢包率分别计算，不将人工故障称为拥塞自然产生的丢包。
