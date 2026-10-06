# 联合后端接口与执行规则

本目录将 ASTRA 的任务执行与 ns-3-UB 的事件、协议和网络状态连接起来，负责图装载、控制协议、收发匹配、完成回调及原始观测。整体过程见[系统架构](../docs/architecture.md)；普通运行与构建使用[用户指南](../docs/user_guide.md)中的 `moe-ub` 命令。本文件供修改后端或编写集成检查时查阅，不记录逐次开发结果。

## 构建与程序入口

构建由 ns-3-UB 的 CMake 工程进入，[inject.cmake](inject.cmake) 将本目录加入同一工程，不能直接以 `backend/` 作为 `cmake -S`。联合程序只链接这一套 ns-3-UB 库。适配器和 UB 使用 C++20，锁定 ASTRA 及日志桥接使用 C++17；日志桥接隔离上游日志头的编译差异。

`moe-ub build` 构建生产入口 `cosim-service`，实际二进制路径写入构建目录的 `backend.json`。其余目标保留为功能检查，不是完整模型用户入口。

| 目标 | 用途 |
|---|---|
| `cosim-service` | 接收 Python 服务前端或图重放驱动的提交，在持续存在的网络中执行多轮、多组任务 |
| `cosim-compute`、`cosim-write` | 单个计算与单份写事务的最小检查 |
| `cosim-layer`、`cosim-sequence` | 给定层图及固定两轮图的执行检查 |
| `cosim-plan-load-probe` | 使用同一计划加载器，测量完整图的解析与校验；不创建网络、不推进仿真 |
| `cosim-service-resource-probe` | 使用服务准备流程进行资源测量；在业务激活前停止，不生成正常服务完成证明 |

开发者直接调用 `cosim-service` 时，参数顺序如下。它要求有程序通过标准输入回复协议消息，不能像普通离线命令一样单独启动后等待退出。

```text
cosim-service RUN_ID SHARED_DIR SYSTEM.json UB_CASE OBSERVATION.json|- NEW_RUN_DIR MAX_NS
```

`SHARED_DIR` 保存 `deployment.json`、可选存储策略及将要提交的图；`UB_CASE` 是网络目录；`MAX_NS` 是正整数纳秒上限。输出目录必须尚不存在。ASTRA 配置要求 `roofline-enabled=0`、`replay-only=0`、`trace-enabled=1`，使用实际任务执行与日志。程序还提供两个只读入口：

```text
cosim-service --backend-catalog
cosim-service --check-backend BACKEND.json
```

前者导出当前二进制注册的 UB 属性、全局参数与构建常量，后者使用原生类型检查器校验并规范化选择。二者成功只证明配置可以解析，不代表网络执行通过。

## 图装载与部署校验

[LoadedGraph 与计划加载器](graph_sequence.cc)读取 `plan.json` 及同目录的 `layer.<rank>.et`。不同输入版本分别校验单层、KV 交接、多层或完整模型；完整模型的稠密层、注意力、共享专家等计算也保留为执行节点。前端的完整模型入口及其 schema 见用户指南，消息复制和计算规则见[模型说明](../docs/model_validation.md)。旧测试输入仍受各自 schema 的范围约束，不自动获得新版能力。

装载时必须核对以下内容：

- 图、轮次、操作和传输的运行内唯一性，rank 集合、每轮批次预算、静态专家放置及 `rank_to_ub_node` 映射；映射目标必须是已有计算端点，组间也不能重叠。
- 每个请求的解码位置与上下文、逐专家输入、计算时间、发送/接收配对、消息与分块字节、局部依赖和完整图的一致性。
- 实际 Chakra 文件与计划逐项一致；资源限制、发送顺序和传输配置满足当前执行范围。
- 所有待装载 rank 已结束旧图，且没有旧图的活动执行对象；一组全部成员完成检查后，先装载全部图、登记传输，再按 rank 顺序在同一时刻激活。

`load_graph` 不发任务，`activate_graph` 才允许 ASTRA 派发。登记传输描述不等于 SEND 已提交，也不使 RECV 提前完成。某个成员校验失败时，整组提交失败，不先运行其余成员。初始化使用只有元数据的空图，在 0 ns 创建执行对象，不产生业务包或端侧操作。

