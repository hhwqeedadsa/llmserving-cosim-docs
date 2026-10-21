# 非 UB Scale-Out 小规模运行

本入口执行 ready-KV Decode 的有限 MoE 片段：锁定 LLMServingSim 的实际 Scheduler 组批，生成 Chakra 依赖图，由 ASTRA 调度，单一原生 ns-3 RDMA 网络执行数据包，再把完成事件返回调度器。默认 8 GPU 逻辑端点、2 层、4 轮，每 rank 4 个请求。它使用声明的计算成本和合成路由，不能当作完整模型或实机 A100 性能。

需要用逐请求来源替换合成选择、改变专家放置时，使用[路由回放小例](../scaleout_route_replay/README.md)的可选`route_replay`输入。原件经hash和全形状校验，运行保存快照；默认合成配置保持原行为。

## 构建与运行

使用已经具备 LLMServingSim 依赖及 Python protobuf 的 Python 环境，以及现有 CMake、Ninja、Protobuf；命令不会安装软件。`--protobuf-prefix` 指向带有 CMake 包配置的既有 Protobuf 安装。输出卷至少留 8 GiB。下面目录须不存在，父目录由使用者创建。

锁定 ASTRA 的构建要求 Chakra C++ schema 已初始化，`rdma-build` 会在缺少 `et_def.pb.h/.cc` 时明确拒绝。新检出可先用同一 Protobuf 安装的编译器生成这两个被忽略的构建输入（不修改 `.proto` 或依赖提交）：

```bash
/path/to/protobuf/bin/protoc \
  -I third_party/LLMServingSim/astra-sim/extern/graph_frontend/chakra/schema/protobuf \
  --cpp_out=third_party/LLMServingSim/astra-sim/extern/graph_frontend/chakra/schema/protobuf \
  third_party/LLMServingSim/astra-sim/extern/graph_frontend/chakra/schema/protobuf/et_def.proto
```

```bash
python -m moe_ub_cosim scaleout rdma-build \
  --output /path/to/rdma-build \
  --cmake /path/to/cmake --ninja /path/to/ninja \
  --protobuf-prefix /path/to/protobuf

python -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_rdma/small-clos-100g.json --output /path/to/topology

python -m moe_ub_cosim scaleout rdma-probe \
  --topology /path/to/topology --config examples/scaleout_rdma/rdma.json \
  --messages examples/scaleout_rdma/messages.json \
  --backend /executable/from/backend-json/probe --output /path/to/probe-result

python -m moe_ub_cosim scaleout rdma-run \
  --config examples/scaleout_rdma/service.json --topology /path/to/topology \
  --protocol examples/scaleout_rdma/rdma.json \
  --backend /executable/from/backend-json/service --protoc /path/to/protoc \
  --output /path/to/service-result

python -m moe_ub_cosim scaleout rdma-check --run /path/to/service-result
```

真实可执行路径从构建目录 `backend.json` 的 `probe`、`service` 读取，不猜测文件名。构建失败保留日志；修正源码或工具链配置后，可显式加 `--resume` 复用同一构建，旧日志保留在 `logs/resume-N`。资源预算失败不得靠自动重试或扩大预算绕过。

## 修改哪些参数

| 文件 | 参数与作用 |
|---|---|
| `service.json` 的 model | hidden/FFN 维度、专家数、Top-K、激活字节及连续层数；GPU 类型为描述标签 |
| workload | 每 rank batch（1—16）、Decode 轮数（2—4）、初始 context、路由 seed、热点比例（0 或 0.5） |
| parallel.groups | 明确的 EP rank 分组；全部 GPU 恰好出现一次，专家在每个组内连续放置 |
| compute | attention/pack/merge 的每 token 成本及每专家贡献成本，单位 ns；来源必须是 declared_reference |
| 拓扑配置 | 各层交换机数量、Pod、rail/plane、NIC 映射、分层带宽和传播时延；用 topology 重新生成 |
| rdma.json | DCQCN 模式1、payload 分包尺寸、缓冲、每速率 ECN 阈值和网络 seed |

所有 EP 组共用一个持续存在的网络实例。独立 rail 无交换路径时，必须把 EP 组限制在可达范围；前端在启动网络前拒绝不合法组合。双 plane 按消息身份固定哈希，消息不复制；每个 plane 的实际 payload 单独统计。

