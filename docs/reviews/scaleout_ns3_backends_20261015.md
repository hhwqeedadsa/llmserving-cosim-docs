# Scale-Out：ns-3、阿里库与 ASTRA 接入的源码核查

2026-10-15。结论适用于下列固定提交，不能代替上游最新版本或实机校准。本文区分已有代码、源码风险、运行验证和后续设计。

## 1. 结论与路线修正

**ASTRA-sim 已经可以接 ns-3。当前仓库锁定的 LLMServingSim/ASTRA 中就有这条路径，不需要从零重写计算图执行器。** 它的网络库是 HPCC 项目派生的 RDMA 数据中心模型，不是标准 ns-3 的普通 TCP/UDP 链路示例。

**Scale-Out 应选独立的非 UB 后端。** 先用现有 ASTRA/ns-3 路径建立固定图基线，再把本仓库的在线调度控制与诊断接入。阿里库提供另一套成熟的 RDMA、交换机和 NVSwitch 模型，值得作对照及后续候选，但其 SimAI 前端、ASTRA 分支、时间单位和完成回调不能直接复制到当前 ASTRA。

**服务器不是天然的一条共享上联。** 8 GPU 服务器可能有 8 NIC，各自通过不同 rail（轨道，即按 GPU/NIC 位置划分的交换连接）外发。也可能多个 GPU 共用 NIC、PCIe 根或某个交换出口。必须显式描述 GPU—NIC—交换机映射，才能判断共享在哪里。此前的单服务器汇聚节点、单 spine UB 树只是一种人为限定的容量实验，不能代表所有 AIDC 网络。

本仓库现有 UB 服务继续作为独立基线保存；本次不再沿用 UB 传输做 Scale-Out。**目前尚未实现可由 `moe-ub run` 选择的非 UB 在线服务后端。** 源码研究、时间单位探针与 UB 功能验证都不能写成 RoCE 联合服务已经跑通。

## 2. 研究对象和证据

| 对象 | 核查版本 | 用途 |
|---|---|---|
| 本仓库 | 原始基线 `dd7d8e826d2e6cba37bd3f511cc17002b900b794` | 在线调度、模型图、UB 接口、诊断 |
| LLMServingSim | `dfd0d0594e8f19bd783ab6851af47c6cf1380b30` | 固定的服务与 ASTRA 依赖 |
| ASTRA | `0210e24bf76f49b4111a73d1e419f6fee4f3f0b6` | 原生 `AstraNetworkAPI`、ns-3 前端和动态图入口 |
| ASTRA 的 ns-3 | `3964b1d41cb5f7b51004c316b0eae1a3713c2498`，`VERSION=3.42` | 已锁定的 RDMA 网络后端 |
| SimAI | `a57d765c5478f319c5f5ab6fdc53f9cc7f461743` | 阿里 ASTRA 前端、通信分解与拓扑生成器 |
| ns-3-alibabacloud | `3e0c7c1bfbbe9f77890ddcf5e5b9c79fc6dd7437`，`simulation/VERSION=3.36.1` | SimAI gitlink 指定的网络库，已下载并阅读 |

阿里库位于本仓库忽略的 `data/research/ns-3-alibabacloud-3e0c7c1/`；没有初始化或修改用户原 SimAI 的子模块，没有更新现有锁文件或 gitlink。来源和关键文件 SHA256 保存在[来源清单](../../experiments/scaleout_backend_review/sources.json)。可重新取得的公共来源：