同 rank 的前后层用本地依赖连接，跨 rank 等待由收发节点表达。无本地 Token 的成员仍执行实际收到的专家工作；完全无工作的成员用空图完成。不因多层合并额外增加全 rank 逐层屏障。

## 前后端控制协议

生产入口使用 `cosim/1`，每行一个 JSON 对象。后端 stdout 专用于协议，诊断输出到 stderr 或文件。后端发送一条 `DECIDE` 后等待对应的 `COMMIT`，期间不执行后续 ns-3 事件。

| 消息 | 字段与含义 |
|---|---|
| 公共字段 | `schema_version`、`type`、`run_id`、从 0 开始递增的 `decision_id`、绝对时间 `now_ns` |
| `DECIDE` | `completed` 给出已完成的图；`available_rank_ids` 给出可提交的完整组成员 |
| 完成项 | `kind` 为 `WAVE_DONE` 或 `KV_DONE`，另含 `graph_id`、`wave_id`、`finished_ns` |
| `COMMIT` | `submissions`、`next_wakeup_ns`、`input_exhausted`、`service_finished` |
| 提交项 | `graph_id`、`wave_id`、`rank_ids`、`workload_prefix`、`semantic_manifest` |

提交路径相对 `SHARED_DIR`，解析后必须仍在该目录内。`semantic_manifest` 必须指向 `plan.json`，`workload_prefix` 必须是同目录的 `layer` 前缀。图在消息中的当前 `now_ns` 激活，不在一条消息中预约未来图。

`next_wakeup_ns` 为未来绝对时间或 `null`，每次 COMMIT 替换此前的外部唤醒。`input_exhausted=true` 表示没有未来到达，要求唤醒为 null；它不表示活动请求已经完成。`service_finished=true` 还要求无提交、无未来唤醒、全部组空闲，后端随后等待物理网络排空才正常退出。

同一仿真时刻的完成通知按组顺序处理；该时刻稍后产生的新事件可以再触发一次决策，不回溯修改已经激活的图。忙组到达的新请求由 Python 排队；后端不会替换在途图。独立组可在另一组仍有包或事务时提交，网络对象保持连续。

未知字段、编号或时间不匹配、EOF、路径错误和子进程失败均报告错误。Python 的协议读取限制单行大小并使用有限宿主超时，部分 JSON 行也不能导致无限等待。协议超时与 `MAX_NS` 分别限制现实等待和仿真运行，不可混用。[协议读取代码](../python/moe_ub_cosim/protocol_io.py)与[服务驱动](cosim_service.cc)是完整字段校验的实现位置。

## ASTRA 与 UB 的对应

| 接口 | 动作 |
|---|---|
| `sim_schedule` | 将相对计算时间放入 ns-3 事件队列，完成时异步调用 ASTRA 回调 |
| `sim_get_time` | 返回 ns-3 当前绝对纳秒时间 |
| `sim_send` | 按收发匹配键查找不可变描述，登记回调，提交一份 UB 操作或本地交付；LOAD 由消费者发起 |
| `sim_recv` | 登记同一传输的数据等待；不再产生另一份网络任务 |
| `sim_notify_finished` | 报告该 rank 的图完成；不直接停止其他组或网络事件 |

`sim_schedule` 只接受锁定 Sys 使用的 NS 单位；正小数纳秒向上取整，零也通过事件队列完成。负数、非有限值、错误单位、空回调和溢出拒绝。每个进程只在事件运行前设置一次 NS 精度。

锁定 Chakra 分支名为 `duration_micros` 的历史字段实际按纳秒读取，因此导出和读取之间不再乘除 1,000。零 runtime、零 num_ops 的 COMP 会被原 Workload 作为零时间节点移除；需要观测的阶段必须有可核验的执行节点或依赖定义，不能把未记录的零节点当成实际测量。

