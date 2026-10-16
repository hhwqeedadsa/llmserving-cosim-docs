# 完整形状 MoE Decode：16 / 32 / 128 卡三层 Clos

本例使用 Qwen3-30B-A3B-Instruct-2507 的完整结构声明：48 层、隐藏维 2048、专家中间维 768、128 专家、Top-K=8、BF16。保留 embedding、逐层 attention/残差/归一化/MoE 和最终 head/sampling 的依赖，但不执行张量数值运算。计算成本及路由仍是声明参考值和合成输入，没有 H100 硬件校准。

非 UB RDMA 入口与普通 UB `run` 分别构建，只链接固定 ASTRA 所带原生 ns-3。机内和跨机共用该可执行程序的一个 Simulator，不相加独立运行耗时。[实验方法审阅稿](../../docs/review_artifacts/ScaleOut实验方法_审阅稿_v0.3_20261016.pdf)保存实施口径与当时状态；实际运行以校验报告为准。

## 配置和物理连接

| 配置 | GPU / server | ToR / Agg / Core | 外部链路 |
|---|---:|---:|---:|
| `clos-16.json` | 16 / 2 | 2 / 4 / 2 | 24 |
| `clos-32.json` | 32 / 4 | 4 / 4 / 2 | 44 |
| `clos-128.json` | 128 / 16 | 8 / 8 / 4 | 168 |

每台服务器 8 GPU；两 Pod，每 GPU 一条 400 Gb/s 外部 NIC。所有外部层的单方向速率 400 Gb/s，逐边传播 1000 ns。128 卡每 Pod 4 ToR / 4 Agg，每 ToR 连全部本 Pod Agg，每 Agg 仅连一个分组 Core。跨 Pod 路径经 ToR—Agg—Core—Agg—ToR；不是无阻塞 Clos：ToR 和 Agg 的局部下/上带宽比各为 4:1，Pod 全部 GPU 接入/跨 Pod 出口为 16:1。Core 为普通电交换机。

`moe-scaleout-topology/2` 增加 `intra_host`：

```json
{"kind":"nonblocking_chunked_crossbar","enabled":true,
 "rate_bps":3600000000000,"delay_ns":100,"chunk_bytes":16384}
```

同 GPU 项只保留本地计算依赖；同 server 的不同 GPU 走机内模型；其他走 RDMA。机内每 GPU 入、出方向各有一条共享带宽链路，按块 FIFO 竞争，交换织构内部无阻塞。`rate_bps` 是每方向 450 GB/s，`delay_ns` 对进入和离开织构各计一次；不是完整 NVLink/NVSwitch 协议。文本 ASTRA/SimAI 拓扑只导出外部 RDMA，机内模型保留在 JSON，不能把文本文件当完整联合配置。

## 模型、设备与负载

`qwen3-N.json` 使用 `moe-scaleout-service/2`；旧 `/1` 有限层配置继续有效。新增 `model.llm` 明确 Q/KV heads、head_dim、词表、embedding 是否共享、权重元素字节和设备容量，`compute` 明确首尾/归一化/残差和上下文 attention 成本。

总参数 30,532,122,624；其中专家 28,991,029,248，组内各 rank 复制的其他参数 1,541,093,376。权重按 EP 分片专家、复制其他部分核算。Q/KV heads 为 32/4，head_dim=128，每 token 的全层 GQA KV 为 98,304 B；声明 80 GiB 设备容量。检查权重＋按块对齐的 KV 是否装得下，其他 workspace/激活/分配器开销尚未校准，报告中的 `other_workspace_bytes=0` 是缺少预算模型，不是硬件真实开销为零。

默认每 rank batch=1、context=2048、2 轮 Decode。context 是已经准备好的 KV 长度，每序列每轮只新增 1 token。全局 token=N×batch；增加 N 同时增加负载与 EP，不能拿三个完成时间计算强扩展效率。

通信按 token×expert 每贡献复制一次，按源/目的 rank 聚合为直接成对 All-to-All-v；不等于 NCCL/DeepEP 实现。EP 组必须覆盖物理 GPU 且互不重叠，每组大小至少 2，且整除专家数。更大集群使用多个推理副本，不能将 128 专家直接设置为 EP1024。

## 运行

先按 [RDMA 构建说明](../scaleout_rdma/README.md)构建非 UB 后端。以下假定使用已安装前端依赖的 Python、已有 `protoc`，并将后端参数替换为真实可执行文件路径。输出采用新目录；保存旧失败，不覆盖。

```bash
PYTHONPATH=python python -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_clos128/clos-16.json --output /dev/shm/qwen16-topology-new
PYTHONPATH=python python -m moe_ub_cosim scaleout rdma-run \
  --config examples/scaleout_clos128/qwen3-16.json \
  --topology /dev/shm/qwen16-topology-new \
  --protocol examples/scaleout_clos128/rdma.json \
  --backend /absolute/path/to/ns3.42-moe-rdma-service \
  --protoc /absolute/path/to/protoc --output /dev/shm/qwen16-run-new
PYTHONPATH=python python -m moe_ub_cosim scaleout rdma-check --run /dev/shm/qwen16-run-new
```

`rdma-run` 已执行一次校验；单独 `rdma-check` 用于独立复核保存证据。先 16/32，再将拓扑和服务输入一起换成 128；不能只改 GPU 标签或只改拓扑。

`rdma.json` 使用 `moe-scaleout-rdma/2` 显式声明限额：128 GPU、32 switch、1024 link、300000 message、summary 队列；模拟 100 ms、宿主 300 秒、RSS 6 GiB、地址空间 12 GiB、产物 3 GiB、结果 JSON 512 MiB。旧 `/1` 仍是 32 GPU、16 switch、8192 message 等小图默认值。数量预算不会自动提高，超限会拒绝或保留失败；声明较大限额不证明已经执行。

## 修改参数与读取证据

修改 `workload.batch_per_rank` 会同时改变 KV、计算和通信；`context_tokens` 改变 KV 与声明 attention 上下文成本，不等于增加每轮通信 token。修改逐层带宽时，也要为每种实际速率填写 `ecn_by_rate`。模型形状可在输入约束内修改，并重新核算 `model.llm`、显存和资源；改变 `gpu_type` 标签不会自动换算成本。

`result.json` 保留消息路径、连续接收/ACK、物理端口计数及机内链路占用；`frontend.json` 保留请求进度与实际下一轮启动；`graphs/*/wave.json` 和 Chakra 保留图。`log/astra_nodes.log` 完整保存 ASTRA 节点 issue/callback；诊断滚动日志不作为新运行的完整证据。服务只在 EP 组真正完成后返回调度，不为每个局部 rank 完成发送空决策。

机内消息明确 `transport=intra_host_chunked_crossbar`、`completion_kind=local_data_ready_no_RDMA_ACK`；兼容字段 `ack_ns==receive_ready_ns` 不表示 RDMA ACK。`intra_host_links` 按 GPU/方向统计 payload、busy、max_wait、chunks。`queue_capture=summary` 更新钩子观测计数和峰值但不保留时序；不能从空 `queue_events` 推断没有排队。

为减少宿主内存，关闭 prefix caching 的 Scale-Out KV 池按需创建块元数据，仍按原池 FIFO 次序、原引用计数/容量方法运行；未使用显存容量没有缩小。终态 JSON 记录转移所有权并流式输出，不重复复制全体消息；这些是表示优化，不修改包或模拟时间。
