# 百B模型与300/500卡级网络输入

本目录提供六套可修改的物理网络配置和两个Qwen3-235B模型形状配置。查看[图片与解释](../../experiments/large_model_scaleout/results/20261028-final/report.html)，来源与复现见[实验说明](../../experiments/large_model_scaleout/README.md)。这些大规模输入通过静态校验，没有完成百卡包级网络执行。

| 配置 | GPU / 服务器 | 网络 |
|---|---|---|
| `clos-300.json` | 300 / 75，四卡服务器 | 5 Pod，25 ToR / 20 Agg / 8 Core |
| `clos-500.json` | 500 / 125，四卡服务器 | 5 Pod，25 ToR / 20 Agg / 8 Core |
| `clos-320.json` | 320 / 40，八卡服务器 | 5 Pod，20 ToR / 20 Agg / 8 Core |
| `clos-512.json` | 512 / 64，八卡服务器 | 8 Pod，32 ToR / 32 Agg / 8 Core |
| `hpn-inspired-320.json` | 320 / 40，八卡服务器 | 2 Pod，8 rail、2 plane；64 ToR / 16 Agg / 8 Core |
| `hpn-inspired-512.json` | 512 / 64，八卡服务器 | 同上层数量，增加接入规模 |

Clos所有链路400G；HPN启发结构每plane接入200G，上层400G，每GPU声明NIC总预算400G。所有链路传播时延声明1µs。HPN启发结构采用普通电交换机、跨rail共享Agg/Core，不包含OCS控制或生产HPN完整语义。GPU类型H100仅为标签，服务器是放置属性，没有机内NVLink模型；同服务器GPU之间需要远端消息时仍走所声明的网络。

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_large_models/clos-500.json --output /path/to/new-topology
PYTHONPATH=python python3 -m moe_ub_cosim scaleout rdma-preflight \
  --topology /path/to/new-topology \
  --protocol examples/scaleout_large_models/rdma-static-preflight.json \
  --output /path/to/new-buffer-report.json
```

`rdma-static-preflight.json`只提供本轮输入校验采用的显式协议和资源上限，ECN/RDMA参数未经硬件校准。`rdma-preflight`不运行网络，也不保证运行时间/资源足够。该配置采用`port_dense`交换机计数、摘要队列记录；没有自动设置完整逐包捕获。开展新的大规模运行或逐包观测时，应另定预算与捕获范围，先完成小规模检查。

`qwen235-320.json`、`qwen235-512.json`采用上游SimAI中Qwen3-235B BF16形状：H4096、FFN1536、94层、128专家、Top8、64个Q头、4个KV头、head_dim128。按当前前端公式共235,093,634,560参数。EP64，分别5/8个独立推理副本，按rank交错分组以跨Pod通信；每rank每层2专家。两份模型输入均可配相同规模的Clos或HPN启发结构，通过完整服务输入校验，不表示已执行网络。

每rank batch=1，ready-KV Decode初始上下文2048、生成2 token，使用合成专家选择和声明计算成本。权重21.51GiB/rank，KV预留约0.370GiB/rank；未核算激活、工作区和通信缓冲，不能当作真实H100吞吐或内存校准。没有TP/PP，非专家权重在EP组内复制。

当前均匀专家放置要求EP整除专家数。全部300/500卡若使用等大EP组，最大EP4；本模型BF16仅权重约120.65GiB/rank，超过声明80GiB，所以精确300/500卡没有提供假装可运行的模型配置。备用卡、非等大组、TP/PP或量化须分别建模。320/512卡配置的全局并发分别320/512，不能直接作为固定负载的强扩展对照。

R1-0528的MLA、3个Dense层和共享专家尚未接入当前Scale-Out完整模型前端；现有真实R1-AWQ路由片段也不能改名为Qwen或R1-0528路由。更多GPU不会补齐这些模型能力。
