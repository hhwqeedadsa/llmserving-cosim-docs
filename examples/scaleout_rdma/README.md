# 非 UB Scale-Out 小规模运行

本入口执行 ready-KV Decode 的有限 MoE 片段：锁定 LLMServingSim 的实际 Scheduler 组批，生成 Chakra 依赖图，由 ASTRA 调度，单一原生 ns-3 RDMA 网络执行数据包，再把完成事件返回调度器。默认 8 GPU 逻辑端点、2 层、4 轮，每 rank 4 个请求。它使用声明的计算成本和合成路由，不能当作完整模型或实机 A100 性能。

需要用逐请求来源替换合成选择、改变专家放置时，使用[路由回放小例](../scaleout_route_replay/README.md)的可选`route_replay`输入。原件经hash和全形状校验，运行保存快照；默认合成配置保持原行为。

## 构建与运行

`rdma-build`从`deps.lock.json`读取原生ns-3的`checkout_commit`，并核对ASTRA父仓库gitlink、实际HEAD和已跟踪源码的干净状态；不会跟随工作区HEAD接受未知版本。`--resume`也要求旧构建收据与当前固定版本一致。构建收据的版本和实际链接库来自本次构建；probe/事务/会话输入收据的`native_commit_role`则明确其版本字段只是源码预期，任意传入二进制仍需结合程序及实际加载库的hash核对。

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

构建保留`-Wall -Wpedantic`，绕开固定依赖把告警与`-O0`捆绑的配置分支；配置后、编译前检查`cmake/compile_commands.json`中每个单元的有效优化必须是`-O3`，并覆盖原生网络和会话关键源码。摘要保存到`compiler-options.json`、`provenance.json`和`backend.json`。这证明生成的编译命令，不代表硬件校准或加速收益；旧构建仅有Release标签不足以证明启用了优化。

编译默认上限仍为300秒。可在一次新构建开始前显式传入`--compile-wall-seconds 900`等60—1800秒预算；不会自动重试或加限。配置仍为60秒，两并发及内存/磁盘限制保持。旧构建要重新配置并编译后才会得到这份核查摘要。

## 修改哪些参数

修改拓扑带宽或传播延迟后，可先运行不需要后端程序的检查：

```bash
python -m moe_ub_cosim scaleout rdma-preflight \
  --topology /path/to/topology --protocol examples/scaleout_rdma/rdma.json \
  --output /path/to/new-preflight.json
```

它按固定原生PFC模型计算每台交换机各端口的headroom（暂停反馈到达前的缓冲预留）和每端口4096B保留量，要求合计严格小于配置缓冲。报告列出端口贡献和剩余共享容量；容量不满足时仍保存报告并返回退出码1。各Python运行入口也会在启动后端前执行此检查；不会自动增加缓冲或改延迟。该规则对应当前模型，不能当作实际硬件缓冲设计或无损保证。

对事务小例可加`--transactions examples/scaleout_rdma_transactions/bounded-buffers.json`检查整份计划的窗口、地址和依赖。跨连接同址写仍需显式声明旧写`applied`/ACK/完成等有效顺序；控制器已经收到回调也不能代替计划里的依赖。在线追加的未来操作须在提交时继续检查。预检不运行网络，不预测时延或保证任务完成。

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

## 可选宿主阶段计时

在上面的 `rdma-run` 命令追加 `--host-timing`，会额外保存 `worker_host.jsonl` 和 `native_host.jsonl`。默认关闭，不生成这两个文件，不修改网络结果格式。使用配套新构建的 service；旧二进制不接受这个可选参数。

```bash
python -m moe_ub_cosim scaleout host-timing-check \
  --run /path/to/service-result --output /path/to/new-host-summary.json
```

该命令也接受失败目录中已经落盘的记录前缀。`passed=true` 只表示记录解析通过，`all_traces_complete` 单独表示两个记录器是否正常结束；服务成功仍由 `rdma-check` 核验。读取不会补造未结束阶段的耗时。

宿主阶段用于区分初始化、前端组图/导出、阻塞交互、图载入/激活、事件执行及报告开销。它使用 Linux 单调时钟和本进程 CPU，单位 ns，与仿真的 GPU/网络纳秒不同。后端 `simulator_run` 包含 `control_exchange`、`commit_parse`、`graph_load`、`graph_activate` 等子阶段；`control_exchange` 又包含 `frontend_wait`。汇总提供含子阶段的 inclusive 和扣除直接子阶段的 exclusive，不能相加嵌套阶段，也不能把 Python 与 native 重叠的墙钟时间相加。`simulator_run` 的 exclusive 仍包含 ns-3、ASTRA、未细分回调和观测开销，不能称为纯包 CPU。CPU 包含该进程所有线程，可大于墙钟时间，不含子进程。

原始记录逐行刷新，有每文件16MiB读取和65,536条记录上限；额外I/O会影响宿主耗时。记录格式、截断与消息计数口径见[输出数据](../../docs/record_storage.md#原生rdma宿主阶段记录)。

## 限制与资源

小图最多 32 GPU、16 交换机、64 总节点、256 条物理边；服务累计最多 8192 条逻辑消息、256 次决策，队列观测最多20万事件。单例最多5 ms仿真时间、宿主60秒、RSS1 GiB、地址空间2 GiB；构建并发2、300秒、RSS2 GiB/地址空间4 GiB，产物上限512 MiB，卷预留8 GiB。达到限额保留失败，不自动扩限。

首版要求 ACK interval=1，保证任意短消息获得确认；锁定原生接收器的延迟 ACK 尚未验证。协议探针已复现中间包丢失由 NACK 恢复、尾包丢失不能自动恢复：缺少后续包触发 NACK，运行按预期失败。默认原生模式保留此行为；可显式开启[项目恢复扩展](../scaleout_rdma_recovery/README.md)，进行有限重试与真实重复确认。ECN、PFC 是否触发看真实指标，不能仅依据配置名称。

协议微实验可增加 `drop_once: {"message_id":0,"dst_gpu":4,"sequence":1000}`，在指定目的端仅丢弃该消息对应字节序号的一个分包。新增`faults`支持数据/ACK/NACK、指定接收节点/端口、时间窗口和共享丢弃次数；探针与服务均可运行故障实验，并用`rdma-diagnose`读取失败产物，操作及边界见[丢包小例](../scaleout_rdma_faults/README.md)。主业务基线仍是无故障场景；故障注入与可选恢复是独立配置。中间包和尾包恢复须分别验证，不能由前者推断后者。ECN 输入单位为字节，适配层设置原生 MMU 的准确字节字段，并将生效阈值写入端口结果。

A100 等 GPU 标签不提供算子性能校准；attention 成本是每 token 声明值，不随 context 自动外推。KV 是有限片段的 MHA 尺寸与容量声明，权重按三矩阵 gated FFN 估算，只覆盖片段；没有声称完成 A100 80GB 上整模型容量验证。无 Prefill、KV 迁移或训练反向，不报告完整模型 TTFT/TPOT 或生产 p99。

独立构建固定使用 ASTRA 所带 ns-3 3.42。构建目录的 serial overlay 给 Qbb 的 MPI 引用增加条件保护、移除串行构建不需要的 MPI remote 源文件；原始依赖源码和 gitlink 均不变，overlay hash 单独记录。RDMA、队列、交换机和拥塞控制仍来自锁定源码。可执行程序只链接这一套 ns-3，不链接 UB。