MoE 使用 per-expert-copy 语义：一次专家贡献为 hidden_size × activation_bytes；同 rank 项为本地依赖，不进网络。Dispatch 按源/目的 rank 聚合，Combine 反向返回相同贡献量；这是不等长 All-to-All-v 的直接成对消息实现，没有声称模拟 NCCL 或 DeepEP 的完整协议。两个 EP 组表示两个独立推理副本，不执行训练 DP AllReduce。

## 结果与核验

- `input.json`、`topology.json`、`rdma.json`：有效输入快照；`provenance.json` 保存来源和 hash。
- `graphs/*/wave.json`：实际组批请求对应的专家选择、消息及每 rank DAG；`.et` 为送入 ASTRA 的真实 Chakra 文件。
- `frontend.json`：实际组批、回传时间、下一轮启动、请求最终状态；`log/` 保存 ASTRA issue/callback 记录。
- `result.json`：消息的发送、连续接收就绪、ACK 时刻，实际首包路径、端口字节、ECN/PFC、队列事件、加载库及网络完成状态。
- `verification.json`：从逐 token 路由独立重算 payload，并核对所有 DAG 前驱、声明计算耗时、SEND/RECV 回调与下一轮调度。

`rdma-run` 成功前自动执行以上核验；也可独立使用 `rdma-check`。接收节点只登记等待，接收端连续接受完整消息后放行；发送节点等待完整 ACK，回调在原生 QP 清理返回后执行。绝不以原生的按包发送通知推进业务。下一轮由完成事件触发，不预设发送时间。

队列事件来自 BEgressQueue 入/出队之后，列为 `[time_ns,node,port,bytes]`，不含已进入链路串行化的数据。线上字节为原生模型中的 Packet 大小，未额外模拟 Ethernet preamble/FCS/IFG。Rx QP 的已接收状态保留至本次 Fabric 销毁，不能把它称为待完成接收。

## 限制与资源

小图最多 32 GPU、16 交换机、64 总节点、256 条物理边；服务累计最多 8192 条逻辑消息、256 次决策，队列观测最多20万事件。单例最多5 ms仿真时间、宿主60秒、RSS1 GiB、地址空间2 GiB；构建并发2、300秒、RSS2 GiB/地址空间4 GiB，产物上限512 MiB，卷预留8 GiB。达到限额保留失败，不自动扩限。

首版要求 ACK interval=1，保证任意短消息获得确认；锁定原生接收器的延迟 ACK 尚未验证。协议探针已复现中间包丢失由 NACK 恢复、尾包丢失不能自动恢复：缺少后续包触发 NACK，运行按预期失败。本轮服务研究无故障的网络拥塞，不把这条路径当作通用故障恢复实现。ECN、PFC 是否触发看真实指标，不能仅依据配置名称。

协议微实验可增加 `drop_once: {"message_id":0,"dst_gpu":4,"sequence":1000}`，在指定目的端仅丢弃该消息对应字节序号的一个分包。新增`faults`支持数据/ACK/NACK、指定接收节点/端口、时间窗口和共享丢弃次数；探针与服务均可运行故障实验，并用`rdma-diagnose`读取失败产物，操作及边界见[丢包小例](../scaleout_rdma_faults/README.md)。主业务基线仍是无故障场景；支持故障注入不表示支持通用恢复。中间包和尾包恢复须分别验证，不能由前者推断后者。ECN 输入单位为字节，适配层设置原生 MMU 的准确字节字段，并将生效阈值写入端口结果。

A100 等 GPU 标签不提供算子性能校准；attention 成本是每 token 声明值，不随 context 自动外推。KV 是有限片段的 MHA 尺寸与容量声明，权重按三矩阵 gated FFN 估算，只覆盖片段；没有声称完成 A100 80GB 上整模型容量验证。无 Prefill、KV 迁移或训练反向，不报告完整模型 TTFT/TPOT 或生产 p99。

独立构建固定使用 ASTRA 所带 ns-3 3.42。构建目录的 serial overlay 给 Qbb 的 MPI 引用增加条件保护、移除串行构建不需要的 MPI remote 源文件；原始依赖源码和 gitlink 均不变，overlay hash 单独记录。RDMA、队列、交换机和拥塞控制仍来自锁定源码。可执行程序只链接这一套 ns-3，不链接 UB。