收发按 `(src_rank, dst_rank, comm_tag)` 匹配，核对 `transfer_id`、字节量及 `UINT8`。当前匹配标签的已支持范围是 `1..499999999`，超界拒绝，以避开锁定 ASTRA NATIVE 路径的标签重映射。内部传输号为 64 位，UB 任务号和单个任务字节数需满足 32 位接口上界；总量较大时由通信模板显式分块，单块超界不能静默截断。零业务贡献不创建零字节 UB 任务，但模板要求的非零状态消息仍须保留。

`sim_request*` 可能来自调用栈且包含未初始化字段，适配器只复制明确需要的实参，不异步保留或读取整份请求对象。回调保存图、节点和传输身份，旧图回调不得绑定到下一轮相同局部节点号。

ASTRA 的点对点发送槽按节点身份占用和释放，支持当前配置的一槽或两槽，乱序完成也释放对应槽。RECV 不占发送槽；每 rank 保留一个计算槽。已展开的 Dispatch/Combine 不再交给 ASTRA 集合通信模型或 `UbTrafficGen` 重复生成与调度。

## 数据就绪与发送完成

联合路径包括 **URMA WRITE / RTP** 与原生 **STORE / LOAD**。`UbTransferExecutor` 复用 UB 的协议提交和资源处理，保存任务上下文；CSV 原生入口完成后通知 `UbTrafficGen`，联合入口通知适配器，联合任务不查询 CSV 阶段表。以下 WQE、Jetty、TA 与 TP 说明适用于 WRITE/RTP。

这里的 WQE 是描述一次写操作的队列条目；Jetty 是承接 WQE 并维护事务上下文的端点资源对象。TA 是事务层拆分与重组的传输单元，TP 是承载这些单元的网络传输通道，保存分包和包序号等状态；这里的 TP 与模型的张量并行参数是不同概念。

| 事件 | 实际来源 | 后续动作 |
|---|---|---|
| `data_ready` | 目标端 `RemoteWriteCommitted`，按原 Jetty 和 TA 序号累计整份任务的有效写入字节 | 完成 RECV，允许依赖数据的计算继续 |
| `transaction_done` | 源端协议完成并清理物理事务后，适配器收到的完成回调 | 确认原事务及应释放的 Jetty 已清理，记录完成时刻 |
| `send_done` | 当前固定 `sender_completion="transaction_done"` | 清理事件退出内部回调栈后，异步完成 SEND 并释放发送槽 |

目标通知发生在事务层更新 `m_memoryStore` 后、构造事务 ACK 前；RTP 在此之前已去重并重组完整 TA 单元。`GetWqeSize` 只是当前重组分段大小，整份任务总长来自不可变描述。不能以最后包到达、链路 ACK 或源端事务完成直接代替目标数据就绪。

数据先到而 RECV 尚未登记时，保留完成状态；之后的 RECV 在登记当时通过 `ScheduleNow` 完成，即使源 Jetty 已释放。每个有效分段和两侧回调只处理一次，回调不会在登记函数尚未返回时同步重入。`m_memoryStore` 只模拟地址和写入大小，不执行真实张量写入，也不代表硬件内存一致性已建模。[write_completion.cc](write_completion.cc)和[ub_write_transfer.cc](ub_write_transfer.cc)实现累计与生命周期检查。

`cann_a3_v850_native_v1` 保留 CANN 消息布局，在输入 `native_profile` 中分别指定 `dispatch`、`combine` 的 `STORE` 或 `LOAD`，并显式设置 `ready_notice_bytes=32`。状态发布始终使用 STORE。这是平台定义的原生操作映射，不代表执行 CANN 的 MTE 指令。

| 原生操作 | 发起与数据方向 | 数据就绪和完成 |
|---|---|---|
| STORE | 数据拥有者发起，数据送到目标消费者 | 目标到达通知累计实际有效范围，满足消费者 RECV；反向确认归还请求资源后完成发起方 SEND |
| LOAD | 消费者发起请求，数据拥有者返回响应 | 消费者收到完整有效响应并结束原生请求后完成 SEND；数据拥有者的 RECV 登记缓冲释放等待 |

