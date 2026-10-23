# 联合后端接口与执行规则

transactions/4支持local节点显式copy源/目标：`AdmitLocal`先检查全部冲突，再消费并保护输入/独占输出；`StartLocal`读取实际字节，`CompleteLocal`先提交输出再释放记录，最后由probe标记完成。`RemoteMemory`将输出独占检查应用于源读、LE64、put/原子/reset；只允许持有该输出记录的提交写出。记录覆盖排队与运行，原生截止及异常保留前缀，见[本地输入输出](../examples/scaleout_rdma_transactions/README.md#本地处理显式读取输入和写出输出)。这是声明耗时的字节复制功能，未声明copy节点的旧语义保持。

transactions/3的put可从注册源窗口读取实际字节。`RemoteMemory`按owner和物理范围保护活跃读；`RemoteTransactions`在提交时取得保护、发布时快照、实际事务完成时释放，排队也保留保护。多个条件reset先全部检查。运行期冲突保存部分结果后失败，不将冲突变成虚构的硬件等待；见[源窗口接口](../examples/scaleout_rdma_transactions/README.md#将实际接收内容作为后续发送的数据)。本地处理输入尚无lease，MRT1的ACK保护不等价GPU源安全完成。

`transaction_probe.cc`新增transactions/2混合节点：实际本地接收窗口条件满足并消费后，进入每GPU声明耗时的FIFO处理槽，完成再解锁后继事务。`RemoteTransactions::AdmitLocal`只消费本GPU条件，不预留网络工作；本地尾部保持持久连接。入队消费不代表buffer租约或GPU可见。输入、截止状态和限制见[事务本地处理](../examples/scaleout_rdma_transactions/README.md#接收条件与本地处理交替执行)。v1字段和执行保持。

`stream_probe.cc`支持stream-work/3的因果本地阶段：每GPU一个声明耗时的非抢占FIFO槽，由实际接收/ACK和本地完成事件释放后继；已排队节点优先于新解锁节点。同一原生Simulator保留本地处理期间的QP及控制器状态，全部节点完成且传输物理排空才关闭连接。新节点字段、整数类型、GPU身份及依赖事件由原生入口复核；旧v1/v2接口保持。它不是CUDA或现代GIN执行，见[公开输入和限制](../examples/scaleout_rdma_streams/README.md#网络回调与本地处理交替执行)。

`RemoteTransactions::QueueFenceReady`只捕获一次源端队列前缀，通过GinWorkQueue的FlushComplete查询实际进度；coverage日志不参与执行。完成来自MRT1应用队列，不是GIN CQ/源buffer保证，见[快照边界](../fixtures/queue_fences/README.md)。

`RemoteMemory`提供有owner/key/范围限制的本地LE64读写，`RemoteTransactions`用它执行eq/ge条件等待和可选消费清零；调用者只读当前提交连接src的窗口。远端应用与本地消费的离线order记录共用序列，不参与线上协议；见[接口与限制](../examples/scaleout_rdma_transactions/README.md#根据实际收到的值推进下一轮)。

`remote_memory.hh`实现MRT1解码、窗口权限/范围检查及串行put/fetch-add，`remote_transactions.hh/.cc`将其接入持久原生载体；`transaction_probe.cc`提供独立功能入口。普通写入按请求ACK完成，原子另等实际返回值，同上下文按前缀回收；不把应用完成当作网络排空。MRT1与原生载体的组合不是RoCE/GIN provider或硬件成本模型，输入、事件、分层限制与验收见[远端事务](../examples/scaleout_rdma_transactions/README.md)。

## GIN工作队列功能核心

独立头文件[gin_work_queue.hh](rdma/gin_work_queue.hh)提供未接入网络的C++17资源状态。它将取得ticket、槽可写、描述符准备、doorbell发布、NIC消费和累计CQ完成分开；完成前缀限制环形槽复用，flush保存调用时的reserved前缀，队列空闲时可继续追加。`Reserve(work_id, operation)`要求队列内work_id唯一，同一应用operation允许拆成多个工作。调用方只能在应用依赖实际满足后reserve，并须用provider真实完成回调推进CQ；该类没有远端数据真值、时钟或调度器，不会自动发包或唤醒GPU消费者。

`ReserveBatch(vector<WorkSpec>)`在一次reservation中取得连续多个WQE条目，每条仍有独立work_id。整段末尾槽满足CQ复用条件后才允许准备该段任意条目；发布可以覆盖多个完整reservation，但不能截在其中间。完成前缀可以逐WQE推进，不将整个应用operation强制视为一次完成。这里只支持显式前缀发布与正常availability检查，不实现CPU_PROXY_FREE_FLOW或SKIP_AVAILABILITY_CHECK；也不由字节数猜测WQE数、WQEBB大小或包数。

深度显式取1—4096内的2的幂，单对象最多记录65,536个工作；每次reservation长度1—depth，超出明确拒绝。后一限制是当前支持边界，避免整段等待自身尚未发布前缀；不是provider或硬件上限的声明。`Reserve`等价单条`ReserveBatch`，旧调用保持其行为。`ResolveGinContext`按声明的连接数分解应用索引，并用传入设备表映射本地设备；它不探测NIC、验证拓扑或创建物理QP。该模块没有接入`Fabric`，不表示在线QP复用、原子协议或现代DeepEP已实现。接口阶段与功能复核见[独立检查](../fixtures/gin_work_queue/README.md)。下面原生服务保持现有行为。

## 现有原生服务扩展

持久字节入口可选`dcqcn_reference`，由[纯状态核心](rdma/dcqcn_reference.hh)和`PersistentStreams`实际接入NP周期、独立CNP、RP timer/实际发送字节恢复。`CnpSwitch`用局部元数据投影补足固定上游CNP的未初始化哈希字，不改线上报文和固定依赖。默认控制模式保持原有行为；参考模式不是校准的RoCE或现代通信库成本模型。配置、取整、同纳秒和排空规则见[独立反馈小例](../examples/scaleout_rdma_streams/README.md#可选的独立拥塞反馈参考模式)。

新增独立`PersistentStreams`与`moe-rdma-stream-probe`使用同一原生Fabric的设备回调，实现空闲可追加的持久字节连接、真实接收/ACK前缀和ACK释放的GinWorkQueue信用。连接显式绑定唯一源/反向NIC；实际收发决定后继就绪。它独占Fabric传输入口并拒绝旧Send混用，没有接入现代计划、原子事务或GPU可见性，不改变下面在线服务的语义。见[持久字节小例](../examples/scaleout_rdma_streams/README.md)。

Fabric在初始化时从实际`RdmaHw`对象读回拥塞/传输属性，统一导出`rdma_parameters`，不替换原生默认值。持久字节入口可选有界`source_rate_capture`，在已有源TX及ACK处理前后同步读状态，无额外仿真事件；不改变包或完成条件。详细单位、采样阶段和容量失败语义见[输出说明](../examples/scaleout_rdma_streams/README.md#可选的源端速率观测)。

持久路径按线端端口索引连接、按已发布/接收/ACK前缀推进工作，发送按序号二分定位工作以支持原生NACK回退。`stream-work/2`允许显式最多128连接/8192工作；v1及事务入口仍默认16/64。应用依赖按真实回调更新后继，输入顺序是就绪平局规则；没有独立仿真时钟、预设完成或修改协议事件。验收范围见[索引与计数](../fixtures/indexed_streams/README.md)。

原生RDMA准入控制器`communication_admission.hh`支持消息数和逻辑字节双约束。`queue_integral.hh`保存每出口常量状态，`rdma/fabric.cc`将出队前钩子规范为出队后状态，核对字节守恒后积分，不需要每包历史或采样事件。源QP序号和阶段耗时只用于离线报告。配置见[准入示例](../examples/scaleout_rdma_admission/README.md)。

可选超时恢复由`retransmission_timer.hh`和专用协议模块`rdma/recovery.cc`实现，经原生NIC的实际发送/接收回调接入，与被动观测分离。源端按累计ACK进展计时，有限重试、指数退避；超时调用原生`RecoverQueue`和`TriggerTransmit`，重复数据在接收端生成真实累计ACK。控制器不读取全局接收真值驱动发送。默认原生模式不增加协议行为，依赖无修改。服务结束在扩展模式中还等待实际网络排空；耗尽重试停止整个共享仿真并保留失败时资源，不补造成功。参数和边界见[恢复示例](../examples/scaleout_rdma_recovery/README.md)。

恢复代码进一步分为纯协议`source_recovery.hh`、可选Karn采样`ack_rtt_estimator.hh`、ns-3接入`rdma/recovery.cc`和纯被动记录`rdma/recovery_records.hh`。RTT样本仅来自源实际发包与反馈，重传/NACK/超时作废歧义样本，每QP最多一个待测包。无配置时保留固定超时。Python配置、RTT重放、协议审计和条件终止/额度证据分别维护；[自适应示例](../examples/scaleout_rdma_adaptive/README.md)提供用户入口，[形式化说明](../docs/communication_fragility.md)说明不变量、证明假设与有限状态检查范围。

可选`recovery_history.hh`只存源端有效RTT或超时退避形成的保守初值，每源独立容量、年龄与LRU，目的地址隔离plane，不改已有QP计时器。`CommunicationAdmission`可在总槽/字节内进一步限制单一目的GPU占用并跳过不合份额的队首；不按故障真值分类、不提前释放所有者。独立离线重放核对历史、准入和条件证书，公开配置见[恢复与源资源保护](../examples/scaleout_rdma_resilience/README.md)。

本目录将 ASTRA 的任务执行与 ns-3-UB 的事件、协议和网络状态连接起来，负责图装载、控制协议、收发匹配、完成回调及原始观测。整体过程见[系统架构](../docs/architecture.md)；普通运行与构建使用[用户指南](../docs/user_guide.md)中的 `moe-ub` 命令。本文件供修改后端或编写集成检查时查阅，不记录逐次开发结果。

## 构建与程序入口

原生RDMA的确定性故障规则与包级记录由`Fabric`实现，消息身份仅通过原端点/端口被动关联，不写入包头。数据和ACK/NACK可在指定接收设备按次数与窗口丢弃；BEgressQueue与设备钩子的被动记录有明确上限。`scaleout rdma-diagnose`在执行结束后读取成功或失败结果，不参与调度。接口、完成/字节口径与协议限制见[丢包小例](../examples/scaleout_rdma_faults/README.md)。

可选每源软件准入由纯C++17的`communication_admission.hh`管理当前就绪任务与额度所有者，RDMA适配负责真实注入和完整ACK后确认发送QP映射移除，再归还额度。数据就绪只放行接收消费者。同机crossbar排除；默认不开启时保留原路径。它没有模拟有限硬件QP槽或GPU缓冲寿命，具体配置和事件见[软件准入示例](../examples/scaleout_rdma_admission/README.md)。

非 UB Scale-Out 的独立入口位于 [rdma/](rdma/)，经公共 `scaleout rdma-build` 配置锁定 ASTRA 自带的 ns-3。`moe-rdma-probe` 做真实分组消息检查，`moe-rdma-service` 由实际 LLMServingSim 前端在线提交 Chakra 图；每 rank 保留 Sys，所有组共享 Fabric 和 Simulator。完成语义、串行构建兼容 overlay、观测及限制见[RDMA 小例](../examples/scaleout_rdma/README.md)。该入口只链接原生 RDMA ns-3；下面的生产 UB 入口及构建规则保持独立。

`moe-rdma-service TOPOLOGY RDMA SYSTEM BOOTSTRAP_PREFIX NEW_RESULT [NEW_HOST_TIMING.jsonl]` 的最后一个参数为可选宿主旁路计时，由公开 `rdma-run --host-timing` 传入。它不读取或更新ns-3时钟、不安排事件、不改控制消息；默认关闭。阶段、嵌套、计数及失败前缀语义统一见[输出数据](../docs/record_storage.md#原生rdma宿主阶段记录)。

新 [百卡配置](../examples/scaleout_clos128/README.md)以 `rdma/2` 显式声明数量和宿主预算，`topology/2` 在同一 Simulator 增加分块机内织构。跨机按源/目的地址与线端口关联，保留完整 int32 逻辑 tag，冲突时拒绝。完整节点证据为 `log/astra_nodes.log`，不使用滚动诊断日志覆盖早期事件。`Fabric::Report()` 和 `Messages::Report()` 在最终 Run 结束后仅调用一次并转移记录所有权；之后不能再次 Run 或访问已消费记录。新配置终态 JSON 紧凑流式输出，字段语义不变。

RDMA 端口、消息、路径和回调时间在执行中使用数值记录，终态才构造同字段 JSON；消息身份排序后导出，保持原顺序。流和端点查找用哈希表，端口按节点/设备编号索引，可达性为受 GPU/plane 上限约束的字节矩阵。它们不参与发包、选路或计算调度；[表示等价证据](../fixtures/scaleout_numeric_observer/README.md)分别验证旧 8 卡、完整 16/128 卡结果与任务图逐字节一致，不据此声称千卡已经完成。

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

有界记录模式先封存并核验完整传输描述，再释放这些 JSON 对象；实际 Chakra 执行读取器在随后安装图时创建。这样避免执行读取器与仅供封存的传输 JSON 同时驻留。所有语义和 Chakra 节点检查仍在装载阶段执行，全部读取器创建成功后才安装各 rank，激活仍发生在最后。运行期间输入图保持不变。

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

旧模板的 ASTRA 点对点发送槽按节点身份占用和释放，支持一槽或两槽，乱序完成也释放对应槽。RECV 不占发送槽；旧模板每 rank 保留一个计算槽。已展开的 Dispatch/Combine 不再交给 ASTRA 集合通信模型或 `UbTrafficGen` 重复生成与调度。

### A3 局部资源模板

`cann_a3_v850_store_resources_v1` 固定采用原生 STORE/STORE，沿用 CANN 的消息布局与独立状态消息。输入仍为 `layer-fixture/3`，`send_slots_per_rank=0` 专门表示由局部缓冲及原生请求池控制发送；它不是无限容量的请求池，旧模板不接受零。结构化场景的 Dispatch/Combine profile 必须均为 STORE，同一次运行不能切换发送资源模型。

`cann_profile` 保留 `source_revision`、`window_bytes`、`local_cost_ns`，另要求 `algorithm="a3_fullmesh_v1_store_resources_tp1"`、`platform_aiv_cores`（1～48）、`core_count_basis="declared_platform_reference"` 和 `copy_fence`。通信核数量是显式参考参数，不能当成已确认的硬件配置。`copy_fence` 默认为示例中声明的 `data_ready`，也允许显式选择 `local_read_complete` 做完成条件敏感性检查；输入不能省略该字段。

Python 的 [a3.py](../python/moe_ub_cosim/a3.py) 与 C++ 的 [a3_plan.cc](a3_plan.cc) 分别重建分核、缓冲复用和完成依赖。每个 `aiv:N` 是一个独占计算资源，普通模型计算使用 `gpu_compute`；独立通信核可同时工作。局部缓冲用成对的准备与复用依赖表示：同一核的同一缓冲必须等上次数据被提交路径实际读取，才允许再次准备。原生 worker 的 outstanding 上限和节点请求号池继续由 ns-3-UB 管理。

图中 `EVENT` 不计计算耗时。它在 Chakra 中编码为零时长 COMP，但由 `Workload::set_external_events` 绑定到已校验的屏障、实际请求形成或目标交付事件，保留正常异步回调及执行日志，不走原有的零 COMP 删除路径。`RequestCreated` 累计整份 STORE 的有效字节后产生 `local_read_complete`；它不表示上链路或到达目标。默认复制栅栏等待实际目标 `data_ready`；SEND 仍等原生事务完成和资源回收，RECV 仍只等待数据。

Dispatch 保留源码的跨核数据栅栏再发布计数。Combine 按核发送，并按本核分配的 Token 分别等待与归约；下一算子等待整个本地算子完成。连续层依赖使用显式 `terminal_nodes`，最终图完成仍要求所有发送、回调和协议资源清理。`phase_members` 保存拆分后的准备、重排、打包和归约节点，聚合准备阶段给出这些节点的时间包络，不代表它们串行执行。旧的全 rank `send_state` 采集不适用于此模板，会明确拒绝。

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

`ns3::UbLdstThread::StoreQueuePolicy` 默认为 `fifo`，保留统一 STORE 任务队列；`producer_round_robin` 是可选的按生产者轮转参考，要求 `ThreadNum=1`。所有生产者共用这个 worker 的 STORE 额度和端点编号池。最初从非空序列的最小核编号开始；每次成功形成一个既有请求后，游标移至该核，下一次选编号更大的首个非空序列，没有则回绕。池或额度阻塞不移动游标，仍由原有响应事件唤醒，不增加轮询、时延或随机状态。任务形成全部请求后退出核内队首，不额外等待协议确认；LOAD 队列及两种操作的占用/释放语义保持原状。

联合后端仅为已校验的 `cann_a3_v850_store_resources_v1` 图提供生产者身份。在 `ValidateA3Plan` 独立重建静态分配时，把产生 SEND 的核编号保存到描述符，再随 RuntimeTask 传至任务分段；不从近邻计算节点、目的端或实验真值猜测。零成本工作和跨核状态屏障也保留唯一分配。完整模型组合保留此内部字段，原 JSON 图、依赖和摘要不变。身份不写入包或选路哈希，启用策略但缺少身份、非法策略或多个 worker 均拒绝。各核逻辑序列是模型参考，不能据此认定真实 A3 的硬件队列结构。公开配置见[A3 小例](../examples/a3_segment/README.md)。

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

当前场景采用统一业务优先级 7、CBFC 信用流控和 SP 严格优先级调度。WRITE/RTP 保留预建通道，每次 WQE 选择一个最短 TP；该路径不启用多 TP 或逐包喷洒。二级多平面中的交换出口由 HASH 选择，同芯片及独立组之间不因拓扑完整而自动产生业务通道。

原生 STORE/LOAD 使用 `ns3::UbLdstApi::UsePacketSpray`（默认 `false`）。关闭时，同一源/目的的方向固定选路；开启时，原生 API 递增 8 位负载均衡字段，源端和交换机按该字段选择包路径，响应回显此字段并独立选返回路径。源端使用节点主地址，fabric 的候选是各平面的源端口；交换机保持在已选平面，在实际可达最短出口中选择。有限样本不保证均匀。这条路径不使用 TP；种子/运行号仍保存，观测不读取或消耗协议随机状态。

已验证组合为 WRITE/RTP 固定选路、原生 STORE/LOAD 固定选路或逐包喷洒，均使用 HASH、最短路径和 CBFC；原生结构化输入的 `UseShortestPaths=false` 被拒绝。`effective_backend.json` 的 `fabric_audit.native_memory` 保存实际候选检查，生成器的声明与运行时核对分开。输入及受控乱序检查见[原生喷洒示例](../fixtures/m12_native_routing/README.md)。

`requested_backend.json` 与 `resolution.json` 保存请求值和来源；`network/backend.json` 保存规范化输入；执行目录的 `effective_backend.json` 读取实际网络对象、链路、路由与通道并核对。动态创建的 Jetty 则在 WQE 启动时核对 TA 与 inflight。文本 `network_attribute.txt` 在新路径中只是审计副本，不覆盖 `backend.json`。合法的资源数值仍可能不足以完成某个负载，实际耗尽或超时按失败处理。

原生 API、instance 和每个 worker 的参数也从实际对象回读。结构化原生配置要求 `StoreRequestSize`、`LoadResponseSize` 为 64～8192 B 的二次幂，避免源码向上取整造成输入与实际值不一致。`ThreadNum`、两类 outstanding 和 `RequestIdCount` 必须为正。参考输入见 [原生操作样例](../fixtures/m11_native/README.md)。沿用的网络案例可以保留预建 TP，但这些通道不承载原生操作。

目标场景具体数值及尚无测量依据的参数见[CM384/R1 场景](../docs/scenarios/cm384_r1.md)。改变路径、速率或延迟时须走相应输入校验；现有首轮 `replay` 允许的调整比完整场景解析更窄，见用户指南，不能把后端支持配置等同于任意固定图重放都接受该覆盖。

重放带 `backend.json` 的输入时，[frozen_replay.network_input](../python/moe_ub_cosim/frozen_replay.py)会读取所用二进制的参数目录。通常要求除 `ub_revision` 外完全相同，再将输入副本绑定到当前修订。旧 v0.3 WRITE/RTP 数据另有一项明确迁移：只补充未激活的原生 `RequestIdCount=65536`，并接受两个原生请求长度参数从 nearest 更正为 up 的帮助文字；原参数值、默认值、类型及其他目录项必须保持。原生 profile 不接受上述 WRITE 专用迁移。另一项兼容规则允许旧目录缺少 `StoreQueuePolicy` 时补入精确声明的 `fifo` 默认值，并记录来源；其余目录项仍须完全匹配，显式轮转覆盖还要经过单 worker 和图身份检查。其他差异仍拒绝。副本记录兼容规则、增补来源及 `behavior_equivalence="must_be_checked_by_execution"`，不修改原数据。目录兼容不证明行为等价，仍需实际对照。结构化重放允许下述出口服务区间、已验证原生路径的 `UsePacketSpray` 开关、STORE 总额度，以及单 worker A3 资源图的 STORE 队列策略；其他属性、任务 profile、基础拓扑和端点保持一致。

### 准备成本与出口服务区间

CANN/原生 `layer-fixture/3` 可附加 `endpoint_prepare`：`schema_version="moe-ub-endpoint-prepare/1"`、`relation="additional_to_existing_once"`，以及按 `rank_ids` 顺序完整列出的 `ranks`。每行包含 `rank_id`、`dispatch_ns_per_local_token`、`combine_ns_per_expert_input`，后两项为非负整数纳秒，未使用的 rank 显式填零。省略整个配置等价于全部零。Dispatch 额外成本加到已有准备节点（完整模型名称 `dispatch-quant-pack`），Combine 额外成本加到 `expert-result-pack`，乘数分别为本地 token 数和本 rank 实际专家输入数；既有打包、专家计算、本地交付成本保持。声明者只填写尚未包含在已有成本中的工作。Python 与 C++ 分别重建并核对实际图，溢出及篡改被拒绝；修改成本必须重新构图。

`scenario-run/3` 在原场景字段外要求 `port_service`，网络可沿用 CSV 或 `fabric_file`；解析为 `backend-config/3`。服务配置格式和公开操作见[用户指南](../docs/user_guide.md)。`UbPort::SetServiceIntervals` 在执行前登记有界区间，`GetEffectiveDataRate` 在每次普通或 detached 包发送时查询当前仿真时间，无需安排改速事件；同刻发包使用半开区间定义，已经开始的序列化时长不被追改。`GetDataRate` 仍表示拓扑基础速率，区间只改变指定物理出口，不改变对端基础值。

`effective_backend/3` 的 `live_port_service` 从实际端口读取区间并核对，逐条记录 `base_rate_bps`。原图重放可增减或替换这些区间，并记录旧、新配置；运行 `max_simulation_ns` 必须与服务区间声明的执行范围相同。执行超时保留 `failure.json`，不生成正常完成结果。包级实际速率、边界和在途包检查见[端侧与出口服务输入](../fixtures/m13_endpoint_service/README.md)。

## 被动观测接口

端侧发送资源由 ASTRA `Workload::set_send_state_observer` 订阅读取：完整依赖就绪扫描恢复未提交队列后，给出其中 SEND 数、`HardwareResource` 实际占用集合大小和容量。首次配置槽后也通知一次。回调保留在同一 Workload 中跨图使用；不插入事件、不变更队列顺序或发包。LOAD 的占用属于发起方，不属于响应供数方；接收数据就绪不释放发起槽，仍由正常 SEND 完成回调释放。

`port-observation-set/6` 可附带 `native_queue` 有界诊断策略。被选端点的原生 worker 在实际入队、发出、额度阻塞、编号池阻塞和响应资源释放分支通知监听器。监听器复制该 worker 的有序队列及整个端点的 pending 请求表，保留跨 worker 占用者；不安排事件、不改变包或随机数。原始 `native_queue` 流只导出到 `truth/`。初始与终止状态、条数/快照限制及公共配置见[请求队列格式](../docs/record_storage.md#原生请求队列诊断)。

`port-observation-set/7` 允许 `network_facts`。`NetworkFacts` 订阅实际出口服务、VOQ/FIFO 和独占 CBFC 的信用变更/队首判断；不调用会弹出队首的 `GetNextPacket()`，也不额外执行信用判断。全局顺序、记录上限与零时刻初态使覆盖可核验，资料只导出到 `truth/`。具体语义、读取接口和限制见[网络机制事实](../docs/record_storage.md#网络机制事实)。

`port-observation-set/5` 以 `send_state` 携带 `moe-ub-send-state-config/1`，端口成员继续使用 `/4`。后端要求有界记录策略，由 `SendStateRecords` 保留每 rank 当前值并流式写入共享记录存储；没有开关时不订阅、不生成发送状态流。初始与排空后的结束边界、局部时钟和条数限制见[发送状态格式](../docs/record_storage.md#端侧发送资源状态)。

`PortObservation` 复用 `UbPort::TraComEventNotify`：该通知虽然名称含完成含义，实际在通道 `TransmitStart` 成功后发出。记录使用当时的仿真时间与 `Packet::GetSize()`，不移到发送末尾，也不在尝试发送前计数。累计字节另与原端口 `GetTxBytes` 增量核对。

源 Jetty 根据 WQE 进度给出 TA 的任务偏移，TP 创建 WRITE 数据包后通过 `WritePacketCreated` 给出包内载荷范围。`WriteProvenance` 按已有 Packet UID 保存任务与包的对应；多端口共享一份描述和对应表，端口自己的窗口与队列记录独立。它不持有 TP，也不增加包头或标签。来源打开时，未知 WRITE 来源、错位或重叠的业务范围报错。

原生路径的 `RequestCreated` 给出任务有效范围，`RequestArrived` 和 `ResponseArrived` 表示实际接收；`ResponseCreated` 显式关联请求 UID 与新响应 UID。STORE 有效数据来自请求，LOAD 有效数据来自响应；响应不需要继承请求的 UID 或标签。短尾 LOAD 的剩余响应体计入填充，读取请求和原生确认不获得业务归属。原生创建通知只维护被动对应表，不修改包内容、路径、优先级或时延。

共享表采用所有端口声明的最严格包数和传输数预算，包括关闭来源输出的端口。单个端口的释放不会断开其他端口仍需的源 TP 监听；监听由共享对象维护，包创建回调只持有对应表。原生回收的 TP 即使晚于其映射项销毁，晚到回调仍有有效状态，不持有已经释放的端口。原生 STORE 请求在目标 `RequestArrived`、LOAD 数据响应在消费者 `ResponseArrived` 后释放该包的被动来源范围；所有交换出口已经经过，后续协议确认不携带应用数据。API 自己的请求、响应及资源状态保持原生命周期。WRITE 范围和描述表仍在图完成且共享网络排空后清理，分别报告峰值与释放计数。

数据包各次经过端口均计数。业务、应用元数据、填充、数据协议开销与控制包分别核算；传统业务归属表只用业务范围，新版分类真值按声明选择应用消息类别，状态 WRITE/STORE 属于应用消息，不能当成 ACK 一类协议控制包。原始出口 FIFO 入队/出队审计不包含 VOQ、信用等待或已经在发送的包，不能把该 FIFO 指标称为完整端到端排队时间。字段和统计口径集中见[记录与存储格式](../docs/record_storage.md)。

`port-observation/4` 在统计输入外携带 `port-recording/1`。`MessageWindows` 直接累计当前本地窗口计数和有限的分类归属表，`QueueWindows` 独立累计等待量；只有窗口关闭时生成汇总 JSON。详细捕获在端口与区间判断之后才构造事件 JSON，区间初末队列快照由 `DetailQueueState` 被动补记。执行不改变包、时序或调度；参数与原始文件的对应见记录格式。

## 修改后的验证范围

新增操作阶段通过 `OperationStages` 在图退休前读取实际 COMP 执行时刻和传输完成状态，按操作、rank 汇总；它不参与调度。队列使用 `UbQueueManager::OutPortVoqWaitingBytes` 与 `UbEgressQueue::WaitingBytes` 的 64 位被动状态通知。前者包含本地控制包但排除 ingress processing；旧 `GetTotalOutPortBufferUsed` 仍包含处理中的字节，不能替代纯 VOQ getter。一个共享 `VoqSources` 每交换机订阅一次通知，避免为每个包遍历全部观测端口。

`QueueWindows` 仅保留当前窗口、两种等待量及上次本地时刻，用 128 位整数累计 byte·ns；零状态空窗直接跳过，持续积压按实际窗口拆分。`port-observation/3` 的 `queue_sampling` 声明被动本地窗口及变换，要求有界记录策略。详细原始记录、公开表和同刻变化的峰值定义见[记录格式](../docs/record_storage.md#v04-操作阶段与等待统计)，新增字段不进入业务包或路由。

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

## 有限原始层段

`model-segment-plan/1` 复用完整模型成本与 A3 资源图，只执行声明的连续原始 MoE 块，保留原层号。C++ 从完整模型、成本、原始子层输入与 `segment` 初态独立重建，拒绝范围或上下文不一致。`service-deployment/6` 固定该范围，协议提交不能中途改变。布局、事件和容量边界见[输出数据](../docs/record_storage.md)及[公开小例](../examples/a3_segment/README.md)。A3 STORE 的局部与分类到达时刻取自原生请求形成及真实目标交付回调；这些被动字段不调度事件、不改变包或路径。