- [ASTRA/LLMServingSim 的 ns-3 固定提交](https://github.com/casys-kaist/astra-network-ns3/tree/3964b1d41cb5f7b51004c316b0eae1a3713c2498)
- [阿里 ns-3 固定提交](https://github.com/aliyun/ns-3-alibabacloud/tree/3e0c7c1bfbbe9f77890ddcf5e5b9c79fc6dd7437)
- [SimAI 固定提交](https://github.com/aliyun/SimAI/tree/a57d765c5478f319c5f5ab6fdc53f9cc7f461743)

阿里 README 另提到 `dev/qp` 的持久 QP、perIP/perQP CC 和调度改进；**本轮没有检出该分支，不把这些能力算到被核查的 master 提交。** `VERSION` 是派生树的版本标记，不能据此认定它等同于未修改的官方同版本 ns-3。

## 3. 标准 ns-3 与两个派生库的分工

标准 ns-3 提供离散事件时间、节点、设备、链路、队列、协议栈和 TCP 等组件。**“使用 ns-3”不等于已经具有完整 RDMA NIC、PFC、DCQCN 或 GPU 通信库模型。** 后者来自额外模块、应用和参数配置。

以下 ✓ 表示本轮被审阅源码中存在相应实现，✗ 表示该列所限定的基础组合不自带；不表示标准 ns-3 无法扩展，也不表示该机制已经在当前业务负载中被触发或校准。

| 能力 | 标准 ns-3 基础网络组件 | 已锁定 ASTRA/ns-3 | 已核查 Alibaba 网络库 + SimAI 前端 | 当前 UB 联合服务 |
|---|:---:|:---:|:---:|:---:|
| 离散事件、链路序列化、队列 | ✓ | ✓ | ✓ | ✓ |
| 此 HPCC 派生的 RDMA QP/ACK/NACK 栈 | ✗ | ✓ | ✓ | ✗ |
| Ethernet PFC 风格 PAUSE/RESUME | ✗ | ✓ | ✓ | ✗ |
| ECN、DCQCN/HPCC/TIMELY 等 RDMA CC | ✗ | ✓ | ✓ | ✗ |
| 五元组哈希 ECMP 的 RDMA 交换转发 | ✗ | ✓ | ✓ | ✗ |
| 现成 ASTRA 网络接口与完成回调 | ✗ | ✓ | ✓ | ✓ |
| NVSwitch 节点与 NVLS 特定模型 | ✗ | ✗ | ✓ | ✗ |
| SimAI 中 rail/双 ToR 拓扑模板 | ✗ | ✗ | ✓ | ✗ |
| UB 原生 STORE/LOAD 与信用流控 | ✗ | ✗ | ✗ | ✓ |

不对“全 SimAI 有没有我们的某项服务功能”作负面推断：其本机文档已有推理、多请求、KV 与 P/D。上表比较明确的网络实现，不拿未审阅的平台功能制造独有优势。

### 3.1 RDMA 栈如何产生排队与反馈

两个 RDMA 派生库的主要路径相似：

```text
逻辑通信 / 消息字节
  → RdmaClient / RdmaDriver 创建 QP
  → RdmaHw 分包、窗口和发送速率控制
  → QbbNetDevice 的 NIC 发送队列 / QP 调度
  → QbbChannel 链路序列化与传播
  → SwitchNode 查表、ECMP、SwitchMmu 缓冲准入
  → 出口队列、ECN 标记、必要时向上游发 PFC
  → 接收 RdmaHw 按序累计、产生 ACK/NACK / 拥塞反馈
  → 发送端更新已确认字节、窗口、CC 速率
  → 完成回调推进上层
```

代码入口分别为各库的 `src/point-to-point/model/rdma-hw.cc`、`rdma-driver.cc`、`rdma-queue-pair.cc`、`qbb-net-device.cc`、`qbb-channel.cc`、`switch-node.cc`、`switch-mmu.cc`；Alibaba 前面多一层 `simulation/`。

- `RdmaHw::GetNicIdxOfQp()` 从路由表选择 NIC；同一个物理 NIC 上多个 QP 进入共同的设备发送调度，而不是每流独享链路速率。
- `SwitchNode::GetOutDev()` 对地址/端口信息哈希，从等价下一跳中选出口。增加 spine 只提供路径选择，不能保证所有流均匀分摊；改变 QP 拆分或源端口也可能改变路径。
- `SwitchMmu` 跟踪入口、出口、共享缓冲和 headroom。PFC 暂停上游相应优先级，ECN 是给发送端降速的反馈，两者不是同一个开关或同一种排队时间。
- 核查到 CC 分支编号 `1=DCQCN/MLX`、`3=HPCC`、`7=TIMELY`、`8=DCTCP 风格`、`10=HPCC-PINT`。应检查选择分支、所需反馈和有效参数，不能根据配置名称推定运行了算法。
- 这些是 RDMA 研究模型。出现 ACK/NACK 分支不代表任意丢包、丢 ACK、超时恢复都覆盖真实 NIC；错误实验前需要专门验证恢复能力。

### 3.2 阿里库增加了什么

`NVSwitchNode` 为类型 2，Ethernet `SwitchNode` 为类型 1，普通端点为类型 0。SimAI `common.h::SetupNetwork()` 读取首行：

```text
node_num gpus_per_server nvswitch_num switch_num link_num gpu_type
```

随后读取 NVSwitch ID、普通交换机 ID，再读每条边：

```text
src dst data_rate link_delay error_rate
```

速率和传播时延逐边配置；同服务器与跨服务器可以经过不同设备、采用不同速率。SimAI 的 `CalculateRoute()` 区分 NVSwitch 路径，`RdmaHw::GetNicIdxOfQp()` 使用服务器归属和 NVSwitch 路由表选择机内或机外路径。

**但不能直接照搬其编号推断。** 此实现用 `src / gpus_per_server` 判断服务器，默认 GPU 编号按服务器连续排列。我们允许任意 rank 放置，必须先作显式 rank→GPU→server 映射，再传给后端；不能拿逻辑专家 rank 直接做整除。

SimAI `inputs/topo/gen_Topo_Template.py` 包含 single/dual ToR、单/双平面以及 rail 与非 rail 模板。rail 例子按服务器中的 GPU 位置接入不同接入交换机；并不是所有 GPU 先走同一个服务器汇聚交换机。模板名为 Spectrum-X/AlibabaHPN/DCN+，**名称本身不能证明已模拟相应商用设备的全部自适应路由、端到端协议或固件行为。**

MTP 是宿主多线程执行机制，MPI 是分布式仿真机制；均不等于被仿真的 GPU 并行度、NIC 条带化或网络多平面。第一版应关闭这两项，先核对单进程事件与因果关系。

## 4. ASTRA 到 ns-3 的接口已经怎样工作

当前固定 ASTRA 的入口在：

- [`network_frontend/ns3/AstraSimNetwork.cc`](../../third_party/LLMServingSim/astra-sim/astra-sim/network_frontend/ns3/AstraSimNetwork.cc)
- [`network_frontend/ns3/entry.h`](../../third_party/LLMServingSim/astra-sim/astra-sim/network_frontend/ns3/entry.h)
- [`build/astra_ns3/build.sh`](../../third_party/LLMServingSim/astra-sim/build/astra_ns3/build.sh)

### 4.1 图与真实网络的关系

1. 工作负载提供计算、通信和依赖；ASTRA 决定哪个节点现在能发起。Chakra 图不是预先指定流完成时刻的流量表。
2. `sim_schedule()` 把计算事件登记到同一个 `Simulator`；`sim_get_time()` 直接读取 `Simulator::Now().GetNanoSeconds()`。
3. `sim_send()` 调用 `send_flow()`，创建 RDMA QP，传入源、目的、字节数、tag 和完成处理函数。**只由发送路径发起传输。**
4. `sim_recv()` 登记等待；用 `(tag, src, dst)` 匹配。先到数据存入 standby，后登记接收时消费，不再重复发包。
5. RDMA 完成通知经保存的 handler 返回 ASTRA，释放依赖和资源。

这是已经存在的接口路线，最有价值的工作是把它与当前推理服务协议适配，而不是再次创造一套静态 trace 重放。

### 4.2 两个重要边界

**物理网络与集合通信逻辑拓扑分别配置。** `TOPOLOGY_FILE` 描述真实节点、交换机、链路；`logical-topology-configuration` 描述所用计算成员及通信维度。8×8 的逻辑维度不自动生成一个两级 Clos，物理 128 端点也不要求本轮使用全部端点。GPU/NIC 放置必须另行对应。

**现有动态入口不能直接替代当前服务控制。** 锁定 fork 的 `main()` 使用 `PreRun()` / `RunOneEvent()`，发现某 rank 的 workload 完成后读取 stdin 的文件名、`pass` 或 `exit`。这证明它已有持续网络上的动态图装载，但尚不是本仓库 `DECIDE → COMMIT → WAVE_DONE` 的组级原子提交协议。需要补组内校验、组间独立推进、外部到达事件、busy 状态、跨图身份和网络排空，不能在每个 batch 结束时销毁网络。

### 4.3 完成含义不能直接继承

原 ASTRA `entry.h::qp_finish()` 在同一函数中先通知 sender，再通知 receiver；网络库 `RdmaHw::ReceiveAck()` 在 `snd_una >= m_size` 时触发它。因此两个上层完成都发生在**发送方收到完整 ACK 之后**。

对当前研究，应至少区分三个事件：

| 事件 | 具体含义 | 能释放什么 |
|---|---|---|
| `tx_done` | 规定数据已从本地 NIC 发出 | 明确声明可在此时复用的本地发送引擎；不自动释放重传所需数据 |
| `data_ready` | 接收端已按协议得到完整、可交付的逻辑消息 | 相应消费者依赖；不代表其已完成 HBM 写入或 GPU 消费，后两者另建模 |
| `transaction_done` | 发送端获得必要确认、协议及事务资源可回收 | SEND/事务额度、相关缓冲和退出检查 |

当前 UB 已区分后两项，新 RDMA 后端也应保留这种区分，但必须从 RDMA 的实际接收状态与确认事件取得，不能用一个固定 RTT 差值“校正”。先到接收后登记等待时，在登记的当前时刻异步完成，不回溯时钟。传输主键至少包含运行/组/图/消息/分块身份，避免后续 wave 的 tag 复用与迟到 ACK 混淆。

## 5. 本次查到的具体风险

### A. ASTRA 默认 `CC_MODE 12` 没有对应已核查发送控制分支

其 `scratch/config/config.txt` 写 `CC_MODE 12`；`common.h` 将值传入 `RdmaHw` 与 `SwitchNode`。但上述 RdmaHw 的算法分支只有 1/3/7/8/10，没有找到 12。**不能把原样运行示例称为 DCQCN 实验。** PFC、窗口和 ECN 等其他约束仍可能存在，不应扩大结论为“网络完全没有流控”。

修改方向：新输入对 CC 使用显式名字到已核查编号的白名单，拒绝未知模式；每次保存有效配置。用拥塞微实验确认实际标记、速率下降和恢复，而非只检查成功退出。还需检查每种实际链路速率都具有合法 `kmin/kmax/pmax`，防止新增 800G 链路却缺少对应阈值映射。

### B. SimAI `send_lat` 存在 1000 倍时间单位风险

SimAI `entry.h` 表中 Ring/LL 的同机启动延迟为 7200ns；代码又执行 `send_lat *= 1000`，随后 `appCon.Start(Time(send_lat))`，注释称内部时间是 ps。

但是所锁定 Alibaba `core/model/time.cc::SetDefaultNsResolution()` 设为 NS；本轮审阅的 SimAI ASTRA 和所选 MockNccl v2.30 源中没有发现把分辨率改成 PS 的调用。`Time(integer)` 解释为当前时间刻度，而不是根据上一行注释解释。**在这组默认分辨率下，7200ns 的意图会变成 7200000ns，即 7.2ms。** 应用启动调用相对/绝对时刻及动态注入行为还需与调度协议一起验证。

为此新增[独立时间单位探针](../../experiments/scaleout_backend_review/README.md)，直接编译阿里库 core 并调度两个事件。它不链接 UB 或 ASTRA，实际确认 `Time(7200*1000)` 的事件在 7200000ns 执行，`NanoSeconds(7200)` 在 7200ns 执行。**这个探针不能证明整套 SimAI 已成功构建或其端到端误差就是 1000 倍。** 接入代码应使用显式 `NanoSeconds()` 并对起始时间作断言，不能直接抄该乘法。

### C. 阿里 `SendComplete` 使用包长猜测尾包

`QbbNetDevice::TransmitStart()` 和 NVSwitch 对应路径在 `60 < packet_size < 9000` 时安排 `SendCallback()`；`RdmaHw::SendPacketComplete()` 找到 QP 就调 `SendComplete()`，没有按逻辑总字节核验该包是否真是最后一个数据包。SimAI 前端则将该回调作为一个 QP 发送完毕参与计数。

因此该路径受 MTU、报文头、消息余数和控制包格式影响。本轮进一步构建了阿里 RDMA 所需模块，实际执行双端点 100Gbps、1µs 传播、DCQCN 模式 1 的直连小例。8 个 QP 均恰好一次 ACK 完成，但发送回调出现以下结果：

| payload 大小 | 消息字节 | SendComplete 次数 | QpComplete 次数 |
|---:|---:|---:|---:|
| 1000 | 999 | 1 | 1 |
| 1000 | 1000 | 1 | 1 |
| 1000 | 1001 | 1 | 1 |
| 1000 | 3000 | **3** | 1 |
| 9000 | 8999 / 9000 / 9001 / 27000 | **每例 0** | 每例 1 |

**重复与缺失发送完成已由真实 RDMA 运行证实**，不是只看函数名作出的猜测。payload=1000、消息=1001 的单次发送回调对应首个大包，不足以保证逻辑消息已经全部发出。完整记录见[探针结果](../../experiments/scaleout_backend_review/results.json)。该小例没有交换机、拥塞、SimAI ASTRA 或服务调度，不能据此推断特定业务已经卡死，也不能证明 DCQCN 的拥塞响应已校准。

修改方向：根据协议类型、QP 身份与序列区间判断最后一个有效数据包的实际序列化完成；用一次性状态防止重传或重复事件导致重复完成。测试覆盖 payload−1、payload、payload+1、多倍 payload，1000/9000 两种 payload 以及双 QP 拆分。不要把这个 `SendComplete` 当作接收数据就绪或 ACK 完成。

### D. 字节宽度、tag 与长期运行资源需要补防护

ASTRA `sim_send()` 接收 `uint64_t message_size`，但 `send_flow()` 的参数是 `int maxPacketCount`，`MsgEvent` 的剩余字节也是 int；一次大消息可能窄化。QP 源端口是 16 位，持续波次中单调分配还可能复用/回绕。新后端需明确单消息上限、拆分后的聚合和端口复用规则，不能将模型总通信量直接塞进窄接口。

阿里 master 按每次消息创建 QP 的实现也不能宣称具备 `dev/qp` 提出的持久 QP 行为。创建销毁成本、在途 QP 数和发送调度随 batch 的变化，需要单独审计。

### E. GPU 名称不是全部硬件参数

SimAI `GPUType` 枚举包括 A100/A800/H100/H800 等；此 ns-3 `SetupNetwork()` 的字符串解析实际列出 A100/A800/H100/H800，否则为 NONE。枚举多一个名称不等于该入口已支持；拓扑内写 H100 也不能自动生成对应算子 profile、PCIe/HBM 或 NIC DMA 行为。

网络侧至少同时明确 GPU/server/NIC 数、GPU—NIC 亲和、每端口方向速率、ToR/spine 容量与时延、超卖比和路由。计算成本另列设备、精度、算子形状及来源。已有 A100 小形状表不能代表完整 R1 MLA；参考网络探针也不需要实际拥有 A100 才能执行。

## 6. 我们应采用的 Scale-Out 体系结构

### 6.1 先限定被服务的问题

第一阶段研究：**MoE Decode 的跨服务器 Dispatch/Combine，在不同专家放置、批次与网络共享方式下，哪里形成排队，怎样推迟 Token/请求完成。** 从 ready-KV、TP=PP=1、小 EP 组开始。暂不声称完整 prefill、KV 迁移、多协议混合、任意模型和任意 GPU 均已校准。

前端提供请求与模型生成的动态依赖图；网络后端提供实际消息和包完成事件。业务输出是每请求延迟/TPOT 与阶段完成，网络解释是 NIC、端口、队列、路径、ECN/PFC/CC 和等待前驱。不把单个微流 FCT 的变化直接当作 TTFT/TPOT 的同等变化。

### 6.2 物理与逻辑映射分开

需要的配置结构如下，是设计要求，**尚非已实现 schema**：

| 对象 | 应显式声明的字段 | 解决的歧义 |
|---|---|---|
| 模型与请求 | 模型维度/精度、EP、序列数、输入/输出长度、到达时刻、Decode 位置 | 不能只用 batch 一个数代表负载 |
| 计算与容量 | GPU 类型、profile 身份/形状、权重/KV/工作区 | 固定计算释放节奏，避免网络实验同时改计算 |
| 放置 | rank→GPU→server；专家→rank | 不再用 rank 整除推断物理位置 |
| NIC | NIC 数、GPU 可达 NIC、共享关系、端口速率、队列与发送槽 | 区分每 GPU 独占 NIC、多 GPU 共用 NIC、多 rail |
| Fabric | NIC→ToR、ToR→spine、plane/rail、逐边速率与时延 | 同架和跨架不是只多一个常数延迟 |
| 交换机与传输 | 缓冲、headroom、PFC、ECN 曲线、CC、payload、ACK/窗口 | 保证不同速率有匹配阈值和有效分支 |
| 通信生成 | all-to-all 字节矩阵、分块、QP、依赖、库/算法版本 | 算法改变可能同时改变线上字节、并发与路径 |
| 观测与预算 | seed/run、端口/队列/事件范围、仿真与宿主预算 | 可复现、数据有界、观察不改变网络 |

可先实现两类以便区分共享机制：

1. **通用两级 Clos**：NIC→leaf/ToR→多个 spine→leaf→目标 NIC。检查同 ToR 与跨 ToR、上联超卖和 ECMP 碰撞。
2. **rail-optimized**：同服务器不同 GPU/NIC 位置连到不同 rail 的 leaf；再声明 rail 间可达性、跨 rail 中转与 spine 连接。不能只给链路增加 rail 名称却仍让全部 GPU 共享同一假想出口。

若多个 GPU 真共用同一 NIC，要把消息注入同一个 RDMA 端点/设备队列，并保留 rank 消费者身份；在每个 GPU 外面新增一个普通交换机不能代替共享 NIC、DMA 和 PCIe。首版可只支持每 GPU 一 NIC，并明确拒绝共享 NIC 模式，直至资源适配验证完成。

### 6.3 一个可执行程序、一套 ns-3 时间

新 Scale-Out 程序链接**一套非 UB ns-3**和 ASTRA。复用 Python 请求/KV/组批与 C++ 组级控制的设计；将必要的通用代码从 UB 配置、计划校验和观测依赖中分离。每次运行选择一个后端和一套输入能力，不将 UB 参数传给 RDMA 程序。

旧 UB 可执行程序保留用于另一组独立基线实验；**不能各跑一套 UB 和 RDMA，再把二者耗时相加，声称完成了同一服务的混合网络仿真。** 如未来确需单次执行内同时模拟两种协议，需要明确的保守/乐观时间同步和跨模拟器资源边界，或将模型移植到同一 ns-3 版本。这是独立的大工程，不是两个可执行程序就自然具备。

第一版非 UB 运行的机内通信可以采用同一事件队列上的明确、可校准成本，或选定库中的 NVSwitch 模型；两者分别标注精度。不能一边采用机内理想成本，一边宣称已验证机内逐包拥塞。

### 6.4 重新生成通信，不能翻译 UB 包记录

可复用请求、batch、逐层路由、Token×专家分配和计算依赖；**不可复用已生成的 UB STORE/LOAD 图当作 RDMA 消息图**。UB LOAD 的请求/数据方向、就绪通知、线程、信用和资源回收有特定含义；RoCE dispatch/combine 应根据所选通信库形成 SEND/RECV、消息字节和依赖，再提交 ASTRA。

先做语义清楚的 all-to-all reference；若接 DeepEP/NCCL，必须注明版本、分层/聚合规则、分块/通道、协议和额外字节。参考 all-to-all 不能标成 DeepEP 真正实现。流的源/目的、字节与实际就绪依赖是注入接口，固定“发包时刻 trace”只适合独立网络重放，不适合作为在线服务的最终性能来源。

## 7. 两个候选后端怎样选择

| 方案 | 直接可复用内容 | 主要代价 | 当前建议 |
|---|---|---|---|
| 锁定 ASTRA 的 ns-3 3.42 派生库 | 同一 ASTRA API、Chakra、现有动态加载、RDMA/PFC/CC | CC 默认、接收完成、窄字节接口、组级服务协议与诊断 | **先作接入基线**，避免同时更换 ASTRA 和网络库 |
| 阿里 ns-3 3.36.1 派生库 | RDMA、NVSwitch/NVLS、MTP，以及 SimAI 的 rail 模板参考 | `ncclFlowTag`/SimCCL/不同 ASTRA 分支、单位和发送完成风险、编译集成 | 先作固定版本网络对照与修复验证；NVSwitch/rail 需求明确后评估接入 |
| 标准 ns-3 新建 RDMA 栈 | 网络通用组件、较干净的依赖 | 要重新实现和验证 RDMA/NIC/CC，不能解决当前最快接入需求 | 目前不选 |

选择第一项不是说它精度高于第二项，而是接口已经处于同一锁定 ASTRA 体系，变更因素少。所有性能比较均需保持业务消息、拓扑和有效参数一致；不能只保证模型名称相同。

## 8. 分步目标、验收与研究产出

| 步骤 | 目标 | 必须得到的结果 |
|---|---|---|
| 1：源码审阅与范围修正 | 明确库的关系、协议、拓扑与完成含义 | 本报告、固定来源清单、已验证事实与待验证风险分开 |
| 2：独立非 UB 网络微基准 | 先验证选定库的 NIC、交换机、RDMA 回调 | 单流、多对一、同 leaf/跨 leaf、整 MTU 边界；一次逻辑消息一次最终回调；网络排空 |
| 3：固定图 ASTRA 基线 | 验证网络影响依赖执行 | 消费计算开始随实际 `data_ready` 变化；计算持续时间固定；不重复发包/计时 |
| 4：在线服务接入 | 将网络变化传到下一轮组批和请求 | 双独立组共享持续网络；迟到请求、空 rank、先到接收、跨 wave tag、真实完成驱动进度 |
| 5：网络机制对照 | 找出瓶颈而不是制造减速 | 固定字节矩阵；分别改变 NIC 数/亲和、上联容量、rail/Clos、ECMP、PFC/CC；解释无收益与反例 |
| 6：硬件校准与贡献验证 | 将模型内机制对应到实机 | 匹配 NIC/GPU/交换机/库与消息；小/大消息、汇聚和分位数误差，随后验证优化 |

近期产物是可信的非 UB 接入基线、配置/来源审计、完成语义测试与网络诊断数据。下一阶段论文需要一个可证伪的问题，例如“专家负载看似均衡时，GPU—NIC/rail 的共享关系是否造成服务长尾，在哪些负载下映射或分层传输有效”。这仍是候选假设，不能把提高带宽后变快当作机制创新。

长期目标是：给定服务、专家放置与物理网络，解释哪一类网络等待影响了多少服务长尾，并验证一种有适用边界的优化。工具支持跨设备 profile、拓扑和通信参数，是验证能力；论文贡献取决于新机制、真实问题、强基线、校准与反例。投稿方向按成熟度选择测量/工具 workshop、IMC/CoNEXT/ICDCS 或机制充分后的 INFOCOM/SIGCOMM/NSDI，本次没有核实具体截稿日。

## 9. 本次已经做了什么、还没有做什么

- 已审阅两个 RDMA 派生库及其 ASTRA/SimAI 前端，并取得阿里锁定源码；没有更换主仓依赖。
- 已保留此前 UB 功能验证：26 项相关单元检查通过；两个联合执行条件完成。服务器上联 100G/10G 时结束于 117098/146942ns；仅支持该 synthetic UB 功能结论。
- 新增非 UB 时间单位探针和 8 条 RDMA 直连消息边界实验，实际构建/运行状态及数字在[探针记录](../../experiments/scaleout_backend_review/README.md)，完整日志在 `runs/scaleout_ns3_review_20261015/`。统一脚本 `run_probes.py` 已实际执行，验证来源并在相同预算内完成配置、编译与两个探针。
- 已构建阿里 RDMA 相关模块并跑通直连消息，**尚未完成交换机 fabric/PFC/ECN 拥塞微基准、完整 SimAI、ASTRA 原生 ns-3 全栈重跑或新在线服务后端**。不把直连 ACK 成功写成发送完成语义正确，或 RoCE 联合服务完成。

下一步有明确顺序：先通过选定非 UB 库的网络微基准和完成语义核验，再接固定图和在线服务；不继续扩展 UB 拓扑来替代这一步。