LOAD 前增加一份真实的 32 B 元数据 STORE：数据拥有者原有准备依赖完成后发出，消费者收到后才可发起 LOAD。消费者后继计算依赖 LOAD SEND，供数方后继缓冲使用依赖对应 RECV。计划和结果显式记录 `initiator_rank`、`data_owner_rank`、`consumer_rank`；`src_rank/dst_rank` 是请求发起方向，不能直接当作 LOAD 的数据方向。改变操作类型必须重新构图。

原生请求直接进入 `UbLdstApi`、VOQ 和物理端口，不创建或绑定 RTP TP/Jetty。每份任务按配置的 `ThreadNum` 均分给有效 worker，短任务不创建零字节分段；每个 worker 的 LOAD/STORE outstanding 分别限制在途请求。`RequestIdCount` 限定每个发起节点的 16 位请求标识池（1～65,536），池满等待响应，标识只在响应匹配并释放后复用。内部任务分段标识不再截断写入报文。队列、确认计数、任务映射及请求映射均在完成时清理；资源配额属性的回读值不随占用改变。

同芯片不同 rank 使用 `LocalDelivery`，源端准备节点完成后发布数据，再通过同一 ns-3 时刻的排队回调满足双方等待。它没有 UB app/task 和网络包，也支持晚到 RECV。`local_deliveries` 独立保存，`transfers` 及端口包来源只记录实际跨芯片 UB 操作；同 rank 的贡献在原本地计算中保留。

## 图完成、网络排空与资源释放

图完成与物理网络空闲是两个条件，按不同对象分别处理。

| 对象 | 释放条件 |
|---|---|
| 图、适配器借用指针、传输和本地交付对象 | 必要节点已完成，目标等待和源事务结束，无挂起回调，原始执行记录已刷新、保存并核验 |
| 端侧执行时间索引 | 当前图的记录读取和核验结束；在发送 WAVE_DONE 前销毁 |
| 操作到包的内部对应表 | 已登记该图完成，且共享物理网络的包、端口、队列、TP 活动、原生 worker 和请求映射全部排空；仍活动图的记录继续保留 |
| 已销毁 Jetty 的接收 TA 序号历史 | 物理网络排空、相关 Jetty 全部销毁，且无部分接收、待发送或未确认分段及控制包 |

跨图保留 Sys、UB 应用、控制器、物理网络与连接状态；释放已结束 Jetty 的历史不会清空 TP 通道、传输累计序号、PSN 包序号或回绕参考。当前回收入口仅用于每个任务新建并销毁 Jetty 的 WRITE/RTP 服务，不用于持续复用旧 Jetty 的原生应用。持续重叠而网络一直不空闲时，来源记录仍受明确数量上限约束，超限报错。

启用压缩记录时，服务先封存并核验计划中的传输记录，再释放重复 JSON；结束图保存节点、端点、操作、传输和本地交付记录后才销毁执行对象。运行级编号用精确区间集合检查重复，不能因释放旧图而失去重复检查。存储格式与归档规则见[记录与存储格式](../docs/record_storage.md)。

原生 Linux/glibc 构建会在上述安全边界调用 `malloc_trim(0)` 请求归还空闲页；对象数归零不等于进程 RSS 归零。可通过 `MOE_UB_HOST_PREPARATION_TRACE=1` 保存准备阶段、图交接资源及 `transport-retirement.jsonl`，该诊断不改变仿真事件。UB task/packet/port 文本日志由有效 backend globals 显式控制，与研究所需端点、逐包、队列和来源记录分别开关。

正常结束要求 Python 声明服务完成、全部图和事务完成、实际链路包与队列排空、必要统计已刷新，再取消剩余周期巡检事件。没有未来唤醒、计算/适配器事件、待提交任务或可前进网络状态，但图仍等待时，报告等待对象；尚有网络状态却无法前进的情况由仿真上限报告，不能一概声称已证明死锁。可捕获失败写入 `failure.json`；进程被信号直接终止时只能保留外层状态和已写日志，不能记为 C++ 正常清理。

## 后端参数及实际生效值

模型通信模板、访问方式和 UB 传输模式属于不同层。模板决定贡献、消息和依赖；访问方式说明谁发起、数据流向与消费条件；RTP 等模式决定具体传输协议。事务 profile 接受 WRITE/RTP 和原生 STORE/LOAD，原生 profile 必须与原生模板选择一致。URMA READ、CTP、拥塞控制、故障及重传模式仍未在联合路径启用。底层属性目录中存在某个字段不代表对应模式已经联调。

