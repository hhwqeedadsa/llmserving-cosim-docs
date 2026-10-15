# Scale-Out 容量与通信量核算

此命令在运行网络前检查工作量及固定数组开销。它不启动 ns-3，不预测时延，也不验证 GPU 显存能否容纳模型。仅需 Python 基础安装，无需 GPU、模型权重、真实路由或历史运行。

仓库根目录执行（输出须是新目录）：

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout capacity \
  --config examples/scaleout_capacity/1024-decode.json \
  --output runs/capacity-1024-decode
```

输出 `config.json` 保存原始配置，`capacity.json` 保存输入 SHA-256、并行组数、逻辑字节、Clos 带宽及交换机数组开销；状态为 `estimate_only`、`network_executed=false`。命令返回成功表示输入有效且核算完成。不会调用 `run`，也没有将此配置直接变成 RoCE 仿真的功能。

复制 JSON 到新文件后修改字段，再用新输出目录重跑。字段均须显式填写，无配置继承或隐式覆盖；配置中没有文件路径，命令行路径相对当前目录。配置最多 64 KiB，不接受未知字段。

| 字段 | 含义与范围 |
|---|---|
| `parallel.world_gpus` | 总 rank 数，1–4096；上限是核算输入限制，并非已验证仿真规模 |
| `parallel.ep_size` | 专家并行组大小，须整除 world 和专家数；每个 rank 持有整数个专家，不含专家张量并行 |
| `parallel.tp_size/pp_size` | 当前均限 1；张量并行和流水并行需要另行定义 token 分发与字节，不可只加乘数 |
| `model.routed_experts/top_k` | 每层路由专家数及每 token 无放回 Top-K；分别最多65536/256，K不大于E；不包含共享专家 |
| `model.hidden_dim/activation_bytes/moe_layers` | 向量维度、通信元素字节、参与统计的 MoE 层数；不是权重精度或模型总参数量 |
| `workload.phase` | `decode`、`prefill` 或 `train`；训练这里只核算激活通信 |
| `workload.sequences_per_rank` | 每 rank 的序列数；不是 EP 组总 batch，也不是全局 batch |
| `workload.sequence_length` | 本次处理的 query token 数。Decode 限 1，不能填 KV context；Prefill/训练假定等长序列 |
| `workload.gradient_accumulation` | 非训练限 1；训练时表示一个优化器 step 的累积 microbatch 数，不改变同时在网的流量 |
| `fabric` | 一个双层全互连 leaf-spine 网络；每 GPU 一张 NIC，每 leaf-spine 对一根链路。总下行口等于 GPU 数，检查两层端口数上限 |
| `fabric.*_rate_bps` | NIC 和 leaf 上行的单方向每口 bit/s，非 GB/s；模型没有强制物理交换机无内部争用 |
| `fabric.include_nvswitch` | 是否在资源估算中加入每服务器一个 `NVSwitchNode`；不代表服务器真实只有一颗 NVSwitch 芯片 |
| `payload_bytes` | 1–65536，计算理想 payload 包数规模；不修改任何实际网络 MTU |

例如 `sequences_per_rank=4`：Decode 每 rank 每步 4 token；Prefill 如再设 `sequence_length=128`，则为每 rank 512 token。训练若设 `gradient_accumulation=8`，全局优化器 batch 为 `world×4×8` 条序列。当前 TP=PP=1，dense/attention 的 DP rank 数为 world；专家副本组数为 `world/EP`，不要再把 world 乘以 EP。

示例沿用 R1 的部分通信维度（256 专家、Top-8、7168 维、58 个 MoE 层），仅用于参数化核算；没有声明 TP=1 的 R1 权重、KV 或训练优化器状态能在某型号 GPU 上容纳。示例也不指定 A100/RTX 的计算性能。真实 GPU 类型应通过权重/KV 容量检查、算子测量和 NIC/PCIe 拓扑单独校准。

`logical_remote_rank_volume` 同时输出两种参考通信方式：逐专家复制向量、同 token 同目标 rank 只发一次向量。二者假设均匀选专家、平均分配，不包含热点、padding、drop、共享专家、元数据、梯度 DP、ACK/PFC 或重传。相同服务器内的远程 rank 流量不必走 Ethernet，故这里不能得到跨机字节或 NIC 利用率。详细推导、千卡路线和训练研究设计见[研究报告](../../docs/reviews/scaleout_scaling_hotspots_20261015.md)。

参数或端口关系非法会在创建输出前失败；已有输出目录不会覆盖。核算是常数规模计数加 Top-K 的有界计算，不生成 world² 个对象；它不提供延迟、资源峰值或千卡运行成功的证据。