新场景生成 `network/backend.json`，旧网络目录没有该文件时沿用 TXT 配置加载。参数校验以[backend_config.cc](backend_config.cc)和当前二进制的 `--backend-catalog` 为准，不在文档复制一份容易过期的全量默认值表。

| 层次 | 选择与检查 |
|---|---|
| UB 属性与全局参数 | 运行时注册的源码默认值 → 场景引用的参考值 → 场景覆盖；联合驱动的固定约束必须满足，冲突报错 |
| 节点和链路 | 从案例或 fabric 生成连通关系，再使用 `network.defaults` 替换速率、传播、仲裁与输入处理时间，最后应用显式 `nodes/links` 覆盖 |
| 分块与端点资源 | 集合 `chunk_bytes` 决定 UB 任务划分，TA segment 决定事务分段，TP 载荷上限决定包级切分；发送槽、Jetty TA 与 inflight 分别检查 |
| 交换与选路 | 保存实际 VL、调度、队列、信用、通道建立、最短候选和路径配置；单平面 CSV 与二级多平面 fabric 各有独立校验范围 |
| 复现信息 | 保存 RNG seed/run、观测端口及范围、二进制与固定依赖、源码默认值和构建常量 |

属性使用完整原生键名及原生 checker 支持的值，例如时间字符串 `10ns`、带宽字符串 `224Gbps`；`network.defaults` 中的 `rate_bps`、`delay_ns` 等按字段单位接受整数。未知名称、重复或不存在的节点/链路、错误单位、目录与二进制不一致及不兼容选择都在业务激活前拒绝。TP 最大数据载荷 `4096 B` 来自锁定源码常量，不是可任意修改的运行时开关。

当前场景采用预建通道和统一业务优先级 7。CBFC 按接收侧信用额度约束发送，SP 按严格优先级调度，HASH 按哈希选择候选交换出口；不启用多 TP 同时传输或按包分散到多个路径的 packet spray。二级多平面场景在每次 WQE 从最短 TP 候选中选择一个平面，交换机再在该平面内选择最短出口。此选择使用原生随机配置和 HASH，不读取内部操作来源。同芯片及独立组之间不因拓扑完整而自动产生业务通道。

`requested_backend.json` 与 `resolution.json` 保存请求值和来源；`network/backend.json` 保存规范化输入；执行目录的 `effective_backend.json` 读取实际网络对象、链路、路由与通道并核对。动态创建的 Jetty 则在 WQE 启动时核对 TA 与 inflight。文本 `network_attribute.txt` 在新路径中只是审计副本，不覆盖 `backend.json`。合法的资源数值仍可能不足以完成某个负载，实际耗尽或超时按失败处理。

原生 API、instance 和每个 worker 的参数也从实际对象回读。结构化原生配置要求 `StoreRequestSize`、`LoadResponseSize` 为 64～8192 B 的二次幂，避免源码向上取整造成输入与实际值不一致。`ThreadNum`、两类 outstanding 和 `RequestIdCount` 必须为正。参考输入见 [原生操作样例](../fixtures/m11_native/README.md)。沿用的网络案例可以保留预建 TP，但这些通道不承载原生操作。

目标场景具体数值及尚无测量依据的参数见[CM384/R1 场景](../docs/scenarios/cm384_r1.md)。改变路径、速率或延迟时须走相应输入校验；现有首轮 `replay` 允许的调整比完整场景解析更窄，见用户指南，不能把后端支持配置等同于任意固定图重放都接受该覆盖。

重放带 `backend.json` 的输入时，[frozen_replay.network_input](../python/moe_ub_cosim/frozen_replay.py)会读取所用二进制的参数目录。除 `ub_revision` 外必须与原目录完全相同；只有修订号不同而目录相同时，才将输入副本绑定到当前修订，并记录旧、新修订及 `behavior_equivalence="must_be_checked_by_execution"`。参数目录兼容不证明行为等价，仍需实际运行对照；它也不授权重放时修改结构化网络参数。

## 被动观测接口

`PortObservation` 复用 `UbPort::TraComEventNotify`：该通知虽然名称含完成含义，实际在通道 `TransmitStart` 成功后发出。记录使用当时的仿真时间与 `Packet::GetSize()`，不移到发送末尾，也不在尝试发送前计数。累计字节另与原端口 `GetTxBytes` 增量核对。

源 Jetty 根据 WQE 进度给出 TA 的任务偏移，TP 创建 WRITE 数据包后通过 `WritePacketCreated` 给出包内载荷范围。`WriteProvenance` 按已有 Packet UID 保存任务与包的对应；多端口共享一份描述和对应表，端口自己的窗口与队列记录独立。它不持有 TP，也不增加包头或标签。来源打开时，未知 WRITE 来源、错位或重叠的业务范围报错。

原生路径的 `RequestCreated` 给出任务有效范围，`RequestArrived` 和 `ResponseArrived` 表示实际接收；`ResponseCreated` 显式关联请求 UID 与新响应 UID。STORE 有效数据来自请求，LOAD 有效数据来自响应；响应不需要继承请求的 UID 或标签。短尾 LOAD 的剩余响应体计入填充，读取请求和原生确认不获得业务归属。原生创建通知只维护被动对应表，不修改包内容、路径、优先级或时延。

共享表采用所有端口声明的最严格包数和传输数预算，包括关闭来源输出的端口。单个端口的释放不会断开其他端口仍需的源 TP 监听；监听由共享对象维护，包创建回调只持有对应表。原生回收的 TP 即使晚于其映射项销毁，晚到回调仍有有效状态，不持有已经释放的端口。图完成后的登记与真正网络排空后的来源清理分别执行。

数据包各次经过端口均计数。业务、应用元数据、填充、数据协议开销与控制包分别核算；只有业务范围参与操作归属，状态 WRITE 属于应用消息，不能当成 ACK 一类协议控制包。原始出口 FIFO 入队/出队审计不包含 VOQ、信用等待或已经在发送的包，不能把该 FIFO 指标称为完整端到端排队时间。字段和统计口径集中见[记录与存储格式](../docs/record_storage.md)。

## 修改后的验证范围

按修改对象选择小型检查，构建与执行方法见[开发维护](../docs/development.md)。不要仅因文档调整重复运行目标规模仿真。

| 修改对象 | 主要现有检查 |
|---|---|
| 时间、发送与接收完成 | [check_compute.py](../tests/integration/check_compute.py)、[check_write.py](../tests/integration/check_write.py)、[check_delay.py](../tests/integration/check_delay.py) |
| 原生 STORE/LOAD、供数通知、资源和字节方向 | [native_runtime_test.cc](../tests/integration/native_runtime_test.cc)、[check_native_behavior.py](../tests/integration/check_native_behavior.py) |
| 分块、发送槽及连续图 | [check_variants.py](../tests/integration/check_variants.py)、[check_sequence.py](../tests/integration/check_sequence.py) |
| 服务、动态到达及共享网络 | [check_service.py](../tests/integration/check_service.py)、[check_dynamic_service.py](../tests/integration/check_dynamic_service.py)、[check_shared_bottleneck.py](../tests/integration/check_shared_bottleneck.py) |
| 参数生效及 fabric | [check_scenario_backend.py](../tests/integration/check_scenario_backend.py)、[check_target_fabric.py](../tests/integration/check_target_fabric.py) |
| 端口观测、来源及存储 | [check_observation.py](../tests/integration/check_observation.py)、[资源工程测试记录](../fixtures/m10_resource_efficiency/README.md) |

原设计的时间、控制协议、完成与身份要求保留在 [v0.2 §7～8](../docs/moe_ub_cosim_dev_v0.2.md)，参数化和多组执行要求见 [v0.3 §3、4.5～4.7](../docs/archive/moe_ub_cosim_dev_v0.3.md)。各阶段的历史命令、耗时和失败修正通过[历史索引](../docs/milestone_history.md)查阅；这些记录不改变当前接口的范围。
